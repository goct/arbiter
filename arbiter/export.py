"""A run as data, so a night does not have to be re-parsed to be re-read.

The text report is for reading once. Everything else anybody has wanted from
this tool -- how a habit moved across a month, whether a band is calibrated,
what a dungeon's par time must be -- is a question across MANY keys, and the
only way to ask it was to re-parse gigabytes of log. A 365 MB night reduces to
about 100 KB here -- 17 KB a key -- which is small enough to keep beside
the repo forever.

Deliberately flat and boring: no objects, no dates as objects, nothing that
needs this package to interpret. Every axis carries its `raw` pre-band value,
which is what `score.Axis` was carrying it for -- recalibrating from a corpus
of these is the whole point.
"""

import json

from . import knowledge as K
from . import logfile as L


SCHEMA = 2


def run_record(run, ev):
    """One key, as plain JSON-able data."""
    k = run.key
    ledger = ev.get("ledger") or {}
    need = ev.get("need") or {}
    check = ev.get("death_check") or {}
    return {
        "schema": SCHEMA,
        "dungeon": run.name,
        "level": run.level,
        "timed": bool(run.timed),
        "finished": bool(k.finished) if k else None,
        "affixes": L.affix_names(k.affixes) if k and k.affixes else [],
        "started": k.start_t if k else None,
        "clear_seconds": (k.timer_seconds if k and k.timer_seconds else run.dur),
        "wall_seconds": run.dur,
        "run_score": k.score if k else None,
        "rating_after": k.rating if k else None,
        "deaths": len(run.death_events),
        "deaths_implied_by_timer": check.get("expected"),
        "unconscious": sum(run.unconscious.values()),
        "enemies_killed": run.trash_kills,
        "party_damage_taken": sum(run.dmg_taken.values()),
        "healing_need": need.get("need"),
        "self_covered": need.get("self"),
        "interrupts": {"stopped": ev["board"]["stopped"], "total": ev["board"]["total"]},
        "dispels": {"cleansed": ev["dispel"].get("cleansed", 0),
                    "opportunities": ev["dispel"].get("opportunities", 0),
                    "damage_through": ev["dispel"].get("cost", 0)},
        "time": {n: round(ledger.get(n, 0.0), 1) for n in
                 ("total", "combat", "routing", "wipe_recovery",
                  "death_penalty", "lead_in", "unaccounted")},
        "bosses": [{"name": n, "seconds": round(s, 1), "killed": bool(kk)}
                   for n, s, kk in (ev["bosses"]["bosses"] if ev.get("bosses") else [])],
        "pulls": [{key: (round(p[key], 1) if isinstance(p[key], float) else p[key])
                   for key in ("n", "at", "seconds", "deaths", "wipe", "taken",
                               "kills", "opportunities", "stopped", "cost")}
                  for p in (ev.get("by_pull") or [])],
        "players": [_player(run, ev, g) for g in run.players],
        "deaths_detail": [{
            "at": round(d["at"], 1), "name": d["name"],
            "killer": (d["killer"][2] if d["killer"] else None),
            "source": (d["killer"][3] if d["killer"] else None),
            "burst": d["burst"], "over": round(d["over"], 1),
            "healing_received": d["healed"],
            "defensives": d["own_cd"],
            "externals": [f"{s} from {w}" for s, w in d["externals"]],
        } for d in ev.get("forensics", [])],
    }


def _player(run, ev, g):
    p, row = run.players[g], ev["rows"][g]
    return {
        "name": p["name"],
        "spec": K.SPECS.get(p["spec"], str(p["spec"])),
        "spec_id": p["spec"],
        "role": p["role"],
        "ilvl": round(p["ilvl"], 1),
        "grade": row["letter"],
        "score": round(row["total"], 1),
        # `raw` is the measurement BEFORE banding. Bands are the part most
        # likely to be re-cut; raws are the part worth keeping.
        "axes": {a.name: {"score": (round(a.score, 1) if a.score is not None else None),
                          "weight": round(a.weight, 4),
                          "raw": (round(a.raw, 5) if isinstance(a.raw, float)
                                  else a.raw),
                          "note": a.note}
                 for a in row["axes"]},
        "dps": round(row["dps"], 1),
        "hps": round(row["hps"], 1),
        "damage_done": run.dmg_done[g] + run.dmg_support[g],
        "healing_done": run.healed[g],
        "absorb_given": run.absorb_given[g],
        "damage_taken": run.dmg_taken[g],
        "deaths": run.deaths[g],
        "interrupts": row["kicks"],
        "cc_stops": row["cc"],
        "dispels": row["dispels"],
        "resurrects": row["rezzes"],
        "over_exposed": row["avoid"]["amount"],
        "over_exposed_detail": [{"spell": sp, "mine": mine, "peer_median": med,
                                 "attributed": round(amt)}
                                for sp, mine, med, amt in row["avoid"]["detail"]],
        # Where the damage actually went. Not scored -- target priority needs a
        # kill order this tool does not have -- but it is the raw material for
        # asking that question later, and it costs nothing to carry.
        "top_targets": [{"target": m, "damage": v} for m, v in
                        (run.dmg_to_target[g].most_common(5) if g in run.dmg_to_target else [])],
        "active_pct": round(row["act"], 1),
        "casts_per_min": round(row["cpm"], 2),
        "overcapped": [{"resource": n, "lost": lost, "generated": got}
                       for n, lost, got, _f in row["waste"]],
        "refused_presses": [{"spell": sp, "refused": n, "casts": u}
                            for sp, n, u, _r in row["mash"]],
        "never_pressed": row["unused"],
    }


def write(path, records):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"schema": SCHEMA, "runs": records}, fh, indent=1,
                  ensure_ascii=False)
