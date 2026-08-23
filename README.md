# Arbiter

Per-player analysis and letter grades for a World of Warcraft **Mythic+** run, read
straight out of the game's own combat log. No addon, no upload, no account — one text
file in, five graded players out.

```
python grade.py WoWCombatLog-082126_180639.txt --last
```

```
== Altar of Fangs  +10  TIMED  26:43   upgrade unknown -- par unknown, bounded to 31:02-33:25 ==
   24:14 of combat (91%) in 13 pulls, 1:58 walking, 150 enemies killed
   party took 236,466,115 damage; 4 deaths (4 in combat, 0 outside)
   deaths agree with the game's timer (4.00 implied)
   interrupts: 82/88 stoppable casts stopped (93%)

Healname     Holy Paladin     C+  (71/100)
     survival      50   2 death(s)  [w 17%]
     throughput    85   covered 76% of the 171,610,063 that needed an outside heal  [w 23%]
     response      63   2.1x baseline output during the worst windows  [w 17%]
     mechanics     90   2% of intake was damage comparable players did not take  [w 15%]
     interrupts    --   n/a, no interrupt on the bar
     dispel        30   8/33 cleansed  [w 11%]
     activity      86   91% engaged while alive in combat  [w 11%]
     utility      100   40 used of ~87 the kit offered  [w 6%]
     resource overcapped: Holy Power 114 of 838 generated (14%)
     presses refused, still on cooldown: Aura Mastery 4.6x per cast, Divine Toll 3.0x
     also talented, never pressed (unclassified): Auras of the Resolute
     over-exposed: Bloodletting x10 (peers 2)
```

Roughly 6 seconds per key; a 365 MB night of six keys grades in about 35 seconds.

## Why this exists

Most log tools answer *how much*. Damage, healing, interrupts per minute. Those numbers
mostly describe the key level and the class you logged in on, and a grade built on them
tells you to press your buttons harder.

Arbiter tries to answer *what was left on the table*, and it holds itself to three rules
that turn out to do most of the work:

1. **Score against a reference the run itself provides**, never an absolute band. A +2 and
   a +9 are different games. A healer who pushed 60k HPS while the party took 200k/s did a
   harder job than one who pushed 90k while it took 500k/s.
2. **Never score a player on something they could not do.** A Holy Paladin has no
   interrupt. The tank is *supposed* to be taking the damage. A dungeon with no dispellable
   debuff cannot be failed on dispels.
3. **When an axis does not apply, drop it and renormalise** the remaining weights. Scoring
   an inapplicable axis as zero is the most common way a grading model ends up measuring
   group composition instead of play.

The letter curve is shifted down from school grades on purpose: 100 means "nothing left on
the table", not "fine". An even, competent performance lands near 75 by construction.

## What each axis actually measures

| Axis | The naive version | What Arbiter does instead |
|---|---|---|
| `interrupts` | kicks per minute | kicks against the casts that **could** have been stopped. One +8 leaked 75 of 124 Light Bolts while the tank's raw kick rate scored a flat 100. |
| `mechanics` | damage taken | damage from a spell the **rest of the party did not take**. Unavoidable raid damage lands on everyone; the difference is the decision. |
| `mitigation` | defensive presses | active-mitigation **uptime and coverage of the player's worst damage windows**. Counting presses gave a Vengeance DH a free 100 for pressing Demon Spikes 126 times. |
| `response` | absolute HPS | whether the healer's output actually **rose during the party's worst windows**. |
| `activity` | casts per minute | cast rate with procs and ticks removed. A Ret Paladin logs 117 casts/min against a GCD that caps near 50. |
| `utility` | externals per minute | externals and dispels against the presses **this player's own kit offered** — from cooldowns measured out of the logs and the talent loadout in `COMBATANT_INFO`. |
| `throughput` | healing ÷ damage taken | healing ÷ the damage that actually **needed an outside heal**. The old denominator included every point a Vengeance DH healed back on himself, so a party that looked after itself lowered the healer's grade. |

Deliberately removed as measuring the class rather than the player: raw CC count, defensive
press count, absolute HPS bands, and overhealing percent.

Full reasoning, including the bugs that motivated each change, is in
[`docs/grading.md`](docs/grading.md).

## The kit a player owns, not the kit they used

Every denominator is built from what the player **talented**, plus whatever they pressed —
never presses alone. Presses alone are self-fulfilling: skip a button all key and it leaves
your own denominator, the ratio is measured against the smaller number, and the grade goes
**up** for not pressing it.

`COMBATANT_INFO` carries the exact loadout as trait entry ids.
[`arbiter/knowledge.py`](arbiter/knowledge.py) resolves them against a Raidbots tree dump
and takes the **union** of two imperfect sources — the tree's own `active`/`passive` flag,
and hand-written lists of what each button is *for*. Union, so the answer only ever grows.
Trusting the tree alone was tried first and immediately dropped Renewing Blaze and Shield
of Vengeance out of two players' reports, because the Midnight dump types both `passive`
and both are pressed.

## What it deliberately will not tell you

**The keystone upgrade (+1/+2/+3).** A combat log contains no par time, so it cannot be
derived. An earlier version derived it anyway by reading `CHALLENGE_MODE_END` field 7 as
"par seconds" and published a +2 for a key that was a +1. Field 7 is the character's total
M+ rating; field 6 is the run's dungeon score. Both are now printed, correctly labelled.
The upgrade appears only once [`data/dungeons.json`](data/dungeons.json) holds a real par —
and par bounds narrow only in the direction the evidence points, a timed key proving par is
beyond the clear time and a depleted one proving it is short of it.

The death count is cross-checked against the game's own timer on every run:
`CHALLENGE_MODE_END`'s milliseconds are wall clock plus five seconds per death, so the count
falls out of arithmetic that never touches this parser. A disagreement prints a `!!` line
rather than being quietly graded.

## Beyond the grades

- **Time ledger** — the timer is wall clock *plus* five seconds a death, split into
  fighting / routing / wipe recovery / death penalty. Names the minutes that were
  recoverable.
- **Per pull** — `cost` is time **lost**, not time spent. A wiped pull is wasted in full
  because the pack has to be killed again; one that held costs only its deaths.
- **Habits** — resource overcap, presses the client refused as still-on-cooldown, the mana
  floor, and buttons that never came off the bar. Printed under the grade and never
  weighted: the model scores outcomes, but a habit is the part somebody can change on the
  next pull.
- **`--json`** — a night as ~100 KB of flat data, 17 KB a key, every axis carrying its
  pre-band `raw` value. Recalibrating a band or tracking a habit across a month no longer
  means re-parsing gigabytes.

## Commands

```bash
python grade.py <log>                      # list the M+ runs in the log
python grade.py <log> --last               # grade the newest
python grade.py <log> --all                # grade every run
python grade.py <log> --player Hyporock    # one player's axes across every key
python grade.py <log> --all --json night.json
python ask.py night.json player Hyporock   # ask one question of a graded night
python log.py <log> --tail                 # raw log inspection
python raid-healing.py <log>               # one healer across raid encounters
python test_arbiter.py                     # 63 tests, under a second
```

`--player` is the view worth reading most often. A single key is a noisy sample and one bad
pull moves a letter; the same axis read down a column across a night is what separates a
habit from an accident.

## What it learns

[`data/spell-registry.json`](data/spell-registry.json) records what the logs have **proven**
interruptible and dispellable, plus measured cooldowns and buff durations. Proof runs one
way on purpose: seeing a spell interrupted proves it is interruptible; never seeing it
interrupted proves nothing, because the group may simply have had no kick up. So the
registry only grows, and an unknown spell is left *out* of opportunity counting rather than
counted as a miss. Interrupt scores start conservative and sharpen as more keys are fed
through.

Cooldowns there are a low **percentile** of observed gaps rather than the record low: proc
resets put two casts of a long-cooldown ability seconds apart, and a minimum latches onto
that permanently, inflating every availability denominator built on it.

Delete the registry and the tool still runs — it just knows less. It is data, not config.

## Layout

```
arbiter/     the package: collect (facts) -> derive (inference) -> score (judgement) -> report
grade.py     the entry point and its CLI
ask.py       queries a --json night without re-parsing the log
log.py       raw log inspection: field offsets, COMBATANT_INFO, event census
data/        talents-live.json, abilities.json, spell-registry.json, dungeons.json
docs/        the grading model, the Season 2 ability tables, a glossary
tools/       extract-abilities.py (from installed BigWigs), decode-loadout.py
```

The split between `collect` and `derive` is deliberate: everything in `collect.py` is
something the log literally says, and everything in `derive.py` is an inference drawn from
it. When a grade looks wrong, that boundary is where you find out whether the parser lied
or the model did.

## Caveats worth reading before you trust a number

- The bands were cut from **one player's** corpus of Season 2 keys — sixteen runs, one
  healer, one tank. They encode that player's range and will want re-cutting against a
  wider corpus.
- **`mitigation` currently scores every tank at or near 100** and should not be read. Its
  bands were calibrated against numbers produced by a since-fixed aura-parsing bug, and they
  have not been re-cut.
- Only **37%** of a typical player's talented buttons are classified, so most of a wide kit
  reaches the report as a name and reaches no axis at all. Coverage is worst where the kit
  is widest: 11% for a Warlock, 60% for a Vengeance DH.
- `damage` is a within-group comparison, not a population percentile.
- Written against **Midnight (12.1)** combat log formats. Field offsets move between
  expansions, and reading one column too far left made every damage number wrong in both
  directions at once. `arbiter/logfile.py` documents each offset and the trap around it.

The rest are listed under "Known limits" in [`docs/grading.md`](docs/grading.md).

## Requirements

Python 3.12+. No third-party dependencies.

## License

MIT — see [LICENSE](LICENSE).
