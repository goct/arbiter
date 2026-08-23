#!/usr/bin/env python3
"""Per-player analysis and letter grades for a Mythic+ run, from a combat log.

    C:/Python312/python.exe grade.py <WoWCombatLog-*.txt>       # list M+ runs
    C:/Python312/python.exe grade.py <log> --last               # grade the newest
    C:/Python312/python.exe grade.py <log> --n 2                # grade run 2
    C:/Python312/python.exe grade.py <log> --last --raw         # metrics, no grades
    C:/Python312/python.exe grade.py <log> --all                # every run in the log
    C:/Python312/python.exe grade.py <log> --player Hyporock    # one player, all keys
    C:/Python312/python.exe grade.py <log> --all --json n.json  # machine-readable

WHAT THIS SCORES, AND WHY -------------------------------------------------

Companion to `raid-healing.py`, which reports one healer across raid encounters.
This one scores ALL FIVE players across a whole key -- trash included, because
in a key that is where the run is won or lost.

The model rests on three rules, spelled out in `arbiter/score.py`: score against
a reference the run itself provides, never score a player on something they
could not do, and DROP an axis that does not apply rather than scoring it zero.

The axes that are new relative to the first version of this script, and the
naive metric each one replaces:

  interrupts   kicks are scored against the casts that COULD have been stopped,
               not per minute. One +8 leaked 75 of 124 Light Bolts while the
               tank's raw kick rate was scoring a flat 100.
  mechanics    "damage taken" punishes whoever stood nearest the boss. Damage
               from a spell the REST of the party did not take is the part that
               was a decision -- unavoidable raid damage lands on everyone.
  mitigation   active-mitigation UPTIME and coverage of the player's worst
               damage windows, not defensive cast count. Counting presses gave
               a Veng DH a free 100 for pressing Demon Spikes 126 times.
  response     whether a healer's output actually rose during the party's worst
               windows, rather than absolute HPS, which mostly measures key level.
  activity     cast rate with procs and ticks removed. See `arbiter/derive.gcd_set`;
               a Ret paladin logs 117 casts/min against a GCD that caps near 50.
  utility      externals and dispels against the presses the player's own KIT
               offered, from cooldowns measured out of the logs and a talent
               loadout read from COMBATANT_INFO -- not a flat rate that grades
               a Veng DH's five sigils against a Prot Warrior's kit.

Every denominator above is built from what the player TALENTED plus what they
pressed, never presses alone -- skip a button all key and a press-only
denominator shrinks with it, so not pressing it raises the grade. See
`knowledge.buttons` for why the answer is the union of the tree's own
active/passive flag and the hand-written name lists, and never either alone.

  throughput   the share of the damage that actually NEEDED an outside heal,
               not the share of everything the party took. The old denominator
               included the sixty million a Vengeance DH heals back on himself
               and every self-shield in the group, so a party that looked after
               itself lowered the healer's grade -- a report could say very
               little healing was needed and mark the healer down for not
               healing much, on the same page.

Removed on purpose, as measuring the class rather than the player: raw CC
count, defensive press count, absolute HPS bands, and overhealing percent.

WHAT THIS DELIBERATELY WILL NOT TELL YOU ----------------------------------

The keystone UPGRADE (+1/+2/+3). A combat log contains no par time, so it
cannot be derived, and a previous report that derived it anyway read
`CHALLENGE_MODE_END` field 7 as "par seconds" and published a +2 for a key
that was a +1. Field 7 is the character's TOTAL M+ RATING; field 6 is the
run's dungeon score. Both are now printed, correctly labelled. The upgrade is
printed only when `data/dungeons.json` holds a real par for the dungeon --
see `arbiter/dungeons.py` for how bounds narrow toward one.

CROSS-CHECKS --------------------------------------------------------------

The death count is verified against the game's own timer on every run:
`CHALLENGE_MODE_END`'s milliseconds are wall clock plus five seconds a death,
so the count falls out of arithmetic that never touches this parser. A
disagreement prints a `!!` line rather than being quietly graded.

FIELD OFFSETS and the traps around them are documented in `arbiter/logfile.py`.
They are not a detail: reading one column too far left made every damage
number in this pipeline wrong in both directions at once.

BEYOND THE GRADES ---------------------------------------------------------

  time ledger  the timer is wall clock PLUS five seconds a death. Split into
               fighting / routing / wipe recovery / death penalty, which names
               the minutes that were recoverable.
  per pull     `cost` is time LOST, not time spent. A wiped pull is wasted in
               full because the pack has to be killed again; one that held
               costs only its deaths. Ranking on duration put a six-minute pull
               that killed 39 enemies without a death above the two that wiped.
  habits       resource overcap, presses the client refused as still-on-
               cooldown, and the mana floor. Printed under the grade and never
               weighted -- the model scores outcomes, but a habit is the part
               somebody can change on the next pull.
  --json       a night as ~100 KB of flat data, every axis with its pre-band
               `raw`. Recalibrating a band or tracking a habit across a month
               no longer means re-parsing gigabytes.

The registry of what is interruptible and dispellable lives in
`data/spell-registry.json` and grows every time this is run; see
`arbiter/knowledge.py` for why proof only runs one way. Cooldowns there are a low
PERCENTILE of observed gaps rather than the record low: proc resets put two
casts of a long-cooldown ability seconds apart, and a minimum latches onto that
permanently, inflating every availability denominator built on it.
"""

import argparse
import sys

from arbiter import collect, derive, dungeons, export, knowledge, logfile
from arbiter import report, score


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--n", type=int, help="run number from the listing")
    ap.add_argument("--last", action="store_true")
    ap.add_argument("--all", action="store_true", help="grade every run in the log")
    ap.add_argument("--raw", action="store_true", help="metrics table only, no grades")
    ap.add_argument("--no-learn", action="store_true",
                    help="do not update data/spell-registry.json")
    ap.add_argument("--player", metavar="NAME",
                    help="one player's axes across every key in the log")
    ap.add_argument("--json", metavar="FILE",
                    help="also write every graded run to FILE as JSON")
    ap.add_argument("--quiet", action="store_true",
                    help="suppress the text report (use with --json)")
    a = ap.parse_args()
    if a.player:
        a.all = True

    found = logfile.find_runs(a.log)
    if not found:
        sys.exit("no CHALLENGE_MODE_START/END pairs in that log")

    if not (a.n or a.last or a.all):
        for k in found:
            state = ("timed" if k.timed else
                     "depleted" if k.finished else "abandoned")
            print(f"{k.index:3}. {k.name:28} +{k.level:<3} {state:9} "
                  f"{k.seconds / 60:5.1f}m  lines {k.start_line}-{k.end_line}")
        return

    if a.all:
        picks = found
    elif a.last:
        picks = [found[-1]]
    else:
        picks = [k for k in found if k.index == a.n]
    if not picks:
        sys.exit(f"no run {a.n}")

    reg = knowledge.Registry()
    abilities = knowledge.load_abilities()

    if len(picks) > 1:
        # Learn from every key BEFORE grading any of them. The registry only
        # discovers that a cast is interruptible by watching somebody kick it,
        # so grading in file order judged the first key of the night with less
        # knowledge than the last -- the same dungeon could score differently
        # depending on where it happened to sit in the log. One extra parse buys
        # every key the same evidence. Run objects are discarded as they go, so
        # this costs time, not memory.
        print(f"[registry] learning from {len(picks)} keys first...", flush=True)
        for _k, run in collect.stream(a.log, picks, reg, light=True):
            derive.learn_abilities(run, reg)

    entries, records = [], []
    for k, run in collect.stream(a.log, picks, reg):
        nm, lvl = k.name, k.level
        if not run.players:
            print(f"-- {nm}: no COMBATANT_INFO inside that run "
                  "(key started before logging?)")
            continue
        ev = score.evaluate(run, reg, abilities)
        if a.player:
            want = a.player.lower()
            for g, row in ev["rows"].items():
                if run.players[g]["name"].lower().startswith(want):
                    entries.append({
                        "run": nm, "level": lvl, "name": run.players[g]["name"],
                        "spec": knowledge.SPECS.get(run.players[g]["spec"], "?"),
                        "letter": row["letter"], "total": row["total"],
                        "axes": {x.name: x.score for x in row["axes"]},
                    })
            continue
        if a.json:
            records.append(export.run_record(run, ev))
        if not a.quiet:
            report.full(run, ev, a.raw)
            print()

    if a.json:
        export.write(a.json, records)
        print(f"[json] {len(records)} run(s) -> {a.json}")
    if a.player:
        report.trend(a.player, entries)
    if not a.no_learn:
        # Par bounds narrow the same way the spell registry grows: only in the
        # direction the evidence actually points. A TIMED key proves par is
        # beyond the clear time and a DEPLETED one proves it is short of it.
        # The UPGRADE is not in the log and is never guessed -- to pin a par
        # down, put the "+" count from Raider.IO into data/dungeons.json by
        # hand and the bound closes from the other side.
        table = dungeons.bounds_from(
            [(k.name, k.timer_seconds, k.timed, None)
             for k in picks if k.finished and k.timer_seconds])
        dungeons.save(table)
        unknown = sorted(n for n in {k.name for k in picks}
                         if not dungeons.par_for(n, table))
        if unknown:
            print(f"[par] still unknown for {', '.join(unknown)} -- upgrades "
                  f"cannot be reported for these until a par time is entered")
        # Saved unconditionally: cooldowns and buff durations are refined on
        # almost every run even when no new spell is identified, and gating the
        # write on new spell IDs silently threw that away.
        gained = reg.gained
        reg.save()
        print(f"[registry] {len(reg.interruptible)} interruptible, "
              f"{len(reg.dispellable)} dispellable, {len(reg.cooldowns)} cooldowns, "
              f"{len(reg.durations)} durations"
              + (f"  (+{gained[0]} / +{gained[1]} new this run)" if any(gained) else ""))


if __name__ == "__main__":
    main()
