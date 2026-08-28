from datetime import date


AFFINITY_LEVELS = (
    (0, "初识"),
    (20, "熟悉"),
    (40, "信赖"),
    (70, "亲近"),
    (90, "默契"),
)
AFFINITY_MAX = 100
AFFINITY_DAILY_LIMIT = 5


def clamp_affinity(value):
    try:
        return max(0, min(AFFINITY_MAX, int(value)))
    except (TypeError, ValueError):
        return 0


def affinity_info(value):
    value = clamp_affinity(value)
    current = AFFINITY_LEVELS[0]
    for level in AFFINITY_LEVELS:
        if value >= level[0]:
            current = level
        else:
            break
    return {"value": value, "threshold": current[0], "label": current[1]}


def apply_affinity_gain(state, amount, today=None):
    """Apply a capped positive gain to one pet state and return the actual gain."""
    if not isinstance(state, dict):
        return 0
    today = today or date.today().isoformat()
    if state.get("affinity_date") != today:
        state["affinity_date"] = today
        state["affinity_gain_today"] = 0
    gained_today = max(0, int(state.get("affinity_gain_today", 0) or 0))
    remaining = max(0, AFFINITY_DAILY_LIMIT - gained_today)
    try:
        requested = max(0, int(amount))
    except (TypeError, ValueError):
        requested = 0
    gain = min(requested, remaining)
    state["affinity"] = clamp_affinity(state.get("affinity", 0)) + gain
    state["affinity_gain_today"] = gained_today + gain
    return gain
