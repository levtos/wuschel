import copy
from datetime import timedelta
from uuid import uuid4

import pytest

from core_contracts.evidence import Observation
from core_contracts.identity import reconcile
from core_contracts.runtime import Runtime
from core_contracts.testing.contract_types import type_registry
from core_contracts.validation import validate_document as validate
from tests.conftest import activate_draft

from .conftest import activate


def obs(runtime, value=True, **kwargs):
    return Observation(
        source_id="source.a",
        binding_id="binding.a",
        adapter="ha_state",
        value_raw=value,
        observation_kind="live_change",
        ha_time_fired=runtime.clock.now_utc(),
        received_at=runtime.clock.now_utc(),
        epoch_id=runtime.state.epoch_id,
        ingest_seq=1,
        **kwargs,
    )


async def test_full_echo_path_and_commit_failure(runtime, config):
    await activate(runtime, config)
    await runtime.submit("observation", obs(runtime))
    snapshot = runtime.snapshot()
    assert snapshot["contracts"]["fixture.echo"]["fields"]["value"]["value"] is True
    runtime.store.before_commit = lambda: (_ for _ in ()).throw(OSError("injected"))
    with pytest.raises(OSError):
        await runtime.submit("observation", obs(runtime, False))
    assert runtime.snapshot()["contracts"] == snapshot["contracts"]
    assert not runtime.confirmed and not runtime.ready
    runtime.store.before_commit = None
    await runtime.submit("recover")
    assert runtime.state.epoch_id != snapshot["epoch_id"]
    assert runtime.state.publication_seq > snapshot["publication_seq"]
    assert any(g["reason"] == "db_outage" for g in await runtime.store.rows("history_gap"))


async def test_draft_occ_activation_rollback_disabled(runtime, config):
    draft = await runtime.submit("draft_create", {"config": config})
    assert not runtime.snapshot()["contracts"]
    with pytest.raises(ValueError):
        await runtime.submit("draft_update", {**draft, "draft_version": 0})
    await activate_draft(runtime, draft, expected_active_revision=0)
    config["contracts"][0]["enabled"] = False
    await activate(runtime, config)
    assert not runtime.snapshot()["contracts"]
    await runtime.submit("rollback", {"revision": 1, "expected_active_revision": 2})
    assert runtime.state.active_revision == 3
    assert (
        runtime.state.tables["registry_revision"]["3"]["checksum"]
        == (await runtime.store.get("registry_revision", "1"))["checksum"]
    )
    with pytest.raises(ValueError):
        await runtime.submit("rollback", {"revision": 1, "expected_active_revision": 1})
    assert runtime.state.active_revision == 3


async def test_commands_atomic_idempotent_and_restore(runtime, config):
    config["contracts"][0].update(
        type_id="test.state_machine", parameters={"deadline_s": 10, "sessions": True}
    )
    await activate(runtime, config)
    await runtime.submit("observation", obs(runtime))
    command = {
        "command_id": "cmd.1",
        "contract_id": "fixture.echo",
        "command": "request_b",
        "args": {},
        "origin": {"kind": "test", "actor": "example", "client_name": "test"},
        "issued_at": runtime.clock.now_utc(),
        "valid_until": runtime.clock.now_utc() + timedelta(seconds=30),
    }
    first = await runtime.submit("command", command)
    seq = runtime.state.publication_seq
    assert first["status"] == "accepted"
    assert await runtime.submit("command", command) == first
    assert runtime.state.publication_seq == seq
    assert (await runtime.submit("command", {**command, "command": "other"}))[
        "status"
    ] == "command_id_conflict"
    await runtime.stop()
    restarted = Runtime(runtime.store, runtime.clock, type_registry())
    await restarted.start()
    assert (await restarted.submit("command", command))["status"] == "accepted"
    assert restarted.snapshot()["contracts"]["fixture.echo"]["state_machine"]["state"] == "b"
    restarted.clock.advance(11)
    await restarted.submit(
        "observation", obs(restarted).model_copy(update={"observation_kind": "snapshot"})
    )
    await restarted.submit("tick")
    assert restarted.snapshot()["contracts"]["fixture.echo"]["state_machine"]["state"] == "c"
    await restarted.stop()


async def test_overflow_and_slow_subscriber(runtime, config):
    await activate(runtime, config)
    queue = runtime.subscribe(max_queue=1)
    await runtime.submit("observation", obs(runtime))
    await runtime.submit("observation", obs(runtime, False))
    assert (await queue.get())["type"] == "resync_required"
    runtime.subscribers.discard(queue)
    runtime.persistence = False
    for value in (True, False, True):
        runtime.ingest(obs(runtime, value))
    assert len(runtime.latest) == 1
    await runtime.submit("recover")
    assert runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]["value"] is True


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c: c.update(profile="example"),
        lambda c: c.update(consumer_ids=[]),
        lambda c: c.update(shadow=True),
        lambda c: c["contracts"][0]["parameters"].update(set_state=True),
        lambda c: c["contracts"].append(copy.deepcopy(c["contracts"][0])),
        lambda c: c["contracts"][0]["inputs"][0].update(ref="fixture.echo"),
        lambda c: c["contracts"][0].update(type_id="opening.v1"),
        lambda c: c["contracts"][0]["parameters"].update(password="redacted"),
        lambda c: c["contracts"][0].update(type_id="test.temporal", parameters={}),
    ],
)
def test_registry_rejects(config, mutation):
    mutation(config)
    with pytest.raises(ValueError):
        validate(config, type_registry())


def test_builtin_fixtures_remain_identifiable():
    types = type_registry().types
    assert {name for name, _ in types} == {
        "test.echo",
        "test.multi",
        "test.boolean",
        "test.fusion",
        "test.temporal",
        "test.state_machine",
    }
    assert all(t.schema.fixture for t in types.values())


@pytest.mark.parametrize(
    "local,db,initialize,adopt,success",
    [
        (False, False, False, False, True),
        (True, True, False, False, True),
        (True, False, False, False, False),
        (True, False, True, False, True),
        (False, True, False, False, False),
        (False, True, False, True, True),
    ],
)
def test_identity_matrix(local, db, initialize, adopt, success):
    identity = str(uuid4())
    args = dict(
        initialize_empty_database=initialize, adopt_installation_id=identity if adopt else None
    )
    if success:
        assert reconcile(identity if local else None, identity if db else None, **args)
    else:
        with pytest.raises(ValueError):
            reconcile(identity if local else None, identity if db else None, **args)


def test_identity_mismatch():
    with pytest.raises(ValueError, match="installation_mismatch"):
        reconcile(
            str(uuid4()), str(uuid4()), initialize_empty_database=False, adopt_installation_id=None
        )
