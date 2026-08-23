"""One pass over a key, producing raw facts. No judgement happens here.

The split between this and `derive` is deliberate: everything in this file is
something the log literally says, and everything in `derive` is an inference
drawn from it. When a grade looks wrong, that boundary is where you find out
whether the parser lied or the model did.
"""

import collections

from . import knowledge as K
from . import logfile as L


class Run:
    def __init__(self, name, level, timed, dur, key=None, light=False):
        # `light` builds only what the registry needs to learn from -- casts,
        # auras, interrupts, dispels -- and drops the per-event detail. The
        # learning pre-pass reads every key in the night before grading any of
        # them, and holding six keys' worth of taken_events at once is what
        # made that pass expensive in memory rather than just in time.
        self.light = light
        self.name, self.level, self.timed, self.dur = name, level, timed, dur
        self.key = key                  # logfile.Key: score, rating, timer, par
        self.t0 = key.start_t if key else None   # the KEY clock, not first combat
        self.players = {}                                   # guid -> info
        self.pets = {}                                      # pet guid -> owner guid
        self.dmg_done = collections.Counter()
        self.dmg_support = collections.Counter()
        self.dmg_taken = collections.Counter()
        self.self_dmg = collections.Counter()
        # Damage eaten by ANY shield on this player, read off the damage events
        # rather than off SPELL_ABSORBED. Scoring does not use it -- absorb_given
        # / self_absorb / absorb_taken split the same total by who cast the
        # shield, which is the question the healer axes ask. Kept because the
        # two totals are derived independently and disagreeing is a parser bug.
        self.absorbed_self = collections.Counter()
        self.overkill = collections.Counter()               # damage past zero health
        self.taken_by_spell = collections.defaultdict(collections.Counter)  # guid->spell->amt
        self.ticks_by_spell = collections.defaultdict(collections.Counter)  # guid->spell->n
        self.taken_events = collections.defaultdict(list)   # guid -> [(t, amt, spell, src)]
        self.health = collections.defaultdict(list)         # guid -> [(t, fraction)]
        self.dmg_to_target = collections.defaultdict(collections.Counter)   # guid->mob->amt
        self.healed = collections.Counter()
        self.overheal = collections.Counter()
        self.self_heal = collections.Counter()
        self.heal_taken = collections.Counter()
        self.heal_times = collections.defaultdict(list)     # guid -> [(t, effective)]
        self.heal_events = collections.defaultdict(list)    # guid -> [(t, amt, healer)]
        self.absorb_given = collections.Counter()           # all their shields ate
        self.absorb_taken = collections.Counter()           # shielded BY someone else
        # Damage a player's OWN shields ate. Kept apart from absorb_given
        # because these two go on opposite sides of the healer's ratio, and
        # apart from self_heal because absorbed damage never lands and so is
        # not in dmg_taken at all -- subtracting it from intake, as an earlier
        # attempt at this did, removes damage that was never there.
        self.self_absorb = collections.Counter()
        self.deaths = collections.Counter()
        self.unconscious = collections.Counter()            # went down, did not die
        self.death_events = []                              # (t, guid, killer, spell)
        self.dead_from = collections.defaultdict(list)
        self.casts = collections.defaultdict(list)          # guid -> [(t, spell)]
        self.cast_start = collections.defaultdict(list)     # guid -> [(t, spell)]
        self.interrupts = []                                # (t, guid, spellid, name)
        self.dispels = []                                   # (t, guid, spellid, name, dest)
        # Split out on purpose. Spellstealing an enemy buff is real utility but
        # it is not a cleanse, and letting it count as a dispel ATTEMPT would let
        # a healer who never touched a friendly debuff look like they tried.
        self.friendly_dispels = []
        self.enemy_start = []                               # (t, srcguid, spellid, name)
        # Only the TIMESTAMPS are kept. A bare count of successful enemy casts
        # was collected alongside these for a long time and never read once --
        # every question about a leaked cast needs to know WHEN it landed, to
        # pair it against the cast start.
        self.enemy_success_t = collections.defaultdict(list)
        self.debuffs = []                                   # (t, spellid, name, destguid)
        self.debuff_removed = collections.defaultdict(list)  # (spellid,dest) -> [t]
        self.self_aura = collections.defaultdict(lambda: collections.defaultdict(list))
        self.ext_casts = []                                 # (t, guid, spell, destguid)
        self.cc_on_enemy = []                               # (t, guid, spell, destguid)
        self.stacking = collections.defaultdict(set)        # guid -> stacking debuffs
        self.fight = []                                     # damage timestamps
        self.trash_kills = 0
        self.kill_times = []      # when each enemy died, for per-pull sizing
        self.bosses = []          # (start, end, name, killed)
        self._boss_open = None
        self.mana_low, self.mana_max = {}, {}
        # Resource generated and THROWN AWAY. A Holy Paladin sitting at 5 Holy
        # Power while Holy Shock refunds another one is spending globals to
        # produce nothing, and no amount of HPS makes that visible.
        self.gained = collections.defaultdict(collections.Counter)   # guid->pw->amt
        self.wasted = collections.defaultdict(collections.Counter)   # guid->pw->amt
        # Why a cast did not happen. "Not yet recovered" is the interesting one:
        # it is the button being mashed while it is on cooldown, which is a
        # habit rather than an accident.
        self.failed = collections.defaultdict(collections.Counter)   # guid->reason->n
        self.failed_spell = collections.defaultdict(collections.Counter)
        self.resurrects = []      # (t, caster, target, spell)
        self.pulls = []           # filled by derive.pull_structure

    def name_of(self, guid):
        p = self.players.get(guid)
        return p["name"] if p else guid


def _num(fields, idx):
    try:
        return int(fields[idx])
    except (ValueError, IndexError):
        return None


def _float(fields, idx):
    try:
        return float(fields[idx])
    except (ValueError, IndexError, TypeError):
        return 0.0


# What the registry pre-pass actually reads. Everything else is skipped before
# the line is even split.
LIGHT_EVENTS = frozenset((
    "COMBATANT_INFO", "SPELL_SUMMON", "SPELL_INTERRUPT", "SPELL_DISPEL",
    "SPELL_STOLEN", "SPELL_CAST_SUCCESS",
    "SPELL_AURA_APPLIED", "SPELL_AURA_REFRESH", "SPELL_AURA_REMOVED"))


def stream(path, keys, registry, light=False):
    """Yield (key, Run) for each key, reading the log ONCE.

    Grading a night of six keys used to re-read the whole file twelve times --
    once per key to learn the registry and once per key to grade -- so a 365 MB
    log cost four and a half gigabytes of I/O to answer a question about six
    stretches of it. The keys are non-overlapping and in file order, so one
    pass can fill them in turn; the caller grades each Run as it arrives and
    lets it go, which keeps only one key in memory at a time."""
    keys = [k for k in keys if k.start_line and k.end_line]
    if not keys:
        return
    lo, hi = keys[0].start_line, keys[-1].end_line
    idx, cur, seen = 0, None, {}
    for t, ev, f in L.events(path, lo, hi, keys[0].start_byte,
                             LIGHT_EVENTS if light else None):
        # `n` is not tracked here -- boundaries are timestamps, which is enough
        # because a key's events are contiguous. Advance past any key that
        # ended before this line.
        while idx < len(keys) and keys[idx].end_t is not None and t > keys[idx].end_t:
            if cur is not None:
                yield keys[idx], cur
                cur = None
            idx += 1
            seen = {}
        if idx >= len(keys):
            break
        k = keys[idx]
        if t < k.start_t:
            continue                       # between keys: town, or a wipe walk
        if cur is None:
            cur = Run(k.name, k.level, k.timed, k.seconds, k, light)
        _feed(cur, t, ev, f, registry, seen)
    if cur is not None and idx < len(keys):
        yield keys[idx], cur


def collect(path, start, end, name, level, timed, dur, registry, key=None,
            light=False):
    r = Run(name, level, timed, dur, key, light)
    seen_name = {}

    for t, ev, f in L.events(path, start, end,
                             key.start_byte if key is not None else None,
                             LIGHT_EVENTS if light else None):
        _feed(r, t, ev, f, registry, seen_name)
    return r


def _feed(r, t, ev, f, registry, seen_name):
    """One parsed line into one Run. Split out of the loop so a single pass
    over the file can feed several keys in turn."""
    if ev == "COMBATANT_INFO":
        guid = f[1]
        spec = _num(f, 25) or 0
        ilvls = L.item_levels(f[28]) if len(f) > 28 else []
        idx = K.load_talent_index()
        picked = [idx[e] for e in (L.talent_entries(f[26]) if len(f) > 26 else [])
                  if e in idx]
        r.players[guid] = {
            "name": seen_name.get(guid, guid), "spec": spec,
            "role": K.role_of(spec),
            "ilvl": sum(ilvls) / len(ilvls) if ilvls else 0.0,
            # The whole loadout, passives included -- a passive is worth naming
            # when a report explains WHY somebody took less damage.
            "talents": {nm for nm, _ty, _sp in picked},
            # The pressable half. Everything downstream that asks "could this
            # player have done X" wants this one: only an active entry can ever
            # appear in `run.casts`, so only an active belongs in a denominator
            # or in a list of buttons that never came off the bar.
            "actives": {nm for nm, ty, _sp in picked if ty == "active"},
        }
        return

    # Bosses inside the key. These lines carry six fields, so they have to be
    # read before the length guard below -- and the split is worth having:
    # in one Ruby Life Pools key all three bosses died first pull with zero
    # deaths, and every one of the nine deaths happened on trash. That is the
    # whole story of the run, and nothing else in the report showed it.
    if ev == "ENCOUNTER_START" and len(f) > 2:
        r._boss_open = (t, f[2])
        return
    if ev == "ENCOUNTER_END" and len(f) > 5:
        if r._boss_open:
            r.bosses.append((r._boss_open[0], t, r._boss_open[1], f[5] == "1"))
            r._boss_open = None
        return

    if len(f) < 8:
        return

    src, dst = f[1], f[5]
    # Names are learned opportunistically; COMBATANT_INFO carries no name.
    if src.startswith("Player-"):
        seen_name[src] = f[2].split("-")[0]
        if src in r.players:
            r.players[src]["name"] = seen_name[src]
    if dst.startswith("Player-"):
        seen_name[dst] = f[6].split("-")[0]
        if dst in r.players:
            r.players[dst]["name"] = seen_name[dst]

    sid, sname = L.spell_fields(ev, f)

    # Deliberately OUTSIDE the dispatch below: a pet's damage event both
    # teaches ownership and has to go on to be counted as damage. Folding
    # this into the elif chain silently ate every pet's contribution.
    if src.startswith("Pet-") and src not in r.pets and (
            ev in L.DAMAGE_EVENTS or ev in L.HEAL_EVENTS
            or ev == "SPELL_CAST_SUCCESS"):
        own = L.owner_guid(ev, f)
        if own:
            r.pets[src] = own

    if ev == "SPELL_SUMMON" and src.startswith("Player-"):
        r.pets[dst] = src

    elif ev in L.DAMAGE_EVENTS:
        amt, over, absorbed = L.damage_fields(f)
        if amt is None:
            return
        owner = r.pets.get(src, src)
        if owner.startswith("Player-") and L.hostile(f[7]):
            if ev.endswith("_SUPPORT"):
                r.dmg_support[owner] += amt
            else:
                r.dmg_done[owner] += amt
                r.dmg_to_target[owner][f[6].split("-")[0]] += amt
            r.fight.append(t)
        elif dst.startswith("Player-"):
            r.dmg_taken[dst] += amt
            r.taken_events[dst].append((t, amt, sname, f[2].split("-")[0], over))
            hp = L.health_fields(ev, f)
            if hp:
                r.health[dst].append((t, hp[0] / hp[1]))
            r.absorbed_self[dst] += absorbed
            if over > 0:
                # Damage past zero health. Nobody heals it and nobody could
                # have, so it does not belong in a healer's denominator.
                r.overkill[dst] += min(over, amt)
            if src != dst:
                # Self-sourced damage is deliberately kept OUT of the
                # per-spell tallies, because those feed the avoidable-damage
                # analysis. A Holy Paladin running Blessing of Dawn takes 258
                # hits from their own talent in a key, and every one of them
                # read as "stood in Blessing of Dawn" until this split.
                r.taken_by_spell[dst][sname] += amt
                r.ticks_by_spell[dst][sname] += 1
            if src == dst:
                # Self-sourced: talent redirects like Blessing of Dawn, or
                # Refraction. Charging a player's avoidance for a talent
                # doing its job is a bug, so keep it separable.
                r.self_dmg[dst] += amt
            if L.hostile(f[3]):
                r.fight.append(t)

    elif ev in L.HEAL_EVENTS:
        eff, over, _ab = L.heal_fields(f)
        if eff is None:
            return
        owner = r.pets.get(src, src)
        if not owner.startswith("Player-"):
            return
        r.healed[owner] += eff
        r.overheal[owner] += over
        r.heal_times[owner].append((t, eff))
        if dst.startswith("Player-"):
            r.heal_taken[dst] += eff
            # Kept per-target and timestamped so a death can be asked the
            # question the raw total cannot answer: was the healer there.
            # Warcraft Logs prints this next to every death and this tool
            # did not, which is the difference between "the healer was
            # short" and "nothing arrived at all".
            r.heal_events[dst].append((t, eff, owner))
        if src == dst:
            r.self_heal[owner] += eff

    elif ev == "SPELL_ABSORBED":
        caster, amt = L.absorb_fields(f)
        if caster and amt and caster.startswith("Player-"):
            r.absorb_given[caster] += amt
            if dst.startswith("Player-"):
                if caster == dst:
                    r.self_absorb[dst] += amt
                else:
                    r.absorb_taken[dst] += amt

    elif ev == "UNIT_DIED":
        if dst.startswith("Player-"):
            # The trailing field is `unconsciousOnDeath`. A 1 means the unit
            # went down without actually dying, and the GAME does not count
            # it: three of the five player UNIT_DIED in one Blinding Vale
            # +8 carried it, and Blizzard's own timer charged the key for
            # two deaths, not five. Counting them cost real survival grades.
            if len(f) > 9 and f[9] == "1":
                r.unconscious[dst] += 1
            else:
                r.deaths[dst] += 1
                r.dead_from[dst].append(t)
                r.death_events.append((t, dst))
        elif src in r.pets or dst in r.pets:
            pass                                    # a pet, not an enemy
        elif L.hostile(f[7]):
            r.trash_kills += 1
            r.kill_times.append(t)

    elif ev == "SPELL_ENERGIZE" and src.startswith("Player-"):
        # tail: amount, overEnergize, powerType, maxPower. The overflow is
        # the whole point -- `amount` alone says a resource arrived, not
        # that it was usable.
        #
        # These two are written as FLOATS ("8.0000"), unlike the integer
        # amounts on every other event. int() throws on that and quietly
        # yields zero, which reads as a player who never wasted a single
        # Holy Power all night.
        amt, over = _float(f, -4), _float(f, -3)
        pw = f[-2] if len(f) > 2 else "?"
        r.gained[src][pw] += amt
        if over:
            r.wasted[src][pw] += over

    elif ev == "SPELL_CAST_FAILED" and src.startswith("Player-"):
        r.failed[src][f[-1]] += 1
        if f[-1] == "Not yet recovered":
            r.failed_spell[src][sname] += 1

    elif ev == "SPELL_RESURRECT" and src.startswith("Player-"):
        r.resurrects.append((t, src, dst, sname))

    elif ev == "SPELL_INTERRUPT":
        # The INTERRUPTED spell is f[13]; f[10] is the button that did it.
        #
        # Credited to the OWNER, not the caster. A warlock's felhunter, a
        # hunter's pet and an unholy DK's ghoul all carry a `Pet-...` GUID
        # that is not in the party roster, so gating on "Player-" dropped
        # every Spell Lock in the log: one Destruction Warlock was scored 0
        # stops against 14 expected while his pet landed 15, which cost him
        # a whole letter grade for a button he was pressing.
        owner = r.pets.get(src, src)
        if owner.startswith("Player-") and len(f) > 13:
            r.interrupts.append((t, owner, f[12], f[13]))
            registry.learn_interrupt(f[12], f[13])

    elif ev in ("SPELL_DISPEL", "SPELL_STOLEN") and src.startswith("Player-"):
        # sname here is the CASTING spell. Gating on it is what keeps a
        # druid shifting out of a root from being counted as a cleanse --
        # see knowledge.DISPEL_SPELLS.
        if len(f) > 13 and sname in K.DISPEL_SPELLS:
            r.dispels.append((t, src, f[12], f[13], dst))
            if L.friendly(f[7]):
                r.friendly_dispels.append((t, src, f[12], f[13], dst))
                registry.learn_dispel(f[12], f[13])

    elif ev == "SPELL_CAST_START":
        if src.startswith("Player-"):
            r.cast_start[src].append((t, sname))
        elif L.hostile(f[3]):
            r.enemy_start.append((t, src, sid, sname))

    elif ev == "SPELL_CAST_SUCCESS":
        if src.startswith("Player-"):
            r.casts[src].append((t, sname))
            if sname in K.EXTERNALS or sname in K.RAID_COOLDOWNS:
                r.ext_casts.append((t, src, sname, dst))
            # powerType can be PIPE-SEPARATED when a cast touches more than
            # one resource: a Holy Paladin logs "9|0" on 127 casts in a key.
            # currentPower and maxPower are then lists too, so the parts have
            # to be lined up rather than read as scalars.
            pw = f[-9].split("|") if len(f) > 9 else []
            if "0" in pw:
                i = pw.index("0")
                cur = _num(f[-8].split("|"), i)
                mx = _num(f[-7].split("|"), i)
                if cur is not None and mx:
                    # Max mana RISES during a key -- an Arcane Mage's went from
                    # 368,485 to 436,465 across one. Keeping the last value read
                    # dated the floor against a ceiling that did not exist yet.
                    r.mana_max[src] = max(r.mana_max.get(src, 0), mx)
                    r.mana_low[src] = min(r.mana_low.get(src, cur), cur)
        elif L.hostile(f[3]):
            r.enemy_success_t[(src, sid)].append(t)

    elif ev == "SPELL_AURA_APPLIED_DOSE" and dst.startswith("Player-"):
        # A stacking debuff. Worth separating because the damage it does is
        # a debuff-pressure problem, not a positioning one: the tank ate 142
        # Spore Spines ticks off 9 applications, which is the mechanic doing
        # its job rather than the tank standing somewhere stupid.
        r.stacking[dst].add(sname)

    elif ev in ("SPELL_AURA_APPLIED", "SPELL_AURA_REFRESH", "SPELL_AURA_REMOVED"):
        if len(f) < 13:
            return
        kind = f[12]
        if kind == "DEBUFF" and L.hostile(f[3]) and dst.startswith("Player-"):
            if ev == "SPELL_AURA_REMOVED":
                r.debuff_removed[(sid, dst)].append(t)
            else:
                r.debuffs.append((t, sid, sname, dst))
        elif kind == "DEBUFF" and src.startswith("Player-") and L.hostile(f[7]):
            # A stun that stops a cast did the same job as a kick. Specs with
            # no interrupt on the bar stop casts this way, and grading them
            # on a button they do not have is how a healer ends up with a
            # zero in a column that was never theirs to fill.
            if ev != "SPELL_AURA_REMOVED" and sname in K.CC:
                r.cc_on_enemy.append((t, src, sname, dst))
        elif kind == "BUFF" and src == dst and src.startswith("Player-"):
            r.self_aura[src][sname].append((t, ev))
