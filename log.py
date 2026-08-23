#!/usr/bin/env python3
"""Answer one question about a combat log, without grepping it by hand.

    C:/Python312/python.exe log.py <log> runs
    C:/Python312/python.exe log.py <log> spell "Holy Shock"      # name -> id
    C:/Python312/python.exe log.py <log> spell 20473             # id -> name
    C:/Python312/python.exe log.py <log> --last events SPELL_DISPEL
    C:/Python312/python.exe log.py <log> --last taken Hyporock
    C:/Python312/python.exe log.py <log> --last casts Hyporock
    C:/Python312/python.exe log.py <log> --last uptime "Avenging Wrath" --unit Hyporock
    C:/Python312/python.exe log.py <log> --last gear Hyporock

WHY THIS EXISTS -----------------------------------------------------------

`grade.py` answers the questions the grading model asks. Everything ELSE
was being answered by hand -- a `grep -a` down a 365 MB file, or a throwaway
Python script written into a scratchpad and thrown away, which is how the same
dispel scan got written five times.

Two costs to that. The obvious one is tokens: measured across this repo's own
sessions, hand-grepping raw log lines was 28% of the dwell cost of a grading
night -- MORE than the grading script's entire output. The other is that a
throwaway grep re-derives the field offsets each time, and the offsets in this
format are wrong in the obvious guesses. `arbiter/logfile.py` documents the traps
that cost real debugging time: damage is not at a fixed offset, the interrupted
spell is f[13] and not f[14], the CRLF rides on the last field. Every command
here goes through those primitives, so a question asked from this tool cannot
quietly reproduce a bug that was already found and fixed once.

Scoping: with `--last` / `--n N` a command reads only that key's byte range,
which is a seek rather than a scan. Without either, it reads the whole file --
correct for `spell`, slow and rarely what you want for anything else.

Every command caps its output and says what it held back.
"""

import argparse
import collections
import io
import sys

from arbiter import knowledge, logfile as L


# ---------------------------------------------------------------- scoping

def pick(path, a):
    """(Key or None, start_line, end_line, start_byte) for the requested scope."""
    if not (a.last or a.n):
        return None, 1, float("inf"), None
    found = L.find_runs(path)
    if not found:
        sys.exit("no CHALLENGE_MODE_START/END pairs in that log")
    if a.last:
        k = found[-1]
    else:
        hit = [x for x in found if x.index == a.n]
        if not hit:
            sys.exit(f"no run {a.n}; there are {len(found)}")
        k = hit[0]
    return k, k.start_line, k.end_line, k.start_byte


def scope_line(k):
    if not k:
        return "whole log"
    return (f"{k.name} +{k.level} "
            f"({'timed' if k.timed else 'depleted' if k.finished else 'abandoned'}, "
            f"{k.seconds / 60:.1f}m, lines {k.start_line}-{k.end_line})")


def _stream(path, a, keep=None):
    k, s, e, b = pick(path, a)
    print(f"-- {scope_line(k)}")
    return k, L.events(path, s, e, b, keep=keep)


def _clock(k, t):
    """Elapsed time into the key, or time of day when the scope is the whole log.

    Without the fallback this printed the raw absolute value -- 63923018835.1 --
    which is a date expressed in seconds and reads as a parser bug."""
    if k and k.start_t:
        d = int(t - k.start_t)
        return f"{d // 60}:{d % 60:02d}"
    s = int(t) % 86400
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}"


# Events whose interesting spell is the extraSpell at f[12]/f[13] rather than
# the spell at f[9]/f[10]. The interrupt offset here is the one documented in
# arbiter/logfile.py as f[13] and not f[14] -- getting it wrong reads as "nothing
# in this dungeon is kickable".
_EXTRA_SPELL = {"SPELL_INTERRUPT": "stopped", "SPELL_DISPEL": "removed",
                "SPELL_DISPEL_FAILED": "failed on", "SPELL_STOLEN": "stole",
                "SPELL_AURA_BROKEN_SPELL": "broke"}


# ---------------------------------------------------------------- commands

def cmd_runs(path, a):
    for k in L.find_runs(path):
        state = "timed" if k.timed else "depleted" if k.finished else "abandoned"
        print(f"{k.index:3}. {k.name:28} +{k.level:<3} {state:9} "
              f"{k.seconds / 60:5.1f}m  lines {k.start_line}-{k.end_line}")


def cmd_spell(path, a):
    """Resolve a spell name to its id, or an id to its name.

    This is the single most-repeated hand query in the history of this repo, and
    the naive form (`grep -o ',<id>,"[^"]*"'`) is wrong in a way that bites:
    the same NAME is several different ids across specs and ranks, so the first
    match is not the answer -- see the Paladin Judgment/Hammer of Wrath split.
    Every distinct pairing is printed with how often it was seen."""
    want = a.what
    numeric = want.isdigit()
    needle = (f",{want},").encode() if numeric else want.encode("utf-8")
    seen = collections.Counter()
    where = {}
    n = 0
    with io.open(path, "rb") as fh:
        for raw in fh:
            n += 1
            if needle not in raw:
                continue
            b = L.body_of(raw.decode("utf-8", "replace"))
            if not b:
                continue
            f = L.split(b)
            ev = f[0]
            sid, name = L.spell_fields(ev, f)
            if not sid:
                continue
            if numeric:
                if sid != want:
                    continue
            elif name.lower() != want.lower():
                continue
            seen[(sid, name)] += 1
            where.setdefault((sid, name), ev)
            if len(seen) >= a.limit and sum(seen.values()) > 5000:
                break
    if not seen:
        sys.exit(f"'{want}' never appears as a spell in that log")
    print(f"{'id':>10}  {'name':34} {'events':>8}  first seen as")
    for (sid, name), c in seen.most_common(a.limit):
        print(f"{sid:>10}  {name[:34]:34} {c:>8}  {where[(sid, name)]}")
    if len(seen) > a.limit:
        print(f"... {len(seen) - a.limit} more distinct id/name pairs")


def cmd_events(path, a):
    kinds = tuple(x.strip() for x in a.kinds.split(",") if x.strip())
    k, stream = _stream(path, a, keep=set(kinds) if kinds else None)
    shown = total = 0
    for t, ev, f in stream:
        if len(f) < 7:
            continue
        src, dst = f[2], f[6]
        _sid, name = L.spell_fields(ev, f)
        if a.spell and a.spell.lower() not in (name or "").lower():
            continue
        if a.actor and not src.lower().startswith(a.actor.lower()):
            continue
        if a.target and not dst.lower().startswith(a.target.lower()):
            continue
        total += 1
        if shown >= a.limit:
            continue
        shown += 1
        extra = ""
        if ev in L.DAMAGE_EVENTS:
            amt, over, absorbed = L.damage_fields(f)
            extra = f"  {amt:,}" + (f" (overkill {over:,})" if over > 0 else "")
            if absorbed:
                extra += f" [absorbed {absorbed:,}]"
        elif ev in L.HEAL_EVENTS:
            eff, ovh, absorbed = L.heal_fields(f)
            extra = f"  {eff:,}" + (f" (overheal {ovh:,})" if ovh else "")
        elif ev in _EXTRA_SPELL and len(f) > 13:
            # f[9]/f[10] is the spell the PLAYER pressed; the thing it acted on
            # is the extraSpell at f[12]/f[13]. Printing only the first reads as
            # twenty identical lines that say "Cleanse" and answer nothing.
            extra = f"  {_EXTRA_SPELL[ev]}: {f[13]}"
        print(f"{_clock(k, t):>7}  {ev:26} {src[:16]:16} -> {dst[:16]:16} "
              f"{(name or '')[:26]:26}{extra}")
    print(f"\n{total} matching event(s)"
          + (f"; showed {shown} (raise --limit)" if total > shown else ""))


def cmd_taken(path, a):
    """Damage on one player, grouped by what did it.

    The grader's `mechanics` axis answers whether damage was avoidable by
    comparing against peers. This is the flat question underneath it -- what
    actually hit me -- which is what you want when reading a death back."""
    k, stream = _stream(path, a, keep=set(L.DAMAGE_EVENTS))
    by = collections.Counter()
    hits = collections.Counter()
    tot = 0
    for _t, ev, f in stream:
        if len(f) < 7 or not f[6].lower().startswith(a.who.lower()):
            continue
        amt, _over, _ab = L.damage_fields(f)
        if not amt:
            continue
        _sid, name = L.spell_fields(ev, f)
        src = f[2] or "?"
        by[(name, src)] += amt
        hits[(name, src)] += 1
        tot += amt
    if not tot:
        sys.exit(f"no damage on anyone matching '{a.who}' in that scope")
    print(f"\n{tot:,} total damage taken\n")
    print(f"{'spell':32} {'source':22} {'damage':>14} {'hits':>6} {'avg':>11}  share")
    for (name, src), amt in by.most_common(a.limit):
        n = hits[(name, src)]
        print(f"{(name or '?')[:32]:32} {src[:22]:22} {amt:>14,} {n:>6} "
              f"{amt // n:>11,}  {100 * amt / tot:4.1f}%")
    if len(by) > a.limit:
        rest = sum(v for _kk, v in by.most_common()[a.limit:])
        print(f"{'... ' + str(len(by) - a.limit) + ' more':32} {'':22} {rest:>14,}"
              f" {'':6} {'':11}  {100 * rest / tot:4.1f}%")


def cmd_casts(path, a):
    k, stream = _stream(path, a, keep={"SPELL_CAST_SUCCESS"})
    by = collections.Counter()
    first, last = {}, {}
    for t, ev, f in stream:
        if len(f) < 3 or not f[2].lower().startswith(a.who.lower()):
            continue
        _sid, name = L.spell_fields(ev, f)
        if a.spell and a.spell.lower() not in (name or "").lower():
            continue
        by[name] += 1
        first.setdefault(name, t)
        last[name] = t
    if not by:
        sys.exit(f"no casts by anyone matching '{a.who}' in that scope")
    span = (k.seconds / 60.0) if k and k.seconds else None
    print(f"\n{sum(by.values())} cast(s)"
          + (f" over {span:.1f}m" if span else "") + "\n")
    print(f"{'spell':34} {'casts':>6} {'per min':>8} {'first':>7} {'last':>7}")
    for name, c in by.most_common(a.limit):
        pm = f"{c / span:.2f}" if span else "--"
        print(f"{(name or '?')[:34]:34} {c:>6} {pm:>8} "
              f"{_clock(k, first[name]):>7} {_clock(k, last[name]):>7}")
    if len(by) > a.limit:
        print(f"... {len(by) - a.limit} more distinct spells (raise --limit)")


def cmd_uptime(path, a):
    """Uptime of one aura on one unit.

    Written from APPLIED/REFRESH/REMOVED rather than from the client, because
    the aura APIs are exactly what an addon cannot read reliably in Midnight --
    the log is the only place this is answerable at all.

    An aura still up when the key ends is closed at the end of the window
    rather than dropped, which is the difference between a 90-second cooldown
    reading 0% and reading what it actually was."""
    k, stream = _stream(path, a, keep={"SPELL_AURA_APPLIED", "SPELL_AURA_REFRESH",
                                       "SPELL_AURA_REMOVED", "SPELL_AURA_APPLIED_DOSE"})
    up = collections.defaultdict(float)
    open_at = {}
    applied = collections.Counter()
    t0 = t1 = None
    for t, ev, f in stream:
        if t0 is None:
            t0 = t
        t1 = t
        if len(f) < 11:
            continue
        _sid, name = L.spell_fields(ev, f)
        if a.what.lower() not in (name or "").lower():
            continue
        unit = f[6]
        if a.unit and not unit.lower().startswith(a.unit.lower()):
            continue
        key = (name, unit)
        if ev == "SPELL_AURA_REMOVED":
            if key in open_at:
                up[key] += t - open_at.pop(key)
        elif ev == "SPELL_AURA_APPLIED":
            open_at.setdefault(key, t)
            applied[key] += 1
    for key, t in open_at.items():
        up[key] += (t1 or t) - t
    if not up:
        # An instant with no buff behind it (Cleanse, Judgment) legitimately has
        # no uptime and is not a typo, so say which question to ask instead
        # rather than only reporting the absence.
        sys.exit(f"'{a.what}' never applied as an aura in that scope"
                 + (f" to {a.unit}" if a.unit else "")
                 + ".\nIf it is an instant rather than a buff, ask "
                   "`casts <player> --spell ...` or `events SPELL_CAST_SUCCESS "
                   "--spell ...` instead.")
    window = (k.seconds if k and k.seconds else (t1 - t0) if t0 else 0) or 1
    print(f"\nwindow {window / 60:.1f}m\n")
    print(f"{'aura':30} {'unit':18} {'uptime':>9} {'of window':>10} {'applied':>8}")
    for key, secs in sorted(up.items(), key=lambda x: -x[1])[:a.limit]:
        name, unit = key
        print(f"{name[:30]:30} {unit[:18]:18} {secs:>8.1f}s "
              f"{100 * secs / window:>9.1f}% {applied[key]:>8}")
    if len(up) > a.limit:
        print(f"... {len(up) - a.limit} more (raise --limit)")


def cmd_gear(path, a):
    """COMBATANT_INFO for one player: spec, item level, equipped ilvls.

    specID is f[25]. f[24] is armor and parses to a plausible-looking integer,
    which is how a wrong offset here produces confident nonsense rather than an
    error -- see the note at the top of arbiter/logfile.py."""
    k, stream = _stream(path, a, keep={"COMBATANT_INFO"})
    rows = []
    for _t, _ev, f in stream:
        if len(f) < 29:
            continue
        rows.append(f)
    if not rows:
        sys.exit("no COMBATANT_INFO in that scope (key started before logging?)")
    # COMBATANT_INFO carries the GUID, not the name, so names come from a second
    # sweep of cheap events in the same window.
    names = {}
    _k2, s, e, b = pick(path, a)
    for _t, ev, f in L.events(path, s, e, b, keep={"SPELL_CAST_SUCCESS",
                                                   "SPELL_AURA_APPLIED"}):
        if len(f) > 2 and f[1].startswith("Player-"):
            names[f[1]] = f[2]
        if len(f) > 6 and f[5].startswith("Player-"):
            names[f[5]] = f[6]
    print()
    shown = set()
    # A key emits COMBATANT_INFO once per player, but a log that spans a wipe
    # and re-entry emits the whole block again -- printing every row listed the
    # same five players three times. First row per GUID wins.
    for f in rows:
        guid = f[1]
        if guid in shown:
            continue
        shown.add(guid)
        nm = names.get(guid, guid).split("-")[0]
        if a.who and not nm.lower().startswith(a.who.lower()):
            continue
        # SPECS is keyed by int; f[25] is a string straight out of the log, so
        # `.get(f[25])` misses every time and falls back to the raw id.
        try:
            spec = knowledge.SPECS.get(int(f[25]), f"spec {f[25]}")
        except ValueError:
            spec = f"spec {f[25]}"
        ilvls = L.item_levels(f[28])
        avg = sum(ilvls) / len(ilvls) if ilvls else 0
        tal = L.talent_entries(f[26]) if len(f) > 26 else []
        print(f"{nm[:16]:16} {spec:20} ilvl {avg:6.1f}  ({len(ilvls)} slots)  "
              f"{len(tal)} talents")
        if a.verbose:
            print(f"   equipped ilvls: {', '.join(str(x) for x in ilvls)}")
            print(f"   guid: {guid}")


# ---------------------------------------------------------------- cli

def main():
    # Scope flags are accepted on BOTH sides of the subcommand -- `--last taken
    # X` and `taken X --last` are the same thing. SUPPRESS is what makes that
    # work: without it the subparser's own default overwrites a value already
    # parsed at the top level, so `--last events ...` silently lost `--last`
    # and scanned the entire 365 MB file.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--n", type=int, default=argparse.SUPPRESS,
                        help="run number (see `runs`)")
    common.add_argument("--last", action="store_true", default=argparse.SUPPRESS,
                        help="scope to the newest key")
    common.add_argument("--limit", type=int, default=argparse.SUPPRESS)

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 parents=[common])
    ap.add_argument("log")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("runs", parents=[common])
    p = sub.add_parser("spell", parents=[common])
    p.add_argument("what", help="spell name or id")
    p = sub.add_parser("events", parents=[common])
    p.add_argument("kinds", help="event name(s), comma separated")
    p.add_argument("--spell"); p.add_argument("--actor"); p.add_argument("--target")
    p = sub.add_parser("taken", parents=[common]); p.add_argument("who")
    p = sub.add_parser("casts", parents=[common])
    p.add_argument("who"); p.add_argument("--spell")
    p = sub.add_parser("uptime", parents=[common])
    p.add_argument("what"); p.add_argument("--unit")
    p = sub.add_parser("gear", parents=[common]); p.add_argument("who", nargs="?")
    p.add_argument("--verbose", action="store_true")

    a = ap.parse_args()
    # Applied here rather than with set_defaults: `parents=` shares the ACTION
    # objects, and set_defaults mutates them, so setting a default on the main
    # parser also un-suppressed it on every subparser -- which then copied its
    # own default back over the value already parsed. `--last taken X` scanned
    # the whole 365 MB file while reporting that it had.
    for name, val in (("n", None), ("last", False), ("limit", 20)):
        if not hasattr(a, name):
            setattr(a, name, val)
    {"runs": cmd_runs, "spell": cmd_spell, "events": cmd_events,
     "taken": cmd_taken, "casts": cmd_casts, "uptime": cmd_uptime,
     "gear": cmd_gear}[a.cmd](a.log, a)


if __name__ == "__main__":
    main()
