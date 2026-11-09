"""Small, shared validators for public simulation inputs."""
import math
from numbers import Integral, Real


def integer(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, Integral) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def finite(value, name, minimum=0.0, strict=False):
    try:
        is_finite = math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        is_finite = False
    if (isinstance(value, bool) or not isinstance(value, Real)
            or not is_finite
            or (value <= minimum if strict else value < minimum)):
        relation = ">" if strict else ">="
        raise ValueError(f"{name} must be finite and {relation} {minimum}")
    return value
