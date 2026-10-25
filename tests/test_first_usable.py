"""Argument-order fusion is distinct from health-prioritized fusion."""

from datetime import timedelta

import pytest

from core_contracts.capabilities import capabilities
from core_contracts.declarative import Evaluation, check_definition
from core_contracts.declarative_models import DeclarativeDefinition
from core_contracts.fusion import Fusion
from core_contracts.quality import FieldValue, Reason, ReasonCode, unknown
from core_contracts.testing.contract_types import type_registry

from .test_core import NOW
from .test_declarative import definition, literal


def good(value=True, **kw):
    return FieldValue(status="valid", quality="healthy", value=value, measured_at=NOW, **kw)


@pytest.mark.parametrize(
    "code", [ReasonCode.INPUT_UNKNOWN, ReasonCode.SELECTION_BLOCKED, ReasonCode.INPUT_STALE]
)
def test_missing_predecessor_degrades_and_carries_its_reason(code):
    missing = unknown(code, "first", NOW)
    if code == ReasonCode.SELECTION_BLOCKED:
        missing = missing.model_copy(update={"status": "unresolved"})
    result = Fusion().evaluate("first_usable", [missing, good(False)], NOW)
    assert result.value is False and result.quality == "degraded"
    assert [reason.code for reason in result.reasons] == [ReasonCode.PARTIAL_EVIDENCE, code]
    assert result.reasons[-1] == missing.reasons[0]


def test_first_usable_keeps_degraded_priority_and_ignores_later_failures():
    first = good().model_copy(update={"quality": "degraded"})
    assert Fusion().evaluate("first_usable", [first, good(False)], NOW) == first
    missing = unknown(ReasonCode.INPUT_STALE, "last", NOW)
    assert Fusion().evaluate("first_usable", [good(), missing], NOW) == good()
    # The old strategy still chooses the later healthy candidate.
    assert Fusion().evaluate("first_healthy", [first, good(False)], NOW).value is False


def test_no_match_is_not_missing_but_mixed_reasons_preserve_real_failure():
    no_match = unknown(ReasonCode.NO_MATCH, "case", NOW)
    assert Fusion().evaluate("first_usable", [no_match, good()], NOW) == good()
    mixed = no_match.model_copy(
        update={
            "reasons": no_match.reasons + unknown(ReasonCode.INPUT_UNKNOWN, "input", NOW).reasons
        }
    )
    assert Fusion().evaluate("first_usable", [mixed, good()], NOW).quality == "degraded"


def test_no_usable_candidate_retains_all_causes_and_empty_has_a_reason():
    missing = [
        unknown(code, str(i), NOW)
        for i, code in enumerate(
            [ReasonCode.NO_MATCH, ReasonCode.INPUT_UNKNOWN, ReasonCode.SELECTION_BLOCKED]
        )
    ]
    result = Fusion().evaluate("first_usable", missing, NOW)
    assert result.status == "unknown" and result.value is None
    assert result.reasons == tuple(reason for item in missing for reason in item.reasons)
    assert Fusion().evaluate("first_usable", [], NOW).reasons[0].code == ReasonCode.INPUT_UNKNOWN


def test_held_deadline_and_quality_are_preserved_even_after_missing_predecessor():
    held = FieldValue(
        status="held",
        quality="healthy",
        value="chosen",
        grace_declared=True,
        held_until=NOW + timedelta(seconds=10),
        measured_at=NOW,
        reasons=(Reason(code=ReasonCode.GRACE, input="chosen", since=NOW),),
        evidence=("selected",),
    )
    result = Fusion().evaluate(
        "first_usable", [unknown(ReasonCode.INPUT_STALE, "first", NOW), held], NOW
    )
    assert result.status == "held" and result.quality == "healthy"
    assert result.held_until == held.held_until and result.evidence == held.evidence
    assert FieldValue.model_validate(result.model_dump()) == result
    assert Fusion().evaluate("first_usable", [held, good(False)], held.held_until).value is False


def test_chosen_metadata_and_validity_window_are_not_widened():
    chosen = good(evidence=("selected",), valid_until=NOW + timedelta(seconds=3))
    assert Fusion().evaluate("first_usable", [chosen, good(False)], NOW) == chosen
    result = Fusion().evaluate("first_usable", [chosen, good(False)], chosen.valid_until)
    assert result.value is False and result.quality == "degraded"


def test_trace_capability_validation_and_reserved_name():
    data = definition(
        dict(
            op="fusion", strategy="first_usable", args=[dict(op="input", name="a"), literal(False)]
        )
    )
    parsed = DeclarativeDefinition.model_validate(data)
    check_definition(parsed)
    evaluation = Evaluation(parsed, {}, NOW)
    assert evaluation.evaluate(parsed.outputs["value"]).value is False
    trace = next(item for item in evaluation.trace if item["op"] == "fusion_selection")
    assert trace["selected_indices"] == [1] and trace["selection"] == "engine"
    evaluation = Evaluation(parsed, {"a": good()}, NOW)
    evaluation.evaluate(parsed.outputs["value"])
    assert next(item for item in evaluation.trace if item["op"] == "fusion_selection")[
        "selected_indices"
    ] == [0]
    discovery = capabilities(None, type_registry())
    assert (
        next(item for item in discovery["fusion"] if item["strategy"] == "first_usable")["priority"]
        == "argument_order"
    )
    with pytest.raises(ValueError, match="duplicate strategy"):
        Fusion().register("first_usable", lambda items, now: items[0])

    data = definition(dict(op="fusion", strategy="first_usable", args=[dict(op="input", name="a")]))
    parsed = DeclarativeDefinition.model_validate(data)
    evaluation = Evaluation(parsed, {}, NOW)
    assert evaluation.evaluate(parsed.outputs["value"]).status == "unknown"
    assert (
        next(item for item in evaluation.trace if item["op"] == "fusion_selection")[
            "selected_indices"
        ]
        == []
    )


@pytest.mark.parametrize(
    "strategy,expected",
    [("first_healthy", True), ("latest", False), ("any_true", True), ("all_true", False)],
)
def test_existing_strategies_keep_their_results_and_partial_evidence(strategy, expected):
    candidates = [
        good(),
        good(False).model_copy(update={"measured_at": NOW + timedelta(seconds=1)}),
        unknown(ReasonCode.NO_MATCH, "last", NOW),
    ]
    result = Fusion().evaluate(strategy, candidates, NOW)
    assert result.value is expected and result.status == "valid" and result.quality == "degraded"
    assert ReasonCode.PARTIAL_EVIDENCE in {reason.code for reason in result.reasons}
