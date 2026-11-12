"""ADR 0004: one admin-only, opt-in restart to the declared initial state."""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import Field, field_validator

from . import slots
from .model import Model, checksum, utc
from .persistence import State
from .registry import (
    RegistryConfig,
    fingerprints,
    registry_types,
    resolve_ref,
)

if TYPE_CHECKING:
    from .runtime import Runtime

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runtime import Runtime


class RestartRequest(Model):
    command_id: str = Field(min_length=1, max_length=128)
    contract_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=256)
    confirm: bool = Field(strict=True)
    issued_at: datetime
    valid_until: datetime
    expected_registry_revision: int = Field(ge=1, strict=True)
    expected_state_token: str = Field(pattern=r"^[a-f0-9]{64}$")

    _times = field_validator("issued_at", "valid_until")(utc)

    @field_validator("reason")
    @classmethod
    def nonempty_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("restart_reason_required")
        return value


def preview(runtime: Runtime, state: State, contract_id: str) -> dict[str, Any]:
    config = runtime._config(state)
    contract = (
        next((c for c in config.contracts if c.contract_id == contract_id), None)
        if config
        else None
    )
    declaration = (
        registry_types(config, runtime.types).get(contract.type_id, contract.type_version)
        if config and contract
        else None
    )
    machine = (
        declaration.machine_factory(contract.parameters)
        if declaration and declaration.machine_factory and contract
        else None
    )
    current = state.tables["node_state"].get(contract_id, {}).get("machine")
    reason = (
        "not_supported"
        if not machine
        else "contract_disabled"
        if not contract or not contract.enabled
        else "restart_not_enabled"
        if not machine.definition.allow_manual_restart
        else ""
    )
    direct: list[str] = []
    indirect: list[str] = []
    if config:
        graph = {
            c.contract_id: {resolve_ref(i.ref, config)[0] for i in c.inputs}
            for c in config.contracts
        }
        direct = sorted(key for key, refs in graph.items() if contract_id in refs)
        visited = {contract_id, *direct}
        queue = list(direct)
        while queue:
            current_id = queue.pop(0)
            for key, refs in graph.items():
                if current_id in refs and key not in visited:
                    visited.add(key)
                    indirect.append(key)
                    queue.append(key)
    return {
        "contract_id": contract_id,
        "allowed": not reason,
        "reason": reason,
        "initial": machine.definition.initial if machine else None,
        "current_state": current.get("state") if current else None,
        "previous_episode": current.get("episode_id") if current else None,
        "registry_revision": state.active_revision,
        "state_token": checksum(current),
        "direct_contracts": direct,
        "indirect_contracts": sorted(indirect),
        "external_automations_known": False,
        "server_time": runtime.clock.now_utc().isoformat(),
    }


async def execute(runtime: Runtime, state: State, payload: dict[str, Any]) -> dict[str, Any]:
    actor = payload.get("_actor", {})
    if actor.get("role") != "admin":
        raise ValueError("admin_required")
    request = RestartRequest.model_validate(payload["request"])
    digest = checksum(
        {"operation": "manual_restart", "request": request.model_dump(mode="json"), "actor": actor}
    )
    stored = await runtime.store.get("command_log", request.command_id)
    if stored:
        return stored["result"] if stored["digest"] == digest else {"status": "command_id_conflict"}
    now = runtime.clock.now_utc()
    checked = preview(runtime, state, request.contract_id)
    result: dict[str, Any] = {"command_id": request.command_id, "status": "rejected"}
    if request.valid_until <= now:
        result["status"] = "expired"
    elif request.issued_at > now or request.valid_until <= request.issued_at or not request.confirm:
        result["reason"] = "invalid_value"
    elif request.expected_registry_revision != state.active_revision:
        result["reason"] = "active_revision_conflict"
    elif request.expected_state_token != checked["state_token"]:
        result["reason"] = "machine_state_conflict"
    elif not checked["allowed"]:
        result["reason"] = checked["reason"]
    else:
        config = runtime._config(state)
        assert config is not None
        contract = next(c for c in config.contracts if c.contract_id == request.contract_id)
        factory = (
            registry_types(config, runtime.types)
            .get(contract.type_id, contract.type_version)
            .machine_factory
        )
        assert factory is not None
        updated = (
            factory(contract.parameters)
            .initial(
                now, state.active_revision, fingerprints(config, runtime.types)[request.contract_id]
            )
            .model_copy(
                update={
                    "origin": "manual",
                    "trigger": "manual_restart",
                    "source": "administrator",
                    "transition_reason": "manual_restart",
                    "manual_restart": True,
                    "manual_restart_at": now,
                    "previous_state": checked["current_state"],
                }
            )
        )
        proposed = state.clone()
        failures = runtime._evaluate(
            proposed, config, origin="command", machine_override={request.contract_id: updated}
        )
        if request.contract_id in failures:
            runtime.pending_latches = None
            runtime.pending_refresh.clear()
            result["reason"] = "evaluation_error"
        else:
            state = proposed
            old_episode = checked["previous_episode"]
            if old_episode:
                old = await runtime.store.get("sm_episode", old_episode)
                if old:
                    state.tables["sm_episode"][old_episode] = {
                        **old,
                        "episode_ended_at": now.isoformat(),
                    }
            result.update(
                status="accepted",
                origin="manual",
                previous_episode=old_episode,
                episode_id=updated.episode_id,
                initial_state=updated.state,
                registry_revision=state.active_revision,
                manual_restart=True,
            )
    command = {
        **request.model_dump(mode="json"),
        "command": "manual_restart",
        "origin": {"kind": "manual", "actor": actor},
    }
    state.tables["command_log"][request.command_id] = {
        "digest": digest,
        "result": result,
        "at": now.isoformat(),
        "command": command,
    }
    runtime._audit(
        state,
        "manual_restart",
        {"_actor": actor},
        contract_id=request.contract_id,
        command_id=request.command_id,
        reason=request.reason,
        origin="manual",
        previous_episode=checked["previous_episode"],
        episode_id=result.get("episode_id"),
        result=result,
    )
    await runtime._commit(state, publication=result["status"] == "accepted")
    return result


async def operation_manual_restart_preview(
    runtime: Runtime,
    state: State,
    operation: str,
    payload: Any,
    now: datetime,
    config: RegistryConfig | None,
) -> Any:
    from .manual_restart import preview

    return preview(runtime, state, payload)


slots.OPERATIONS["manual_restart_preview"] = slots.Operation(
    operation_manual_restart_preview, state="full", early=False
)


async def operation_manual_restart(
    runtime: Runtime,
    state: State,
    operation: str,
    payload: Any,
    now: datetime,
    config: RegistryConfig | None,
) -> Any:
    from .manual_restart import execute

    return await execute(runtime, state, payload)


slots.OPERATIONS["manual_restart"] = slots.Operation(
    operation_manual_restart, state="full", early=False
)
