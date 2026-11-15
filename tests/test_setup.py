import asyncio
import base64
import json
import os
import ssl
from uuid import uuid4

import pytest

import core_contracts.setup as setup_control
from core_contracts.api import API
from core_contracts.identity import write_private
from core_contracts.security import Tokens
from core_contracts.setup import Connection, Setup


def connection(**changes):
    return Connection(
        host="postgres-test",
        database="cc_test",
        user="cc_test",
        password="ephemeral-secret-only",
        **changes,
    )


async def empty_probe(value, **kwargs):
    return {"server_version": 170000, "empty": True, "database_id": None}


async def test_setup_ui_before_identity_only_through_ingress(runtime, tmp_path, aiohttp_client):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<h1>Setup</h1>")
    setup = Setup(tmp_path, None)
    api = API(
        runtime,
        Tokens(tmp_path / "secrets"),
        "",
        setup=setup,
        frontend=frontend,
        ingress_address="127.0.0.1",
    )
    ingress = await aiohttp_client(api.application(ingress=True))
    consumer = await aiohttp_client(api.application())
    assert (await ingress.get("/")).status == 200
    result = await (await ingress.get("/api/v1/setup/status")).json()
    assert result["phase"] == "welcome"
    assert not result["database_configured"]
    assert (await ingress.get("/api/v1/info")).status == 503
    assert (await ingress.get("/api/v1/ws")).status == 503
    assert (await consumer.get("/health/live")).status == 200
    assert (await consumer.get("/health/ready")).status == 503
    for role in ("admin", "consumer"):
        response = await consumer.get(
            "/api/v1/setup/status", headers={"Authorization": "Bearer " + api.tokens.values[role]}
        )
        assert response.status == 404
    assert (await consumer.get("/api/v1/setup/status")).status == 401


@pytest.mark.parametrize(
    "headers", [{}, {"X-Setup-CSRF": "wrong"}, {"Sec-Fetch-Site": "cross-site"}]
)
async def test_setup_csrf_before_initialization(runtime, tmp_path, aiohttp_client, headers):
    setup = Setup(tmp_path, None)
    api = API(runtime, Tokens(tmp_path / "tokens"), "", setup=setup, ingress_address="127.0.0.1")
    ingress = await aiohttp_client(api.application(ingress=True))
    assert (await ingress.post("/api/v1/setup/provision", json={}, headers=headers)).status == 403
    assert not setup.path.exists()


async def test_setup_peer_is_not_forwardable(runtime, tmp_path, aiohttp_client):
    setup = Setup(tmp_path, None)
    api = API(runtime, Tokens(tmp_path), "", setup=setup)
    client = await aiohttp_client(api.application(ingress=True))
    assert (
        await client.get("/api/v1/setup/status", headers={"X-Forwarded-For": "172.30.32.2"})
    ).status == 403


async def test_provision_request_resume_import_and_redaction(tmp_path, monkeypatch):
    setup = Setup(tmp_path, None)
    spec = {k: v for k, v in connection().private().items() if k != "password"}
    first = await setup.dispatch("provision", "POST", spec)
    again = await Setup(tmp_path, None).dispatch("provision", "POST", spec)
    assert again["request_id"] == first["request_id"]
    assert "REQUEST = None" not in first["script"]
    import ast

    program = "\n".join(first["script"].splitlines()[1:-1])
    ast.parse(program, feature_version=(3, 10))
    compile(program, "generated.py", "exec")
    code = base64.b64encode(
        json.dumps(
            {"request_id": first["request_id"], "connection": connection().private()}
        ).encode()
    ).decode()
    monkeypatch.setattr(setup_control, "probe", empty_probe)
    result = await setup.dispatch(
        "import", "POST", {"code": code, "confirm": True, "initialize_empty_database": True}
    )
    assert result["accepted"]
    status = setup.status()
    assert "ephemeral-secret-only" not in json.dumps(status)
    assert "password" not in status["database"]
    restarted = Setup(tmp_path, connection())
    assert restarted.authority == "wizard"
    assert restarted.connection.password.get_secret_value() == "ephemeral-secret-only"
    assert not (tmp_path / "installation.json").exists()
    if os.name != "nt":
        assert setup.path.stat().st_mode & 0o777 == 0o600
        assert setup.directory.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize(
    "body,error",
    [
        ({"confirm": False}, "confirmation_required"),
        ({"confirm": True}, "initialize_empty_database_required"),
        (
            {
                "confirm": True,
                "initialize_empty_database": True,
                "connection": connection(sslmode="disable").private(),
            },
            "insecure_tls_confirmation_required",
        ),
    ],
)
async def test_setup_requires_confirmation_and_empty_database_gate(
    tmp_path, monkeypatch, body, error
):
    setup = Setup(tmp_path, None)
    monkeypatch.setattr(setup_control, "probe", empty_probe)
    with pytest.raises(ValueError, match=error):
        await setup.configure({"connection": connection().private(), **body})
    assert not setup.path.exists()


async def test_db_outage_does_not_replace_connection_or_leak_exception(
    runtime, tmp_path, aiohttp_client, monkeypatch
):
    async def unavailable(value, **kwargs):
        raise OSError("password=must-not-appear")

    monkeypatch.setattr(setup_control, "probe", unavailable)
    setup = Setup(tmp_path, None)
    api = API(runtime, Tokens(tmp_path / "tokens"), "", setup=setup, ingress_address="127.0.0.1")
    client = await aiohttp_client(api.application(ingress=True))
    response = await client.post(
        "/api/v1/setup/connect",
        headers={"X-Setup-CSRF": setup.csrf},
        json={"connection": connection().private(), "confirm": True},
    )
    assert response.status == 503
    assert "password" not in await response.text()
    assert not setup.path.exists()
    assert (await client.get("/api/v1/setup/status")).status == 200


async def test_identity_adoption_and_mismatch_and_staged_reconfiguration(tmp_path, monkeypatch):
    identity = str(uuid4())

    async def existing(value, **kwargs):
        return {"server_version": 170000, "database_id": identity, "empty": False}

    monkeypatch.setattr(setup_control, "probe", existing)
    setup = Setup(tmp_path, None)
    payload = {"connection": connection().private(), "confirm": True}
    with pytest.raises(ValueError, match="adopt_installation_id_required"):
        await setup.configure(payload)
    await setup.configure({**payload, "adopt_installation_id": identity})
    assert setup.pending["prepared_identity"] == identity
    write_private(tmp_path / "installation.json", json.dumps({"installation_id": identity}))
    setup.initialized()
    assert not setup.pending["initialize_empty_database"]
    assert not setup.pending["adopt_installation_id"]
    old = setup.connection
    await setup.configure(payload)
    assert setup.restart_required and setup.connection is old
    foreign = str(uuid4())
    write_private(tmp_path / "installation.json", json.dumps({"installation_id": foreign}))
    with pytest.raises(ValueError, match="installation_mismatch"):
        await setup.configure(payload)


def test_existing_alpha_authority_no_new_database_or_identity(tmp_path):
    legacy = connection()
    setup = Setup(tmp_path, legacy)
    assert setup.authority == "supervisor" and setup.connection is legacy
    assert not setup.directory.exists()
    assert setup.status()["installation_id"] is None
    assert setup.status()["onboarding_complete"]


async def test_wizard_completion_requires_database_and_bridge_and_survives_restart(
    tmp_path, monkeypatch
):
    setup = Setup(tmp_path, None)
    with pytest.raises(ValueError, match="database_setup_pending"):
        await setup.dispatch("finish", "POST", {"confirm": True})
    setup.completed = True

    async def missing(token, **kwargs):
        return {"state": "missing_or_restart_pending"}

    monkeypatch.setattr(setup_control, "bridge_status", missing)
    with pytest.raises(ValueError, match="bridge_setup_pending"):
        await setup.dispatch("finish", "POST", {"confirm": True})
    assert not setup.status()["onboarding_complete"]

    async def active(token, **kwargs):
        return {"state": "active"}

    monkeypatch.setattr(setup_control, "bridge_status", active)
    with pytest.raises(ValueError, match="confirmation_required"):
        await setup.dispatch("finish", "POST", {"confirm": False})
    assert await setup.dispatch("finish", "POST", {"confirm": True}) == {"completed": True}
    restored = Setup(tmp_path, None)
    assert restored.status()["onboarding_complete"]
    assert restored.status()["phase"] == "welcome"
    assert {path.name for path in tmp_path.iterdir()} == {"setup"}


def test_tls_and_secret_repr():
    value = connection()
    assert "ephemeral-secret-only" not in repr(value)
    tls = value.tls()
    assert tls.check_hostname and tls.verify_mode == ssl.CERT_REQUIRED
    tls = connection(sslmode="require").tls()
    assert not tls.check_hostname and tls.verify_mode == ssl.CERT_NONE
    assert connection(sslmode="disable").tls() is False
    with pytest.raises(ValueError):
        connection(ca="/etc/secrets")
    with pytest.raises(ValueError):
        connection(ca="/ssl/../etc/secrets")


@pytest.mark.parametrize("reply,tls", [(b"S", True), (b"N", False)])
async def test_unauthenticated_reachability_uses_ssl_request_only(reply, tls):
    received = []

    async def server(reader, writer):
        received.append(await reader.readexactly(8))
        writer.write(reply)
        await writer.drain()
        writer.close()

    listener = await asyncio.start_server(server, "127.0.0.1", 0)
    try:
        result = await setup_control.reachability("127.0.0.1", listener.sockets[0].getsockname()[1])
        assert result == {"reachable": True, "tls_available": tls, "server_version": None}
        assert received == [b"\x00\x00\x00\x08\x04\xd2\x16\x2f"]
    finally:
        listener.close()
        await listener.wait_closed()


@pytest.mark.parametrize(
    "info,handlers,expected",
    [
        ({"protocol_version": 1, "bridge_version": "1.0.0a1"}, [], "active"),
        ({"protocol_version": 1, "bridge_version": "1.0.0a9", "negotiated": 2}, [], "active"),
        ({"protocol_version": 2}, [], "incompatible"),
        (None, ["traber"], "installed_not_configured"),
        (None, [], "missing_or_restart_pending"),
    ],
)
async def test_bridge_detection_readonly_and_no_false_filesystem_claim(
    monkeypatch, info, handlers, expected
):
    commands = []
    messages = [
        {"type": "auth_required"},
        {"type": "auth_ok"},
        {"success": bool(info), "result": info, "error": {"code": "unknown_command"}},
        {
            "success": bool(info and info.get("negotiated")),
            "result": {"protocol_selected": 2, "features": ["calendar_events"]},
            "error": {"code": "unknown_command"},
        },
    ]

    class Socket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def send_json(self, command):
            commands.append(command)

        async def receive_json(self):
            return messages.pop(0)

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def json(self):
            return handlers

    class Session:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def ws_connect(self, url):
            assert url == "ws://supervisor/core/websocket"
            return Socket()

        def get(self, url, **kwargs):
            assert url.endswith("/config/config_entries/flow_handlers")
            return Response()

    monkeypatch.setattr(setup_control.aiohttp, "ClientSession", Session)
    result = await setup_control.bridge_status("ephemeral-supervisor-token")
    assert result["state"] == expected
    assert result["release_blocker"] == (expected != "active")
    assert [command["type"] for command in commands] == ["auth", "traber/info"] + (
        ["traber/negotiate"] if expected == "active" else []
    )
    if expected == "active":
        assert result["protocol_selected"] == info.get("negotiated", 1)


@pytest.mark.parametrize(
    "existing,handler,expected_posts",
    [
        (True, "traber", 0),
        (False, "traber", 2),
        (False, "other_domain", 1),
    ],
)
async def test_supported_bridge_flow_is_fixed_domain_and_preserves_existing_entries(
    monkeypatch, existing, handler, expected_posts
):
    posts = []
    values = [
        {
            "type": "form",
            "handler": handler,
            "step_id": "user",
            "flow_id": "test_flow",
            "data_schema": [],
        },
        {"type": "create_entry", "handler": "traber"},
    ]

    class Response:
        status = 200

        def __init__(self, value):
            self.value = value

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def json(self):
            return self.value

    class Session:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def get(self, url, **kwargs):
            assert url.endswith("/entry?domain=traber")
            return Response([{"domain": "traber"}] if existing else [])

        def post(self, url, **kwargs):
            posts.append((url, kwargs["json"]))
            return Response(values.pop(0))

    monkeypatch.setattr(setup_control.aiohttp, "ClientSession", Session)
    if handler != "traber":
        with pytest.raises(ValueError, match="bridge_flow_changed"):
            await setup_control.configure_bridge("ephemeral-token")
    else:
        assert (await setup_control.configure_bridge("ephemeral-token"))["created"] == (
            not existing
        )
    assert len(posts) == expected_posts
    if posts:
        assert posts[0][1] == {"handler": "traber"}
    if len(posts) == 2:
        assert posts[1][0].endswith("/flow/test_flow") and posts[1][1] == {}


@pytest.mark.parametrize("value", [None, [], 42])
async def test_malformed_import_is_safe_and_never_persisted(tmp_path, value):
    setup = Setup(tmp_path, None)
    code = base64.b64encode(json.dumps(value).encode()).decode()
    with pytest.raises(ValueError, match="invalid_import"):
        await setup.dispatch("import", "POST", {"code": code})
    assert not setup.path.exists()
