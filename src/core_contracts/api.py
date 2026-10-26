"""Two listeners with independent ingress and bearer trust boundaries."""

import asyncio
import logging
import re
import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiohttp
import asyncpg
from aiohttp import WSMsgType, web
from pydantic import ValidationError

from . import __version__
from .authoring import AuthoringError
from .capabilities import capabilities, configuration_schema, describe_types, reasons
from .history import HISTORY_TABLES, HistoryQuery
from .model import canonical
from .registry import fingerprints, registry_types
from .retention import EvidenceOutsideRetention
from .runtime import Runtime
from .security import RateLimiter, Tokens
from .setup import Setup
from .validation import RegistryValidationError


class API:
    def __init__(
        self,
        runtime: Runtime,
        tokens: Tokens,
        installation_id: str,
        *,
        installation_label: str = "",
        ingress_address: str = "172.30.32.2",
        frontend: Path | None = None,
        setup: Setup | None = None,
    ) -> None:
        self.runtime, self.tokens, self.installation_id = runtime, tokens, installation_id
        self.installation_label, self.ingress_address, self.frontend = (
            installation_label,
            ingress_address,
            frontend,
        )
        self.limiter = RateLimiter(runtime.clock)
        self.setup = setup
        runtime.configuration_error = bool(setup and setup.configuration_error)
        self.auth_limiter = RateLimiter(runtime.clock)
        self.sockets: set[web.WebSocketResponse] = set()

    def application(self, *, ingress: bool = False) -> web.Application:
        @web.middleware
        async def boundary(
            request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
        ) -> web.StreamResponse:
            if ingress:
                if request.remote != self.ingress_address:
                    raise web.HTTPForbidden()
                role = "admin"
            elif request.path.startswith("/health/"):
                role = "health"
            else:
                if not self.auth_limiter.allow(f"auth:{request.remote}"):
                    raise web.HTTPTooManyRequests()
                role = self.tokens.role(request.headers.get("Authorization", "")) or ""
                if not role:
                    raise web.HTTPUnauthorized()
            if role != "health" and not self.limiter.allow(f"{request.remote}:{role}"):
                raise web.HTTPTooManyRequests()
            request["role"] = role
            # No client identity header is accepted. The ingress peer is checked
            # above; bearer identities are represented by their authenticated role.
            request["actor"] = {"kind": "ingress" if ingress else "token", "role": role}
            setup_path = request.path.startswith("/api/v1/setup/")
            static_path = ingress and not request.path.startswith(("/api/", "/health/"))
            if setup_path and (not ingress or self.setup is None):
                raise web.HTTPNotFound()
            if setup_path and request.method != "GET":
                if (
                    request.content_type != "application/json"
                    or request.headers.get("Sec-Fetch-Site") == "cross-site"
                    or not secrets.compare_digest(
                        request.headers.get("X-Setup-CSRF", ""),
                        self.setup.csrf if self.setup else "",
                    )
                ):
                    raise web.HTTPForbidden()
            if not self.installation_id and role != "health" and not (setup_path or static_path):
                raise web.HTTPServiceUnavailable()
            if request.method not in {"GET", "HEAD"} and request.path != "/api/v1/commands":
                if role != "admin":
                    raise web.HTTPForbidden()
                # Ingress does not expose bearer credentials to JS; reject cross-origin writes.
                if ingress and request.headers.get("Sec-Fetch-Site") == "cross-site":
                    raise web.HTTPForbidden()
            try:
                response = await handler(request)
            except AuthoringError as error:
                response = web.json_response(
                    {"error": error.code, "cause": error.code, "details": error.details}, status=409
                )
            except RegistryValidationError as error:
                response = web.json_response(
                    {
                        "error": "invalid_request",
                        "valid": False,
                        "issues": [issue.model_dump() for issue in error.issues],
                        "details": [
                            {
                                "location": issue.path.split("/parameters/", 1)[1].split("/"),
                                "type": issue.code.removeprefix("parameter."),
                            }
                            for issue in error.issues
                            if issue.code.startswith("parameter.") and "/parameters/" in issue.path
                        ],
                    },
                    status=400,
                )
            except (KeyError, ValueError, ValidationError) as error:
                # Do not reflect input, DSNs, credentials, or Pydantic's input_value.
                code = (
                    str(error)
                    if type(error) is ValueError
                    and str(error)
                    in {
                        "active_revision_conflict",
                        "catalog_master_invalid",
                        "catalog_variant_override",
                        "catalog_class_invalid",
                        "catalog_key_empty",
                        "catalog_expansion_limit",
                        "catalog_retired",
                        "catalog_definition_missing",
                        "catalog_evaluation_failed",
                        "import_format_unknown",
                        "import_legacy_format_unknown",
                        "import_digest_mismatch",
                        "import_object_digest_mismatch",
                        "import_duplicate_key",
                        "import_nonfinite",
                        "import_size_limit",
                        "import_catalog_definition",
                        "import_catalog_duplicate",
                        "import_remap_conflict",
                        "import_ambiguous_remap",
                        "import_unresolved_conflicts",
                        "import_target_draft_required",
                        "import_target_has_plan",
                        "import_entry_limit",
                        "catalog_content_conflict",
                        "catalog_request_conflict",
                        "catalog_definition_conflict",
                        "import_plan_conflict",
                        "draft_version_conflict",
                        "editorial_version_conflict",
                        "editorial_scope_conflict",
                        "editorial_object_not_found",
                        "editorial_draft_limit",
                        "editorial_restore_conflict",
                        "draft_limit",
                        "draft_identity_conflict",
                        "cycle",
                        "contract_disabled",
                        "config_incomplete: type or parameters",
                        "config_incomplete: inputs",
                        "duplicate identity / producer",
                        "duplicate adapter evidence path",
                        "duplicate physical evidence path",
                        "unknown reference",
                        "unknown catalog",
                        "unknown liveness source",
                        "unbound source",
                        "disabled required dependency",
                        "revision_not_found",
                        "scheduler source adapter not implemented",
                        "dependency monitor source required",
                        "dependency monitor cycle",
                        "unbound dependency monitor",
                        "unsupported internal dependency monitor",
                        "refresh requires HA source binding",
                    }
                    else "invalid_request"
                )
                if type(error) is ValueError and str(error) in {"sensitive key", "credential URL"}:
                    code = "sensitive_document"
                response = web.json_response(
                    {
                        "error": code,
                        "details": [
                            {"location": list(item["loc"]), "type": item["type"]}
                            for item in error.errors(
                                include_input=False, include_context=False, include_url=False
                            )
                        ]
                        if isinstance(error, ValidationError)
                        else [],
                    },
                    status=409 if "conflict" in code else 400,
                )
            except RuntimeError, OSError, asyncpg.PostgresError, asyncpg.InterfaceError:
                response = web.json_response({"error": "persistence_unavailable"}, status=503)
            response.headers.setdefault("Cache-Control", "no-store")
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; connect-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'self'"
            )
            return response

        app = web.Application(middlewares=[boundary], client_max_size=50 * 1024 * 1024)
        app.on_shutdown.append(self.close_sockets)
        app.router.add_get("/health/live", self.health)
        app.router.add_get("/health/ready", self.health)
        app.router.add_get("/api/v1/ws", self.websocket)
        if ingress and self.setup:
            app.router.add_route("*", "/api/v1/setup/{operation}", self.setup_dispatch)
        app.router.add_route("*", "/api/v1/{path:.*}", self.dispatch)
        if ingress:
            app.router.add_get("/{path:.*}", self.static)
        return app

    async def setup_dispatch(self, request: web.Request) -> web.Response:
        assert self.setup is not None
        try:
            result = await self.setup.dispatch(
                request.match_info["operation"],
                request.method,
                await request.json() if request.method != "GET" else None,
            )
            if request.match_info["operation"] == "status" and request.method == "GET":
                result["registry_revision"] = (
                    self.runtime.state.active_revision if self.runtime else None
                )
            return web.json_response(result)
        except aiohttp.ClientError:
            return web.json_response({"error": "ha_connection_failed"}, status=503)
        except asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError:
            return web.json_response({"error": "database_connection_failed"}, status=503)
        except (ValueError, RuntimeError) as error:
            safe = {
                "invalid_supervisor_options",
                "confirmation_required",
                "insecure_tls_confirmation_required",
                "initialize_empty_database_required",
                "adopt_installation_id_required",
                "installation_mismatch",
                "foreign_database",
                "application_role_too_privileged",
                "postgres_version_unsupported",
                "provision_request_mismatch",
                "provision_request_missing",
                "ha_unavailable",
                "bridge_not_discovered",
                "bridge_flow_changed",
                "bridge_configuration_failed",
                "database_setup_pending",
                "bridge_setup_pending",
            }
            return web.json_response(
                {
                    "error": str(error)
                    if type(error) is ValueError and str(error) in safe
                    else "invalid_setup_request"
                },
                status=400,
            )

    async def close_sockets(self, app: web.Application) -> None:
        async def close(socket: web.WebSocketResponse) -> None:
            try:
                async with asyncio.timeout(2):
                    await socket.send_json({"type": "going_away"})
                    await socket.close(code=1001, message=b"shutdown")
            except TimeoutError, ConnectionError:
                pass

        await asyncio.gather(*(close(socket) for socket in list(self.sockets)))

    async def health(self, request: web.Request) -> web.Response:
        runtime = self.runtime
        if request.path.endswith("live"):
            return web.json_response({"live": runtime.live}, status=200 if runtime.live else 503)
        return web.json_response(runtime.service_state(), status=200 if runtime.ready else 503)

    def info(self) -> dict[str, Any]:
        return {
            "version": __version__,
            "api_protocol": 1,
            "authoring_protocol": 2,
            "bridge_protocol": 1,
            "config_schema_version": 1,
            "installation_id": self.installation_id,
            "installation_label": self.installation_label,
            "epoch_id": self.runtime.state.epoch_id,
            "publication_seq": self.runtime.state.publication_seq,
            "server_time": self.runtime.clock.now_utc().isoformat(),
            "command_time": {
                "clock": "server_utc",
                "issued_at_must_not_be_future": True,
                "valid_until_exclusive": True,
                "client_guidance": "measure round-trip uncertainty",
            },
        }

    @staticmethod
    async def object_body(request: web.Request) -> dict[str, Any]:
        value = await request.json()
        if not isinstance(value, dict):
            raise ValueError("request object required")
        return value

    async def dispatch(self, request: web.Request) -> web.Response:
        path, method = request.match_info["path"], request.method
        runtime, state, config = self.runtime, self.runtime.state, self.runtime.config
        public = path in {
            "info",
            "types",
            "capabilities",
            "reasons",
            "contracts",
            "snapshot",
            "diagnostics/problems",
            "commands",
        } or path.startswith(("contracts/", "commands/"))
        if not public and request["role"] != "admin":
            raise web.HTTPForbidden()
        if (
            method not in {"GET", "HEAD"}
            and path.startswith(("registry/drafts", "registry/continuations", "portable/stage"))
            and request.headers.get("X-Authoring-Protocol") != "2"
        ):
            raise AuthoringError("authoring_upgrade_required")
        result: Any
        if path == "info" and method == "GET":
            result = self.info()
        elif path == "types" and method == "GET":
            result = describe_types(config, runtime.types)
        elif path == "capabilities" and method == "GET":
            result = capabilities(config, runtime.types)
        elif path == "reasons" and method == "GET":
            result = reasons()
        elif path == "registry/schema" and method == "GET":
            result = configuration_schema()
        elif path == "registry/freshness" and method == "POST":
            from .freshness_templates import effective_freshness
            from .registry import validate

            result = effective_freshness(validate(await self.object_body(request), runtime.types))
        elif path in {"portable/export", "portable/preview", "portable/stage"} and method == "POST":
            from .portable import strict_json

            body = strict_json(await request.text())
            if not isinstance(body, dict):
                raise ValueError("import_object_required")
            result = await runtime.submit(
                path.replace("/", "_"), {**body, "_actor": request["actor"]}
            )
        elif path == "catalogs" and method == "GET":
            from .catalog_models import ManagedCatalog

            result = (
                [
                    c.model_dump(mode="json")
                    for c in config.catalogs
                    if isinstance(c, ManagedCatalog)
                ]
                if config
                else []
            )
        elif path == "catalogs/entries" and method == "GET":
            from .managed_catalog import definition, page

            catalog = definition(
                config, request.query["catalog_id"], int(request.query["definition_version"])
            )
            result = page(
                state,
                catalog,
                query=request.query.get("q", ""),
                view=request.query.get("view", "all"),
                after=request.query.get("after", ""),
                limit=int(request.query.get("limit", "100")),
                filters={
                    key: request.query.get(key, "")
                    for key in ("scope", "media_type", "signal_type")
                },
            )
        elif path == "catalogs/entries" and method == "POST":
            result = await runtime.submit(
                "catalog_write",
                {"request": await self.object_body(request), "_actor": request["actor"]},
            )
        elif path == "catalogs/preview" and method == "POST":
            result = await runtime.submit(
                "catalog_preview",
                {"request": await self.object_body(request), "_actor": request["actor"]},
            )
        elif path == "catalogs/entry" and method == "GET":
            from .managed_catalog import definition, entry_view

            catalog = definition(
                config, request.query["catalog_id"], int(request.query["definition_version"])
            )
            result = entry_view(state, catalog, request.query["identity"])
        elif path == "catalogs/history" and method == "GET":
            from .managed_catalog import definition, head, head_key

            catalog = definition(
                config, request.query["catalog_id"], int(request.query["definition_version"])
            )
            revision = head(state, catalog)["revision"]
            before = min(int(request.query.get("after") or revision + 1), revision + 1)
            rows = await runtime.store.catalog_revisions(head_key(catalog), before)
            result = [
                {**row, "head_revision": revision, "active_revision": state.active_revision}
                for row in rows
            ]
        elif path == "catalogs/revision" and method == "GET":
            result = await runtime.submit(
                "catalog_revision",
                {
                    "catalog_id": request.query["catalog_id"],
                    "definition_version": int(request.query["definition_version"]),
                    "revision": int(request.query["revision"]),
                    "after": request.query.get("after", ""),
                },
            )
        elif path == "registry/validate" and method == "POST":
            result = await runtime.submit("validate", await request.json())
        elif path == "definitions" and method == "GET":
            result = [d.model_dump(mode="json") for d in config.definitions] if config else []
        elif path == "editorial" and method == "GET":
            from .editorial import CATEGORIES

            draft_id = request.query.get("draft_id")
            result = {
                "items": list(state.tables["editorial_current"].values()),
                "draft_items": [
                    row
                    for row in state.tables["editorial_draft"].values()
                    if row["draft_id"] == draft_id
                ],
                "categories": CATEGORIES,
            }
        elif path == "editorial" and method == "PUT":
            result = await runtime.submit(
                "editorial_put", {"request": await request.json(), "_actor": request["actor"]}
            )
        elif path == "ha/contexts" and method == "GET":
            result = {
                "ha_epoch_id": runtime.context_index.epoch if runtime.context_index else None,
                "producers": [
                    producer.model_dump(mode="json") for producer in config.context_producers
                ]
                if config
                else [],
            }
        elif path == "ha/contexts" and method == "POST":
            result = await runtime.submit(
                "context_register", {"request": await request.json(), "_actor": request["actor"]}
            )
        elif path == "ha/entities" and method == "GET":
            if runtime.entity_discovery is None:
                return web.json_response({"error": "ha_discovery_unavailable"}, status=503)
            limit = int(request.query.get("limit", "100"))
            after = request.query.get("after", "")
            if not 1 <= limit <= 200 or len(after) > 256:
                raise ValueError("invalid discovery page")
            preview = request.query.get("preview", "0")
            if preview not in {"0", "1"}:
                raise ValueError("invalid discovery preview")
            try:
                entities = (
                    await runtime.entity_discovery(preview=True)
                    if preview == "1"
                    else await runtime.entity_discovery()
                )
            except aiohttp.ClientError, ConnectionError, TimeoutError, ValueError, TypeError:
                return web.json_response({"error": "ha_discovery_unavailable"}, status=503)
            selected_entities = [e for e in entities if e["entity_id"] > after]
            if preview == "1" and runtime.discovered_areas is not None:
                await runtime.submit("editorial_area_refresh", runtime.discovered_areas)
            result = {
                "items": selected_entities[:limit],
                "next_cursor": selected_entities[limit - 1]["entity_id"]
                if len(selected_entities) > limit
                else None,
                "read_only": True,
                **(
                    {"areas": runtime.discovered_areas}
                    if preview == "1" and runtime.discovered_areas is not None
                    else {}
                ),
            }
        elif path == "contracts" and method == "GET":
            result = runtime.snapshot()["contracts"]
        elif path.startswith("contracts/") and method == "GET":
            key = path.removeprefix("contracts/")
            if config and any(c.contract_id == key and not c.enabled for c in config.contracts):
                return web.json_response({"error": "contract_disabled"}, status=409)
            result = state.tables["contract_state_current"].get(key)
            if result is None:
                raise web.HTTPNotFound()
        elif path == "snapshot" and method == "GET":
            selected = (
                request.query["contracts"].split(",") if "contracts" in request.query else None
            )
            result = runtime.snapshot(selected)
        elif path == "commands" and method == "POST":
            result = await runtime.submit("command", await request.json())
        elif path.startswith("manual-restarts/preview/") and method == "GET":
            result = await runtime.submit(
                "manual_restart_preview", path.removeprefix("manual-restarts/preview/")
            )
        elif path.startswith("manual-sources/") and method in {"GET", "POST"}:
            parts = path.split("/")
            if len(parts) == 3 and parts[2] == "observations" and method == "POST":
                result = await runtime.submit(
                    "manual_source_enter",
                    {
                        "source_id": parts[1],
                        "request": await request.json(),
                        "_actor": request["actor"],
                    },
                )
            elif len(parts) == 4 and parts[2] == "requests" and method == "GET":
                record = await runtime.store.get("manual_request", parts[3])
                if record is None or record["source_id"] != parts[1]:
                    raise web.HTTPNotFound()
                result = record["result"]
            else:
                raise web.HTTPNotFound()
        elif path == "manual-restarts" and method == "POST":
            result = await runtime.submit(
                "manual_restart", {"request": await request.json(), "_actor": request["actor"]}
            )
        elif path.startswith("commands/") and method == "GET":
            result = await runtime.store.get("command_log", path.removeprefix("commands/"))
            if result is None:
                raise web.HTTPNotFound()
            result = result["result"]
        elif path == "sources/calendar-status" and method == "GET":
            from .service_jobs import calendar_status

            result = calendar_status(state, config)
        elif path == "diagnostics/problems" and method == "GET":
            result = {
                "configuration_problems": [
                    {"contract_id": k, "field": f, "configuration": v["configuration"]}
                    for k, c in state.tables["contract_state_current"].items()
                    for f, v in c["fields"].items()
                    if not v.get("configuration", {}).get("complete", True)
                ],
                "service": runtime.service_state(),
                "problems": [
                    {"contract_id": k, "field": f, "status": v["status"], "reasons": v["reasons"]}
                    for k, c in state.tables["contract_state_current"].items()
                    for f, v in c["fields"].items()
                    if v["status"] in {"unknown", "unresolved"}
                ],
            }
        elif path == "diagnostics" and method == "GET":
            result = {
                **self.info(),
                "service": runtime.service_state(),
                "queue_size": runtime.queue.qsize(),
                "commit_duration_s": runtime.commit_duration,
                "bridge_version": runtime.bridge_version,
                "bridge_protocol_selected": runtime.bridge_protocol_selected,
                "bridge_features": runtime.bridge_features,
                "bridge_subscriptions": runtime.bridge_subscriptions,
                "ingest_overflow_count": runtime.overflow_serial,
                "refresh_overflow_count": runtime.refresh_overflow,
                "gaps": await runtime.store.rows("history_gap"),
                "dependencies": [d.model_dump() for d in config.dependencies] if config else [],
            }
        elif path.startswith("diagnostics/contracts/") and method == "GET":
            key = path.removeprefix("diagnostics/contracts/")
            contract = (
                next((c for c in config.contracts if c.contract_id == key), None)
                if config
                else None
            )
            if contract is None or config is None:
                raise web.HTTPNotFound()
            declaration = registry_types(config, runtime.types).get(
                contract.type_id, contract.type_version
            )
            node = state.tables["node_state"].get(key, {})
            expected = fingerprints(config, runtime.types)[key]
            # Temporal contexts contain prior raw values: expose timing/identity
            # only, never dump node_state into a diagnostic response.
            temporal = []
            for identity, context in [("legacy", node), *node.get("rules", {}).items()]:
                if "temporal" in context:
                    temporal.append(
                        {
                            "id": identity,
                            "state": {
                                k: v
                                for k, v in context["temporal"].items()
                                if k
                                in {
                                    "fingerprint",
                                    "since_at",
                                    "grace_until",
                                    "grace_armed",
                                    "gap_at",
                                }
                            },
                            "restore_pending": "restore_pending" in context,
                        }
                    )
            machine = (
                declaration.machine_factory(contract.parameters)
                if declaration.machine_factory
                else None
            )
            due = (
                declaration.next_due(contract.parameters, node, runtime.clock.now_utc())
                if declaration.next_due
                else None
            )
            result = {
                "contract_id": key,
                "registry_revision": state.active_revision,
                "start_case": state.tables["contract_lifecycle"].get(key, {}).get("start_case"),
                "fingerprint": node.get("fingerprint"),
                "expected_fingerprint": expected,
                "fingerprint_compatible": node.get("fingerprint") == expected,
                "next_due": due.isoformat() if due else None,
                "temporal": temporal,
                "trace": node.get("trace"),
                "machine": {
                    "states": machine.definition.states,
                    "initial": machine.definition.initial,
                    "commands": machine.definition.commands,
                    "deadline_seconds": machine.definition.deadline_seconds,
                    "sessions": machine.definition.sessions,
                    "allow_manual_restart": machine.definition.allow_manual_restart,
                    "transitions": [
                        {
                            "source": t.source,
                            "target": t.target,
                            "event": t.event,
                            "reason": t.reason,
                            "new_episode": t.new_episode,
                            "ends_episode": t.ends_episode,
                        }
                        for t in machine.definition.transitions
                    ],
                    "current": node.get("machine"),
                    "recovery": node.get("machine_recovery"),
                }
                if machine
                else None,
            }
        elif path in {"history/retention/preview", "history/retention/run"} and method == "POST":
            result = await runtime.submit(
                "retention_preview" if path.endswith("preview") else "retention_run"
            )
        elif path == "history/rollups" and method == "GET":
            result = await runtime.store.rollups(HistoryQuery.model_validate(dict(request.query)))
        elif path.startswith("history/query/") and method == "GET":
            kind = path.removeprefix("history/query/")
            if kind not in HISTORY_TABLES:
                raise web.HTTPNotFound()
            result = await runtime.store.history(
                HISTORY_TABLES[kind], HistoryQuery.model_validate(dict(request.query))
            )
        elif path.startswith("evidence/reference/") and method == "GET":
            reference = path.removeprefix("evidence/reference/")
            if len(reference) > 1024:
                raise ValueError("invalid evidence reference")
            try:
                result = await runtime.store.evidence(reference)
            except EvidenceOutsideRetention as error:
                return web.json_response(
                    {
                        "error": "evidence_outside_retention",
                        "reference": error.reference,
                        "retained_after": error.cutoff,
                    },
                    status=410,
                )
            if result is None:
                return web.json_response(
                    {"error": "evidence_not_found", "reference": reference}, status=404
                )
        elif path in {"sources", "bindings"} and method == "GET":
            result = [v.model_dump(mode="json") for v in getattr(config, path)] if config else []
        elif path.startswith("evidence/") and method == "GET":
            source_id = path.removeprefix("evidence/")
            result = [
                v
                for v in state.tables["source_observation_current"].values()
                if v["source_id"] == source_id
            ]
        elif path.startswith("history/") and method == "GET":
            key = path.removeprefix("history/")
            result = await runtime.store.rows("contract_state_history", contract_id=key)
        elif path in {"registry/active", "registry/active/export"} and method == "GET":
            if path.endswith("export") and config is None:
                return web.json_response({"error": "no_active_registry"}, status=409)
            result = (
                config.model_dump(mode="json")
                if path.endswith("export") and config
                else state.tables["registry_revision"].get(str(state.active_revision))
            )
        elif path == "registry/revisions" and method == "GET":
            result = await runtime.store.rows("registry_revision")
        elif path.startswith("registry/revisions/") and method == "GET":
            result = await runtime.store.get("registry_revision", path.split("/")[-1])
        elif path.startswith("registry/activation-requests/") and method == "GET":
            receipt = await runtime.store.get("activation_receipt", path.split("/")[-1])
            result = (
                {"status": "completed", "receipt": receipt, "result": receipt["result"]}
                if receipt
                else {"status": "not_found"}
            )
        elif path.startswith("registry/continuations/"):
            parts = path.split("/")
            key = parts[2]
            if method == "GET" and len(parts) == 3:
                result = state.tables["draft_continuation"][key]
            elif len(parts) == 4 and (
                parts[3] == "decisions"
                and method == "PUT"
                or parts[3] in {"finish", "cancel"}
                and method == "POST"
            ):
                result = await runtime.submit(
                    "continuation_" + parts[3],
                    {
                        **await self.object_body(request),
                        "session_id": key,
                        "_actor": request["actor"],
                    },
                )
            else:
                raise web.HTTPNotFound()
        elif path == "registry/drafts" and method == "GET":
            from .authoring import view

            lifecycle = request.query.get("lifecycle")
            result = [
                view(state, d)
                for d in state.tables["registry_draft"].values()
                if not lifecycle or (d.get("lifecycle", "open") == "open") == (lifecycle == "open")
            ]
        elif path == "registry/drafts" and method == "POST":
            document = await request.json()
            payload = (
                document
                if isinstance(document, dict) and "config" in document
                else {"config": document}
            )
            if not isinstance(payload["config"], dict):
                raise ValueError("configuration object required")
            from .registry import safe_document

            safe_document(payload["config"])
            if type(payload.get("expected_active_revision")) is not int:
                raise ValueError("invalid_expected_revision")
            result = await runtime.submit("draft_create", {**payload, "_actor": request["actor"]})
        elif path.startswith("registry/drafts/"):
            parts = path.split("/")
            key = parts[2]
            if len(parts) == 3 and method == "GET":
                from .authoring import view

                result = view(state, state.tables["registry_draft"][key])
            elif len(parts) == 3 and method == "PUT":
                result = await runtime.submit(
                    "draft_update",
                    {
                        **await self.object_body(request),
                        "draft_id": key,
                        "_actor": request["actor"],
                    },
                )
            elif len(parts) == 3 and method == "DELETE":
                result = await runtime.submit(
                    "draft_delete",
                    {
                        **await self.object_body(request),
                        "draft_id": key,
                        "_actor": request["actor"],
                    },
                )
            elif len(parts) == 4 and parts[3] == "validate" and method == "POST":
                result = await runtime.submit(
                    "validate", state.tables["registry_draft"][key]["config"]
                )
                from .authoring import view

                draft_view = view(state, state.tables["registry_draft"][key])
                result.update(
                    content_valid=result["valid"],
                    base_current=draft_view["base_current"],
                    lifecycle=draft_view.get("lifecycle"),
                    base_status=draft_view.get("base_status"),
                )
            elif (
                len(parts) == 4
                and parts[3] in {"activation-preview", "complete", "continuations", "reconcile"}
                and method == "POST"
            ):
                operation = {
                    "activation-preview": "draft_preview",
                    "complete": "draft_complete",
                    "continuations": "continuation_start",
                    "reconcile": "draft_reconcile",
                }[parts[3]]
                result = await runtime.submit(
                    operation,
                    {
                        **await self.object_body(request),
                        "draft_id": key,
                        "_actor": request["actor"],
                    },
                )
            elif len(parts) == 4 and parts[3] == "activate" and method == "POST":
                result = await runtime.submit(
                    "activate",
                    {
                        **await self.object_body(request),
                        "draft_id": key,
                        "_actor": request["actor"],
                    },
                )
            else:
                raise web.HTTPMethodNotAllowed(method, ["GET", "PUT", "POST"])
        elif path == "registry/rollback" and method == "POST":
            result = await runtime.submit(
                "rollback", {**await self.object_body(request), "_actor": request["actor"]}
            )
        elif path == "tokens" and method == "GET":
            result = {"roles": ["consumer", "admin"], "tokens_visible": False}
        elif path == "tokens/rotate" and method == "POST":
            role = (await self.object_body(request))["role"]
            if role not in {"admin", "consumer"}:
                raise ValueError("invalid token role")
            rotation_id = str(uuid4())
            await runtime.submit(
                "audit",
                {
                    "operation": "token_rotation_requested",
                    "_actor": request["actor"],
                    "details": {"role": role, "rotation_id": rotation_id},
                },
            )
            result = {
                "role": role,
                "token": self.tokens.rotate(role),
                "status": "rotated",
                "rotation_id": rotation_id,
                "audit_status": "complete",
            }
            try:
                await runtime.submit(
                    "audit",
                    {
                        "operation": "token_rotated",
                        "_actor": request["actor"],
                        "details": {"role": role, "rotation_id": rotation_id},
                    },
                )
            except Exception:
                # The private token file has already committed. Never hide the
                # new credential behind an audit error or invite a blind retry.
                result["audit_status"] = "completion_unconfirmed"
                logging.getLogger(__name__).warning(
                    "token_rotation_audit_unconfirmed", extra={"rotation_id": rotation_id}
                )
        else:
            raise web.HTTPNotFound()
        return web.Response(text=canonical(result), content_type="application/json")

    async def websocket(self, request: web.Request) -> web.WebSocketResponse:
        socket = web.WebSocketResponse(max_msg_size=64_000, heartbeat=30)
        await socket.prepare(request)
        self.sockets.add(socket)
        queue = None
        sender = None
        selected: list[str] | None = None

        async def send_events() -> None:
            assert queue is not None
            while True:
                event = await queue.get()
                if event["type"] == "delta" and selected is not None:
                    event = {
                        **event,
                        "contracts": {k: v for k, v in event["contracts"].items() if k in selected},
                    }
                await socket.send_json(event)
                if event["type"] == "going_away":
                    await socket.close(code=1001)
                    return

        try:
            hello = await socket.receive_json(timeout=10)
            if hello.get("type") != "hello" or hello.get("protocol_version") != 1:
                await socket.close(code=1002)
                return socket
            if hello.get("expected_installation_id", self.installation_id) != self.installation_id:
                await socket.send_json({"type": "installation_mismatch"})
                await socket.close(code=1008)
                return socket
            await socket.send_json({"type": "welcome", **self.info(), "protocol_version": 1})
            await socket.send_json(self.runtime.service_state())
            async for message in socket:
                if message.type != WSMsgType.TEXT:
                    break
                data = message.json()
                if data.get("type") == "ping":
                    await socket.send_json({"type": "pong"})
                elif data.get("type") == "subscribe":
                    selected = data.get("contracts")
                    if selected is not None and (
                        not isinstance(selected, list)
                        or not all(isinstance(k, str) for k in selected)
                    ):
                        await socket.close(code=1002)
                        break
                    if sender:
                        sender.cancel()
                        await asyncio.gather(sender, return_exceptions=True)
                    if queue:
                        self.runtime.subscribers.discard(queue)
                    queue = self.runtime.subscribe()
                    # No await between queue registration and snapshot capture.
                    snapshot = self.runtime.snapshot(selected)
                    await socket.send_json({**snapshot, "id": data.get("id")})
                    sender = asyncio.create_task(send_events())
        except ValueError, TimeoutError:
            await socket.close(code=1002)
        finally:
            self.sockets.discard(socket)
            if queue:
                self.runtime.subscribers.discard(queue)
            if sender:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
        return socket

    async def static(self, request: web.Request) -> web.StreamResponse:
        if self.frontend is None:
            raise web.HTTPNotFound()
        root = self.frontend.resolve()
        target = (root / (request.match_info["path"] or "index.html")).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise web.HTTPNotFound()
        versioned = target.parent == root / "assets" and re.search(
            r"-[A-Za-z0-9_-]{8,}\.(?:js|css|woff2|svg|png)$", target.name
        )
        return web.FileResponse(
            target,
            headers={
                "Cache-Control": "public, max-age=31536000, immutable" if versioned else "no-store"
            },
        )
