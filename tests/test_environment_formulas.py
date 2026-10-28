"""F-01–F-04: pinned Legacy answers, strict required evidence and named quantities."""

from datetime import timedelta

import pytest

from core_contracts.engines import FORMULA_SCHEMAS, FORMULAS
from core_contracts.quality import FieldValue, Reason, ReasonCode, unknown
from core_contracts.value_origin import ValueOrigin

from .test_core import NOW


def evaluate(name, values, **patch):
    arguments = {
        key: FieldValue(status="valid", value=value, quality="healthy", measured_at=NOW)
        for key, value in values.items()
    }
    arguments.update(patch)
    return FORMULAS.evaluate(name, 1, list(arguments.values()), NOW, names=tuple(arguments))


@pytest.mark.parametrize(
    "temperature,humidity,dew,absolute",
    [
        (20, 50, 9.26, 8.64),
        (24, 80, 20.33, 17.40),
        (0, 100, 0, 4.85),
        (-10, 80, -12.80, 1.89),
        (21, 100, 21, 18.31),
        (30, 0, -53.51, 0),
    ],
)
def test_bathroom_pinned_cc886b3_answers(temperature, humidity, dew, absolute):
    args = dict(temperature=temperature, relative_humidity=humidity)
    assert evaluate("dew_point", args).value == dew
    assert evaluate("absolute_humidity", args).value == absolute


@pytest.mark.parametrize(
    "temperature,humidity,wind,expected",
    [
        (20, 50, 1, 19.15),
        (0, 100, 0, -1.99),
        (30, 70, 2, 34.37),
    ],
)
def test_apparent_pinned_cc886b3_answers(temperature, humidity, wind, expected):
    assert (
        evaluate(
            "apparent_temperature",
            dict(wind_speed=wind, relative_humidity=humidity, temperature=temperature),
        ).value
        == expected
    )


RADIATION = dict(
    sun_elevation=30, sun_azimuth=180, dni=800, diffuse=100, window_azimuth=180, window_tilt=60
)


def test_radiation_geometry_and_explicit_issue80_differences():
    assert evaluate("expected_radiation", RADIATION).value == pytest.approx(850, abs=1e-9)
    assert (
        evaluate("expected_radiation", {**RADIATION, "window_azimuth": 0, "window_tilt": 90}).value
        == 50
    )
    assert evaluate("expected_radiation", {**RADIATION, "sun_elevation": 0}).value == 0
    assert evaluate("expected_radiation", {**RADIATION, "sun_elevation": -20}).value == 0
    assert evaluate("expected_radiation", {**RADIATION, "window_tilt": 0}).value == pytest.approx(
        450
    )
    assert evaluate("expected_radiation", {**RADIATION, "dni": -1}).value == 50
    assert evaluate("expected_radiation", {**RADIATION, "sun_azimuth": 540}).value == pytest.approx(
        850
    )
    for name in ("dni", "diffuse", "sun_elevation"):
        assert (
            evaluate(
                "expected_radiation",
                RADIATION,
                **{name: unknown(ReasonCode.INPUT_ABSENT, name, NOW)},
            ).status
            == "unknown"
        )
        assert (
            evaluate(
                "expected_radiation",
                {**RADIATION, "sun_elevation": -20},
                **{name: unknown(ReasonCode.INPUT_ABSENT, name, NOW)},
            ).status
            == "unknown"
        )


@pytest.mark.parametrize(
    "name,category,values",
    [
        ("dew_point", "temperature", dict(temperature=20, relative_humidity=50)),
        ("absolute_humidity", "humidity", dict(temperature=20, relative_humidity=50)),
        (
            "apparent_temperature",
            "temperature",
            dict(temperature=20, relative_humidity=50, wind_speed=1),
        ),
        ("expected_radiation", "radiation", RADIATION),
    ],
)
def test_catalog_quality_origin_and_every_required_argument(name, category, values):
    declaration = FORMULA_SCHEMAS[name, 1]
    assert declaration.category == category
    assert {i.name for i in declaration.inputs} == set(values)
    assert all(i.required and i.schema.unit for i in declaration.inputs)
    assert (
        evaluate(name, dict(reversed(list(values.items())))).value == evaluate(name, values).value
    )
    for key in values:
        absent = unknown(ReasonCode.INPUT_STALE, key, NOW)
        result = evaluate(name, values, **{key: absent})
        assert result.status == "unknown" and result.reasons == absent.reasons
        degraded = FieldValue(
            status="valid", value=values[key], quality="degraded", measured_at=NOW
        )
        assert evaluate(name, values, **{key: degraded}).quality == "degraded"
        held = FieldValue(
            status="held",
            value=values[key],
            quality="healthy",
            measured_at=NOW,
            held_until=NOW + timedelta(seconds=5),
            grace_declared=True,
            reasons=(Reason(code=ReasonCode.GRACE, input=key, since=NOW),),
        )
        result = evaluate(name, values, **{key: held})
        assert result.status == "held" and result.held_until == held.held_until
        assert result.measured_at == NOW
        expired = held.model_copy(update={"held_until": NOW})
        assert evaluate(name, values, **{key: expired}).status == "unknown"
        limited = degraded.model_copy(
            update={
                "value_origin": ValueOrigin(
                    kind="model",
                    provider="synthetic",
                    valid_from=NOW - timedelta(seconds=1),
                    valid_until=NOW + timedelta(seconds=1),
                ),
                "valid_until": NOW + timedelta(seconds=1),
                "evidence": ("synthetic:report",),
            }
        )
        result = evaluate(name, values, **{key: limited})
        assert result.valid_until == limited.valid_until
        assert limited.value_origin in result.input_origins
        assert "synthetic:report" in result.evidence
    for key in values:
        invalid = {**values, key: True}
        assert evaluate(name, invalid).status == "unknown"
        for invalid_number in [float("nan"), float("inf"), float("-inf")]:
            assert evaluate(name, {**values, key: invalid_number}).status == "unknown"


@pytest.mark.parametrize(
    "name,temperature",
    [
        ("dew_point", -243.12),
        ("absolute_humidity", -243.5),
        ("absolute_humidity", -273.15),
        ("apparent_temperature", -237.7),
    ],
)
def test_singularities_use_existing_formula_error_path(name, temperature):
    result = evaluate(name, dict(temperature=temperature, relative_humidity=50, wind_speed=1))
    assert result.status == "unknown"
    assert result.reasons[0].code == ReasonCode.EVALUATION_ERROR
