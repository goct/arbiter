---
title: Season 2 — boss debuffs and buffs, and how to keep them current
scope: season-2
compiled: 2026-08-18
keywords: season 2, mythic plus, M+, dungeon pool, dispel, debuff, buff, spell id, boss abilities, refresh
---

# Season 2 — boss debuffs and buffs

Season 2 opened **2026-08-18**. This folder is the tracked answer to "what lands on people
in this season's content, and what do I do about it".

| | |
|---|---|
| Source of truth | [`abilities.md`](abilities.md) — generated |
| Machine-readable copy | [`abilities.json`](../../data/abilities.json) — generated |
| Generator | [`extract.py`](../../tools/extract-abilities.py) |
| Regenerate | `C:/Python312/python.exe extract.py` |

Everything downstream — TollPips dispel presets, any boss-mod filter list, any audio cue
pack — is meant to be generated from `abilities.json`, not hand-maintained alongside it.
When a patch moves something, re-run the script and diff.

## The pool

Read out of RaiderIO's `db/db_dungeons.lua`, which it regenerates per season. It is
labelled `season-mn-2` and was generated **2026-08-18T07:38:58Z**. The pool is not typed
by hand anywhere; if RaiderIO updates, `extract.py` picks the change up. Only the
name → module-folder mapping is hard-coded, in `FOLDER_FOR`.

| Dungeon | Origin | Module folder |
|---|---|---|
| Altar of Fangs | Midnight | `LittleWigs/Midnight/AltarOfFangs` |
| Murder Row | Midnight | `LittleWigs/Midnight/MurderRow` |
| Den of Nalorakk | Midnight | `LittleWigs/Midnight/DenOfNalorakk` |
| The Blinding Vale | Midnight | `LittleWigs/Midnight/TheBlindingVale` |
| Voidscar Arena | Midnight | `LittleWigs/Midnight/VoidscarArena` |
| Kings' Rest | Battle for Azeroth | `LittleWigs_BattleForAzeroth/KingsRest` |
| Temple of Sethraliss | Battle for Azeroth | `LittleWigs_BattleForAzeroth/TempleOfSethraliss` |
| Ruby Life Pools | Dragonflight | `LittleWigs_Dragonflight/RubyLifePools` |

Raid: **The Venomous Abyss**, 8 bosses, `BigWigs_TheVenomousAbyss`.

The three legacy dungeons live in per-expansion LittleWigs packages that are **separately
enableable**. If one of those packages is switched off, its dungeons silently vanish from
the table rather than erroring — check the coverage table in `abilities.md` for a zone
that suddenly reports 0 bosses.

## Where it currently stands

438 abilities, 36 bosses, 9 zones. First-pass classification **A 44 / B 62 / C 332**.

### The open item: Midnight has no dispel data

`dispels.py --gate Midnight` still **exits 1** as of 2026-08-18, with Season 2 live:

```
Midnight: NO dispel data in the installed LittleWigs yet (0 sites).
```

Raw `Dispeller(` call counts in the installed source:

| Package | Sites |
|---|---:|
| `LittleWigs` (contains all of Midnight) | **0** |
| `LittleWigs_BattleForAzeroth` | 72 |
| `LittleWigs_Dragonflight` | 50 |
| `BigWigs_TheVenomousAbyss` | 1 |

So of the eight dungeons in the pool, only the **three legacy ones** can have their
dispels derived from source today. All five Midnight dungeons come back empty — not
because they have no dispellable debuffs, but because nobody has written the
`self:Dispeller(...)` calls into those modules yet.

The five that do resolve:

| Spell ID | Ability | Dungeon | Type |
|---:|---|---|---|
| 268008 | Snake Charm | Temple of Sethraliss | magic |
| 269686 | Plague | Temple of Sethraliss | disease |
| 381512 | Stormslam *(option key — the cast)* | Ruby Life Pools | magic |
| 381515 | Stormslam *(the aura that lands)* | Ruby Life Pools | magic |
| 372682 | Primal Chill | Ruby Life Pools | movement |

Stormslam is the documented trap in person: the module keys its option to the **cast**
(381512) while the debuff that lands is **381515**. A presence check against the key can
never match. `dispels.py` reports both and prefers the aura; so does `extract.py`.

**Action:** re-run `extract.py` after each LittleWigs update until the Midnight numbers
stop being zero, then top the TollPips presets up from the result. Nothing else unblocks
it — the data has to be written upstream first.

## What the modules turned out to expose

Worth recording, because it contradicts what the healer-audio handoff lists under
"dead ends — do not attempt". Read out of `BigWigs_Core/BossPrototype.lua` and the
installed modules on 2026-08-18:

- **`mod:SetAuraData({...})`** (`BossPrototype.lua:1180`) — a per-boss table of the auras
  that land on players, keyed by spell ID, carrying `soundOnApplied`,
  `soundOnAppliedDose`, `soundOnRemoved` and a `note`. Used by 79 modules. This is the
  debuff/buff inventory, already written, already keyed the way we need.
- **Sounds are LibSharedMedia names.** `BossPrototype.lua:1168` maps the short names
  (`long` `info` `alert` `alarm` `warning` `underyou` `none`) onto LSM entries. A custom
  sound pack registered with LSM is therefore addressable per aura.
- **`mod:SetRenames({...})`** (`BossPrototype.lua:926`) — modules register alternate
  display names per spell ID, including "on you" variants and an `original`. Used by 84
  modules.

`soundOnApplied = "none"` turned out to be the single most useful signal in the whole
table: it is the module author stating that this aura is not worth a noise. That is what
drives the C classification, rather than anything guessed from role flags.

**This is a source reading, not a tested claim.** None of it has been run in game, and
whether `SetRenames` reaches the Blizzard encounter timeline or only BigWigs' own bars is
*not* established here. Before building on any of it, confirm in game. The handoff's
warning stands: confident wrong answers in this area have cost time on this project once
already.

## Refresh procedure

1. `C:/Python312/python.exe extract.py`
2. `git diff` (or eyeball) `abilities.md` — spell IDs that moved are the interesting part.
3. `C:/Python312/python.exe dispels.py --check` in `TollPips/tests` — what the shipped
   presets are missing.
4. Re-apply any hand-written **Healer action** text. The script does not preserve it; see
   the caveat below.

### The one real weakness

`extract.py` overwrites `abilities.md` wholesale, so hand-written Healer action notes are
lost on regeneration. Right now that column is entirely generated placeholder text, so
nothing is at risk. Before doing a hand-annotation pass, either move the notes into a
side file keyed by spell ID that the generator merges in, or stop regenerating in place.
Decide that first — it is much cheaper than redoing the annotations.
