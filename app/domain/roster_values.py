from __future__ import annotations

from typing import Final


# The Excel roster's エリア column is the authority.  These are its closed
# reporting regions; there is deliberately no second location dictionary.
CANONICAL_AREAS: Final[tuple[str, ...]] = (
    "北海道東北",
    "関東A",
    "関東B",
    "首都圏A",
    "首都圏B",
    "東海北陸",
    "関西",
    "中四国",
    "九州",
    "本社",
)
HEADQUARTERS_AREA: Final[str] = "本社"
HEADQUARTERS_WORKPLACE: Final[str] = "虎ノ門"
HEADQUARTERS_AREA_KEY: Final[str] = "本社・虎ノ門"
