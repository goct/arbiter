#!/usr/bin/env python3
"""Per-encounter healer report from a WoW combat log.

    C:/Python312/python.exe raid-healing.py <WoWCombatLog-*.txt>            # list encounters
    C:/Python312/python.exe raid-healing.py <log> --last                    # analyse the newest
    C:/Python312/python.exe raid-healing.py <log> --n 3                     # analyse encounter 3
    C:/Python312/python.exe raid-healing.py <log> --last --me Hyporock      # whose detail to print

WHY THIS EXISTS -----------------------------------------------------------

Every number in `session-handoff-2026-08-19-raid.md` came out of this. The
field offsets below cost real debugging time to get right and are wrong in the
obvious guesses, so they are written down once here rather than re-derived:

  SPELL_HEAL tail          amount, baseAmount, overhealing, absorbed, critical
                           -> effective = f[-5] - f[-3].  NOT (amount, overheal,
                           absorbed, critical); the baseAmount field is easy to
                           miss and shifts everything by one.
  COMBATANT_INFO           specID is f[25] (f[24] is armor, which parses fine
                           and silently yields nonsense spec labels).
  SPELL_ABSORBED           absorb CASTER is f[-9], amount is f[-3]. f[-2] is
                           totalAbsorb, i.e. the shield's size, not what it ate.
  SPELL_ENERGIZE tail      amount, overEnergize, powerType, maxPower
                           -> Holy Power is powerType 9 at f[-2].
  UNIT_DIED                dest GUID is f[5], name f[6].
  damage taken             dest GUID is f[5]; amount is f[-10].

Also: the event name is separated from the timestamp by TWO SPACES, not a
comma, so `grep ",SPELL_HEAL,"` silently matches nothing.

Beacon transfer for all Holy Paladin beacons logs under the name
"Beacon of Light" (spell 53652) whichever beacon produced it -- that is the
row to read for a Faith-vs-Virtue comparison, not the individual auras.
"""

import argparse
import collections
import io
import sys

SPECS = {65: "Holy Paladin", 105: "Resto Druid", 250: "Blood DK", 256: "Disc Priest",
         257: "Holy Priest", 264: "Resto Shaman", 268: "Brewmaster", 270: "Mistweaver",
         1468: "Pres Evoker"}
HEALER_SPECS = {65, 105, 256, 257, 264, 270, 1468}
HEALS = ("SPELL_HEAL", "SPELL_PERIODIC_HEAL")
DAMAGE = ("SPELL_DAMAGE", "SPELL_PERIODIC_DAMAGE", "SWING_DAMAGE", "SWING_DAMAGE_LANDED")


def split(s):
    """Split a log line on commas, respecting quotes and [ ( ) ] nesting."""
    out, cur, quoted, depth = [], [], False, 0
    for ch in s:
        if ch == '"':
            quoted = not quoted
            continue
        if not quoted:
            if ch in "[(":
                depth += 1
            elif ch in "])":
                depth -= 1
            elif ch == "," and depth == 0:
                out.append("".join(cur))
                cur = []
                continue
        cur.append(ch)
    out.append("".join(cur))
    return out


def body_of(line):
    parts = line.split("  ", 1)
    return parts[1].rstrip("\n") if len(parts) > 1 else None


def encounters(path):
    """[(index, name, start_line, end_line, seconds, killed)] -- 1-based lines."""
    found, open_at = [], None
    for n, line in enumerate(io.open(path, encoding="utf-8", errors="replace"), 1):
        b = body_of(line)
        if not b:
            continue
        if b.startswith("ENCOUNTER_START"):
            f = split(b)
            open_at = (n, f[2])
        elif b.startswith("ENCOUNTER_END") and open_at:
            f = split(b)
            secs = int(f[-1]) / 1000 if f[-1].isdigit() else 0.0
            found.append((len(found) + 1, open_at[1], open_at[0], n, secs, f[-2] == "1"))
            open_at = None
    return found


def report(path, start, end, dur, me):
    heal = collections.defaultdict(lambda: [0, 0])   # name -> [effective, overheal]
    selfheal, absorb, taken = collections.Counter(), collections.Counter(), collections.Counter()
    spec, guidname = {}, {}
    deaths, casts = [], collections.Counter()
    myspells = collections.defaultdict(lambda: [0, 0])
    transfer = collections.Counter()
    hopo_gen = hopo_waste = 0

    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh, 1):
            if n < start:
                continue
            if n > end:
                break
            b = body_of(line)
            if not b:
                continue
            ev = b.split(",", 1)[0]

            if ev in HEALS:
                f = split(b)
                src, dst, spell = f[2], f[6], f[10]
                eff = int(f[-5]) - int(f[-3])
                heal[src][0] += eff
                heal[src][1] += int(f[-3])
                if src == dst:
                    selfheal[src] += eff
                if spell == "Beacon of Light":
                    transfer[src] += eff
                if src.startswith(me):
                    myspells[spell][0] += eff
                    myspells[spell][1] += int(f[-3])
            elif ev == "SPELL_ABSORBED":
                f = split(b)
                if len(f) > 10:
                    try:
                        absorb[f[-9]] += int(f[-3])
                    except ValueError:
                        pass
            elif ev == "SPELL_CAST_SUCCESS":
                f = split(b)
                if f[2].startswith(me):
                    casts[f[10]] += 1
            elif ev in ("SPELL_ENERGIZE", "SPELL_PERIODIC_ENERGIZE"):
                f = split(b)
                if f[2].startswith(me) and f[-2] == "9":
                    try:
                        hopo_gen += int(float(f[-4]))
                        hopo_waste += int(float(f[-3]))
                    except ValueError:
                        pass
            elif ev == "COMBATANT_INFO":
                f = split(b)
                spec[f[1]] = int(f[25])
            elif ev == "UNIT_DIED":
                f = split(b)
                if f[5].startswith("Player-"):
                    deaths.append(f[6].split("-")[0])
            elif ev in DAMAGE:
                f = split(b)
                if f[5].startswith("Player-") and not f[1].startswith("Player-"):
                    try:
                        taken[f[6].split("-")[0]] += int(f[-10])
                    except ValueError:
                        pass

            f = split(b)
            if len(f) > 2 and f[1].startswith("Player-") and ev != "COMBATANT_INFO":
                guidname[f[1]] = f[2]

    byname = {guidname.get(g, g): s for g, s in spec.items()}
    raid = sum(taken.values())

    print(f"duration {dur:.0f}s   raid damage taken {raid:,} ({raid / dur:,.0f}/s)")
    print(f"deaths {len(deaths)}: {collections.Counter(deaths).most_common(8)}\n")

    hdr = f"{'healer':12} {'spec':13} {'effective':>12} {'HPS':>8} {'over%':>6} {'self%':>6} {'absorb':>11} {'transfer/s':>10}"
    print(hdr)
    for name, (eff, over) in sorted(heal.items(), key=lambda kv: -kv[1][0]):
        s = byname.get(name)
        if s not in HEALER_SPECS:
            continue
        print(f"{name.split('-')[0]:12} {SPECS.get(s, str(s)):13} {eff:12,} {eff / dur:8,.0f} "
              f"{100 * over / (eff + over):5.1f}% {100 * selfheal[name] / eff if eff else 0:5.1f}% "
              f"{absorb[name]:11,} {transfer[name] / dur:10,.0f}")

    print(f"\n{me} Holy Power: generated {hopo_gen}, wasted {hopo_waste} "
          f"({100 * hopo_waste / (hopo_gen + hopo_waste) if hopo_gen + hopo_waste else 0:.1f}%)")
    print(f"{me} casts: {dict(casts.most_common(18))}")
    print(f"\n{me} top heals:")
    for sp, (eff, over) in sorted(myspells.items(), key=lambda kv: -kv[1][0])[:10]:
        pct = 100 * over / (eff + over) if eff + over else 0
        print(f"  {sp:26} {eff:12,}  {pct:5.1f}% over")
    print("\ndamage taken, top 8:")
    for i, (n, v) in enumerate(taken.most_common(8), 1):
        print(f" {i:2}. {n:14} {v:,}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--n", type=int, help="encounter number from the listing")
    ap.add_argument("--last", action="store_true")
    ap.add_argument("--me", default="Hyporock", help="name prefix for the detail sections")
    a = ap.parse_args()

    enc = encounters(a.log)
    if not enc:
        sys.exit("no ENCOUNTER_START/END pairs in that log")

    if not a.n and not a.last:
        for i, name, s, e, dur, killed in enc:
            print(f"{i:3}. {name:34} {'KILL' if killed else 'wipe':4} {dur / 60:5.1f}m  lines {s}-{e}")
        return

    pick = enc[-1] if a.last else next((x for x in enc if x[0] == a.n), None)
    if not pick:
        sys.exit(f"no encounter {a.n}")
    i, name, s, e, dur, killed = pick
    print(f"== {name} -- {'KILL' if killed else 'WIPE'} ==\n")
    report(a.log, s, e, dur, a.me)


if __name__ == "__main__":
    main()
