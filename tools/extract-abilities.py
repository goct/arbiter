#!/usr/bin/env python
"""Build the Season 2 boss ability table from the installed BigWigs/LittleWigs source.

WHY THIS EXISTS ------------------------------------------------------------

Everything downstream -- TollPips dispel presets, any boss-mod filter list, any
audio cue pack -- keys off a spell ID. A wrong ID is not a visible error; it is
a feature that silently never fires. So no ID in the generated table is ever
typed from memory or copied off a website. Each one is read out of the module
that BigWigs actually runs, sitting in the AddOns folder next to the game.

Re-run this after any BigWigs/LittleWigs update and diff the output. That is
the whole point: when a patch moves something, you re-run a script instead of
redoing the work.

    C:/Python312/python.exe extract.py            # write abilities.md + .json
    C:/Python312/python.exe extract.py --stdout   # print the markdown instead

WHAT IT READS --------------------------------------------------------------

Three things per boss module, all keyed by spell ID, all carrying the ability
name in a trailing `-- Comment` that LittleWigs maintains by hand:

  mod:GetOptions()    the ability list, with role flags: TANK, HEALER,
                      TANK_HEALER, DISPEL, SAY, ME_ONLY_EMPHASIZE, ...
  mod:SetAuraData()   the 12.0 aura table -- the debuffs and buffs that land on
                      players, with the per-aura sound hooks (soundOnApplied,
                      soundOnAppliedDose, soundOnRemoved).
  mod:SetRenames()    the alternate display names the module registers.

The dispel column comes from TollPips/tests/dispels.py, which is already the
tool of record for that question and handles the trap where a module's option
key is the CAST and not the aura that lands on a player.

WHAT IT DOES NOT DO --------------------------------------------------------

The `class` column is a HEURISTIC derived from BigWigs' own role flags. It is a
starting point to edit, not a reading of the fight. Nothing here has watched a
pull. Treat A/B/C as a first pass and overwrite it by hand -- that is why the
markdown is the source of truth and this script only regenerates the parts that
come from source.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ADDONS = r"D:/World of Warcraft/_retail_/Interface/AddOns"
RAIDERIO_DUNGEONS = os.path.join(ADDONS, "RaiderIO", "db", "db_dungeons.lua")

# Season pool NAMES are not hard-coded -- they are read from RaiderIO's db, which it
# regenerates per season. Only the name -> module folder mapping lives here, because
# nothing on disk states it.
FOLDER_FOR = {
    "Kings' Rest":          ("LittleWigs_BattleForAzeroth", "KingsRest"),
    "Temple of Sethraliss": ("LittleWigs_BattleForAzeroth", "TempleOfSethraliss"),
    "Ruby Life Pools":      ("LittleWigs_Dragonflight", "RubyLifePools"),
    "Murder Row":           ("LittleWigs", "Midnight/MurderRow"),
    "The Blinding Vale":    ("LittleWigs", "Midnight/TheBlindingVale"),
    "Den of Nalorakk":      ("LittleWigs", "Midnight/DenOfNalorakk"),
    "Voidscar Arena":       ("LittleWigs", "Midnight/VoidscarArena"),
    "Altar of Fangs":       ("LittleWigs", "Midnight/AltarOfFangs"),
}

RAID = ("The Venomous Abyss", "BigWigs_TheVenomousAbyss", "")

NEWBOSS_RE = re.compile(r'BigWigs:NewBoss\(\s*("(?:[^"\\]|\\.)*"|[\w.:]+)\s*,\s*(-?\d+)(?:\s*,\s*(-?\d+))?')
ENCOUNTER_RE = re.compile(r'mod:SetEncounterID\((\d+)\)')
# An option entry is either a bare id or a {id, "FLAG", ...} table, with the ability
# name in the trailing comment. Negative ids are journal section headers, not spells.
OPTION_RE = re.compile(r'^\s*(?:\{\s*(-?\d+)((?:\s*,\s*"[A-Z_]+")*)[^}]*\}|(-?\d+))\s*,?\s*(?:--\s*(.*))?$')
AURA_RE = re.compile(r'^\s*\{\s*(\d+)((?:\s*,\s*\d+)*)\s*(.*?)\}\s*,?\s*(?:--\s*(.*))?$')
FLAG_RE = re.compile(r'"([A-Z_]+)"')
KV_RE = re.compile(r'(\w+)\s*=\s*("(?:[^"\\]|\\.)*"|[\w.:]+)')


def block_after(text, opener, closer_depth_chars="{}"):
    """Return the text between the balanced braces that follow `opener`."""
    i = text.find(opener)
    if i < 0:
        return None
    j = text.find(closer_depth_chars[0], i)
    if j < 0:
        return None
    depth = 0
    for k in range(j, len(text)):
        c = text[k]
        if c == closer_depth_chars[0]:
            depth += 1
        elif c == closer_depth_chars[1]:
            depth -= 1
            if depth == 0:
                return text[j + 1:k]
    return None


def parse_boss(path):
    src = open(path, "r", encoding="utf-8-sig").read()

    m = NEWBOSS_RE.search(src)
    if not m:
        return None
    raw_name = m.group(1)
    if not raw_name.startswith('"'):
        return None                      # CL.trash and friends -- not a boss
    name = raw_name.strip('"').replace('\\"', '"')

    enc = ENCOUNTER_RE.search(src)

    abilities = {}

    opts = block_after(src, "function mod:GetOptions()")
    if opts:
        for line in opts.splitlines():
            mo = OPTION_RE.match(line)
            if not mo:
                continue
            sid = mo.group(1)
            if sid is None:
                sid = mo.group(3)
            if sid is None:
                continue
            sid = int(sid)
            if sid <= 0:
                continue
            flags = FLAG_RE.findall(mo.group(2) or "")
            label = (mo.group(4) or "").strip()
            abilities[sid] = {
                "spellID": sid,
                "name": label,
                "flags": flags,
                "aura": False,
                "sounds": {},
                "note": "",
            }

    auras = block_after(src, "mod:SetAuraData(")
    if auras:
        for line in auras.splitlines():
            ma = AURA_RE.match(line)
            if not ma:
                continue
            sid = int(ma.group(1))
            extra = [int(x) for x in re.findall(r'\d+', ma.group(2) or "")]
            kvs = dict(KV_RE.findall(ma.group(3) or ""))
            label = (ma.group(4) or "").strip()

            for one in [sid] + extra:
                rec = abilities.get(one)
                if rec is None:
                    rec = {
                        "spellID": one,
                        "name": label,
                        "flags": [],
                        "aura": False,
                        "sounds": {},
                        "note": "",
                    }
                    abilities[one] = rec
                rec["aura"] = True
                if not rec["name"]:
                    rec["name"] = label
                for key in ("soundOnApplied", "soundOnAppliedDose", "soundOnRemoved"):
                    if key in kvs:
                        rec["sounds"][key] = kvs[key].strip('"')
                if "note" in kvs:
                    rec["note"] = kvs["note"].strip('"')

    renames = block_after(src, "mod:SetRenames(")
    if renames:
        for sid in re.findall(r'\[(\d+)\]\s*=', renames):
            rec = abilities.get(int(sid))
            if rec is not None:
                rec["renamed"] = True

    return {
        "boss": name,
        "encounterID": int(enc.group(1)) if enc else None,
        "file": os.path.basename(path),
        "abilities": [abilities[k] for k in sorted(abilities)],
    }


def classify(rec, dispellable):
    """First-pass A/B/C from BigWigs' own role flags. Biased toward C on purpose."""
    flags = set(rec["flags"])

    if dispellable or "DISPEL" in flags:
        return "A", "dispel"
    if "TANK_HEALER" in flags:
        return "A", "tank damage, heal through"
    if flags & {"ME_ONLY_EMPHASIZE", "ME_ONLY", "SAY", "SAY_COUNTDOWN"}:
        return "A", "lands on you"
    if "underyou" in str(rec.get("note", "")).lower() or rec["sounds"].get("soundOnApplied") == "underyou":
        return "A", "move"
    if "HEALER" in flags:
        return "A", "healer-flagged"
    if "TANK" in flags:
        return "C", "tank only"

    # BigWigs states its own opinion per aura. `soundOnApplied = "none"` is the module
    # author saying this one is not worth a noise, which is a far better signal than
    # anything guessable from the flags -- and an aura with a real sound is one they
    # decided IS worth interrupting you for.
    sound = rec["sounds"].get("soundOnApplied", "")
    if sound and sound != "none":
        return "B", "BigWigs alerts on it (%s)" % sound
    if rec["aura"]:
        return "C", "aura, but BigWigs stays silent"
    return "C", "no healer flag in source"


def load_pool():
    """Season pool, straight out of RaiderIO's per-season generated db."""
    src = open(RAIDERIO_DUNGEONS, "r", encoding="utf-8-sig").read()
    season = re.search(r'-- Dungeons for this season \(([\w-]+)\).*?ns\.dungeons = \{(.*?)\n\}',
                       src, re.S)
    if not season:
        raise SystemExit("could not find the season block in db_dungeons.lua")
    label = season.group(1)
    names = re.findall(r'\["name"\]\s*=\s*"([^"]+)"', season.group(2))
    stamp = re.search(r'Generated by Raider\.IO on (\S+)', src)
    return label, names, (stamp.group(1) if stamp else "unknown")


def dispel_ids():
    """Ask the existing tool of record; it owns the key-vs-aura trap."""
    sys.path.insert(0, os.path.join(ADDONS, "TollPips", "tests"))
    try:
        import dispels
    except Exception as e:
        print("  (dispels.py unavailable: %s)" % e, file=sys.stderr)
        return {}
    out, best = {}, {}
    try:
        records, _files, _roots = dispels.scan()
    except Exception as e:
        print("  (dispels.scan() failed: %s)" % e, file=sys.stderr)
        return out

    # scan() only walks the LittleWigs_* roots, so the raid is not in it.
    raid_dir = os.path.join(ADDONS, RAID[1])
    if os.path.isdir(raid_dir):
        for fn in sorted(os.listdir(raid_dir)):
            if fn.endswith(".lua"):
                records.extend(dispels.parse_file(os.path.join(raid_dir, fn)))

    for rec in records:
        for i in rec["ids"]:
            i = int(i)
            # An id confirmed by an aura registration beats one taken from the
            # module's option key -- the key is often the cast, not what lands.
            if i in best and best[i] == "aura" and rec["source"] != "aura":
                continue
            out[i] = rec["type"]
            best[i] = rec["source"]
    return out


def collect():
    label, pool, stamp = load_pool()
    dispels = dispel_ids()

    zones = []
    missing = []
    for name in pool:
        entry = FOLDER_FOR.get(name)
        if not entry:
            missing.append(name)
            continue
        pkg, sub = entry
        zones.append((name, os.path.join(ADDONS, pkg, sub)))
    zones.append((RAID[0], os.path.join(ADDONS, RAID[1], RAID[2])))

    result = []
    for zone, path in zones:
        if not os.path.isdir(path):
            result.append({"zone": zone, "path": path, "error": "module folder not found",
                           "bosses": []})
            continue
        bosses = []
        for fn in sorted(os.listdir(path)):
            if not fn.endswith(".lua"):
                continue
            if fn in ("!Options.lua", "Trash.lua", "Options.lua"):
                continue
            parsed = parse_boss(os.path.join(path, fn))
            if not parsed:
                continue
            for rec in parsed["abilities"]:
                dtype = dispels.get(rec["spellID"])
                cls, why = classify(rec, dtype)
                rec["dispel"] = dtype or ""
                rec["class"] = cls
                rec["why"] = why
            bosses.append(parsed)
        result.append({"zone": zone, "path": path, "bosses": bosses})

    return {
        "season": label,
        "raiderIOGenerated": stamp,
        "extracted": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "poolMissingAMapping": missing,
        "zones": result,
    }


def markdown(data):
    L = []
    add = L.append

    add("---")
    add("title: Season 2 boss abilities -- debuffs, buffs and healer actions")
    add("scope: season-2")
    add("generated: %s" % data["extracted"])
    add("source: installed BigWigs / LittleWigs modules")
    add("keywords: season 2, mythic plus, dispel, debuff, buff, spell id, healer")
    add("---")
    add("")
    add("# Season 2 boss abilities")
    add("")
    add("**Generated by `extract.py` -- do not hand-edit the spell IDs.** They are read out")
    add("of the BigWigs/LittleWigs modules the game actually loads. Re-run the script after")
    add("any boss-mod update and diff.")
    add("")
    add("The one column that IS yours to edit is **Healer action**. The `Class` column is a")
    add("first pass derived from BigWigs' own role flags and is wrong wherever the flags")
    add("don't happen to line up with what a healer does. Overwrite it.")
    add("")
    add("| | |")
    add("|---|---|")
    add("| Season | `%s` |" % data["season"])
    add("| Pool read from | RaiderIO `db_dungeons.lua`, generated %s |" % data["raiderIOGenerated"])
    add("| Extracted | %s |" % data["extracted"])
    add("")

    total = 0
    counts = {"A": 0, "B": 0, "C": 0}
    dispelable = 0
    for z in data["zones"]:
        for b in z["bosses"]:
            for r in b["abilities"]:
                total += 1
                counts[r["class"]] = counts.get(r["class"], 0) + 1
                if r["dispel"]:
                    dispelable += 1

    add("## Coverage")
    add("")
    add("%d abilities across %d zones -- **A %d / B %d / C %d**, %d dispellable."
        % (total, len(data["zones"]), counts["A"], counts["B"], counts["C"], dispelable))
    add("")

    add("| Zone | Bosses | Abilities | Aura data | Dispels |")
    add("|---|---:|---:|---:|---:|")
    for z in data["zones"]:
        if z.get("error"):
            add("| %s | — | — | — | *%s* |" % (z["zone"], z["error"]))
            continue
        na = sum(len(b["abilities"]) for b in z["bosses"])
        nau = sum(1 for b in z["bosses"] for r in b["abilities"] if r["aura"])
        nd = sum(1 for b in z["bosses"] for r in b["abilities"] if r["dispel"])
        add("| %s | %d | %d | %d | %d |" % (z["zone"], len(z["bosses"]), na, nau, nd))
    add("")

    for z in data["zones"]:
        add("## %s" % z["zone"])
        add("")
        if z.get("error"):
            add("> **%s** — `%s`" % (z["error"], z["path"]))
            add("")
            continue
        if not z["bosses"]:
            add("> No boss modules parsed in `%s`." % z["path"])
            add("")
            continue
        for b in z["bosses"]:
            enc = b["encounterID"]
            add("### %s" % b["boss"])
            add("")
            add("`%s`%s" % (b["file"], ("  ·  encounter %d" % enc) if enc else ""))
            add("")
            add("| Spell ID | Ability | Flags | Aura | Dispel | Sound hook | Class | Healer action |")
            add("|---:|---|---|:-:|---|---|:-:|---|")
            for r in b["abilities"]:
                sound = ""
                if r["sounds"]:
                    sound = ", ".join("%s=%s" % (k.replace("soundOn", "").lower(), v)
                                      for k, v in sorted(r["sounds"].items()))
                add("| %d | %s | %s | %s | %s | %s | **%s** | _%s_ |" % (
                    r["spellID"],
                    r["name"] or "—",
                    " ".join(r["flags"]) or "",
                    "Y" if r["aura"] else "",
                    r["dispel"],
                    sound,
                    r["class"],
                    r["why"],
                ))
            add("")

    if data["poolMissingAMapping"]:
        add("## Unmapped")
        add("")
        add("These are in the season pool but `FOLDER_FOR` in `extract.py` has no module")
        add("folder for them -- add the mapping and re-run:")
        add("")
        for n in data["poolMissingAMapping"]:
            add("- %s" % n)
        add("")

    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--stdout", action="store_true", help="print markdown instead of writing")
    args = ap.parse_args()

    data = collect()
    md = markdown(data)

    if args.stdout:
        sys.stdout.write(md)
        return

    with open(os.path.join(HERE, "abilities.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(md)
    with open(os.path.join(HERE, "abilities.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, sort_keys=False)
        f.write("\n")

    zones = len([z for z in data["zones"] if not z.get("error")])
    bosses = sum(len(z["bosses"]) for z in data["zones"])
    abil = sum(len(b["abilities"]) for z in data["zones"] for b in z["bosses"])
    print("wrote abilities.md and abilities.json  --  %d zones, %d bosses, %d abilities"
          % (zones, bosses, abil))


if __name__ == "__main__":
    main()
