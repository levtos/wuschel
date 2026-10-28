"""Code-owned v1 climate math, ported from the pinned Legacy sources in A1."""

import math


def number(args: dict[str, float], name: str) -> float:
    value = args[name]
    if type(value) not in {int, float} or not math.isfinite(value):
        raise ValueError("invalid_formula_number")
    return value


def dew_point(args: dict[str, float]) -> float:
    temperature = number(args, "temperature")
    humidity = max(0.1, min(100.0, number(args, "relative_humidity")))
    a, b = 17.62, 243.12
    gamma = math.log(humidity / 100.0) + (a * temperature) / (b + temperature)
    return round((b * gamma) / (a - gamma), 2)


def absolute_humidity(args: dict[str, float]) -> float:
    temperature = number(args, "temperature")
    humidity = max(0.0, min(100.0, number(args, "relative_humidity")))
    saturation = 6.112 * math.exp((17.67 * temperature) / (temperature + 243.5))
    vapor_pressure = humidity / 100.0 * saturation
    return round(216.7 * vapor_pressure / (temperature + 273.15), 2)


def apparent_temperature(args: dict[str, float]) -> float:
    temperature = number(args, "temperature")
    humidity = max(0.0, min(100.0, number(args, "relative_humidity")))
    wind = max(0.0, number(args, "wind_speed"))
    vapor_pressure = (
        humidity / 100.0 * 6.105 * math.exp((17.27 * temperature) / (237.7 + temperature))
    )
    return round(temperature + 0.33 * vapor_pressure - 0.70 * wind - 4.0, 2)


def expected_radiation(args: dict[str, float]) -> float:
    # Read every required quantity before the night branch: missing input is not zero.
    height, direction, dni, diffuse, azimuth, tilt = (
        number(args, name)
        for name in (
            "sun_elevation",
            "sun_azimuth",
            "dni",
            "diffuse",
            "window_azimuth",
            "window_tilt",
        )
    )
    if not -90 <= height <= 90 or not 0 <= tilt <= 180:
        raise ValueError("solar_geometry_outside_range")
    if height <= 0:
        return 0.0
    elevation = math.radians(height)
    sun_azimuth = math.radians(direction % 360)
    window_azimuth = math.radians(azimuth % 360)
    window_tilt = math.radians(tilt)
    sun_vector = (
        math.cos(elevation) * math.sin(sun_azimuth),
        math.cos(elevation) * math.cos(sun_azimuth),
        math.sin(elevation),
    )
    window_normal = (
        math.sin(window_tilt) * math.sin(window_azimuth),
        math.sin(window_tilt) * math.cos(window_azimuth),
        math.cos(window_tilt),
    )
    incidence = max(
        0.0, min(1.0, sum(a * b for a, b in zip(sun_vector, window_normal, strict=True)))
    )
    return max(0.0, dni) * incidence + max(0.0, diffuse) * 0.5
