#!/usr/bin/env python3
"""Ask one question of a graded night, without re-reading the whole report.

    C:/Python312/python.exe ask.py night.json                    # one line per key
    C:/Python312/python.exe ask.py night.json player Hyporock    # one player, all keys
    C:/Python312/python.exe ask.py night.json axis interrupts    # one axis, everyone
    C:/Python312/python.exe ask.py night.json deaths --who Hyporock
    C:/Python312/python.exe ask.py night.json habits Hyporock
    C:/Python312/python.exe ask.py night.json pulls
    C:/Python312/python.exe ask.py night.json bands --role heal  # recalibration table

WHY THIS EXISTS -----------------------------------------------------------

`grade.py --all` prints about 50 KB for a six-key night. That is the
right size to read ONCE. The problem is the follow-up: "how did the rogue's
interrupts move across the night", "which pull actually cost us the key",
"what were the healer's raws so I can re-cut the band" -- each of those used to
mean re-printing the whole report, or re-parsing the log.

Measured on this repo's own sessions: the grading script's output is a couple
of percent of a session's token cost, but every payload sits in context for
every turn that follows it. A 50 KB report read at turn 14 of 470 is paid for
456 more times. So the fix is not a smaller report -- it is being able to ask
for the 30 lines that answer the question.

Everything here reads the `--json` export and nothing else. No log, no parsing,
no registry. It is deliberately dumb: the grading already happened.

    C:/Python312/python.exe grade.py <log> --all --json night.json --quiet

Every command caps its own output. A command that could print unbounded rows
takes `--limit` and says how many it held back, because the whole point is to
not paste a night into a conversation by accident.
"""

import argparse
import json
import statistics
import sys


def load(path):
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    runs = d.get("runs", [])
    if not runs:
        sys.exit(f"{path} holds no runs")
    return runs


def _key(r):
    return f"{r['dungeon']} +{r['level']}"


def _state(r):
    if not r.get("finished"):
        return "abandoned"
    return "timed" if r.get("timed") else "depleted"


def _match(players, want):
    """Players whose name starts with `want`, case-insensitively."""
    w = want.lower()
    return [p for p in players if p["name"].lower().startswith(w)]


def _mmss(s):
    if s is None:
        return "--:--"
    s = int(s)
    return f"{s // 60}:{s % 60:02d}"


def cmd_summary(runs, a):
    print(f"{'#':>2}  {'dungeon':28} {'lvl':>4} {'state':9} {'clear':>6} "
          f"{'die':>4} {'kick':>9} {'disp':>8}  party grades")
    for i, r in enumerate(runs, 1):
        ic = r.get("interrupts") or {}
        dc = r.get("dispels") or {}
        kick = (f"{ic.get('stopped', 0)}/{ic.get('total', 0)}"
                if ic.get("total") else "--")
        disp = (f"{dc.get('cleansed', 0)}/{dc.get('opportunities', 0)}"
                if dc.get("opportunities") else "--")
        grades = " ".join(f"{p['name'][:4]}:{p['grade']}" for p in r["players"])
        print(f"{i:>2}. {r['dungeon'][:28]:28} {'+' + str(r['level']):>4} "
              f"{_state(r):9} {_mmss(r.get('clear_seconds')):>6} "
              f"{r.get('deaths', 0):>4} {kick:>9} {disp:>8}  {grades}")
    print(f"\n{len(runs)} key(s).  Ask for more with: player <name> | axis <name> "
          f"| deaths | habits <name> | pulls | bands")


def cmd_player(runs, a):
    seen = 0
    axes_seen = {}
    for r in runs:
        for p in _match(r["players"], a.who):
            seen += 1
            print(f"{_key(r):34} {p['spec']:18} {p['grade']:>3} "
                  f"({p['score']:.0f}/100)  ilvl {p['ilvl']:.0f}")
            for name, ax in p["axes"].items():
                sc = ax["score"]
                axes_seen.setdefault(name, []).append(sc)
                if sc is None:
                    print(f"     {name:13} {'--':>4}  {ax.get('note') or 'n/a'}")
                else:
                    print(f"     {name:13} {sc:>4.0f}  [w {ax['weight'] * 100:.0f}%]"
                          f"  {ax.get('note') or ''}")
            print()
    if not seen:
        sys.exit(f"no player matching '{a.who}' in this file")
    if seen > 1:
        print("across the night (mean per axis):")
        for name, vals in axes_seen.items():
            got = [v for v in vals if v is not None]
            if got:
                print(f"     {name:13} {statistics.mean(got):>4.0f}"
                      f"   {' '.join(f'{v:.0f}' if v is not None else '--' for v in vals)}")


def cmd_axis(runs, a):
    rows = []
    for r in runs:
        for p in r["players"]:
            if a.role and p["role"] != a.role:
                continue
            ax = p["axes"].get(a.name)
            if not ax:
                continue
            rows.append((r, p, ax))
    if not rows:
        have = sorted({n for r in runs for p in r["players"] for n in p["axes"]})
        sys.exit(f"no axis '{a.name}'. Axes in this file: {', '.join(have)}")
    print(f"axis '{a.name}'"
          + (f", role {a.role}" if a.role else "")
          + f"   ({len(rows)} player-keys)\n")
    print(f"{'dungeon':24} {'player':13} {'spec':17} {'role':5} "
          f"{'score':>5} {'raw':>10}  note")
    ordered = sorted(rows, key=lambda x: (x[2]["score"] is None,
                                          x[2]["score"] or 0))
    for r, p, ax in ordered[:a.limit]:
        raw = ax.get("raw")
        raws = f"{raw:.4g}" if isinstance(raw, (int, float)) else str(raw)[:10]
        sc = "--" if ax["score"] is None else f"{ax['score']:.0f}"
        print(f"{_key(r)[:24]:24} {p['name'][:13]:13} {p['spec'][:17]:17} "
              f"{p['role']:5} {sc:>5} {raws:>10}  {(ax.get('note') or '')[:44]}")
    if len(ordered) > a.limit:
        print(f"... {len(ordered) - a.limit} more (raise --limit)")
    got = [ax["score"] for _r, _p, ax in rows if ax["score"] is not None]
    if got:
        print(f"\nscored {len(got)} of {len(rows)}; median {statistics.median(got):.0f}, "
              f"mean {statistics.mean(got):.0f}")


def cmd_deaths(runs, a):
    total, shown = 0, 0
    for r in runs:
        ds = r.get("deaths_detail") or []
        if a.who:
            w = a.who.lower()
            ds = [d for d in ds if d["name"].lower().startswith(w)]
        total += len(ds)
        if not ds:
            continue
        print(f"== {_key(r)} == {len(ds)} death(s)")
        for d in ds[:a.limit]:
            shown += 1
            ext = f"  externals: {', '.join(d['externals'])}" if d.get("externals") else ""
            cds = f"  own cds: {', '.join(d['defensives'])}" if d.get("defensives") else ""
            print(f"   {_mmss(d['at']):>6}  {d['name'][:13]:13} killed by "
                  f"{d.get('killer') or '?'} ({d.get('source') or '?'})")
            print(f"           burst {d.get('burst', 0):,} over {d.get('over', 0):.1f}s, "
                  f"healed {d.get('healing_received', 0):,}{cds}{ext}")
        if len(ds) > a.limit:
            print(f"   ... {len(ds) - a.limit} more (raise --limit)")
        print()
    if not total:
        print("no deaths matched.")


def cmd_habits(runs, a):
    for r in runs:
        for p in _match(r["players"], a.who):
            print(f"== {_key(r)} == {p['name']} ({p['spec']})")
            for o in p.get("overcapped") or []:
                pct = 100 * o["lost"] / o["generated"] if o["generated"] else 0
                print(f"   overcap  {o['resource']:14} lost {o['lost']:,} of "
                      f"{o['generated']:,} ({pct:.0f}%)")
            for m in (p.get("refused_presses") or [])[:a.limit]:
                print(f"   refused  {m['spell'][:24]:24} {m['refused']} refused / "
                      f"{m['casts']} casts")
            un = p.get("never_pressed") or []
            if un:
                print(f"   never    {', '.join(un[:12])}")
            oe = p.get("over_exposed_detail") or []
            for e in oe[:a.limit]:
                print(f"   exposed  {e['spell'][:24]:24} {e['mine']}x "
                      f"(peers {e['peer_median']}) = {e['attributed']:,}")
            print()


def cmd_pulls(runs, a):
    rows = []
    for r in runs:
        for p in r.get("pulls") or []:
            rows.append((p.get("cost") or 0, r, p))
    rows.sort(reverse=True, key=lambda x: x[0])
    print("time LOST per pull (a wipe wastes the pull in full; a hold costs its deaths)\n")
    print(f"{'dungeon':24} {'pull':>5} {'at':>6} {'cost':>6} {'fight':>6} "
          f"{'die':>4} {'kills':>6} {'kick':>6}  ")
    for cost, r, p in rows[:a.limit]:
        opp, stop = p.get("opportunities") or 0, p.get("stopped") or 0
        kick = f"{100 * stop // opp}%" if opp else "--"
        print(f"{_key(r)[:24]:24} {p.get('n', '?'):>5} {_mmss(p.get('at')):>6} "
              f"{_mmss(cost):>6} {_mmss(p.get('seconds')):>6} "
              f"{p.get('deaths', 0):>4} {p.get('kills', 0):>6} {kick:>6}"
              f"  {'WIPE' if p.get('wipe') else ''}")
    if len(rows) > a.limit:
        print(f"... {len(rows) - a.limit} more pulls (raise --limit)")


def cmd_bands(runs, a):
    """Median raw per axis per role -- the table `score.py`'s bands are cut from.

    The documented recalibration rule is that the observed median should land
    near 75 after banding, so both columns are printed: re-cut the band until
    `median score` reads about 75 and the raw column tells you where to put it.
    """
    buckets = {}
    for r in runs:
        for p in r["players"]:
            if a.role and p["role"] != a.role:
                continue
            for name, ax in p["axes"].items():
                b = buckets.setdefault((p["role"], name), {"raw": [], "sc": []})
                if isinstance(ax.get("raw"), (int, float)):
                    b["raw"].append(ax["raw"])
                if ax.get("score") is not None:
                    b["sc"].append(ax["score"])
    print(f"{'role':6} {'axis':14} {'n':>3} {'median raw':>12} {'median score':>13} "
          f"{'p25':>6} {'p75':>6}")
    for (role, name), b in sorted(buckets.items()):
        if not b["sc"]:
            continue
        sc = sorted(b["sc"])
        mr = f"{statistics.median(b['raw']):.4g}" if b["raw"] else "--"
        q = lambda v, f: v[min(len(v) - 1, int(len(v) * f))]
        print(f"{role:6} {name:14} {len(sc):>3} {mr:>12} "
              f"{statistics.median(sc):>13.0f} {q(sc, .25):>6.0f} {q(sc, .75):>6.0f}")
    print("\nbands are cut so the median lands near 75; see arbiter/score.py")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("json", help="a file written by grade.py --json")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("summary")
    p = sub.add_parser("player"); p.add_argument("who")
    p = sub.add_parser("axis"); p.add_argument("name")
    p.add_argument("--role", choices=("tank", "heal", "dps"))
    p.add_argument("--limit", type=int, default=25)
    p = sub.add_parser("deaths"); p.add_argument("--who")
    p.add_argument("--limit", type=int, default=12)
    p = sub.add_parser("habits"); p.add_argument("who")
    p.add_argument("--limit", type=int, default=6)
    p = sub.add_parser("pulls"); p.add_argument("--limit", type=int, default=12)
    p = sub.add_parser("bands"); p.add_argument("--role",
                                                choices=("tank", "heal", "dps"))

    a = ap.parse_args()
    runs = load(a.json)
    {None: cmd_summary, "summary": cmd_summary, "player": cmd_player,
     "axis": cmd_axis, "deaths": cmd_deaths, "habits": cmd_habits,
     "pulls": cmd_pulls, "bands": cmd_bands}[a.cmd](runs, a)


if __name__ == "__main__":
    main()
