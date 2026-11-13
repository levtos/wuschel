"""Wake rule and parameter registrations."""

from datetime import date, datetime, timedelta
from typing import Any

from core_contracts import declarative_models as dm
from core_contracts import slots
from core_contracts.extensions.wake.operator import build_plan, local_start
from core_contracts.extensions.wake.settings import WakeSettings
from core_contracts.model import canonical, utc
from core_contracts.quality import ReasonCode, unknown
from core_contracts.rules.wake import WakeException


def check(
    rule: dm.WakePlanRule, definition: Any, inputs: Any, args: Any, require: Any, scalar: Any
) -> Any:
    wake_declaration = definition.parameter_schema.get(rule.settings_parameter)
    if not wake_declaration or wake_declaration.structured_schema != "wake_planning_v1":
        raise ValueError("wake_settings_parameter_required")
    for name, wake_kind in (
        (rule.date_input, "text"),
        (rule.calendar_input, "object"),
        (rule.holidays_input, "object"),
    ):
        if name not in inputs or inputs[name].type != wake_kind:
            raise ValueError("wake_input_required")
    if rule.exception_input and (
        rule.exception_input not in inputs or inputs[rule.exception_input].type != "object"
    ):
        raise ValueError("wake_exception_input_required")
    require(args[0], scalar("timestamp"))
    return scalar(
        "timestamp"
        if rule.field in {"next_wake_start", "window_start", "window_end"}
        else "bool"
        if rule.field == "minimum_sleep_applied"
        else "text"
    )


def evaluate(evaluation: Any, rule: dm.WakePlanRule) -> Any:
    if rule.id not in evaluation.plans:
        state = evaluation.machine_snapshot()
        evaluation.plans[rule.id] = build_plan(
            WakeSettings.model_validate(evaluation.parameters[rule.settings_parameter]),
            evaluation.now,
            evaluation.inputs[rule.calendar_input],
            evaluation.inputs[rule.holidays_input],
            evaluation.inputs[rule.date_input],
            evaluation.evaluate(rule.sleep_since),
            evaluation.inputs.get(rule.exception_input) if rule.exception_input else None,
            state.consumed_absolute if state else (),
        )
    plan = evaluation.plans[rule.id]
    due(evaluation, rule, plan)
    return plan.get(rule.field, plan.get("all")) or unknown(
        ReasonCode.EVALUATION_ERROR, "wake_plan", evaluation.now
    )


def due(evaluation: Any, rule: dm.WakePlanRule, plan: Any) -> None:
    if evaluation.context:
        due = evaluation.context.node.setdefault("clock_due", [])
        planned = plan.get("next_wake_start")
        if planned and planned.usable(evaluation.now):
            at = datetime.fromisoformat(planned.value) + timedelta(microseconds=1)
            if at > evaluation.now and at.isoformat() not in due:
                due.append(at.isoformat())
        for name in (rule.calendar_input, rule.holidays_input):
            cache = evaluation.inputs[name]
            if cache.usable(evaluation.now) and isinstance(cache.value, dict):
                expires = cache.value.get("expires_at")
                if isinstance(expires, str) and datetime.fromisoformat(expires) > evaluation.now:
                    if expires not in due:
                        due.append(expires)


def children(rule: dm.WakePlanRule) -> list[tuple[str, Any]]:
    return [("sleep_since", rule.sleep_since)]


def validate(rule: dm.WakePlanRule, plans: dict[str, str], issues: Any, error: Any) -> None:
    identity = canonical(rule.model_dump(exclude={"field"}))
    if rule.id in plans and plans[rule.id] != identity:
        issues.append(error("conflicting_plan_id", "/rules"))
    plans[rule.id] = identity


def manual(normalized: Any, end: datetime | None, now: datetime) -> tuple[Any, datetime]:
    exception = WakeException.model_validate(normalized)
    maximum_end = utc(local_start(date.fromisoformat(exception.date) + timedelta(days=1)))
    if maximum_end <= now or (end is not None and end != maximum_end):
        raise ValueError("exception_expiry")
    end = maximum_end
    normalized = exception.model_dump(mode="json", exclude_none=True)
    return normalized, end


slots.PARAMETER_MODELS["wake_planning_v1"] = WakeSettings
slots.RULES[dm.WakePlanRule] = slots.Rule(
    check=check,
    evaluate=evaluate,
    children=children,
    due=due,
    validate=validate,
    minimum_language=6,
)
slots.RULES[WakeException] = slots.Rule(manual=manual, manual_schema="wake_exception_v1")
