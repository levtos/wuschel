"""Extension contracts: ordering, writer ownership and request isolation."""

import pytest

from core_contracts import slots


async def test_registered_operations_keep_writer_override_and_isolate_failures(
    runtime, monkeypatch
):
    committed = []

    async def commit(state, **options):
        committed.append(state)

    async def extension(owner, state, operation, payload, now, config):
        if payload == "fail":
            raise ValueError("extension_failed")
        assert state is not owner.state
        state.tables["service_state"]["extension"] = {"value": payload}
        await owner._commit(state)
        return payload

    monkeypatch.setattr(runtime, "_commit", commit)
    monkeypatch.setitem(slots.OPERATIONS, "test_extension", slots.Operation(extension))
    with pytest.raises(ValueError, match="extension_failed"):
        await runtime.submit("test_extension", "fail")
    assert await runtime.submit("test_extension", "next") == "next"
    assert runtime.task is not None and not runtime.task.done()
    assert "extension" not in runtime.state.tables["service_state"]
    assert committed[0].tables["service_state"]["extension"] == {"value": "next"}


async def test_early_extension_precedes_persistence_and_state_preparation(runtime, monkeypatch):
    async def extension(owner, state, operation, payload, now, config):
        assert state is owner.state
        return "captured"

    monkeypatch.setitem(
        slots.OPERATIONS, "test_early", slots.Operation(extension, state="none", early=True)
    )
    runtime.persistence = False
    assert await runtime.submit("test_early") == "captured"


async def test_restore_hooks_follow_ordinals_and_existing_commit_boundary(runtime, monkeypatch):
    events = []
    published = runtime.state

    async def later(owner, state, context):
        assert owner.state is published
        assert state is not published
        assert events == ["first"]
        events.append("second")

    async def earlier(owner, state, context):
        assert owner.state is published
        assert state is not published
        events.append("first")

    def before_commit(previous, staged):
        assert previous is published
        assert runtime.state is published
        assert staged is not published
        assert events == ["first", "second"]
        events.append("before_commit")

    # Deliberately register the larger ordinal first.
    monkeypatch.setitem(slots.RESTORE, 2000, slots.Hook(later, "migration"))
    monkeypatch.setitem(slots.RESTORE, 1000, slots.Hook(earlier, "migration"))
    monkeypatch.setitem(slots.BEFORE_COMMIT, 1000, slots.Hook(before_commit, "archive"))
    await runtime._restore("test_extension")
    assert events == ["first", "second", "before_commit"]
    assert runtime.state is not published
    assert runtime.persistence and runtime.confirmed


async def test_failed_commit_never_reaches_published_deadline_hooks(runtime, monkeypatch):
    called = []
    published = runtime.state

    async def fail(state):
        raise OSError("commit_failed")

    def after_commit(owner, state):
        called.append(state)

    monkeypatch.setattr(runtime.store, "commit", fail)
    monkeypatch.setitem(slots.NEXT_DUE, 1000, slots.Hook(after_commit, "commit"))
    with pytest.raises(OSError, match="commit_failed"):
        await runtime._commit(runtime.state.clone())
    assert runtime.state is published
    assert called == []
