import copy
from uuid import uuid4

import pytest

from core_contracts.extensions.catalog.catalog_care import all_rows
from core_contracts.extensions.catalog.managed_catalog import head_key, identity
from core_contracts.model import canonical
from tests.test_catalog_care import seeded
from tests.test_title_sync import observed


async def populated(runtime, config, count=3):
    catalog = await seeded(runtime, config, [observed("Synthetic protected")])
    for number in range(count):
        raw = canonical(["Synthetic Artist", f"Synthetic Title {number}"])
        key = identity("synthetic", "music", "title", raw)
        runtime.state.tables["catalog_sighting"][head_key(catalog) + ":" + key] = {
            "catalog": head_key(catalog),
            "identity": key,
            "scope": "synthetic",
            "media_type": "music",
            "signal_type": "title",
            "key": raw,
            "first_seen": "2026-01-01T11:59:00+00:00",
            "last_seen": "2026-01-01T11:59:00+00:00",
            "seen_count": 1,
            "source": "synthetic",
            "report": "synthetic",
        }
    await runtime._commit(runtime.state.clone())
    return catalog


def initial(catalog, **extra):
    return {
        "catalog_id": catalog.catalog_id,
        "definition_version": catalog.version,
        "_actor": {"kind": "test", "role": "admin"},
        **extra,
    }


def application(plan):
    return {
        "plan_id": plan["plan_id"],
        "plan_digest": plan["plan_digest"],
        "confirmed": True,
        "request_id": str(uuid4()),
        "_actor": {"kind": "test", "role": "admin"},
    }


async def test_unclear_bulk_filter_across_pages_and_protected_content(runtime, config):
    catalog = await populated(runtime, config, 201)
    before = copy.deepcopy(runtime.state.tables["catalog_head"])
    requests_before = len(await runtime.store.rows("catalog_request"))
    plan = await runtime.submit("catalog_cleanup_preview", initial(catalog))
    assert plan["counts"] == {"protected": 1, "unclear": 201}
    assert plan["selected"] == []
    assert len(plan["items"]) == 100 and plan["next_offset"] == 100
    plan = await runtime.submit(
        "catalog_cleanup_preview",
        initial(
            catalog,
            plan_id=plan["plan_id"],
            filters={
                "kind": "unclear",
                "one": True,
                "unmaintained": True,
                "since": "2026-01-01T00:00:00+00:00",
                "until": "2026-01-01T12:00:00+00:00",
                "source": "synthetic",
            },
            select_filtered=True,
        ),
    )
    assert len(plan["selected"]) == 201
    assert plan["filtered"] == 201
    body = application(plan)
    body["request_id"] = body["request_id"].upper()
    result = await runtime.submit("catalog_cleanup_apply", body)
    assert result["removed"] == 201
    assert runtime.state.tables["catalog_sighting"] == {}
    assert runtime.state.tables["catalog_head"] == before
    assert list(all_rows(runtime.state, catalog).values())[0]["seen_count"] == 0
    assert await runtime.submit("catalog_cleanup_apply", body) == result
    assert len(await runtime.store.rows("catalog_request")) == requests_before + 1
    # Append-only requests may be outside the loaded runtime window after restart.
    runtime.state.tables["catalog_request"].clear()
    assert await runtime.submit("catalog_cleanup_apply", body) == result


async def test_no_confirmation_protected_selection_changed_head_and_owner_rejected(runtime, config):
    catalog = await populated(runtime, config)
    plan = await runtime.submit("catalog_cleanup_preview", initial(catalog))
    protected = next(row["identity"] for row in plan["items"] if row["protected"])
    with pytest.raises(ValueError, match="catalog_cleanup_invalid"):
        await runtime.submit(
            "catalog_cleanup_preview",
            initial(catalog, plan_id=plan["plan_id"], selected=[protected]),
        )
    body = application(plan)
    with pytest.raises(ValueError, match="catalog_cleanup_conflict"):
        await runtime.submit("catalog_cleanup_apply", {**body, "confirmed": False})
    with pytest.raises(ValueError, match="catalog_cleanup_conflict"):
        await runtime.submit("catalog_cleanup_apply", {**body, "_actor": {"kind": "other"}})
    runtime.state.tables["catalog_head"][head_key(catalog)]["revision"] += 1
    with pytest.raises(ValueError, match="catalog_cleanup_conflict"):
        await runtime.submit("catalog_cleanup_apply", body)


async def test_unselected_new_and_changed_sightings_do_not_block_cleanup_and_are_reset(
    runtime, config
):
    catalog = await populated(runtime, config)
    first = next(iter(runtime.state.tables["catalog_sighting"].values()))
    plan = await runtime.submit(
        "catalog_cleanup_preview", initial(catalog, selected=[first["identity"]])
    )
    other = next(
        row
        for row in runtime.state.tables["catalog_sighting"].values()
        if row["identity"] != first["identity"]
    )
    state = runtime.state.clone()
    state.tables["catalog_sighting"][head_key(catalog) + ":" + other["identity"]]["seen_count"] += 1
    state.tables["catalog_sighting"]["synthetic.other:new"] = {
        **other,
        "catalog": "synthetic.other",
        "identity": "new",
        "key": "Synthetic New",
        "seen_count": 5,
    }
    await runtime._commit(state)
    answer = await runtime.submit("catalog_cleanup_apply", application(plan))
    assert answer["removed"] == 1
    assert len(runtime.state.tables["catalog_sighting"]) == 3
    assert all(row["seen_count"] == 0 for row in runtime.state.tables["catalog_sighting"].values())


async def test_selected_sighting_changed_since_preview_conflicts_with_named_entry(runtime, config):
    catalog = await populated(runtime, config)
    first = next(iter(runtime.state.tables["catalog_sighting"].values()))
    plan = await runtime.submit(
        "catalog_cleanup_preview", initial(catalog, selected=[first["identity"]])
    )
    state = runtime.state.clone()
    state.tables["catalog_sighting"][head_key(catalog) + ":" + first["identity"]]["seen_count"] += 1
    await runtime._commit(state)
    before = runtime.state.model_dump()
    with pytest.raises(ValueError, match="catalog_cleanup_conflict") as caught:
        await runtime.submit("catalog_cleanup_apply", application(plan))
    assert caught.value.details["changed_entries"] == [
        {"identity": first["identity"], "key": first["key"]}
    ]
    assert runtime.state.model_dump() == before


async def test_real_unselected_playback_after_preview_survives_cleanup_with_zero_counter(
    runtime, config
):
    from tests.test_beta_media import observe
    from tests.test_catalog_playback import advance, music

    await music(runtime, config)
    await advance(runtime, 2)
    catalog = next(c for c in runtime.config.catalogs if c.catalog_id == "media.homepods")
    first = next(iter(runtime.state.tables["catalog_sighting"].values()))
    plan = await runtime.submit(
        "catalog_cleanup_preview", initial(catalog, selected=[first["identity"]])
    )
    await observe(runtime, "title", "Synthetic New Playback")
    await advance(runtime, 10)
    assert sum(row["seen_count"] for row in runtime.state.tables["catalog_sighting"].values()) == 2
    answer = await runtime.submit("catalog_cleanup_apply", application(plan))
    assert answer["removed"] == 1
    remaining = list(runtime.state.tables["catalog_sighting"].values())
    assert len(remaining) == 1 and remaining[0]["seen_count"] == 0
    assert remaining[0]["key"] == canonical(["Synthetic Artist A", "Synthetic New Playback"])


async def test_selected_playback_change_returns_http_409_with_named_entry(
    runtime, config, aiohttp_client, tmp_path
):
    from tests.test_beta_media import observe
    from tests.test_catalog_playback import advance, music
    from tests.test_workbench_api import api_client

    await music(runtime, config)
    await advance(runtime, 2)
    catalog = next(c for c in runtime.config.catalogs if c.catalog_id == "media.homepods")
    first = next(iter(runtime.state.tables["catalog_sighting"].values()))
    _, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    response = await client.post(
        "/api/v1/catalogs/cleanup/preview",
        json=initial(catalog, selected=[first["identity"]]),
        headers=headers,
    )
    assert response.status == 200
    plan = await response.json()
    await observe(runtime, "applicable", False)
    await observe(runtime, "applicable", True)
    await advance(runtime, 2)
    assert next(iter(runtime.state.tables["catalog_sighting"].values()))["seen_count"] == 2
    before = runtime.state.model_dump()
    response = await client.post(
        "/api/v1/catalogs/cleanup/apply", json=application(plan), headers=headers
    )
    assert response.status == 409
    body = await response.json()
    assert body["error"] == "catalog_cleanup_conflict"
    assert body["details"]["changed_entries"] == [
        {"identity": first["identity"], "key": first["key"]}
    ]
    assert runtime.state.model_dump() == before


async def test_cleanup_commit_failure_leaves_runtime_content_and_counts_unchanged(runtime, config):
    catalog = await populated(runtime, config)
    plan = await runtime.submit(
        "catalog_cleanup_preview",
        initial(catalog, select_filtered=True, filters={"kind": "unclear"}),
    )
    before = runtime.state.model_dump()
    persisted = runtime.store.state.model_dump()

    def fail():
        raise RuntimeError("synthetic transaction failure")

    runtime.store.before_commit = fail
    with pytest.raises(RuntimeError, match="synthetic transaction failure"):
        await runtime.submit("catalog_cleanup_apply", application(plan))
    runtime.store.before_commit = None
    assert runtime.state.model_dump() == before
    assert runtime.store.state.model_dump() == persisted


def test_proof_requires_every_occurrence_and_preserves_real_short_titles():
    from types import SimpleNamespace

    from core_contracts.extensions.catalog.catalog_cleanup import transitions

    config = SimpleNamespace(
        bindings=[
            SimpleNamespace(
                binding_id="b." + role,
                adapter=SimpleNamespace(
                    ha_attribute="media_" + role, ha_entity_id="media_player.synthetic"
                ),
            )
            for role in ["artist", "title"]
        ]
    )
    history = []

    def pair(second, artist, title):
        for role, value in [("artist", artist), ("title", title)]:
            history.append(
                {
                    "binding_id": "b." + role,
                    "observation_kind": "live_change",
                    "availability": "available",
                    "ha_time_fired": f"2026-01-01T12:00:{second:02}+00:00",
                    "received_at": f"2026-01-01T12:00:{second:02}+00:00",
                    "value_raw": value,
                }
            )

    pair(0, "Synthetic Artist A", "Synthetic Title A")
    pair(10, "Synthetic Artist A", "Synthetic Title B")
    pair(13, "Synthetic Artist B", "Synthetic Title B")
    key = canonical(["Synthetic Artist A", "Synthetic Title B"])
    proven, _ = transitions(history, config)
    assert proven == {key}
    pair(20, "Synthetic Artist A", "Synthetic Title B")
    pair(40, "Synthetic Artist C", "Synthetic Title C")
    proven, _ = transitions(history, config)
    assert key not in proven


async def test_expired_plans_release_capacity_and_cannot_be_applied(runtime, config):
    from core_contracts.extensions.catalog.catalog_cleanup import PLAN_SECONDS

    catalog = await populated(runtime, config)
    plans = [await runtime.submit("catalog_cleanup_preview", initial(catalog)) for _ in range(16)]
    runtime.clock.advance(PLAN_SECONDS)
    await runtime.submit("catalog_cleanup_preview", initial(catalog))
    assert len(runtime.catalog_cleanup_plans) == 1
    with pytest.raises(ValueError, match="catalog_cleanup_conflict"):
        await runtime.submit("catalog_cleanup_apply", application(plans[0]))


def test_unavailable_history_breaks_transition_proof():
    from types import SimpleNamespace

    from core_contracts.extensions.catalog.catalog_cleanup import transitions

    config = SimpleNamespace(
        bindings=[
            SimpleNamespace(
                binding_id=role,
                adapter=SimpleNamespace(
                    ha_attribute="media_" + role, ha_entity_id="media_player.synthetic"
                ),
            )
            for role in ("artist", "title")
        ]
    )
    history = []
    for second, artist, title, available in [
        (0, "Synthetic A", "Synthetic One", True),
        (10, "Synthetic A", "Synthetic Two", True),
        (11, None, None, False),
        (13, "Synthetic B", "Synthetic Two", True),
    ]:
        for role, value in [("artist", artist), ("title", title)]:
            history.append(
                dict(
                    binding_id=role,
                    observation_kind="live_change",
                    availability="available" if available else "unavailable",
                    value_raw=value,
                    received_at=f"2026-01-01T12:00:{second:02}+00:00",
                )
            )
    proven, _ = transitions(history, config)
    assert proven == set()
