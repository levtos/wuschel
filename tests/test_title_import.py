import copy

import pytest

from core_contracts.extensions.tc_migration.title_import import plan_title_import


def row(key="Synthetic Example", **extra):
    return {
        "key": key,
        "normalized_key": key.lower(),
        "scope": "default",
        "media_type": "music",
        "signal_type": "title",
        "enum": 0,
        "contexts": [],
        **extra,
    }


def plan(rows):
    return plan_title_import({"version": 3, "entries": rows}, "a" * 64)


def test_decisions_account_for_every_row_without_application_or_inferred_meanings():
    rows = [
        row(),
        row("Synthetic Variant", parent_key="synthetic example", enum=1),
        row("Synthetic Hidden", hidden_at="2026-01-01T00:00:00Z"),
        row("Synthetic App", signal_type="app"),
    ]
    before = copy.deepcopy(rows)
    result = plan(rows)
    assert rows == before
    assert sum(result["summary"]["outcomes"].values()) == 4
    assert (
        result["summary"]["variant_class_conflicts"]
        == result["summary"]["hidden"]
        == result["summary"]["app_entries"]
        == 1
    )
    assert result["applied"] is False
    assert all(
        value == {"meaning": None, "target_class": None}
        for value in result["class_mapping"].values()
    )
    assert plan(rows)["import_id"] == result["import_id"]


@pytest.mark.parametrize(
    "bad,reason",
    [
        (row(enum=True), "enum_invalid"),
        (row(contexts=None), "contexts_invalid"),
        (row(parent_key="missing"), "parent_missing"),
        (row(artwork="synthetic"), "image_data_not_allowed"),
        (None, "record_object_required"),
    ],
)
def test_bad_rows_are_rejected_with_safe_reasons(bad, reason):
    result = plan([bad])
    assert result["summary"]["rejected"] == 1
    assert reason in result["rejected_rows"][0]["reasons"]


def test_scope_signal_and_normalization_collisions_remain_visible():
    result = plan(
        [
            row("Synthetic-One", normalized_key="first"),
            row("Synthetic One", normalized_key="second"),
            row("Synthetic One", scope="other"),
            row("Synthetic One", signal_type="app"),
        ]
    )
    assert result["summary"]["normalization_collisions"] == 1
    assert result["summary"]["outcomes"]["normalization_collision"] == 2


def test_parent_depth_and_context_override_need_explicit_review():
    result = plan(
        [
            row(),
            row("Synthetic Parent", parent_key="synthetic example"),
            row("Synthetic Child", parent_key="synthetic parent"),
            row(
                "Synthetic Context",
                contexts=[{"context": "synthetic", "source_app": "synthetic", "enum_override": 1}],
            ),
        ]
    )
    assert result["summary"]["rejected"] == 1
    assert result["summary"]["context_overrides"] == 1
