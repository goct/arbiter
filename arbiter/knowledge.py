"""What the log cannot tell you: specs, roles, and what each button is FOR.

Three sources feed this, in increasing order of trustworthiness:

  1. Static tables below -- spec ids, roles, which spells are defensives. Hand
     maintained, so treat as a hint and never as a gate on scoring.
  2. `data/abilities.json` -- generated from the installed BigWigs and
     LittleWigs modules by `tools/extract-abilities.py`. Gives boss ability
     classification (A/B/C) and TANK flags. Coverage is honest but thin: 438
     abilities and only 5 with dispel data, because Midnight's LittleWigs still
     ships no dispel metadata. Do not build anything load-bearing on `dispel`.
  3. `data/spell-registry.json` -- learned from the logs themselves, and the
     only source that improves over time. If a spell was ever interrupted it is
     interruptible; if it was ever dispelled it is dispellable.

The registry exists to answer the question the old grader could not: not "how
many kicks per minute did you press", which is mostly a fact about the dungeon,
but "of the casts that could have been stopped, how many did you stop".
"""

import json
import pathlib

HERE = pathlib.Path(__file__).resolve()
DATA = HERE.parents[1] / "data"
ABILITIES = DATA / "abilities.json"
REGISTRY = DATA / "spell-registry.json"

SPECS = {65: "Holy Paladin", 66: "Prot Paladin", 70: "Ret Paladin", 250: "Blood DK",
         251: "Frost DK", 252: "Unholy DK", 577: "Havoc DH", 581: "Veng DH", 1480: "Devourer DH",
         102: "Balance", 103: "Feral", 104: "Guardian", 105: "Resto Druid",
         253: "BM Hunter", 254: "MM Hunter", 255: "Surv Hunter", 62: "Arcane Mage",
         63: "Fire Mage", 64: "Frost Mage", 268: "Brewmaster", 269: "Windwalker",
         270: "Mistweaver", 256: "Disc Priest", 257: "Holy Priest", 258: "Shadow Priest",
         259: "Assass Rogue", 260: "Outlaw Rogue", 261: "Sub Rogue", 262: "Ele Shaman",
         263: "Enh Shaman", 264: "Resto Shaman", 265: "Affli Lock", 266: "Demo Lock",
         267: "Destro Lock", 71: "Arms", 72: "Fury", 73: "Prot Warrior",
         1467: "Deva Evoker", 1468: "Pres Evoker", 1473: "Aug Evoker"}
TANKS = {66, 73, 104, 250, 268, 581}
HEALERS = {65, 105, 256, 257, 264, 270, 1468}

# Melee specs. Used only to pick a fair comparison group for avoidable damage:
# a ground effect under the boss is trivially avoided by anyone standing 30
# yards away, so comparing a melee player's exposure to a ranged player's is
# comparing positions, not decisions.
MELEE = {66, 70, 250, 251, 252, 577, 581, 1480, 103, 104, 268, 269, 255,
         259, 260, 261, 263, 71, 72, 73}

# No interrupt on the bar at all. Scoring these on kicks is scoring them on a
# button they do not have; their cast-stopping shows up as CC instead.
KICKLESS = {65, 105, 256, 257, 1468}

# Friendly dispel capability by spec. Used only to decide whether a debuff the
# group ate was one the group could have removed -- never to award points for
# owning a button.
DISPELS = {
    65: {"magic", "poison", "disease"}, 66: {"poison", "disease"}, 70: {"poison", "disease"},
    105: {"magic", "curse", "poison"}, 102: {"curse", "poison"}, 103: {"curse", "poison"},
    104: {"curse", "poison"},
    256: {"magic", "disease"}, 257: {"magic", "disease"}, 258: {"disease"},
    264: {"magic", "curse"}, 262: {"curse"}, 263: {"curse"},
    270: {"magic", "poison", "disease"}, 268: {"poison", "disease"}, 269: {"poison", "disease"},
    1468: {"magic", "poison"}, 1467: {"poison"}, 1473: {"poison"},
    62: {"curse"}, 63: {"curse"}, 64: {"curse"},
    265: {"magic"}, 266: {"magic"}, 267: {"magic"},
}

# Personal survival cooldowns. Counted for COVERAGE (was one up when you got
# hit), never for volume -- a Veng DH presses Demon Spikes 126 times a key and
# a Fire Mage presses Ice Block twice, and neither fact is about skill.
PERSONAL_DEFENSIVES = {
    "Divine Shield", "Divine Protection", "Shield of Vengeance", "Ardent Defender",
    "Guardian of Ancient Kings", "Lay on Hands", "Eye of Tyr", "Shield of the Righteous",
    "Demon Spikes", "Blur", "Netherwalk", "Fiery Brand", "Metamorphosis",
    "Barkskin", "Survival Instincts", "Frenzied Regeneration", "Ironfur", "Renewal",
    "Icebound Fortitude", "Anti-Magic Shell", "Vampiric Blood", "Rune Tap",
    "Dancing Rune Weapon",
    "Fortifying Brew", "Diffuse Magic", "Dampen Harm", "Touch of Karma", "Zen Meditation",
    "Desperate Prayer", "Dispersion", "Fade",
    "Cloak of Shadows", "Evasion", "Feint", "Crimson Vial",
    "Astral Shift", "Shamanistic Rage", "Earth Elemental", "Harden Skin",
    "Unending Resolve", "Dark Pact",
    "Ice Block", "Alter Time", "Greater Invisibility", "Prismatic Barrier",
    "Ice Barrier", "Blazing Barrier", "Ice Cold", "Mass Barrier",
    "Aspect of the Turtle", "Exhilaration", "Survival of the Fittest",
    "Obsidian Scales", "Renewing Blaze", "Zephyr",
    "Shield Wall", "Last Stand", "Die by the Sword", "Spell Reflection",
    "Enraged Regeneration", "Impending Victory", "Healthstone",
}
# Cast on somebody else, or on the group. These are the ones worth chasing when
# a party member dies: an unused external is a decision, an unused personal is
# often just a dead player who never saw it coming.
EXTERNALS = {
    "Blessing of Protection", "Blessing of Sacrifice", "Blessing of Spellwarding",
    "Pain Suppression", "Guardian Spirit", "Life Cocoon", "Ironbark", "Time Dilation",
    "Vigilance", "Sacrificial Pact",
}
RAID_COOLDOWNS = {
    "Aura Mastery", "Power Word: Barrier", "Spirit Link Totem", "Darkness",
    "Anti-Magic Zone", "Rallying Cry", "Revival", "Tranquility", "Divine Hymn",
    "Salvation", "Rewind", "Emerald Communion", "Healing Tide Totem", "Barrier of Faith",
}
CC = {"Hammer of Justice", "Blinding Light", "Repentance", "Chastise", "Psychic Scream",
      "Fear", "Howl of Terror", "Mortal Coil", "Polymorph", "Dragon's Breath",
      "Ring of Frost", "Frost Nova", "Blind", "Kidney Shot", "Cheap Shot", "Sap", "Gouge",
      "Imprison", "Chaos Nova", "Sigil of Misery", "Sigil of Chains", "Sigil of Silence",
      "Mighty Bash", "Incapacitating Roar", "Typhoon", "Ursol's Vortex", "Hibernate",
      "Intimidation", "Binding Shot", "Freezing Trap", "Scatter Shot", "Bursting Shot",
      "Leg Sweep", "Paralysis", "Ring of Peace", "Capacitor Totem", "Hex", "Thunderstorm",
      "Earthbind Totem", "Storm Bolt", "Shockwave", "Intimidating Shout", "Asphyxiate",
      "Death Grip", "Sleep Walk", "Landslide", "War Stomp", "Quaking Palm"}

# Seed for the off-GCD detector. It derives these itself from cast timing (see
# derive.gcd_set), but seeding the worst offenders keeps the derivation stable
# across runs where a proc happens to fire rarely.
KNOWN_PASSIVE = {"Reclamation", "Crusading Strikes", "Empyrean Hammer", "Sacrosanct Crusade",
                 "Soul Fragment", "Shadowy Apparition", "Divine Hammer", "Lightbearer",
                 "Refraction", "Leech", "Sun Sear", "Afterimage", "Arcane Phoenix",
                 "Idol of C'Thun", "Thing from Beyond"}


# The actual dispel buttons. This whitelist is load-bearing: a self root-break
# is logged as SPELL_DISPEL, so Cat Form, Bear Form, Travel Form, Blink,
# Disengage and Ice Block all "dispel" a root off their own caster. Accepting any
# SPELL_DISPEL from a player source makes a feral look like he out-cleansed the
# healer 7-0, and teaches the registry that a root is a dispellable debuff.
# Filter on the CASTING spell, never on the event name.
DISPEL_SPELLS = {
    "Cleanse", "Cleanse Toxins", "Purify", "Purify Disease", "Purify Spirit",
    "Cleanse Spirit", "Nature's Cure", "Remove Corruption", "Remove Curse",
    "Detox", "Naturalize", "Cauterizing Flame", "Singe Magic", "Devour Magic",
    "Dispel Magic", "Mass Dispel", "Improved Nature's Cure",
    # Offensive removals. They never match a debuff on a player, so they cannot
    # reach the dispel score, but stripping an enrage off a pack is real work and
    # belongs in utility rather than nowhere.
    "Soothe", "Tranquilizing Shot", "Purge", "Spellsteal", "Consume Magic",
    "Sear Magic", "Shadow Word: Devour",
}

# Established empirically on 2026-08-19 by scanning SPELL_DISPEL across the logs
# on disk, after filtering out the root-breaks above. Seeded rather than left to
# be relearned because nothing installed states dispel types for Midnight -- the
# same gap that still blocks the TollPips Season 2 presets.
SEED_DISPELLABLE = {
    "1238084": "Spore Spines",          # The Blinding Vale
    "1289258": "Corrosive Essence",     # Voidscar Arena
    "1294569": "Paralyzing Shots",      # Altar of Fangs
    "1263971": "Mind-Numbing Poison",   # Voidscar Arena
    "270499": "Frost Shock",            # Kings' Rest
    "392641": "Rolling Thunder",        # Ruby Life Pools
    "1250937": "Toxic Spew",            # The Blinding Vale, poison or curse
}


# Every name above that this file claims to know the PURPOSE of. Used as the
# second half of `buttons`, and to split a report's "never pressed" line into
# the part the axes were built on and the part they were not.
CLASSIFIED = (PERSONAL_DEFENSIVES | EXTERNALS | RAID_COOLDOWNS | CC | DISPEL_SPELLS)


def buttons(info):
    """Every talented ability this player could actually have PRESSED.

    The union of two imperfect sources, deliberately. The talent tree's `type`
    field knows about buttons no hand-written list in this file has ever heard
    of -- across the 2026-08-21 corpus the lists recognise 40% of the active
    talents people actually take, so a Destruction Warlock sat on Demonic Circle
    and Curse of Tongues for six keys and every report came back clean. The
    lists in turn know about buttons the tree gets wrong: the Midnight dump
    types Renewing Blaze and Shield of Vengeance `passive` and both are things
    a player presses.

    Union rather than either one alone, so the answer only ever GROWS -- the
    same one-directional rule the spell registry runs on. Trusting `type` on its
    own silently dropped two abilities out of two players' habit lines the first
    time this was tried, which is exactly the failure mode a kit derived from
    presses already had.

    Baseline abilities are not here and never were; they are not in the tree.
    Callers add what the player was seen to press."""
    tal = set(info.get("talents") or ())
    return set(info.get("actives") or ()) | (tal & CLASSIFIED)


def role_of(spec):
    if spec in TANKS:
        return "tank"
    if spec in HEALERS:
        return "heal"
    return "dps"


def load_abilities():
    """spellID -> {name, class, flags, dispel, boss, zone}. Empty if absent."""
    if not ABILITIES.exists():
        return {}
    try:
        data = json.loads(ABILITIES.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    out = {}
    for zone in data.get("zones", []):
        for boss in zone.get("bosses", []):
            for ab in boss.get("abilities", []):
                out[str(ab["spellID"])] = {
                    "name": ab.get("name", ""), "class": ab.get("class", "C"),
                    "flags": set(ab.get("flags", [])), "dispel": ab.get("dispel", ""),
                    "boss": boss.get("boss", ""), "zone": zone.get("zone", ""),
                }
    return out


_TALENTS = None
_TALENT_NAMES = None


def load_talent_index():
    """traitEntryID -> (spell name, entry type, spellID), from Raidbots' dump.

    COMBATANT_INFO carries the exact loadout every player brought into the key.
    It is the only place this pipeline can learn what somebody OWNS rather than
    what they happened to press, and without it never touching a button exempts
    you from being graded on it -- the same self-fulfilling blind spot the
    dispel registry had.

    `type` is what makes the tree usable rather than merely present. Of the
    3,414 entries in the Midnight dump 2,691 are PASSIVE, and a passive can
    never appear in `run.casts` because there is no button behind it. Feeding
    those into an availability denominator prices a player for presses that do
    not exist, and listing them as "never pressed" fills the habit lines with
    things nobody could have pressed -- a Vengeance DH's Soul Barrier is a
    talent, applies an absorb 300 times a key, and is not a button. Only
    `active` entries are buttons; see `actives` in `collect._feed`.

    Missing or stale file degrades to an empty map, which restores the
    press-only behaviour rather than breaking anything."""
    global _TALENTS
    if _TALENTS is not None:
        return _TALENTS
    _TALENTS = {}
    path = DATA / "talents-live.json"
    if path.exists():
        try:
            for tree in json.loads(path.read_text(encoding="utf-8")):
                for arr in ("classNodes", "specNodes", "heroNodes", "subTreeNodes"):
                    for node in tree.get(arr) or []:
                        for entry in node.get("entries") or []:
                            if entry.get("name"):
                                _TALENTS[str(entry["id"])] = (
                                    entry["name"], entry.get("type"),
                                    entry.get("spellId"))
        except (ValueError, OSError, TypeError, KeyError):
            _TALENTS = {}
    return _TALENTS


def load_talents():
    """traitEntryID -> spell name. Every entry, passive included."""
    global _TALENT_NAMES
    if _TALENT_NAMES is None:
        _TALENT_NAMES = {e: v[0] for e, v in load_talent_index().items()}
    return _TALENT_NAMES


class Registry:
    """Spells the logs have PROVEN interruptible or dispellable.

    Proof is one-directional on purpose. Seeing a spell interrupted proves it is
    interruptible; never seeing it interrupted proves nothing at all, because the
    group may simply have had no kick up. So the registry only ever grows, and a
    spell it has not heard of is left OUT of opportunity counting rather than
    counted as a miss. The interrupt score therefore starts conservative and
    sharpens as more keys are fed through it."""

    def __init__(self, path=REGISTRY):
        self.path = path
        self.interruptible = {}
        self.dispellable = dict(SEED_DISPELLABLE)
        self.cooldowns = {}     # spell -> resolved seconds between casts
        self.samples = {}       # spell -> [observed sustained gaps]
        self.durations = {}     # spell -> longest observed buff duration
        if path.exists():
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
                self.interruptible = d.get("interruptible", {})
                self.dispellable.update(d.get("dispellable", {}))
                self.cooldowns = d.get("cooldowns", {})
                self.durations = d.get("durations", {})
                # Added after the fact, so an older file has none. Absent
                # samples degrade to the scalar already stored rather than
                # wiping every measured cooldown on first load -- a silent
                # reset here would quietly re-band every utility score.
                self.samples = {k: list(v) for k, v in
                                (d.get("cooldown_samples") or {}).items()}
            except (ValueError, OSError):
                pass
        self._before = (len(self.interruptible), len(self.dispellable))

    SAMPLE_CAP = 40
    PERCENTILE = 0.20

    def learn_cooldown(self, spell, gap):
        """Learn from a SAMPLE of observed sustained rates, not the record low.

        Nobody presses everything on cooldown, so any single run only gives an
        upper bound on the real cooldown, and the old rule -- keep the fastest
        gap ever seen, forever -- converged on the truth from above. It also
        never recovered from a single anomaly, and it had a systematic one:
        PROC RESETS. Divine Purpose and Judgment resets put two casts of a
        long-cooldown ability seconds apart, and the minimum latches onto that
        for good. The registry then believes the ability was available three
        times as often as it was, which inflates every `presses_available`
        denominator built on it and quietly deflates utility and mitigation for
        every player thereafter.

        A low percentile of a bounded sample keeps the "converge from above"
        property -- most observations still sit near the real cooldown -- while
        refusing to let one reset define it."""
        if not gap or gap <= 0:
            return
        s = self.samples.setdefault(spell, [])
        s.append(round(gap, 2))
        if len(s) > self.SAMPLE_CAP:
            # Keep the tightest observations: they are the ones that bound the
            # cooldown. Dropping the slowest keeps the sample informative as it
            # fills rather than letting one lazy key dominate it.
            s.sort()
            del s[self.SAMPLE_CAP:]
        self.cooldowns[spell] = self._resolve(s)

    @classmethod
    def _resolve(cls, sample):
        """The percentile value of the sample, or the minimum when the sample
        is too small for rejecting an outlier to mean anything."""
        if not sample:
            return None
        s = sorted(sample)
        if len(s) < 4:
            return round(s[0], 1)
        return round(s[int(cls.PERCENTILE * (len(s) - 1))], 1)

    def learn_duration(self, spell, secs):
        """Keep the longest MEDIAN buff duration seen across runs.

        The caller passes a median of fully-closed intervals, which is already
        robust to truncation; taking the max of those across runs then survives
        a key where the player died repeatedly. Capped at 60s because no
        personal defensive in the game runs longer, and anything claiming to
        is a parse artefact."""
        if secs and secs > 0:
            cur = self.durations.get(spell)
            secs = min(secs, 60.0)
            if cur is None or secs > cur:
                self.durations[spell] = round(secs, 1)

    def learn_interrupt(self, spell_id, name):
        if spell_id and spell_id not in self.interruptible:
            self.interruptible[spell_id] = name

    def learn_dispel(self, spell_id, name):
        if spell_id and spell_id not in self.dispellable:
            self.dispellable[spell_id] = name

    @property
    def gained(self):
        return (len(self.interruptible) - self._before[0],
                len(self.dispellable) - self._before[1])

    def save(self):
        self.path.write_text(json.dumps(
            {"interruptible": self.interruptible, "dispellable": self.dispellable,
             "cooldowns": self.cooldowns, "durations": self.durations,
             "cooldown_samples": self.samples},
            indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
