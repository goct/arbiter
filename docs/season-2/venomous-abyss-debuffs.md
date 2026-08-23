---
title: The Venomous Abyss — debuffs that land on players
scope: season-2
compiled: 2026-08-20
keywords: raid, venomous abyss, cell, raid debuffs, midnight
---

# The Venomous Abyss — raid debuff list for Cell

Extracted from `Logs/WoWCombatLog-081926_175628.txt` (first raid night, 4 kills + wipes,
~30 min of encounter time), counting only `SPELL_AURA_APPLIED` **inside `ENCOUNTER_START` /
`ENCOUNTER_END`** and only onto units with a `COMBATANT_INFO` line, i.e. real players.

**Blocked on one number.** Cell has no block for this raid, and its journal instance id is not
derivable offline. Cell itself knows it — the Raid Debuffs tab lists it under *Current Season* —
so one line anywhere in the world gets it:

```
/dump Cell.funcs.GetInstanceAndBossId("The Venomous Abyss")
```

Then it installs the same way the dungeons did, as
`CellDB["raidDebuffs"][<id>]["general"][<spellId>] = {order, trackByID=false, condition={"None"}}`
with contiguous orders. See [[cell-raid-debuff-db-stale]].

`sec/me` is seconds the debuff sat on Hyporock across the night — the measure that matters, since
a raid-wide DoT nobody can act on is worth less than a targeted one.

| order | id | debuff | apps | sec/me |
|---:|---:|---|---:|---:|
| 1 | 1290336 | Eternal Venom | 104 | 695 |
| 2 | 1288772 | Soulcoil Rite | 615 | 465 |
| 3 | 1307939 | Corpse Blight | 1422 | 387 |
| 4 | 1284506 | Mark of Blood | 649 | 265 |
| 5 | 1277051 | Mutilated Gash | 142 | 149 |
| 6 | 1284500 | Mark of Acid | 655 | 143 |
| 7 | 1287083 | Tempest | 265 | 66 |
| 8 | 1287427 | Essence Rend | 98 | 48 |
| 9 | 1310096 | Feasted | 314 | 43 |
| 10 | 1287205 | Viscous Cyst | 197 | 41 |
| 11 | 1285453 | Raging Crosswinds | 70 | 31 |
| 12 | 1291461 | Virulent Fumes | 271 | 29 |
| 13 | 1288554 | Latent Cultist | 476 | 22 |
| 14 | 1291918 | Shell Spin | 106 | 22 |
| 15 | 1284590 | Helical Toxins | 80 | 20 |
| 16 | 1308853 | Splinters | 98 | 8 |

**Excluded on purpose:** `1216858 Void Pulsar` (the seasonal affix — 1306s on the player, it is on
everyone permanently and there is nothing to do about it, so it would occupy the single centre
slot for the whole night), `57724 Sated`, and Monk Stagger.

**Splinters (1308853) is a bleed** — it is the only debuff here that ticks Physical-school periodic
damage. That makes it Stoneform-removable and worth adding to
[`stoneform-watchlist-s2.md`](README.md) once the raid list is installed.

**This is one night's data and should be refined.** Nothing here distinguishes "targeted mechanic
you must react to" from "ambient raid damage"; the ordering is uptime-on-the-player, which is the
best proxy available from a single log. After two or three more nights, re-run and look for
debuffs that correlate with deaths rather than with duration.
