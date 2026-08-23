"""Printing. Every grade is printed next to the numbers that produced it.

The point of the sub-score lines is that a bad grade can be argued with. If a
player disagrees with a C, the axis that cost them the C is on the same screen,
with the raw figure behind it, and either the number is wrong or the play was.
"""

import statistics
import sys

from . import dungeons as DUN
from . import knowledge as K
from . import logfile as LF

AXIS_ORDER = ["survival", "mitigation", "throughput", "response", "damage",
              "mechanics", "interrupts", "dispel", "activity", "utility"]


def _utf8():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def clock(s):
    return f"{int(s) // 60}:{int(s) % 60:02d}"


def result_line(run):
    """What the key actually did, and -- where the log cannot say -- that it
    cannot say. The upgrade is NOT derivable from a combat log: there is no par
    time in one. It is printed only when `data/dungeons.json` has a real par
    for the dungeon, and the par's provenance is printed next to it so nobody
    has to trust it blindly."""
    k = run.key
    state = ("TIMED" if run.timed else
             "DEPLETED" if (k and k.finished) else "ABANDONED")
    clear = k.timer_seconds if k and k.timer_seconds else run.dur
    table = DUN.load()
    par = DUN.par_for(run.name, table)
    up = DUN.upgrade(clear, par, run.timed)
    if up:
        under = 100 * (par - clear) / par
        badge = f"+{up}  ({under:.1f}% under a {clock(par)} par)"
    elif run.timed:
        badge = f"upgrade unknown -- {DUN.describe(run.name, table)}"
    else:
        badge = "no upgrade"
    print(f"== {run.name}  +{run.level}  {state}  {clock(clear)}   {badge} ==")
    if k and k.affixes:
        print(f"   affixes: {', '.join(LF.affix_names(k.affixes))}")
    if k and k.score is not None:
        line = f"   run score {k.score:.1f}"
        if k.rating is not None:
            line += f"; M+ rating after this key {k.rating:.0f}"
        print(line + "   (CHALLENGE_MODE_END fields 6 and 7 -- neither is a par time)")


def header(run, ev):
    result_line(run)
    p = ev["pulls"]
    print(f"   {clock(ev['combat'])} of combat ({100*ev['combat']/run.dur:.0f}%) in "
          f"{p.get('pulls', 0)} pulls, {clock(p.get('downtime', 0))} walking, "
          f"{run.trash_kills} enemies killed")
    unc = sum(run.unconscious.values())
    print(f"   party took {sum(run.dmg_taken.values()):,} damage; "
          f"{len(run.death_events)} deaths "
          f"({p.get('deaths_in_combat', 0)} in combat, {p.get('deaths_out', 0)} outside)"
          + (f"; {unc} more went down without dying and are not counted" if unc else ""))
    check = ev.get("death_check")
    if check and check.get("expected") is not None:
        got, want = check["counted"], check["expected"]
        if abs(got - want) > 0.35:
            print(f"   !! the game's own timer implies {want:.1f} deaths, this "
                  f"parser found {got}. One of them is wrong -- see "
                  f"logfile.counted_deaths")
        else:
            print(f"   deaths agree with the game's timer ({want:.2f} implied)")
    b = ev["board"]
    if b["total"]:
        print(f"   interrupts: {b['stopped']}/{b['total']} stoppable casts stopped "
              f"({100*b['stopped']/b['total']:.0f}%)")
    else:
        print("   interrupts: no cast in this key is known-kickable yet "
              "(registry still learning)")
    d = ev["dispel"]
    if d["capable"] and d["opportunities"]:
        print(f"   dispels: {d['cleansed']}/{d['opportunities']} damaging dispellable "
              f"debuffs removed"
              + (f"; {d['cost']:,} damage came from the ones left up"
                 if d.get("cost") else ""))


def ledger_note(run, ev):
    """Where the key's minutes went. The clear time is the headline; this is the
    part that says which of those minutes were recoverable."""
    L = ev.get("ledger")
    if not L or not L.get("total"):
        return
    parts = [("fighting", L["combat"]), ("routing", L["routing"]),
             ("wipe recovery", L["wipe_recovery"]),
             ("death penalty", L["death_penalty"]), ("pull timer", L["lead_in"])]
    shown = ", ".join(f"{clock(v)} {n}" for n, v in parts if v >= 1.0)
    print(f"\n   {clock(L['total'])} on the timer = {shown}")


def pull_note(run, ev, top=4):
    """The pulls that actually cost the run.

    Totals average the pull that wiped the group twice into the eleven that went
    fine. This is the view that survives that."""
    pulls = ev.get("by_pull") or []
    if len(pulls) < 3:
        return
    worst = [p for p in sorted(pulls, key=lambda p: -p["cost"])[:top] if p["cost"]]
    if not worst:
        return
    lost = sum(p["cost"] for p in pulls)
    print(f"\n   time lost to pulls: {clock(lost)} of {clock(ev['ledger']['total'])}"
          f"   (a wiped pull is wasted in full; one that held costs only its deaths)")
    for p in worst:
        conv = (f"{100 * p['stopped'] / p['opportunities']:.0f}% kicked"
                if p["opportunities"] else "nothing kickable")
        flag = "  WIPE" if p["wipe"] else ""
        print(f"     pull {p['n']:<3} at {clock(p['at']):>6}  cost {clock(p['cost']):>5}  "
              f"{clock(p['seconds']):>5} fighting  {p['kills']:>3} enemies  "
              f"{p['deaths']} death(s)  {p['taken']:>12,} taken  {conv}{flag}")


def boss_note(run, ev):
    """Where the run actually went wrong: bosses versus the trash between them."""
    b = ev.get("bosses")
    if not b or not b["bosses"]:
        return
    total = b["boss_damage"] + b["trash_damage"] or 1
    print(f"\n   bosses ({len(b['bosses'])}): "
          + ", ".join(f"{n} {clock(sec)}{'' if k else ' WIPE'}"
                      for n, sec, k in b["bosses"]))
    print(f"   deaths on bosses {b['boss_deaths']}, on trash {b['trash_deaths']}"
          f"   |   damage taken on bosses {100*b['boss_damage']/total:.0f}%, "
          f"on trash {100*b['trash_damage']/total:.0f}%")


def table(run, ev):
    hdr = (f"{'player':12} {'spec':15} {'role':5} {'ilvl':>5} {'DPS':>10} {'HPS':>9} "
           f"{'taken/s':>9} {'die':>3} {'kick':>4} {'cc':>3} {'disp':>4} "
           f"{'exposed':>10} {'act%':>5} {'cpm':>5}")
    print("\n" + hdr)
    print("-" * len(hdr))
    rows = sorted(run.players, key=lambda g: (run.players[g]["role"] != "tank",
                                              run.players[g]["role"] != "heal",
                                              -ev["rows"][g]["dps"]))
    for g in rows:
        p, r = run.players[g], ev["rows"][g]
        print(f"{p['name'][:12]:12} {K.SPECS.get(p['spec'], p['spec']):15} {p['role']:5} "
              f"{p['ilvl']:5.0f} {r['dps']:10,.0f} {r['hps']:9,.0f} {r['taken']:9,.0f} "
              f"{run.deaths[g]:3} {r['kicks']:4} {r['cc']:3} {r['dispels']:4} "
              f"{r['avoid']['amount']:10,.0f} {r['act']:5.0f} {r['cpm']:5.1f}")
    return rows


def grades(run, ev, rows):
    print("\n" + "=" * 78)
    print("GRADES   (0-100 per axis; an axis that did not apply is dropped, not zeroed)")
    print("=" * 78)
    for g in rows:
        p, r = run.players[g], ev["rows"][g]
        print(f"\n{p['name'][:12]:12} {K.SPECS.get(p['spec'], p['spec']):15} "
              f"{r['letter']:>3}  ({r['total']:.0f}/100)")
        for a in sorted(r["axes"], key=lambda a: AXIS_ORDER.index(a.name)
                        if a.name in AXIS_ORDER else 99):
            if a.score is None:
                print(f"     {a.name:11}   --   n/a, {a.note}")
            elif a.weight <= 0:
                print(f"     {a.name:11} {a.score:4.0f}   {a.note}"
                      f"   (reported, not graded for this role)")
            else:
                print(f"     {a.name:11} {a.score:4.0f}   {a.note}  "
                      f"[w {100*a.weight:.0f}%]")
        # Reported, never graded. Each of these is a habit rather than an
        # outcome, and the model deliberately scores outcomes -- but a habit is
        # what somebody can actually change on the next pull.
        if r.get("waste"):
            print("     resource overcapped: "
                  + ", ".join(f"{nm} {lost:,} of {got:,} generated ({100*frac:.0f}%)"
                              for nm, lost, got, frac in r["waste"][:2]))
        if r.get("mash"):
            print("     presses refused, still on cooldown (costs nothing; "
                  "shows where the keyboard time goes): "
                  + ", ".join(f"{sp} {ratio:.1f}x per cast"
                              for sp, _n, _u, ratio in r["mash"][:3]))
        if r.get("mana_low") is not None and r["mana_low"] < 0.25:
            print(f"     mana floor: {100*r['mana_low']:.0f}%")
        if r.get("rezzes"):
            print(f"     battle resurrections cast: {r['rezzes']}")
        if r.get("unused"):
            print(f"     never pressed: {', '.join(r['unused'][:6])}")
        if r["avoid"]["detail"]:
            worst = ", ".join(f"{sp} x{mine} (peers {med:.0f})"
                              for sp, mine, med, _amt in r["avoid"]["detail"][:3])
            print(f"     over-exposed: {worst}")


def deaths(run, ev):
    if not ev["forensics"]:
        return
    print("\n" + "=" * 78)
    print("DEATHS")
    print("=" * 78)
    for d in ev["forensics"]:
        k = d["killer"]
        kill = f"{k[2]} from {k[3]} for {k[1]:,}" if k else "unknown"
        over = f"{d['over']:.1f}s" if d["over"] >= 0.1 else "1-shot"
        print(f"\n  {d['name']} at {clock(d['at'])} -- killed by {kill}")
        print(f"     took {d['burst']:,} over {over}: "
              + ", ".join(f"{sp} {amt:,}" for sp, amt in d["by"]))
        # Healing received is the column that separates "the healer was short"
        # from "nothing arrived at all", and the two want opposite advice.
        if d["healed"]:
            print(f"     healing received in that window: {d['healed']:,}"
                  + (f" (from {', '.join(d['healers'])})" if d["healers"] else
                     " -- all of it their own"))
        else:
            print("     healing received in that window: NONE")
        print(f"     own defensives in that window: "
              f"{', '.join(d['own_cd']) if d['own_cd'] else 'NONE PRESSED'}")
        if d["externals"]:
            print("     externals received: "
                  + ", ".join(f"{s} from {who}" for s, who in d["externals"]))


def findings(run, ev):
    print("\n" + "=" * 78)
    print("GROUP FINDINGS")
    print("=" * 78)
    b = ev["board"]
    leaked = [x for x in b["leaked"] if x[1] > 0][:6]
    if leaked:
        print("\n  casts that got through (stoppable, not stopped):")
        for name, n in leaked:
            started = b["by_spell"][name][0]
            print(f"     {name:32} {n:4} of {started} leaked")
    d = ev["dispel"]
    if d["capable"] and d["worst"]:
        print("\n  dispellable debuffs left up (uptime is the case, not the count):")
        for name, n, secs, cost in d["worst"]:
            print(f"     {name:28} {n:3}x left up, {secs:6.0f}s total, "
                  f"{cost:13,} damage")
    worst = sorted(((ev["rows"][g]["avoid"]["amount"], run.players[g]["name"], g)
                    for g in run.players), reverse=True)
    if worst and worst[0][0] > 0:
        print("\n  over-exposure (damage taken that comparable teammates did not):")
        for amt, name, g in worst:
            if amt <= 0:
                continue
            det = ev["rows"][g]["avoid"]["detail"]
            top = det[0][0] if det else "-"
            print(f"     {name[:12]:12} {amt:12,.0f}   worst: {top}")


def dropped_note(run, ev, rows):
    """What the off-GCD detector threw away, so the cpm column is auditable."""
    any_drop = [g for g in rows if ev["rows"][g]["dropped"]]
    if not any_drop:
        return
    print("\n  not counted as button presses (procs, ticks, auto-driven):")
    for g in any_drop:
        d = sorted(ev["rows"][g]["dropped"])
        print(f"     {run.players[g]['name'][:12]:12} {', '.join(d)}")


TREND_AXES = ["survival", "mitigation", "throughput", "response", "damage",
              "mechanics", "interrupts", "dispel", "activity", "utility"]


def trend(name, entries):
    """One player across every key in the log.

    A single key is a noisy sample -- one bad pull moves a letter. Reading the
    same axis down a column across a night is what separates a habit from an
    accident, and it is the only view that answers "am I getting better".
    """
    _utf8()
    if not entries:
        print(f"no keys in this log contain a player called {name!r}")
        return
    spec = entries[0]["spec"]
    used = [a for a in TREND_AXES
            if any(a in e["axes"] and e["axes"][a] is not None for e in entries)]
    print(f"== {entries[0]['name']}  {spec}  --  {len(entries)} keys ==" + chr(10))
    hdr = f"{'key':24} {'lvl':>4} {'grade':>7}  " + "  ".join(f"{a[:4]:>4}" for a in used)
    print(hdr)
    print("-" * len(hdr))
    for e in entries:
        cells = []
        for a in used:
            v = e["axes"].get(a)
            cells.append("   -" if v is None else f"{v:4.0f}")
        print(f"{e['run'][:24]:24} {e['level']:>4} {e['letter']:>3} {e['total']:3.0f}  "
              + "  ".join(cells))
    print("-" * len(hdr))
    med = statistics.median([e["total"] for e in entries])
    cells = []
    for a in used:
        vals = [e["axes"][a] for e in entries
                if a in e["axes"] and e["axes"][a] is not None]
        cells.append(f"{statistics.median(vals):4.0f}" if vals else "   -")
    print(f"{'median':24} {'':>4} {'':>3} {med:3.0f}  " + "  ".join(cells))
    worst = min(used, key=lambda a: statistics.median(
        [e["axes"][a] for e in entries if e["axes"].get(a) is not None] or [100]))
    print(chr(10) + f"Weakest axis across the night: {worst}. "
          f"One key is noise; a column is a habit.")


def full(run, ev, raw=False):
    _utf8()
    header(run, ev)
    ledger_note(run, ev)
    boss_note(run, ev)
    pull_note(run, ev)
    rows = table(run, ev)
    if raw:
        dropped_note(run, ev, rows)
        return
    grades(run, ev, rows)
    deaths(run, ev)
    findings(run, ev)
    dropped_note(run, ev, rows)
