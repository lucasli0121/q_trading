from typing import Any


def to_float(value: Any, default: float = 0.0) -> float:
    """Convert a value to float, handling commas and common Chinese units.

    Returns `default` if the value is empty, None, or cannot be converted.
    """
    if value is None or value == "":
        return default
    try:
        normalized = str(value).replace(",", "").replace("元", "").strip()
        return float(normalized)
    except (TypeError, ValueError):
        return default
