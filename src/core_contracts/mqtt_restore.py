"""Previously accepted device evidence can age across restart; restore is no report."""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from . import slots
from .evidence import Observation, assess
from .persistence import State
from .registry import (
    RegistryConfig,
    fingerprints,
)
from .schema import TypeRegistry

if TYPE_CHECKING:
    from .runtime import Runtime


def eligible(observation: Observation, config: RegistryConfig, now: datetime) -> bool:
    source = next((s for s in config.sources if s.source_id == observation.source_id), None)
    binding = next((b for b in config.bindings if b.binding_id == observation.binding_id), None)
    return bool(
        source
        and binding
        and binding.source_id == source.source_id
        and binding.adapter.kind == "mqtt"
        and binding.adapter.mqtt_time_path
        and observation.adapter == "mqtt"
        and observation.device_time
        and source.freshness.mode == "report_heartbeat"
        and observation.normalized
        and not observation.mqtt_retained
        and not observation.ha_restored
        and not observation.assumed_state
        and not observation.invalid_timestamp
        and not observation.transform_error
        and assess(observation, source.freshness, now, source.source_id).status == "valid"
    )


def remember(
    observation: Observation, config: RegistryConfig, types: TypeRegistry, now: datetime
) -> Observation:
    clean = observation.model_copy(update={"mqtt_device_proof": None, "mqtt_revalidated": False})
    if clean.observation_kind == "report" and eligible(clean, config, now):
        return clean.model_copy(
            update={"mqtt_device_proof": fingerprints(config, types)[clean.source_id]}
        )
    return clean


def retained_replay(
    incoming: Observation,
    previous: Observation | None,
    config: RegistryConfig,
    types: TypeRegistry,
    now: datetime,
) -> bool:
    """An identical retained delivery says nothing about an accepted measurement.

    Preserve its original proof, reference and clock; never admit retained data
    as a new positive report, even when its timestamp happens to look fresh.
    """
    from .model import canonical

    return bool(
        previous
        and incoming.mqtt_retained
        and incoming.adapter == previous.adapter == "mqtt"
        and incoming.source_id == previous.source_id
        and incoming.binding_id == previous.binding_id
        and incoming.device_time is not None
        and incoming.device_time == previous.device_time
        and incoming.availability == "available"
        and incoming.normalized
        and not incoming.ha_restored
        and not incoming.assumed_state
        and not incoming.invalid_timestamp
        and not incoming.transform_error
        and canonical(incoming.value_raw) == canonical(previous.value_raw)
        and canonical(incoming.value) == canonical(previous.value)
        and previous.mqtt_device_proof
        and previous.mqtt_device_proof == fingerprints(config, types).get(previous.source_id)
        and eligible(previous, config, now)
    )


def restore(
    observation: Observation, config: RegistryConfig, types: TypeRegistry, now: datetime
) -> Observation:
    signature = fingerprints(config, types).get(observation.source_id)
    candidate = observation.model_copy(
        update={
            "mqtt_revalidated": bool(
                observation.mqtt_device_proof and observation.mqtt_device_proof == signature
            )
        }
    )
    allowed = bool(candidate.mqtt_revalidated and eligible(candidate, config, now))
    return candidate.model_copy(update={"mqtt_revalidated": allowed, "observation_kind": "restore"})


async def restore_observations(runtime: Runtime, state: State, reason: Any) -> None:
    if reason == "process_restart":
        from .mqtt_restore import restore as restore_mqtt

        for binding_id, observation in list(state.tables["source_observation_current"].items()):
            if observation["adapter"] == "mqtt" and runtime.config:
                restored = restore_mqtt(
                    Observation.model_validate(observation),
                    runtime.config,
                    runtime.types,
                    runtime.clock.now_utc(),
                )
                state.tables["source_observation_current"][binding_id] = restored.storage_document()
            elif observation["adapter"] not in {"ha_calendar", "manual"}:
                observation["observation_kind"] = "restore"


slots.RESTORE[60] = slots.Hook(restore_observations, "observations")


slots.OBSERVATION[10] = slots.Hook(retained_replay, "filter")
slots.OBSERVATION[20] = slots.Hook(remember, "remember")
