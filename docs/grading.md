---
title: The Arbiter grading model — what it measures, and what it deliberately ignores
scope: tooling
compiled: 2026-08-22
keywords: mythic plus, grade, analysis, arbiter, interrupts, dispel, avoidable damage, scoring, calibration
---

# The Arbiter grading model

`grade.py` scores all five players across a whole key, trash included. This file
explains the model so a grade can be argued with. Code lives in [`arbiter/`](../arbiter);
the entry point and its CLI are documented in the module docstring of
[`grade.py`](../grade.py).

```
python grade.py <log>            # list the keys in the log
python grade.py <log> --last     # grade the newest
python grade.py <log> --all      # grade every key in the log
python grade.py <log> --n 2 --raw # metrics table, no grades
python grade.py <log> --player Hyporock   # one player, every key
```

`--player` is the view worth reading most often. A single key is a noisy sample and one
bad pull moves a letter; the same axis read down a column across a night is what separates
a habit from an accident.

Runs in ~13 s against a 1 GB log.

## The three rules

1. **Score against a reference the run provides.** A +2 and a +9 are different games.
   Absolute HPS and DPS bands mostly measure key level, so every axis is normalised
   against something inside the same key — party damage taken, the median damage dealer,
   the casts the dungeon actually offered.
2. **Never score a player on something they could not do.** A Holy Paladin has no
   interrupt. A tank is supposed to be taking the damage. A key with no dispellable
   debuff cannot be failed on dispels.
3. **An axis that does not apply is dropped and the weights renormalised** — never
   scored zero. Scoring an inapplicable axis as zero is the most common way a grading
   model ends up measuring group composition instead of play.

The letter curve is shifted down from school grades: 100 means "nothing left on the
table", not "fine". Across the nine Season 2 keys used to calibrate it, medians land at
**73 for damage dealers, 70 for healers, 90 for the tank**. The tank spread is earned
rather than bias — he did not die once in nine keys — and the healer sits low because the
`dispel` axis is currently a standing zero (see below). Grades are calibrated per role, so
compare a player to their own role's median, not to the other four names on the screen.

## Axes

| Axis | Roles | What it measures |
|---|---|---|
| `survival` | all | Deaths, discounted when three or more died together — that is one group event, not five individual ones. The curve is **interpolated**: indexing it meant rounding, and Python rounds 0.5 to 0, so a player whose single death was discounted as part of a wipe scored a clean 100 |
| `mechanics` | all | Share of intake that comparable teammates did not take (see below) |
| `interrupts` | all with a kick | 60% your share of the group's stops, 40% the group's conversion rate |
| `activity` | all | Engaged time while alive and in combat; band differs per role |
| `mitigation` | tank | Active-mitigation uptime **as a fraction of what the kit can hold**, plus coverage of the tank's own worst damage windows |
| `throughput` | healer | Healing **and absorbs** as a fraction of the damage that actually **needed an outside heal** — see [The healer's denominator](#the-healers-denominator) |
| `response` | healer | Whether output actually rose during the party's worst windows |
| `dispel` | healer | Damaging dispellable debuffs removed, of those that landed |
| `damage` | dps / tank | Against the **median** damage dealer, not the best and not the mean. Includes support damage, so an Augmentation Evoker is not scored zero |
| `utility` | tank / healer | Externals and dispels **as a fraction of the presses their own kit offered**. CC counts only for specs with no interrupt, since for everyone else a stun that stopped a cast is already paid for under `interrupts` |

## What was removed, and why

Each of these was in the first version and measured the class rather than the player.

- **Raw cast rate.** A Ret paladin logs 769 Crusading Strikes, 563 Divine Hammer ticks
  and 491 Empyrean Hammer procs in one key — none of them a button press — and reads
  117 casts/min against a GCD that physically caps near 50. The off-GCD detector in
  `derive.gcd_set` derives the passives from cast timing rather than a maintained
  blocklist, and the report prints what it dropped so the number is auditable.
- **Defensive press counts.** A Veng DH presses Demon Spikes 126 times a key and a Fire
  Mage presses Ice Block twice. Replaced by uptime and spike coverage.
- **Raw CC counts as "utility".** A Frost Nova on a trash pack is not utility. CC now
  only earns credit when it actually **stopped a cast**, which is also how a spec with
  no interrupt finally shows up in the interrupt column instead of scoring n/a.
- **Absolute HPS bands and overhealing percent.** Both mostly track key level and spec.
- **`utility` for damage dealers.** Measured across nine keys it is a flat ~0 whatever
  they do, so all it did was dock every DPS the same ten points.

## Two ideas doing the heavy lifting

**Dispels are counted by uptime, and only from real dispel buttons.** A self root-break
logs as `SPELL_DISPEL`, so Cat Form, Blink, Disengage and Ice Block all "dispel" a root
off their own caster — the whole of one Blinding Vale key's dispel activity was a single
Cat Form. Gating on the *casting* spell (`knowledge.DISPEL_SPELLS`) is what stops a feral
out-cleansing the healer 7-0 and stops the registry learning that a root is a dispellable
debuff. Opportunities are then measured as intervals the debuff was actually on someone,
not applications: in one key Spore Spines, Toxic Spew and Blight Resin logged 15, 15 and
12 applications, which says they are equivalent, while the union of intervals says 273 s,
27 s and 5 s of 1-second flickers. Instances shorter than 4 s are ignored — charging a
healer for not reacting to a flicker measures reflexes, not decisions.

**Interrupts are scored against opportunity.** A spell counts as an opportunity only
once some log has proven it interruptible; never having seen it kicked proves nothing,
because the group may simply have had nothing up. So `data/spell-registry.json` only
ever grows and unknown spells are left out rather than counted as misses — the score
starts conservative and sharpens as keys are fed through it. One +8 Blinding Vale leaked
**49 of 109 Light Bolts that actually went off** while the old per-minute metric was
handing the tank a flat 100 for 1.38 kicks a minute.

**Over-exposure, not "avoidable damage".** Unavoidable raid damage lands on everyone
roughly equally; a spell that hit one player several times as often as comparable
teammates is the part worth asking about. The hedging matters, and four separate
corrections were needed before this stopped producing confident wrong answers:

- self-inflicted damage is excluded — a Holy Paladin running [Blessing of
  Dawn](https://www.wowhead.com/talent-calc) takes 258 hits from their own talent in a key;
- melee are compared with melee, since anyone standing 30 yards away trivially avoids a
  melee-range puddle;
- **stacking** debuffs are excluded — the tank ate 142 Spore Spines ticks off 9
  applications, which is the mechanic working, not bad positioning;
- abilities the tank took **≥80%** of are excluded for the tank, because that is what a
  frontal looks like from the log. One boss barrage put 124 of its 130 hits on the tank
  and read as 33 million damage of misplay.

Even so, the log cannot distinguish a puddle nobody else stepped in from a mob choosing
one target, so where there is no peer baseline only half the damage is attributed, and
the column is called `exposed` rather than `avoidable`.

## Bosses versus trash

`ENCOUNTER_START`/`ENCOUNTER_END` inside a key mark the boss fights, and splitting the run
on them is usually the fastest read on where it went wrong. Those lines carry only six
fields, so they have to be parsed before any length guard aimed at combat events.

It earns its place: in the 2026-08-19 Ruby Life Pools +7, all three bosses died on the
first pull with **zero** deaths between them, and every one of the nine deaths in the key
happened on the trash packs in between — 82% of the damage the party took, too. "Nine
deaths" is a number; "nine deaths, none of them on a boss" is a diagnosis.

## Availability, not volume

Two axes used to band presses per minute, which grades the kit rather than the player:
a Vengeance DH carries five sigils and a Prot Warrior does not; Demon Spikes has charges
and a ~7 s recharge while a Warrior's Shield Wall is a two-minute wall. Both are now
measured against what the player's own buttons could actually have produced.

Cooldowns and buff durations are **measured from the logs**, not tabulated by hand, and
accumulate in the registry alongside the interruptible and dispellable lists. Three
details make the estimates hold up:

- **Sustained rate, not minimum gap.** An ability with charges gets dumped twice back to
  back, and a raw minimum reads that as a one-second cooldown. Measuring the tightest
  window covering four casts and dividing by three prices the charges in without being
  fooled by them.
- **Closed intervals only, median of them.** An aura with no matching `REMOVED` gets
  closed at the end of the log. That measured Demon Spikes — a 12-second buff — at 120
  seconds, and handed every tank a 100% mitigation ceiling regardless of their kit.
- **Nothing under fifteen seconds gets a denominator.** "Presses available" only means
  something for an ability gated by its cooldown. Spellsteal has effectively none, and
  pricing it this way told an Arcane Mage he had 462 utility presses available.

## The kit a player owns, not the kit they used

Every denominator here is built from what the player **talented**, plus whatever they
pressed — never from presses alone. Presses alone reproduce the dispel registry's
self-fulfilling blind spot: skip a button all key and it leaves your own denominator,
the ratio is measured against the smaller number, and the grade goes **up** for not
pressing it. `COMBATANT_INFO` carries the loadout as trait entry ids, and
[`talents-live.json`](../data/talents-live.json) — already in this folder for
[`decode-loadout.py`](../tools/decode-loadout.py) — resolves them. Baseline abilities never
appear in a talent tree, so pressing still counts those in.

That rule reached the utility axis first and the **mitigation ceiling** only on
2026-08-22, where it had survived a version longer with the press-only behaviour written
into the docstring as though it were the intent. A tank who talented Fiery Brand and
never pressed it was being scored against Demon Spikes alone.

`knowledge.buttons` is the single place that answers "what could this player have
pressed", and it is the **union** of two imperfect sources:

- **The tree's `type` field.** 2,691 of the Midnight dump's 3,414 entries are passive —
  no button, so they can never appear in `run.casts` and must never appear in a
  denominator or a habit line. A Vengeance DH's Soul Barrier applies an absorb some 300
  times a key and is not something anybody presses.
- **The five hand-written name lists** in `knowledge.py`, which know what a button is
  *for*. The tree does not: nothing in it says Blessing of Sacrifice is an external.

Union, so the answer only ever **grows** — the same one-directional rule the spell
registry runs on. Trusting `type` alone was tried first and immediately dropped Renewing
Blaze and Shield of Vengeance out of two players' habit lines, because the dump types
both `passive` and both are pressed.

The same data drives the **`never pressed`** lines under each player, split by whether
the model knows what the button is for. The classified line is talented defensives,
externals, dispels and crowd control; the unclassified line is everything else in the
tree, named but never counted — with no measured cooldown behind it there is no honest
claim about how often it *should* have come off the bar. Both are reported, never scored
— an ability can be correctly held all night — but they read directly against the death
forensics. In one Ruby Life Pools key the Arcane Mage died twice having never pressed Ice
Block, and the Ret Paladin died twice with Shield of Vengeance unused.

## The healer's denominator

Throughput used to be *healer output ÷ every point of damage the party took*. That
denominator contains a great deal of work the healer never had to do, and the result was
a report that contradicted itself on the same page — stating that very little healing was
needed in a key and then marking the healer down for not doing much healing.

What comes out of it now, and why:

| Removed | Why |
|---|---|
| **Self-healing** | A Vengeance DH puts back ~60 M a key on himself. A Fury Warrior's leech is not somebody else's throughput. In one Altar of Fangs +10, 47% of the party's intake was covered by the party itself. |
| **Overkill** | Damage past zero health. Nobody healed it and nobody could have. |
| **Absorbed damage** | Not removed — it was never *in* the figure. `amount` is already post-absorb, so subtracting absorbs again removes damage that never landed. An early attempt at this fix did exactly that and drove the denominator to near zero, which produced the opposite absurdity: a healer scoring a free A because the key "needed no healing". |
| **Added back** | Damage a shield cast *by someone else* prevented. It never landed, so it is not in intake — but it is outside work, and it belongs on both sides of the ratio. |

Numerator and denominator have to agree about self-healing: the healer's own self-heals
leave both. Leaving them in the numerator would pay a healer twice for keeping themselves
alive.

Measured across all sixteen keys on disk, coverage runs **58%–82% with a median of 73%**,
and is roughly flat from +2 to +10 — which is what a scale-free axis should look like.
The band is cut so 73% lands at a 75. All sixteen are the same healer, so the band
encodes one player's range and will want re-cutting when a second healer goes through it.

If a key genuinely asked nothing of the healer, the axis is **dropped**, not scored — rule
3. The floor is set an order of magnitude below the quietest key in the corpus, so it
fires on empty data rather than on an easy key.

## What the log does not contain

**Par times.** There is no par time anywhere in a combat log, so the keystone upgrade
(+1/+2/+3) cannot be derived from one. `CHALLENGE_MODE_END` carries:

| Field | What it actually is |
|---|---|
| 5 | total time in **milliseconds**, including the death penalty |
| 6 | this run's **Mythic+ dungeon score** |
| 7 | the character's **total M+ rating** after the run |

A previous report read field 7 as "par seconds" and published an upgrade column from it,
reporting a +2 for a key that was a +1. The tell was in the data the whole time: the same
dungeon read 1972.98 at +9 and 2107.59 at +10, and a par time does not move with key
level. Confirmed against the in-game Raider.IO readout on 2026-08-21 — rating 2108 against
a logged 2107.587, and every per-dungeon best score matching field 6 of the run that set it.

Upgrades are now printed **only** when [`dungeons.json`](../data/dungeons.json) holds a
real par for the dungeon. Otherwise the report says the par is unknown and prints the
bounds. Bounds narrow the same way the spell registry grows — only where the evidence
points:

- a **timed** run proves par is *longer* than the clear;
- a **depleted** run proves par is *shorter*;
- an **observed upgrade** (the leading `+` count in Raider.IO) pins the band it implies.

The first two are learned automatically on every run. The third has to be typed in,
because it is not in the log.

## Cross-checking the death count

The M+ timer is wall clock **plus five seconds per death**, and `CHALLENGE_MODE_START`
fires about 8.4 s before the timer starts. Rearranged, that yields the death count from
arithmetic that never touches the event parser:

```
deaths = (CHALLENGE_MODE_END_ms/1000 − wallclock + 8.4) / 5
```

Across sixteen keys this lands within 0.15 of an integer every time, which makes it a real
check rather than a coincidence. Every report now prints agreement or a `!!` disagreement.

It is what caught the **unconscious-death** bug: `UNIT_DIED`'s trailing field is
`unconsciousOnDeath`, and a `1` means the unit went down without dying. The game does not
count those. One Blinding Vale +8 logged five player `UNIT_DIED` of which three were
unconscious — the timer charged the key for two.

## What a key cost, not just how it scored

Grades answer "how did each player do". They do not answer "where did the run go", which
is usually the more useful question and was missing entirely.

**The time ledger.** The timer the game reports is not wall clock — it is wall clock plus
five seconds a death. Splitting it names the parts that were recoverable:

```
26:03 on the timer = 21:10 fighting, 3:59 routing, 0:23 wipe recovery,
                     0:30 death penalty, 0:08 pull timer
```

**Per-pull cost.** Totals average the pull that wiped the group twice into the eleven that
went fine. `cost` is **time lost, not time spent** — the first version ranked on duration
and put a six-minute pull that killed 39 enemies without a death at the top of "most
expensive", above the two pulls that actually wiped. A wiped pull is wasted in full (the
fight, the walk back, the timer penalty) because the pack has to be killed again. A pull
that held costs only its deaths.

**Affixes** are read from `CHALLENGE_MODE_START` field 6 and were never parsed before. Only
ids confirmable from this corpus are named; anything else prints as `affix 162` rather than
being guessed at, because a wrong affix label changes how a whole key reads.

## Habits, reported and never graded

Three signals that describe what a player *did* rather than what they achieved. They are
printed under the grade and deliberately carry no weight — the model scores outcomes,
but a habit is what somebody can actually change on the next pull.

| Signal | What it is | The trap in it |
|---|---|---|
| **Resource overcap** | `SPELL_ENERGIZE`'s `overEnergize`. A Holy Paladin at five Holy Power taking another one is spending globals to produce nothing, and it is invisible in HPS. | The amounts are written as **floats** (`8.0000`), unlike every other numeric field in the log; `int()` throws and yields zero, which reads as a player who never overcapped all night. Generated is `amount + overEnergize` — dividing by `amount` alone reported **229%** waste for an Arcane Mage at cap. Mana is excluded: at full mana there is nowhere to put a proc, and including it buried Holy Power under a meaningless 65%. |
| **Refused presses** | `SPELL_CAST_FAILED` with "Not yet recovered" — the client refusing a press because the ability is not back. | Reported **per successful cast**, not raw. 478 refusals on Holy Shock is unremarkable against 200 casts; 60 on a rarely-pressed button is the interesting one. And a refused press costs no global and no cast — it is an input habit, not a lost ability. |
| **Mana floor** | The lowest mana seen, as a fraction. Printed only below 25%. | Collected for a long time and never once read. |

Battle resurrections (`SPELL_RESURRECT`) are counted too — Intercession, Rebirth, Soulstone.
They were not collected at all before, so a healer who spent a global saving a run got no
credit for it anywhere.

## Getting the data back out

```bash
python grade.py <log> --all --json night.json --quiet
```

A 365 MB night reduces to about 85 KB of flat JSON: every axis with its **pre-band `raw`
value**, every pull, every death, the time ledger, per-player habits. That is what `Axis.raw`
was being carried for — recalibrating a band against a corpus, or asking how a habit moved
across a month, previously meant re-parsing gigabytes of log.

## Asking one question instead of re-reading the night

Two companion scripts sit on top of this one. Neither grades anything; both exist
because the follow-up question was being answered by re-printing the whole report
or by grepping the log by hand.

```bash
python ask.py night.json player Hyporock
python log.py <log> --last taken Hyporock
```

`ask.py` reads the `--json` export and nothing else — no log, no parsing.
`summary | player | axis | deaths | habits | pulls | bands`. A six-key night reads
as **914 bytes** through `summary` against **50 KB** for the full report, and
`bands` prints the median-raw-per-role table that `score.py`'s bands are cut from,
which previously meant a hand-written script over the JSON every time.

`log.py` answers the questions the grading model does *not* ask —
`runs | spell | events | taken | casts | uptime | gear` — scoped to one key by
`--last`/`--n`, which is a seek to that key's byte offset rather than a scan.

Why they are scripts and not greps, measured on this repo's own sessions: the
grading script's output is about **2% of a session's token cost**, but hand-grepped
raw log lines were **28%** — more than the grader's entire output — because every
payload is re-read on every turn that follows it. A 50 KB report read at turn 14 of
470 is paid for 456 more times. The second reason matters more over time: a
throwaway grep re-derives the field offsets, and the offsets in this format are
wrong in the obvious guesses. Both scripts go through `arbiter/logfile.py`, so a
question asked from them cannot silently reproduce a bug that was already fixed.

Both cap their own output and say what they held back.

## Speed

A full night (six keys, 365 MB) went from **1m43s to 33s**, byte-identical output. Three
changes, in order of how much they mattered:

- **`split` was 85% of runtime** — a character-at-a-time parser with a list append per
  character, 58 million appends for one key. It now works per comma-separated piece and only
  enters a character loop for pieces that actually contain a quote or a bracket.
- **One pass, not twelve.** Grading a night re-read the whole file once per key to learn the
  registry and once per key to grade. `collect.stream` fills the keys in file order in a
  single read, and the caller grades each run as it arrives and lets it go, so only one key
  is in memory at a time. The learning pre-pass also skips damage and healing lines *before*
  splitting them — it never reads either, and they are ~70% of the file.
- **`stamp` memoises the date.** It ran on every line and built a `datetime.date` to compute
  a constant.

## Recalibrating

Every axis carries its pre-band `raw` value. To re-derive the bands against a bigger
corpus, collect `Axis.raw` per role across many keys and set each band so the observed
median lands near 75. The bands currently in `score.py` were set that way against nine
Season 2 keys from 2026-08-18 and 2026-08-19.

## Fixed on 2026-08-21 — the damage fields

Worth its own heading because it was wrong in every number this tool has ever printed,
and wrong in **both directions at once**.

The damage tail is `amount, baseAmount, overkill, school, resisted, blocked, absorbed,
critical, glancing, crushing` — and `SPELL_*` / `RANGE_*` events carry one **more** field
after that (`ST`, `AOE`, or the supporting player's GUID) which `SWING_*` and
`ENVIRONMENTAL_DAMAGE` do not. Counting back from the end with a single offset therefore
read a different column depending on the event:

- on a **spell**, `f[-10]` is `baseAmount` — damage *before* armour and *before* the crit
  multiplier. Damage **taken** was inflated ~45%; damage **done** was *deflated*, because
  a crit's `amount` is double its `baseAmount`.
- on a **swing**, `f[-9]` is `baseAmount` and was being read as `overkill`. `baseAmount`
  is always positive, so **every melee swing was flagged as a killing blow** — 52,547 of
  them in one night against 920 real ones, which is what death forensics was picking a
  killer from.

The invariant that settles it: *overkill can never exceed the damage of the hit that
caused it*. The old offsets violate it 36,406 times in one log; the current ones, zero.

Two smaller ones alongside it:

- `SPELL_ABSORBED`'s shield caster is `f[-10]`; `f[-9]` is the caster's **name**. Testing a
  display name against `"Player-"` never matches, so `absorb_given` was empty on every log
  ever run through this — the entire shielding half of a Disc Priest or a Preservation
  Evoker scored zero.
- These logs are **CRLF**, and seeking to a key's byte offset means reading binary. Left
  on, the `\r` rides on the last field of every event: `f[12] == "BUFF"` was False for
  27,797 of 30,256 aura events in one key, and every tank scored 0% mitigation uptime.

## Known limits

- **The dispel axis is currently a flat zero, and that is the finding.** Across the five
  Season 2 keys that had any dispellable debuff at all, the group made **one** qualifying
  cleanse out of 100 chances, leaving roughly **135 M damage** on the table — 40 M of it
  Spore Spines sitting on somebody for 443 seconds of a single Blinding Vale key. The
  axis has no discriminative power yet because the habit is absent, not because the model
  is broken; it will move the moment anyone presses Cleanse.
- **`cpm` is an estimate**, not a GCD count. The detector is stable where it matters but
  can be a spell or two off at the margin; it is reported, never graded.
- **The tank bands rest on one tank.** Nine keys, one Vengeance DH. A role median drawn
  from a single player encodes that player's habits as the norm; with a second tank in the
  corpus the tank bands would move. Treat the tank column as "compared with himself".
- **An enemy that dies mid-cast is no longer a leak.** A cast start only counts against the
  group if the spell actually landed. Counting every unfinished cast as leaked both
  inflated the failure count and marked the group down for killing the caster — in one key
  it inflated 199 real opportunities to 228, pushing conversion down from 58% to 51%.
- **Shared cooldowns are detected but not applied.** `derive.shared_cooldown_pairs`
  finds ability pairs that never fire close together, which is what a shared cooldown
  looks like. At nine keys it is not trustworthy — it confidently pairs Beacon of Light
  with Judgment and Fracture with Glide — so it is deliberately **not** wired into
  scoring. A wrong correction is worse than none. The Vengeance sigils were checked by
  hand and are independent; so are the Paladin externals.
- **Most of a player's kit is still unclassified.** The tree says what everyone owns;
  the name lists say what only 37% of it is *for*. Measured across the 30 player-keys of
  2026-08-21: 153 of 419 talented buttons land in a list, and the rest reach the report
  as names and reach no axis at all. Coverage is worst where the kit is widest — 11% for
  both Warlocks, 17% for the Unholy DK, against 60% for the Vengeance DH. So the utility
  axis grades a Vengeance DH on Darkness and Consume Magic while his three sigils and
  Chaos Nova sit outside it. Widening the lists is whack-a-mole; the real fix is a spell
  metadata source that states what an ability does, which nothing installed provides.
- **Crowd control only counts as utility for a spec with no interrupt.** For everyone
  else a stun that stopped a cast is already paid for under `interrupts`, and counting it
  twice made a tank's utility score a duplicate of a column he had already been graded
  on. The gap that leaves: CC used to *blunt* a pack rather than to stop a cast — a Sigil
  of Misery on a melee pack — is credited nowhere.
- **Cooldown-reduction talents are invisible.** Cooldowns in the registry are per-spell
  and learned across every player who has ever been fed through it, so Pitch Black and
  Quickened Sigils change nothing about the denominator of the player who took them.
- **`damage` is a within-group comparison.** The honest fix is population percentiles
  from Warcraft Logs, which would replace "182% of your group's median" with a real
  spec- and ilvl-aware reference. Not wired up.
