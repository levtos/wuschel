"""Supervisor process bootstrap and supervised asynchronous services."""

import asyncio
import json
import logging
import os
import re
import signal
from pathlib import Path
from typing import Literal

import aiohttp
import asyncpg
from aiohttp import web
from pydantic import Field, SecretStr, ValidationError

from .adapters.ha import BridgeAdapter
from .adapters.mqtt import MQTTAdapter
from .api import API
from .clock import SystemClock
from .contract_types import type_registry
from .identity import load_id, reconcile, write_private
from .model import Model, canonical
from .persistence import PersistenceUnavailable, PostgresStore
from .runtime import Runtime
from .security import Tokens
from .setup import Connection, Setup, probe

LOGGER = logging.getLogger(__name__)


class Options(Model):
    postgres_host: str
    postgres_port: int = Field(ge=1, le=65535)
    postgres_database: str
    postgres_user: str
    postgres_password: SecretStr
    postgres_sslmode: Literal["disable", "require", "verify-full"]
    postgres_ca: str
    installation_label: str
    initialize_empty_database: bool
    adopt_installation_id: str
    mqtt_mode: Literal["supervisor", "external", "disabled"]
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str
    mqtt_password: SecretStr
    mqtt_tls: bool = False
    mqtt_ca: str = ""
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]
    log_format: Literal["text", "json"]


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # No exception text, request bodies, configuration, or third-party credential URLs.
        return canonical(
            {
                "level": record.levelname,
                "event": record.msg,
                **{
                    k: record.__dict__[k]
                    for k in (
                        "error_type",
                        "reason",
                        "contract_id",
                        "installation_id",
                        "epoch_id",
                        "revision",
                        "operation",
                        "field",
                        "previous_status",
                        "status",
                        "writer_pid",
                    )
                    if k in record.__dict__
                },
            }
        )


class TextFormatter(JsonFormatter):
    def format(self, record: logging.LogRecord) -> str:
        data = json.loads(super().format(record))
        level, event = data.pop("level"), data.pop("event")
        return f"{level} {event}" + "".join(f" {key}={value}" for key, value in data.items())


RETRYABLE_DATABASE = (
    OSError,
    asyncpg.PostgresError,
    asyncpg.InterfaceError,
    PersistenceUnavailable,
)


async def connect_with_health(store: PostgresStore, clock: SystemClock) -> None:
    """The already-bound API and scheduler remain alive throughout startup."""
    delay = 1.0
    while True:
        try:
            await store.open()
            return
        except RETRYABLE_DATABASE as error:
            LOGGER.warning("database_start_pending", extra={"error_type": type(error).__name__})
            await store.close()
            await clock.sleep(delay)
            delay = min(delay * 2, 30)


def prepare_setup(options: Options | None, data: Path) -> Setup:
    try:
        if options is None:
            raise ValueError("invalid_supervisor_options")
        legacy = (
            Connection(
                host=options.postgres_host,
                port=options.postgres_port,
                database=options.postgres_database,
                user=options.postgres_user,
                password=options.postgres_password,
                sslmode=options.postgres_sslmode,
                ca=options.postgres_ca,
            )
            if options.postgres_host
            else None
        )
    except ValueError:
        return Setup(data, None, configuration_error="invalid_supervisor_options")
    return Setup(data, legacy)


def diagnostic_options() -> Options:
    """Only used for the blocked diagnostic server; never opens a database/adapter."""
    return Options(
        postgres_host="",
        postgres_port=5432,
        postgres_database="",
        postgres_user="",
        postgres_password=SecretStr(""),
        postgres_sslmode="disable",
        postgres_ca="",
        installation_label="",
        initialize_empty_database=False,
        adopt_installation_id="",
        mqtt_mode="disabled",
        mqtt_host="",
        mqtt_port=1883,
        mqtt_username="",
        mqtt_password=SecretStr(""),
        log_level="INFO",
        log_format="text",
    )


def mqtt_status(options: Options) -> tuple[str, bool]:
    """Only safe diagnostics cross into the public service status."""
    return options.mqtt_mode, bool(
        options.mqtt_host.strip()
        or options.mqtt_username.strip()
        or options.mqtt_password.get_secret_value()
    )


async def run(options: Options | None, data: Path, migrations: Path, frontend: Path) -> None:
    setup = prepare_setup(options, data)
    options = options or diagnostic_options()
    clock = SystemClock()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if options.log_format == "json" else TextFormatter())
    logging.basicConfig(level=options.log_level, handlers=[handler], force=True)
    logging.getLogger("aiohttp.access").disabled = True
    connection = setup.connection
    store = PostgresStore(
        connection.dsn() if connection else "",
        migrations,
        tls=False,
    )
    runtime = Runtime(store, clock, type_registry(), require_history_retention=True)
    if not setup.configuration_error:
        runtime.mqtt_mode, runtime.mqtt_external_settings_present = mqtt_status(options)
    runtime.task = asyncio.create_task(runtime.run(), name="processor")
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopped.set)
    api = API(
        runtime,
        Tokens(data / "secrets"),
        "",
        installation_label=options.installation_label,
        frontend=frontend,
        setup=setup,
    )
    consumer = web.AppRunner(api.application(), access_log=None, shutdown_timeout=3)
    ingress = web.AppRunner(api.application(ingress=True), access_log=None, shutdown_timeout=3)
    await consumer.setup()
    await ingress.setup()
    await web.TCPSite(consumer, "0.0.0.0", 8787).start()
    await web.TCPSite(ingress, "0.0.0.0", 8099).start()

    async def scheduler() -> None:
        while True:
            if api.installation_id:
                runtime.tick()
            else:
                runtime.last_heartbeat = clock.monotonic()
            await clock.sleep(0.5)

    ticker = asyncio.create_task(scheduler(), name="scheduler")
    stop_waiter = asyncio.create_task(stopped.wait())
    shutdown_file = data / "last_shutdown.json"

    async def initialize_once() -> str:
        while setup.connection is None:
            await setup.changed.wait()
            setup.changed.clear()
        async with setup.lock:
            return await initialize_connection()

    async def initialize_connection() -> str:
        connection = setup.connection
        assert connection is not None
        store.dsn, store.tls = connection.dsn(), connection.tls()
        if setup.authority == "wizard":
            checked = await probe(
                connection, resume=bool(setup.pending.get("initialize_empty_database"))
            )
            expected = load_id(data) or setup.pending.get("prepared_identity")
            if checked["database_id"] and checked["database_id"] != expected:
                raise ValueError("installation_mismatch")
            if not checked["database_id"] and not setup.pending.get("initialize_empty_database"):
                raise ValueError("initialize_empty_database_required")
        await store.open()
        local = load_id(data)
        pending = setup.pending
        # The prepared UUID closes the DB/local identity crash window without
        # inventing a new identity when resuming first-run initialization.
        database_id = await store.metadata("installation_id")
        if setup.authority == "wizard" and not local:
            prepared = pending.get("prepared_identity")
            if database_id and prepared and database_id != prepared:
                raise ValueError("installation_mismatch")
            local = prepared
        installation_id = reconcile(
            local,
            database_id,
            initialize_empty_database=pending.get(
                "initialize_empty_database", options.initialize_empty_database
            ),
            adopt_installation_id=pending.get(
                "adopt_installation_id", options.adopt_installation_id
            )
            or None,
        )
        await store.put_metadata("installation_id", installation_id)
        write_private(data / "installation.json", canonical({"installation_id": installation_id}))
        if shutdown_file.exists():
            last = json.loads(shutdown_file.read_text(encoding="utf-8"))
            loaded = await store.load()
            if (
                last.get("installation_id") == installation_id
                and last["publication_seq"] > loaded.publication_seq
            ):
                LOGGER.warning("database_older_than_last_shutdown")
                loaded.publication_seq = last["publication_seq"]
                runtime._gap(loaded, "database_older_than_last_shutdown")
                await store.commit(loaded)
        runtime.installation_id = installation_id
        hostname = os.environ.get("HOSTNAME", "")
        runtime.ingress_slug = (
            hostname.removeprefix("addon_")
            if re.fullmatch(r"addon_[a-f0-9]{8}_wuschel", hostname)
            else None
        )
        await runtime.start()
        setup.initialized()
        return installation_id

    async def initialize() -> str:
        delay = 1.0
        while True:
            try:
                return await initialize_once()
            except RETRYABLE_DATABASE as error:
                runtime._unavailable()
                setup.error = "database_connection_failed"
                LOGGER.warning("initialization_retry", extra={"error_type": type(error).__name__})
                await store.close()
                await clock.sleep(delay)
                delay = min(delay * 2, 30)
            except ValueError:
                # A rejected identity/schema remains diagnosable through setup.
                # Reconfigure explicitly; never auto-adopt or reinitialize.
                setup.error = "identity_or_schema_rejected"
                await store.close()
                await setup.changed.wait()
                setup.changed.clear()

    initialization = asyncio.create_task(initialize())
    try:
        done, _ = await asyncio.wait(
            {initialization, stop_waiter}, return_when=asyncio.FIRST_COMPLETED
        )
        if stop_waiter in done:
            return
        installation_id = initialization.result()
        api.installation_id = installation_id
        supervisor_token = os.environ.get("SUPERVISOR_TOKEN", "")
        LOGGER.info(
            "started",
            extra={"installation_id": installation_id, "epoch_id": runtime.state.epoch_id},
        )

        async def health() -> None:
            delay = 1.0
            while True:
                if runtime.task and runtime.task.done():
                    runtime.task.result()
                    raise RuntimeError("processor_stopped")
                if not runtime.persistence:
                    try:
                        await store.close()
                        await store.open()
                        if await store.metadata("installation_id") != installation_id:
                            raise ValueError("installation_mismatch")
                        await runtime.submit("recover")
                        delay = 1.0
                        LOGGER.info("persistence_recovered")
                    except RETRYABLE_DATABASE as error:
                        LOGGER.warning(
                            "recovery_pending", extra={"error_type": type(error).__name__}
                        )
                        await clock.sleep(delay)
                        delay = min(delay * 2, 30)
                        continue
                await clock.sleep(2)

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                bridge = BridgeAdapter(
                    runtime, session, "ws://supervisor/core/websocket", supervisor_token
                )
                mqtt = MQTTAdapter(
                    runtime,
                    session,
                    options.mqtt_mode,
                    {
                        "host": options.mqtt_host,
                        "port": options.mqtt_port,
                        "username": options.mqtt_username,
                        "password": options.mqtt_password.get_secret_value(),
                        "tls": options.mqtt_tls,
                        "ca": options.mqtt_ca,
                    },
                    supervisor_token,
                )
                async with asyncio.TaskGroup() as group:
                    tasks = [
                        group.create_task(bridge.run(), name="ha_adapter"),
                        group.create_task(bridge.refresh_worker(), name="ha_refresh"),
                        group.create_task(bridge.service_worker(), name="ha_services"),
                        group.create_task(bridge.projection_worker(), name="ha_projection"),
                        group.create_task(mqtt.run(), name="mqtt_adapter"),
                        group.create_task(clock.run(), name="clock"),
                        group.create_task(health(), name="health"),
                    ]
                    await stopped.wait()
                    for task in tasks:
                        task.cancel()
        finally:
            LOGGER.info("stopping")
    finally:
        for background in (ticker, stop_waiter, initialization):
            background.cancel()
        await asyncio.gather(ticker, stop_waiter, initialization, return_exceptions=True)
        try:
            async with asyncio.timeout(25):
                await api.close_sockets(consumer.app)
                await runtime.stop()
                if runtime.persistence and api.installation_id:
                    write_private(
                        shutdown_file,
                        canonical(
                            {
                                "publication_seq": runtime.state.publication_seq,
                                "installation_id": api.installation_id,
                            }
                        ),
                    )
                await consumer.cleanup()
                await ingress.cleanup()
                await store.close()
                LOGGER.info("stopped")
        finally:
            if runtime.task and not runtime.task.done():
                runtime.task.cancel()
                await asyncio.gather(runtime.task, return_exceptions=True)


def main() -> None:
    data = Path(os.environ.get("CORE_CONTRACTS_DATA", "/data"))
    try:
        try:
            options: Options | None = Options.model_validate_json(
                (data / "options.json").read_text(encoding="utf-8")
            )
        except ValidationError:
            options = None
        asyncio.run(run(options, data, Path("/app/migrations"), Path("/app/frontend")))
    except Exception as error:
        LOGGER.error("startup_failed", extra={"error_type": type(error).__name__})
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
