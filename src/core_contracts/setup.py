"""Ingress-only setup control; no administrator database credentials or HA writes."""

import asyncio
import base64
import json
import os
import re
import secrets
import ssl
import struct
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote
from uuid import UUID, uuid4

import aiohttp
import asyncpg
from pydantic import Field, SecretStr, StrictBool, field_validator

from .identity import load_id, reconcile, write_private
from .model import Model, canonical
from .operations import OperationsSettings
from .persistence import TABLES


class Connection(Model):
    host: str = Field(min_length=1, max_length=253, pattern=r"^[a-zA-Z0-9_.:\-]+$")
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=63)
    user: str = Field(min_length=1, max_length=63)
    password: SecretStr = Field(max_length=4096)
    sslmode: Literal["verify-full", "require", "disable"] = "verify-full"
    ca: str = ""

    @field_validator("database")
    @classmethod
    def dedicated_database(cls, value: str) -> str:
        if value in {"postgres", "template0", "template1"}:
            raise ValueError("dedicated_database_required")
        return value

    @field_validator("ca")
    @classmethod
    def validate_ca(cls, value: str) -> str:
        if value and (not value.startswith("/ssl/") or ".." in Path(value).parts):
            raise ValueError("invalid_ca_path")
        return value

    def private(self) -> dict[str, Any]:
        return {**self.model_dump(), "password": self.password.get_secret_value()}

    def tls(self) -> ssl.SSLContext | bool:
        if self.sslmode == "disable":
            return False
        context = ssl.create_default_context(cafile=self.ca or None)
        if self.sslmode == "require":
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        return context

    def dsn(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return (
            f"postgresql://{quote(self.user, safe='')}:{quote(self.password.get_secret_value(), safe='')}"
            f"@{host}:{self.port}/{quote(self.database, safe='')}"
        )


class ConnectRequest(Model):
    connection: Connection
    confirm: StrictBool = False
    allow_insecure: StrictBool = False
    initialize_empty_database: StrictBool = False
    adopt_installation_id: str = ""


class ProvisionRequest(Model):
    host: str
    port: int = 5432
    database: str = "wuschel"
    user: str = "wuschel_app"
    sslmode: Literal["verify-full", "require", "disable"] = "verify-full"
    ca: str = ""


class ConfirmRequest(Model):
    confirm: StrictBool = False


async def configure_bridge(
    token: str, *, operations: OperationsSettings | None = None
) -> dict[str, Any]:
    """Supported HA config flow, fixed Bridge domain, after explicit confirmation."""
    if not token:
        raise ValueError("ha_unavailable")
    base = "http://supervisor/core/api/config/config_entries"
    headers = {"Authorization": "Bearer " + token}
    settings = operations or OperationsSettings()
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=settings.setup_bridge_timeout_s)
    ) as session:
        async with session.get(base + "/entry?domain=traber", headers=headers) as response:
            if response.status != 200:
                raise ValueError("ha_unavailable")
            entries = await response.json()
        if not isinstance(entries, list):
            raise ValueError("ha_unavailable")
        if any(entry.get("domain") == "traber" for entry in entries):
            return {"created": False, "state": "existing_entry"}
        async with session.post(
            base + "/flow", headers=headers, json={"handler": "traber"}
        ) as response:
            if response.status != 200:
                raise ValueError("bridge_not_discovered")
            flow = await response.json()
        if flow.get("type") == "abort":
            return {"created": False, "state": "existing_entry_or_aborted"}
        if (
            flow.get("handler") != "traber"
            or flow.get("type") != "form"
            or flow.get("step_id") != "user"
            or flow.get("data_schema") != []
            or not re.fullmatch(r"[a-zA-Z0-9_-]+", str(flow.get("flow_id", "")))
        ):
            raise ValueError("bridge_flow_changed")
        async with session.post(
            base + "/flow/" + flow["flow_id"], headers=headers, json={}
        ) as response:
            if response.status != 200:
                raise ValueError("bridge_configuration_failed")
            result = await response.json()
        if result.get("type") != "create_entry" or result.get("handler") != "traber":
            raise ValueError("bridge_configuration_failed")
        return {"created": True, "state": "configuration_created"}


async def reachability(
    host: str, port: int, *, operations: OperationsSettings | None = None
) -> dict[str, Any]:
    """PostgreSQL SSLRequest: no startup credentials, version remains unknown."""
    async with asyncio.timeout((operations or OperationsSettings()).setup_reachability_timeout_s):
        reader, writer = await asyncio.open_connection(host, port)
        try:
            writer.write(struct.pack("!II", 8, 80877103))
            await writer.drain()
            reply = await reader.readexactly(1)
            if reply not in {b"S", b"N"}:
                raise ValueError("not_postgres")
            return {"reachable": True, "tls_available": reply == b"S", "server_version": None}
        finally:
            writer.close()
            await writer.wait_closed()


async def probe(
    connection: Connection, *, resume: bool = False, operations: OperationsSettings | None = None
) -> dict[str, Any]:
    """Authenticated read-only probe, before the migration runner touches any schema."""
    settings = operations or OperationsSettings()
    client = await asyncpg.connect(
        connection.dsn(),
        ssl=connection.tls(),
        timeout=settings.database_connect_timeout_s,
        command_timeout=settings.database_command_timeout_s,
    )
    try:
        async with client.transaction(readonly=True):
            version = int(await client.fetchval("SHOW server_version_num"))
            if version < 140000:
                raise ValueError("postgres_version_unsupported")
            if await client.fetchval(
                "SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls "
                "FROM pg_roles WHERE rolname=current_user"
            ):
                raise ValueError("application_role_too_privileged")
            tables = set(
                await client.fetchval(
                    "SELECT coalesce(array_agg(c.relname), ARRAY[]::text[]) "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S','f')"
                )
            )
            other_schemas = await client.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname NOT IN "
                "('public','information_schema') AND nspname NOT LIKE 'pg_%')"
            )
            public_functions = await client.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n "
                "ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname<>'cc_index_observation')"
            )
            identity = None
            if "cc_meta" in tables:
                value = await client.fetchval(
                    "SELECT value FROM public.cc_meta WHERE key='installation_id'"
                )
                identity = json.loads(value) if value else None
            allowed = set(TABLES) | {"cc_meta", "cc_schema_migrations", "publication"}
            if identity:
                allowed |= {
                    "cc_observation_index",
                    "cc_observation_rollup",
                    "cc_evidence_watermark",
                }
            if (
                tables - allowed
                or (tables and not identity and not resume)
                or other_schemas
                or public_functions
            ):
                raise ValueError("foreign_database")
            if identity and UUID(identity).version != 4:
                raise ValueError("invalid_installation_id")
            return {"server_version": version, "database_id": identity, "empty": not tables}
    finally:
        await client.close()


async def bridge_status(
    token: str, *, operations: OperationsSettings | None = None
) -> dict[str, Any]:
    if not token:
        return {"state": "unavailable", "release_blocker": True}
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(
                total=(operations or OperationsSettings()).setup_status_timeout_s
            )
        ) as session:
            async with session.ws_connect("ws://supervisor/core/websocket") as socket:
                await socket.receive_json()
                await socket.send_json({"type": "auth", "access_token": token})
                if (await socket.receive_json()).get("type") != "auth_ok":
                    return {"state": "authorization_failed", "release_blocker": True}
                await socket.send_json({"id": 1, "type": "traber/info"})
                result = await socket.receive_json()
                if result.get("success"):
                    info = result["result"]
                    selected, features = 1, []
                    compatible = info.get("protocol_version") == 1
                    if compatible:
                        await socket.send_json(
                            {"id": 2, "type": "traber/negotiate", "supported_versions": [2, 1]}
                        )
                        negotiation = await socket.receive_json()
                        if negotiation.get("success"):
                            selected = negotiation.get("result", {}).get("protocol_selected")
                            compatible = selected in (1, 2)
                            features = negotiation.get("result", {}).get("features", [])
                        elif negotiation.get("error", {}).get("code") != "unknown_command":
                            compatible = False
                    return {
                        "state": "active" if compatible else "incompatible",
                        "bridge_version": info.get("bridge_version"),
                        "protocol_selected": selected if compatible else None,
                        "features": features,
                        "release_blocker": not compatible,
                    }
                async with session.get(
                    "http://supervisor/core/api/config/config_entries/flow_handlers",
                    headers={"Authorization": "Bearer " + token},
                ) as response:
                    known = await response.json() if response.status == 200 else None
                if known is None:
                    return {"state": "unavailable", "release_blocker": True}
                if "traber" in known:
                    state = "installed_not_configured"
                else:
                    # This API cannot see unscanned files. Never claim filesystem absence.
                    state = "missing_or_restart_pending"
                return {"state": state, "release_blocker": True}
    except aiohttp.ClientError, TimeoutError, ValueError, KeyError:
        return {"state": "unavailable", "release_blocker": True}


class Setup:
    def __init__(
        self,
        data: Path,
        legacy: Connection | None,
        *,
        configuration_error: str = "",
        operations: OperationsSettings | None = None,
    ) -> None:
        self.data = data
        self.operations = operations or OperationsSettings()
        self.directory = data / "setup"
        self.path = self.directory / "connection.json"
        self.csrf = secrets.token_urlsafe(32)
        self.lock = asyncio.Lock()
        self.changed = asyncio.Event()
        self.error = ""
        self.completed = False
        self.restart_required = False
        self.pending: dict[str, Any] = {}
        self.configuration_error = configuration_error
        if configuration_error:
            self.error = configuration_error
            self.connection: Connection | None = None
            self.authority = "supervisor"
            return
        if self.directory.exists():
            os.chmod(self.directory, 0o700)
        if self.path.exists():
            os.chmod(self.path, 0o600)
            self.pending = json.loads(self.path.read_text(encoding="utf-8"))
            self.connection = Connection.model_validate(self.pending["connection"])
            self.authority = "wizard"
        else:
            self.connection = legacy
            self.authority = "supervisor" if legacy else "unconfigured"

    def status(self) -> dict[str, Any]:
        onboarding = self.directory / "onboarding.json"
        finished = (
            json.loads(onboarding.read_text()).get("completed", False)
            if onboarding.exists()
            else self.authority == "supervisor"
        )
        return {
            "phase": "configuration_error"
            if self.configuration_error
            else "completed"
            if self.completed
            else "connecting"
            if self.connection
            else "welcome",
            "database_configured": self.connection is not None,
            "onboarding_complete": finished,
            "authority": self.authority,
            "installation_id": load_id(self.data),
            "error": self.error,
            "restart_required": self.restart_required,
            "csrf_token": self.csrf,
            "database": {k: v for k, v in self.connection.private().items() if k != "password"}
            if self.connection
            else None,
        }

    async def dispatch(self, path: str, method: str, body: Any) -> Any:
        if path == "status" and method == "GET":
            return self.status()
        if self.configuration_error:
            raise ValueError("invalid_supervisor_options")
        if path == "finish" and method == "POST":
            if not ConfirmRequest.model_validate(body).confirm:
                raise ValueError("confirmation_required")
            if not self.completed:
                raise ValueError("database_setup_pending")
            if (
                await bridge_status(
                    os.environ.get("SUPERVISOR_TOKEN", ""), operations=self.operations
                )
            )["state"] != "active":
                raise ValueError("bridge_setup_pending")
            write_private(self.directory / "onboarding.json", canonical({"completed": True}))
            return {"completed": True}
        if path == "bridge" and method == "GET":
            return await bridge_status(
                os.environ.get("SUPERVISOR_TOKEN", ""), operations=self.operations
            )
        if path == "bridge_configure" and method == "POST":
            if not ConfirmRequest.model_validate(body).confirm:
                raise ValueError("confirmation_required")
            return await configure_bridge(
                os.environ.get("SUPERVISOR_TOKEN", ""), operations=self.operations
            )
        if path == "reachability" and method == "POST":
            request = ProvisionRequest.model_validate(body)
            checked = Connection.model_validate({**request.model_dump(), "password": ""})
            return await reachability(checked.host, checked.port, operations=self.operations)
        if path == "provision" and method == "POST":
            request = ProvisionRequest.model_validate(body)
            # Reuse the exact request/marker after refresh or interrupted setup.
            Connection.model_validate({**request.model_dump(), "password": "validation-only"})
            from .provision import identifier

            identifier(request.database)
            identifier(request.user)
            target = self.directory / "provision.json"
            async with self.lock:
                previous = json.loads(target.read_text()) if target.exists() else None
                spec = request.model_dump()
                if not previous or previous["connection"] != spec:
                    previous = {"request_id": str(uuid4()), "connection": spec}
                    write_private(target, canonical(previous))
                template = Path(__file__).with_name("provision.py").read_text(encoding="utf-8")
                program = template.replace("REQUEST = None", f"REQUEST = {previous!r}")
                return {
                    "script": "sudo -H -u postgres python3 - <<'CORE_CONTRACTS_SETUP'\n"
                    + program
                    + "\nCORE_CONTRACTS_SETUP\n",
                    "request_id": previous["request_id"],
                }
        if path == "import" and method == "POST":
            if (
                not isinstance(body, dict)
                or not isinstance(body.get("code"), str)
                or len(body["code"]) > 20000
            ):
                raise ValueError("invalid_import")
            try:
                decoded = json.loads(base64.b64decode(body["code"].strip(), validate=True))
            except ValueError, UnicodeError:
                raise ValueError("invalid_import") from None
            if not isinstance(decoded, dict):
                raise ValueError("invalid_import")
            target = self.directory / "provision.json"
            if not target.exists():
                raise ValueError("provision_request_missing")
            expected = json.loads(target.read_text())
            if decoded.get("request_id") != expected["request_id"]:
                raise ValueError("provision_request_mismatch")
            connection = Connection.model_validate(decoded["connection"])
            if {k: v for k, v in connection.private().items() if k != "password"} != expected[
                "connection"
            ]:
                raise ValueError("provision_request_mismatch")
            # Return no secret to the browser; connect has the same confirmation policy.
            return await self.configure({**body, "connection": connection.private()})
        if path == "connect" and method == "POST":
            return await self.configure(body)
        raise ValueError("unknown_setup_operation")

    async def configure(self, body: Any) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise ValueError("invalid_setup_request")
        # Import's code is not part of the persisted configuration.
        request = ConnectRequest.model_validate({k: v for k, v in body.items() if k != "code"})
        if not request.confirm:
            raise ValueError("confirmation_required")
        if request.connection.sslmode != "verify-full" and not request.allow_insecure:
            raise ValueError("insecure_tls_confirmation_required")
        async with self.lock:
            result = await probe(request.connection, operations=self.operations)
            local = load_id(self.data)
            database = result["database_id"]
            if not database and not request.initialize_empty_database:
                raise ValueError("initialize_empty_database_required")
            identity = reconcile(
                local,
                database,
                initialize_empty_database=request.initialize_empty_database,
                adopt_installation_id=request.adopt_installation_id or None,
            )
            pending = {
                "connection": request.connection.private(),
                "prepared_identity": identity,
                "initialize_empty_database": not bool(database),
                "adopt_installation_id": database or "",
            }
            write_private(self.path, canonical(pending))
            self.pending = pending
            if self.completed:
                self.restart_required = True
            else:
                self.connection = request.connection
                self.authority = "wizard"
                self.error = ""
                self.changed.set()
            return {
                "accepted": True,
                "restart_required": self.restart_required,
                "server_version": result["server_version"],
            }

    def initialized(self) -> None:
        self.completed = True
        self.error = ""
        if self.authority == "wizard":
            self.pending["initialize_empty_database"] = False
            self.pending["adopt_installation_id"] = ""
            write_private(self.path, canonical(self.pending))
