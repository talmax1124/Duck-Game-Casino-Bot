import secrets
from typing import Optional


def get_secure_hazard(total_lanes: int = 5) -> int:
    """
    Return a cryptographically secure hazard lane index in [0, total_lanes-1].

    This is lane-count aware so it works for all modes:
      - Easy   -> total_lanes = 7
      - Medium -> total_lanes = 5
      - Hard   -> total_lanes = 3

    Notes:
    - The finish lane is NOT part of this range. Callers should treat the
      finish (with graphic at /assets/road/end.png) as the lane AFTER the
      last playable lane and apply the final multiplier there.
    - If total_lanes < 1, we clamp to 1 so randbelow() remains valid.
    """
    try:
        lanes = max(1, int(total_lanes))  # clamp for safety
        return secrets.randbelow(lanes)   # 0 .. lanes-1 inclusive
    except Exception:
        # Safe fallback: first lane
        return 0