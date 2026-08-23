"""Par times, and why they cannot come out of the log.

A combat log contains no par time. It never has. `CHALLENGE_MODE_END` carries
the instance, whether the key was timed, the level, the timer in milliseconds,
the run's Mythic+ dungeon SCORE, and the character's TOTAL M+ rating afterwards
-- and nothing that says how long the dungeon was supposed to take.

This file exists because a previous report computed "par seconds" from field 7
and published an upgrade column off it. Field 7 is the rating. The giveaway was
sitting in the data the whole time: the same dungeon showed 1972.98 at +9 and
2107.59 at +10, and a par time does not change with key level. Two of the five
keys were reported as +2 upgrades. One of them was a +1.

So the rule here is the same one the spell registry runs on: state what is
proven, and say "unknown" rather than guess.

WHAT IS PROVEN
--------------
The upgrade thresholds are the standard ones -- timed is +1, 20% under par is
+2, 40% under is +3 -- confirmed against two dungeons whose par times are known
from earlier expansions:

    Kings' Rest   par 34:00, cleared in 17:07 -> 49.7% under -> +3   (observed)
    Ruby Life Pools par 30:00, cleared in 25:54 -> 13.7% under -> +1 (observed)

Both observations come from the in-game Raider.IO readout on 2026-08-21, which
writes the upgrade count as leading plus signs: `+++2` for Kings' Rest, `+7`
for Ruby Life Pools.

WHAT IS BOUNDED
---------------
Midnight's own dungeons have no published par time here, but every run puts a
bound on one, and the bounds are worth keeping because they close over time:

    a TIMED run          proves  par > clear time
    a DEPLETED run       proves  par < clear time
    an observed upgrade  proves  par is inside the band that upgrade implies

`bounds_from` does that arithmetic. Feed it the keys out of a log plus any
upgrade counts you can read off Raider.IO, and it narrows every dungeon it can.
Nothing here is ever inferred from the score field.
"""

import json
import pathlib

PATH = pathlib.Path(__file__).resolve().parents[1] / "data" / "dungeons.json"

# fraction under par -> upgrade level
THRESHOLDS = ((0.40, 3), (0.20, 2), (0.0, 1))


def upgrade(clear_seconds, par_seconds, timed=True):
    """+1/+2/+3, or None when par is unknown. 0 means the key depleted."""
    if not timed:
        return 0
    if not par_seconds or not clear_seconds:
        return None
    under = (par_seconds - clear_seconds) / par_seconds
    for cut, lvl in THRESHOLDS:
        if under >= cut:
            return lvl
    return 0


def load():
    """{dungeon: {"par": s|None, "low": s|None, "high": s|None, "note": str}}"""
    if not PATH.exists():
        return {}
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def par_for(name, table=None):
    """Par seconds if it is actually known, else None. A bounded-but-unknown
    dungeon returns None on purpose -- a midpoint guess would put an upgrade
    column back on the page, which is the thing this module exists to stop."""
    row = (table if table is not None else load()).get(name)
    if not row:
        return None
    return row.get("par")


def describe(name, table=None):
    """Human-readable par state, for a report that has to be honest about it."""
    row = (table if table is not None else load()).get(name)
    if not row:
        return "par unknown (no observations yet)"
    if row.get("par"):
        return f"par {int(row['par']) // 60}:{int(row['par']) % 60:02d}"
    lo, hi = row.get("low"), row.get("high")
    if lo and hi:
        return (f"par unknown, bounded to "
                f"{int(lo) // 60}:{int(lo) % 60:02d}-{int(hi) // 60}:{int(hi) % 60:02d}")
    if lo:
        return f"par unknown, at least {int(lo) // 60}:{int(lo) % 60:02d}"
    if hi:
        return f"par unknown, at most {int(hi) // 60}:{int(hi) % 60:02d}"
    return "par unknown"


def bounds_from(observations, table=None):
    """Narrow the par bounds from (dungeon, clear_seconds, timed, upgrade) rows.

    `upgrade` may be None when it was not observed; the timed/depleted fact
    alone still moves one side of the bound."""
    table = dict(table if table is not None else load())
    for name, secs, timed, up in observations:
        if not secs:
            continue
        row = dict(table.get(name) or {})
        lo, hi = row.get("low"), row.get("high")
        if timed:
            lo = max(lo or 0, secs)                       # par is beyond the clear
        else:
            hi = min(hi or float("inf"), secs)
        if up == 1:
            hi = min(hi or float("inf"), secs / 0.80)     # under 20%
        elif up == 2:
            lo = max(lo or 0, secs / 0.80)
            hi = min(hi or float("inf"), secs / 0.60)
        elif up == 3:
            lo = max(lo or 0, secs / 0.60)
        row["low"] = round(lo, 1) if lo else None
        row["high"] = round(hi, 1) if hi and hi != float("inf") else None
        if row.get("low") and row.get("high") and row["low"] > row["high"]:
            row["note"] = ("bounds contradict -- an observation is wrong, "
                           "or the thresholds changed")
        table[name] = row
    return table


def save(table):
    PATH.write_text(json.dumps(table, indent=1, sort_keys=True), encoding="utf-8")
