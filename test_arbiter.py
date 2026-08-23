#!/usr/bin/env python3
"""Regression tests for the M+ grading model.

    python test_arbiter.py

Every test here pins down a rule that was added because the naive version of the
same question produced a confident wrong answer on real logs. The comment on
each one names the wrong answer it prevents, so a future edit that breaks a test
can tell whether it is re-introducing a known bug or deliberately changing the
model.

Synthetic fixtures on purpose: these have to run without a 1 GB combat log on
disk, and a rule is easier to trust when the input fits on a screen.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from arbiter import collect, derive as D, dungeons as DUN, knowledge as K  # noqa: E402
from arbiter import logfile as L, score  # noqa: E402


# --------------------------------------------------------------------------
# synthetic log construction
# --------------------------------------------------------------------------
PLAYER = "Player-1-0000000{}"
MOB = "Creature-0-1-1-1-{}-000000000{}"
P_FLAGS, HOSTILE_FLAGS = "0x512", "0xa48"


def line(clock, event, *fields, date="8/19/2026"):
    return f"{date} {clock}-7  {event}," + ",".join(str(f) for f in fields) + "\n"


def unit(guid, name, flags):
    return [guid, f'"{name}"', flags, "0x0"]


def combatant(guid, spec, ilvl=290):
    gear = "[" + ",".join(f"(1000{i},{ilvl},(0,0,0),(),())" for i in range(18)) + "]"
    stats = ["1"] + ["100"] * 22          # f2..f24, so specID lands on f[25]
    return line("20:00:00.000", "COMBATANT_INFO", guid, *stats, spec, "[]", "()", gear)


def damage(clock, src, sname, dst, dname, spell, amount, overkill=-1,
           src_flags=HOSTILE_FLAGS, dst_flags=P_FLAGS, ev="SPELL_DAMAGE",
           base=None, absorbed=0, owner="0000000000000000"):
    """A SPELL_DAMAGE line with the REAL tail shape.

    `base` defaults to something other than `amount` deliberately. An earlier
    fixture wrote a tail that had neither a baseAmount column nor the trailing
    ST/AOE marker, so the tests agreed with a parser that was reading
    pre-mitigation damage on every real log it touched, and agreed with it for
    months. A fixture that cannot tell the two columns apart cannot defend the
    offsets, so this one makes them differ.

    tail: amount, baseAmount, overkill, school, resisted, blocked, absorbed,
          critical, glancing, crushing, marker"""
    if base is None:
        base = amount + 7
    tail = [amount, base, overkill, 1, 0, 0, absorbed, "nil", "nil", "nil", "ST"]
    return line(clock, ev, *unit(src, sname, src_flags), *unit(dst, dname, dst_flags),
                spell[0], f'"{spell[1]}"', "0x1", src, owner, "1000", "1000",
                "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", *tail)


def swing(clock, src, sname, dst, dname, amount, overkill=-1,
          src_flags=HOSTILE_FLAGS, dst_flags=P_FLAGS, ev="SWING_DAMAGE"):
    """A swing carries no spellId/name/school AND no trailing marker, so its
    tail sits three fields earlier and one field later than a spell's. Reading
    both with one offset is what flagged every melee swing in a night as a
    killing blow."""
    tail = [amount, amount + 7, overkill, 1, 0, 0, 0, "nil", "nil", "nil"]
    return line(clock, ev, *unit(src, sname, src_flags), *unit(dst, dname, dst_flags),
                src, "0000000000000000", "1000", "1000",
                "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", *tail)


def heal_line(clock, src, sname, dst, dname, spell, amount, overheal=0, absorbed=0):
    """tail: amount, baseAmount, overhealing, absorbed, critical."""
    tail = [amount, amount, overheal, absorbed, "nil"]
    return line(clock, "SPELL_HEAL", *unit(src, sname, P_FLAGS),
                *unit(dst, dname, P_FLAGS), spell[0], f'"{spell[1]}"', "0x2",
                src, "0000000000000000", "1000", "1000",
                "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", *tail)


def absorbed_line(clock, attacker, aname, dst, dname, shield_caster, cname,
                  spell, amount):
    """SPELL_ABSORBED. The shield's CASTER is f[-10] and its NAME is f[-9];
    reading the name as a GUID scored every absorb in the log as zero."""
    return line(clock, "SPELL_ABSORBED", *unit(attacker, aname, HOSTILE_FLAGS),
                *unit(dst, dname, P_FLAGS), "1111", '"Big Hit"', "0x1",
                shield_caster, f'"{cname}"', P_FLAGS, "0x0",
                spell[0], f'"{spell[1]}"', "0x2", amount, amount * 2, "nil")


def cast(clock, guid, name, spell):
    return line(clock, "SPELL_CAST_SUCCESS", *unit(guid, name, P_FLAGS),
                "0000000000000000", "nil", "0x0", "0x0", spell[0], f'"{spell[1]}"',
                "0x1", guid, "0000000000000000", "1000", "1000",
                "0", "0", "0", "0", "0", "0", "0", "5", "5", "0", "1.0", "2.0",
                "0", "0.0", "80")


def write_log(lines):
    fh = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    fh.write(line("19:59:00.000", "CHALLENGE_MODE_START", '"Test Dungeon"', "1", "1", "10"))
    fh.writelines(lines)
    fh.write(line("20:30:00.000", "CHALLENGE_MODE_END", "1", "1", "10", "1800000"))
    fh.close()
    return fh.name


# --------------------------------------------------------------------------
class ParsingTests(unittest.TestCase):
    def test_event_name_split_on_two_spaces(self):
        # ONE space is the timestamp's own separator. Splitting on a comma, or
        # on a single space, silently matches nothing.
        self.assertEqual(L.body_of("8/19/2026 20:00:00.000-7  SPELL_HEAL,x")[:11],
                         "SPELL_HEAL,"[:11])

    def test_stamp_crosses_midnight(self):
        # A clock-only parser reports a NEGATIVE duration for a key started at
        # 23:50, then divides by it.
        before = L.stamp("8/19/2026 23:50:00.000-7  X")
        after = L.stamp("8/20/2026 00:05:00.000-7  X")
        self.assertAlmostEqual(after - before, 900.0, places=1)

    def test_split_respects_quotes_and_nesting(self):
        f = L.split('A,"Name-Realm, Jr",0x1,[(1,2),(3,4)],end')
        self.assertEqual(f, ["A", "Name-Realm, Jr", "0x1", "[(1,2),(3,4)]", "end"])

    def test_item_levels_skip_empty_slots(self):
        # Shirt and tabard log as ilvl 1 and drag a 290 average to 266.
        blob = "[(1,290,(0,0,0),(),()),(2,1,(0,0,0),(),()),(3,296,(0,0,0),(),())]"
        self.assertEqual(L.item_levels(blob), [290, 296])

    def test_swing_has_no_spell_fields(self):
        # Reading f[10] on a swing yields a GUID, i.e. a spell named
        # "Player-3684-...".
        self.assertEqual(L.spell_fields("SWING_DAMAGE", ["x"] * 20)[1], "Melee")

    def test_hostile_flag_beats_guid_prefix(self):
        # A mage's Mirror Images are Creature- and cast Frostbolt; 107 of them
        # became phantom interrupt opportunities before flags were used.
        self.assertTrue(L.hostile(HOSTILE_FLAGS))
        self.assertFalse(L.hostile(P_FLAGS))


class GcdTests(unittest.TestCase):
    def test_drops_proc_storm_keeps_the_button(self):
        # Empyrean Hammer fires 4x/second on top of Hammer of Light. Blaming
        # both drops the button; the proc has to lose the tie.
        seq = []
        t = 0.0
        for _ in range(60):
            seq.append((t, "Real Button"))
            for k in range(4):
                seq.append((t + 0.05 * k, "Proc Storm"))
            t += 1.2
        banned = D.gcd_set(sorted(seq), minutes=t / 60)
        self.assertIn("Proc Storm", banned)
        self.assertNotIn("Real Button", banned)

    def test_does_not_fire_below_the_physical_cap(self):
        # An Arcane Mage at 29 casts/min never violated the GCD. An earlier
        # version stripped Arcane Blast and Missiles anyway and reported 5/min.
        seq = [(i * 2.0, "Arcane Blast" if i % 2 else "Arcane Missiles")
               for i in range(60)]
        self.assertEqual(D.gcd_set(seq, minutes=2.0), set())

    def test_sustained_gap_prices_charges(self):
        # Two charges dumped back to back are not a 1-second cooldown. The
        # estimate stays BELOW the true 15s sustainable rate because the opening
        # pair comes free out of stored charges -- it is a lower bound by
        # construction, and the point is that it is nowhere near the naive 1.0.
        times = [0.0, 1.0, 30.0, 31.0, 60.0, 61.0]
        naive = min(b - a for a, b in zip(times, times[1:]))
        self.assertEqual(naive, 1.0)
        self.assertGreater(D.sustained_gap(times, k=3), 8.0)


class AuraTests(unittest.TestCase):
    def test_refresh_without_removal_is_one_interval(self):
        # Zipping applications against removals overcounts badly here.
        evs = [(0.0, "SPELL_AURA_APPLIED"), (3.0, "SPELL_AURA_REFRESH"),
               (9.0, "SPELL_AURA_REMOVED")]
        self.assertEqual(D.aura_intervals(evs, 100.0), [(0.0, 9.0)])

    def test_unclosed_aura_runs_to_the_cap(self):
        evs = [(0.0, "SPELL_AURA_APPLIED")]
        self.assertEqual(D.aura_intervals(evs, 12.0), [(0.0, 12.0)])


class SurvivalTests(unittest.TestCase):
    def test_half_a_death_is_not_a_clean_sheet(self):
        # Python rounds 0.5 to 0. Indexing the curve gave a player whose single
        # death was discounted as part of a wipe a flat 100.
        self.assertEqual(score.survival_score(0.0), 100.0)
        self.assertLess(score.survival_score(0.5), 100.0)
        self.assertGreater(score.survival_score(0.5), score.survival_score(1.0))

    def test_curve_is_monotonic_and_bounded(self):
        vals = [score.survival_score(d / 4) for d in range(0, 20)]
        self.assertEqual(vals, sorted(vals, reverse=True))
        self.assertGreaterEqual(min(vals), 28.0)


class AvailabilityTests(unittest.TestCase):
    def test_spammable_ability_has_no_denominator(self):
        # Spellsteal has effectively no cooldown; pricing it told an Arcane Mage
        # he had 462 utility presses available and had used six.
        reg = K.Registry.__new__(K.Registry)
        reg.cooldowns = {"Spellsteal": 1.2, "Aura Mastery": 180.0}
        self.assertIsNone(D.presses_available("Spellsteal", 1800, reg))
        self.assertAlmostEqual(D.presses_available("Aura Mastery", 1800, reg), 10.0)

    def test_unknown_ability_has_no_denominator(self):
        reg = K.Registry.__new__(K.Registry)
        reg.cooldowns = {}
        self.assertIsNone(D.presses_available("Mystery", 1800, reg))


class DispelTests(unittest.TestCase):
    def test_root_break_is_not_a_dispel(self):
        # Cat Form, Blink and Ice Block all "dispel" a root off their own caster.
        # One Blinding Vale key's entire dispel activity was a single Cat Form.
        self.assertNotIn("Cat Form", K.DISPEL_SPELLS)
        self.assertNotIn("Blink", K.DISPEL_SPELLS)
        self.assertIn("Cleanse", K.DISPEL_SPELLS)
        self.assertIn("Cleanse Toxins", K.DISPEL_SPELLS)


class DamageFieldTests(unittest.TestCase):
    """The damage tail. Every one of these was a confident wrong answer that
    survived because the offsets were counted from the end with a single number
    and the two candidate columns both look like damage."""

    def test_spell_damage_reads_amount_not_base(self):
        # f[-10] on a SPELL_DAMAGE is baseAmount -- damage BEFORE armour and
        # mitigation, and before the crit multiplier. Reading it inflated every
        # damage-taken figure in the pipeline by about 45% and DEFLATED every
        # damage-done figure, because a crit's amount is double its base.
        ln = damage("20:00:00.000", "Creature-1", "Mob", PLAYER.format(1), "P",
                    (1, "Axegrinder"), 918057, base=1282592, absorbed=118773)
        f = L.split(L.body_of(ln))
        amt, over, absorbed = L.damage_fields(f)
        self.assertEqual(amt, 918057)
        self.assertEqual(absorbed, 118773)
        self.assertNotEqual(amt, 1282592)

    def test_swing_tail_sits_one_field_later_than_a_spell(self):
        # A swing has no trailing ST/AOE marker, so a spell's offsets read
        # baseAmount as overkill -- and baseAmount is always positive, so every
        # melee swing in the log came back flagged as a killing blow. One night
        # produced 52,547 "killing blows" against 920 real ones.
        ln = swing("20:00:00.000", "Creature-1", "Mob", PLAYER.format(1), "P", 13822)
        f = L.split(L.body_of(ln))
        amt, over, _ab = L.damage_fields(f)
        self.assertEqual(amt, 13822)
        self.assertEqual(over, -1)

    def test_overkill_never_exceeds_the_hit_that_caused_it(self):
        # The invariant that told the two candidate layouts apart on real logs:
        # the old offsets violate it 36,406 times in a single night, the
        # current ones zero times.
        for ln in (damage("20:00:00.000", "Creature-1", "Mob", PLAYER.format(1),
                          "P", (1, "Hit"), 500, overkill=120, base=900),
                   swing("20:00:00.000", "Creature-1", "Mob", PLAYER.format(1),
                         "P", 500, overkill=120)):
            amt, over, _ab = L.damage_fields(L.split(L.body_of(ln)))
            self.assertGreater(over, 0)
            self.assertLessEqual(over, amt)

    def test_one_bad_field_does_not_delete_the_hit(self):
        ln = damage("20:00:00.000", "Creature-1", "Mob", PLAYER.format(1), "P",
                    (1, "Hit"), 4242, absorbed="nil")
        amt, _o, absorbed = L.damage_fields(L.split(L.body_of(ln)))
        self.assertEqual(amt, 4242)
        self.assertEqual(absorbed, 0)

    def test_absorb_credits_the_caster_guid_not_the_display_name(self):
        # f[-9] is the shield caster's NAME. Testing a display name against
        # "Player-" never matches, so absorb_given was empty on every log ever
        # run through this -- the entire shielding half of a Disc Priest or a
        # Preservation Evoker scored zero.
        ln = absorbed_line("20:00:00.000", "Creature-1", "Mob", PLAYER.format(1),
                           "Target", PLAYER.format(2), "Shielder",
                           (17, "Power Word: Shield"), 5000)
        caster, amt = L.absorb_fields(L.split(L.body_of(ln)))
        self.assertEqual(caster, PLAYER.format(2))
        self.assertEqual(amt, 5000)

    def test_crlf_does_not_ride_along_on_the_last_field(self):
        # These logs are CRLF, and seeking to a key's byte offset means reading
        # binary, which text mode would have cleaned up. Left on, the \r rides
        # on the LAST field: f[12] == "BUFF" became False for 27,797 of 30,256
        # aura events and every tank scored 0% mitigation uptime.
        body = L.body_of('8/21/2026 20:00:00.000-7  SPELL_AURA_APPLIED,a,b,c,d,'
                         'e,f,g,h,1,"X",0x1,BUFF\r\n')
        self.assertEqual(L.split(body)[12], "BUFF")


class KeyBoundaryTests(unittest.TestCase):
    """CHALLENGE_MODE_END is two different events wearing one name."""

    def _log(self, tail):
        fh = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                         encoding="utf-8")
        fh.write(line("19:59:00.000", "CHALLENGE_MODE_START", '"Murder Row"',
                      "2813", "587", "9", "[162,10]"))
        fh.write(line("20:15:00.000", "CHALLENGE_MODE_END", *tail))
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        return fh.name

    def test_level_comes_from_start_not_from_a_reset_end(self):
        # An abandoned key closes with an ALL-ZERO END. Reading the level off it
        # printed a +9 as "+0 depleted", and read `success` off an event that
        # says nothing about success.
        k = L.find_runs(self._log(("0", "0", "0", "0", "0.000000", "0.000000")))[0]
        self.assertEqual(k.level, 9)
        self.assertFalse(k.finished)
        self.assertFalse(k.timed)

    def test_real_end_carries_score_and_rating_not_par(self):
        # Fields 6 and 7 are the run's dungeon score and the character's TOTAL
        # M+ rating afterwards. A previous report read field 7 as "par seconds"
        # and published an upgrade column off it; the same dungeon showed
        # 1972.98 at +9 and 2107.59 at +10, and a par time does not move with
        # key level. Two keys were reported as +2. One was a +1.
        k = L.find_runs(self._log(("2813", "1", "8", "1563602", "283.757324",
                                   "2057.197754")))[0]
        self.assertTrue(k.finished)
        self.assertTrue(k.timed)
        self.assertEqual(k.level, 9)          # START is the authority
        self.assertAlmostEqual(k.timer_seconds, 1563.602, places=3)
        self.assertAlmostEqual(k.score, 283.757324, places=4)
        self.assertAlmostEqual(k.rating, 2057.197754, places=4)

    def test_counted_deaths_recovers_the_game_s_own_count(self):
        # Wall clock 1542.1s, timer 1563.6s, six deaths at five seconds each
        # less the ~8.4s countdown. This is the only cross-check on the death
        # count that does not come from the parser being checked.
        k = L.find_runs(self._log(("2813", "1", "8", "1563602", "1.0", "2.0")))[0]
        k.seconds = 1542.104
        self.assertAlmostEqual(L.counted_deaths(k), 6.0, delta=0.15)

    def test_upgrade_needs_a_par_time_and_says_so_without_one(self):
        self.assertIsNone(DUN.upgrade(1603.970, None, timed=True))
        self.assertEqual(DUN.upgrade(1026.919, 2040.0, timed=True), 3)   # 49.7%
        self.assertEqual(DUN.upgrade(1563.602, 1980.0, timed=True), 2)   # 21.0%
        self.assertEqual(DUN.upgrade(1554.235, 1800.0, timed=True), 1)   # 13.7%
        self.assertEqual(DUN.upgrade(1900.0, 1800.0, timed=False), 0)

    def test_bounds_narrow_from_observed_upgrades(self):
        # A +1 proves the clear was LESS than 20% under par, which caps par.
        t = DUN.bounds_from([("Altar of Fangs", 1862.674, True, None),
                             ("Altar of Fangs", 1603.970, True, 1)], table={})
        row = t["Altar of Fangs"]
        self.assertAlmostEqual(row["low"], 1862.674, places=1)
        self.assertAlmostEqual(row["high"], 1603.970 / 0.80, places=1)
        self.assertIsNone(DUN.par_for("Altar of Fangs", t))


class DeathCountTests(unittest.TestCase):
    def test_unconscious_is_not_a_death(self):
        # The trailing field of UNIT_DIED is `unconsciousOnDeath`. Three of the
        # five player UNIT_DIED in one Blinding Vale +8 carried it and the
        # game's own timer charged the key for two deaths, not five.
        guid = PLAYER.format(1)
        dead = line("20:00:00.000", "UNIT_DIED", "0000000000000000", "nil",
                    "0x80000000", "0x80000000", guid, '"P"', P_FLAGS,
                    "0x80000000", "0")
        down = line("20:00:10.000", "UNIT_DIED", "0000000000000000", "nil",
                    "0x80000000", "0x80000000", guid, '"P"', P_FLAGS,
                    "0x80000000", "1")
        path = write_log([combatant(guid, 65), dead, down])
        self.addCleanup(os.unlink, path)
        k = L.find_runs(path)[0]
        run = collect.collect(path, k.start_line, k.end_line, k.name, k.level,
                              k.timed, k.seconds, K.Registry(path=_TmpPath()), k)
        self.assertEqual(run.deaths[guid], 1)
        self.assertEqual(run.unconscious[guid], 1)
        self.assertEqual(len(run.death_events), 1)


class PetTests(unittest.TestCase):
    """A pet's GUID is not in the party roster, and its work is its owner's."""

    def _run(self, extra):
        owner = PLAYER.format(1)
        pet = "Pet-0-1-1-1-417-0000000001"
        mob = MOB.format(500, 1)
        lines = [combatant(owner, 267)] + extra(owner, pet, mob)
        path = write_log(lines)
        self.addCleanup(os.unlink, path)
        k = L.find_runs(path)[0]
        return owner, pet, collect.collect(
            path, k.start_line, k.end_line, k.name, k.level, k.timed,
            k.seconds, K.Registry(path=_TmpPath()), k)

    def test_pet_interrupt_is_credited_to_the_owner(self):
        # A felhunter's Spell Lock carries a Pet-... GUID, so gating on
        # "Player-" dropped all fifteen of them and scored the warlock 0 stops
        # against 14 expected -- a whole letter grade for a button he pressed.
        def build(owner, pet, mob):
            return [line("20:00:01.000", "SPELL_SUMMON", owner, '"Lock"', P_FLAGS,
                         "0x0", pet, '"Zhaamon"', P_FLAGS, "0x0", "691",
                         '"Summon Felhunter"', "0x20"),
                    line("20:00:05.000", "SPELL_INTERRUPT", pet, '"Zhaamon"',
                         P_FLAGS, "0x0", mob, '"Mob"', HOSTILE_FLAGS, "0x0",
                         "19647", '"Spell Lock"', "0x20", "999", '"Bad Cast"',
                         "0x1")]
        owner, pet, run = self._run(build)
        self.assertEqual(run.pets.get(pet), owner)
        self.assertEqual([g for _t, g, *_ in run.interrupts], [owner])

    def test_owner_is_learned_without_a_summon_in_range(self):
        # The warlock summons the pet in town, so SPELL_SUMMON sits OUTSIDE the
        # key and the pet arrives orphaned. Every damage event it does carries
        # the owner in the second advanced field.
        def build(owner, pet, mob):
            return [damage("20:00:05.000", pet, "Zhaamon", mob, "Mob",
                           (1, "Shadow Bite"), 1234, src_flags=P_FLAGS,
                           dst_flags=HOSTILE_FLAGS, owner=owner),
                    line("20:00:06.000", "SPELL_INTERRUPT", pet, '"Zhaamon"',
                         P_FLAGS, "0x0", mob, '"Mob"', HOSTILE_FLAGS, "0x0",
                         "19647", '"Spell Lock"', "0x20", "999", '"Bad Cast"',
                         "0x1")]
        owner, pet, run = self._run(build)
        self.assertEqual(run.pets.get(pet), owner)
        self.assertEqual(run.dmg_done[owner], 1234)
        self.assertEqual([g for _t, g, *_ in run.interrupts], [owner])

    def test_a_dead_pet_is_not_a_trash_kill(self):
        def build(owner, pet, mob):
            return [line("20:00:01.000", "SPELL_SUMMON", owner, '"Lock"', P_FLAGS,
                         "0x0", pet, '"Zhaamon"', P_FLAGS, "0x0", "691",
                         '"Summon Felhunter"', "0x20"),
                    line("20:00:09.000", "UNIT_DIED", "0000000000000000", "nil",
                         "0x80000000", "0x80000000", pet, '"Zhaamon"',
                         HOSTILE_FLAGS, "0x80000000", "0")]
        owner, pet, run = self._run(build)
        self.assertEqual(run.trash_kills, 0)


class HealingNeedTests(unittest.TestCase):
    """The healer's denominator -- the axis that made a report argue with
    itself, saying very little healing was needed and then marking the healer
    down for not doing much healing."""

    def _run(self, lines):
        path = write_log(lines)
        self.addCleanup(os.unlink, path)
        k = L.find_runs(path)[0]
        return collect.collect(path, k.start_line, k.end_line, k.name, k.level,
                               k.timed, k.seconds, K.Registry(path=_TmpPath()), k)

    def test_self_healing_leaves_the_denominator(self):
        # A Vengeance DH heals back sixty million a key. Counting it as damage
        # the healer failed to cover is how a self-sufficient party lowered the
        # healer's grade.
        tank, healer = PLAYER.format(1), PLAYER.format(2)
        mob = MOB.format(500, 1)
        run = self._run([
            combatant(tank, 581), combatant(healer, 65),
            damage("20:00:01.000", mob, "Mob", tank, "Tank", (1, "Swipe"), 1000),
            heal_line("20:00:02.000", tank, "Tank", tank, "Tank", (2, "Soul Cleave"), 600),
            heal_line("20:00:03.000", healer, "Heal", tank, "Tank", (3, "Flash"), 400),
        ])
        need = D.healing_need(run)
        self.assertEqual(need["gross"], 1000)
        self.assertEqual(need["need"], 400)              # not 1000
        self.assertEqual(D.healer_output(run, healer), 400)
        self.assertEqual(D.healer_output(run, tank), 0)  # self-healing is not output

    def test_overkill_leaves_the_denominator(self):
        # Damage past zero health was never healable by anyone.
        p, healer = PLAYER.format(1), PLAYER.format(2)
        mob = MOB.format(500, 1)
        run = self._run([
            combatant(p, 70), combatant(healer, 65),
            damage("20:00:01.000", mob, "Mob", p, "P", (1, "Gib"), 1000, overkill=400),
        ])
        self.assertEqual(D.healing_need(run)["need"], 600)

    def test_absorbed_damage_is_not_subtracted_from_intake(self):
        # `amount` is already post-absorb, so absorbed damage is not in
        # dmg_taken and must not come out of it again. Subtracting it removed
        # damage that was never there and drove the denominator to nearly zero.
        p, healer = PLAYER.format(1), PLAYER.format(2)
        mob = MOB.format(500, 1)
        run = self._run([
            combatant(p, 70), combatant(healer, 65),
            damage("20:00:01.000", mob, "Mob", p, "P", (1, "Hit"), 700, absorbed=300),
            absorbed_line("20:00:01.000", mob, "Mob", p, "P", healer, "Heal",
                          (17, "Shield"), 300),
        ])
        need = D.healing_need(run)
        self.assertEqual(need["gross"], 700)
        # 700 landed and needed healing, plus 300 an outside shield prevented.
        self.assertEqual(need["need"], 1000)
        self.assertEqual(D.healer_output(run, healer), 300)


class DeathWindowTests(unittest.TestCase):
    """The stretch that actually killed someone, read off their health rather
    than guessed at with a fixed eight seconds."""

    def _forensics(self, lines, guid, died="20:00:07.100"):
        died_at = lines[-1].split("  ", 1)[0].split(" ", 1)[1].split("-")[0]
        h, m, s = died_at.split(":")
        died = f"{h}:{m}:{float(s) + 0.1:06.3f}"
        path = write_log([combatant(guid, 70), combatant(PLAYER.format(9), 65)]
                         + lines
                         + [line(died, "UNIT_DIED", "0000000000000000", "nil",
                                 "0x80000000", "0x80000000", guid, '"P"',
                                 P_FLAGS, "0x80000000", "0")])
        self.addCleanup(os.unlink, path)
        k = L.find_runs(path)[0]
        run = collect.collect(path, k.start_line, k.end_line, k.name, k.level,
                              k.timed, k.seconds, K.Registry(path=_TmpPath()), k)
        return D.death_forensics(run, D.combat_windows(run.fight),
                                 {g: {"detail": []} for g in run.players})

    def _hit(self, clock, guid, spell, amount, hp_after, mx=1000000, over=-1):
        # currentHP/maxHP live in the advanced block, two and three fields past
        # the spell school.
        ln = damage(clock, MOB.format(500, 1), "Mob", guid, "P", spell, amount,
                    overkill=over)
        f = ln.split("  ", 1)[1].split(",")
        f[14], f[15] = str(hp_after), str(mx)
        return ln.split("  ", 1)[0] + "  " + ",".join(f)

    def test_a_one_shot_from_full_health_is_a_one_shot(self):
        # A tick landing a second earlier that left the player at 99% is not
        # part of the death, and dragging it in turned a genuine one-shot into
        # a 3.2-second window.
        g = PLAYER.format(1)
        out = self._forensics([
            self._hit("20:00:01.000", g, (1, "Chip"), 5000, 995000),
            self._hit("20:00:04.000", g, (2, "Axegrinder"), 918057, 0, over=300000),
        ], g)
        self.assertEqual(len(out), 1)
        self.assertLess(out[0]["over"], 0.1)
        self.assertEqual(out[0]["burst"], 918057)

    def test_window_reaches_back_to_the_last_healthy_reading(self):
        g = PLAYER.format(1)
        out = self._forensics([
            self._hit("20:00:01.000", g, (1, "Chip"), 5000, 995000),
            self._hit("20:00:03.000", g, (2, "Bite"), 400000, 595000),
            self._hit("20:00:05.000", g, (3, "Bite"), 400000, 195000),
            self._hit("20:00:07.000", g, (4, "Bite"), 195000, 0, over=1000),
        ], g)
        self.assertAlmostEqual(out[0]["over"], 4.0, delta=0.1)
        self.assertEqual(out[0]["burst"], 995000)

    def test_death_is_clocked_from_the_key_not_from_first_combat(self):
        # Warcraft Logs and the in-game timer both count from the key. Clocking
        # from the first damage event printed every death about nineteen
        # seconds early, so cross-referencing one against the other failed.
        g = PLAYER.format(1)
        out = self._forensics([
            self._hit("20:05:01.000", g, (1, "Bite"), 900000, 0, over=1000),
        ], g)
        # key starts 19:59:00, so this is 6:01 into the run
        self.assertAlmostEqual(out[0]["at"], 361.0, delta=0.5)


class RegistryTests(unittest.TestCase):
    def test_a_proc_reset_does_not_define_the_cooldown(self):
        # Divine Purpose and Judgment resets put two casts of a long-cooldown
        # ability seconds apart. Keeping the fastest gap ever seen latched onto
        # that permanently and told the grader the ability was available three
        # times as often as it was.
        r = K.Registry(path=_TmpPath())
        for gap in (12.1, 12.0, 12.4, 12.2, 11.9, 12.3, 12.1, 12.0):
            r.learn_cooldown("Judgment", gap)
        self.assertAlmostEqual(r.cooldowns["Judgment"], 12.0, delta=0.2)
        r.learn_cooldown("Judgment", 1.4)
        self.assertGreater(r.cooldowns["Judgment"], 10.0)

    def test_a_small_sample_still_uses_the_minimum(self):
        # With three observations there is no outlier to reject, and pretending
        # otherwise would just report a slow key as the cooldown.
        r = K.Registry(path=_TmpPath())
        for gap in (30.0, 45.0):
            r.learn_cooldown("Lay on Hands", gap)
        self.assertAlmostEqual(r.cooldowns["Lay on Hands"], 30.0, delta=0.1)

    def test_an_old_registry_without_samples_keeps_its_cooldowns(self):
        # The samples key was added after the fact. A file written before it
        # must not silently lose every measured cooldown on load -- that would
        # re-band every utility score without saying so.
        import pathlib
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8")
        fh.write('{"cooldowns": {"Judgment": 12.0}, "durations": {}}')
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        reg = K.Registry(path=pathlib.Path(fh.name))
        self.assertEqual(reg.cooldowns["Judgment"], 12.0)
        self.assertEqual(reg.samples, {})


class ResourceTests(unittest.TestCase):
    def _run(self, lines):
        path = write_log(lines)
        self.addCleanup(os.unlink, path)
        k = L.find_runs(path)[0]
        return collect.collect(path, k.start_line, k.end_line, k.name, k.level,
                               k.timed, k.seconds, K.Registry(path=_TmpPath()), k)

    def _energize(self, clock, guid, spell, amount, over, power, mx):
        # amount and overEnergize are written as FLOATS, unlike every other
        # numeric field in the log. int() throws on "8.0000" and yields zero,
        # which reads as a player who never overcapped all night.
        return line(clock, "SPELL_ENERGIZE", *unit(guid, "P", P_FLAGS),
                    *unit(guid, "P", P_FLAGS), spell[0], f'"{spell[1]}"', "0x2",
                    guid, "0000000000000000", "1000", "1000",
                    "0", "0", "0", "0", "0", "0", "0", "0", "0", "0",
                    f"{amount:.4f}", f"{over:.4f}", power, mx)

    def test_overcap_is_read_from_float_fields(self):
        g = PLAYER.format(1)
        run = self._run([combatant(g, 65),
                         self._energize("20:00:01.000", g, (1, "Holy Shock"),
                                        1.0, 0.0, "9", "5"),
                         self._energize("20:00:03.000", g, (1, "Holy Shock"),
                                        0.0, 1.0, "9", "5")])
        waste = D.resource_waste(run, g)
        self.assertEqual(run.gained[g]["9"], 1.0)
        self.assertEqual(run.wasted[g]["9"], 1.0)
        # generated is amount + overflow, so the share can never exceed 100%.
        # Dividing by `amount` alone reported 229% for an Arcane Mage at cap.
        self.assertFalse(waste)          # below the 20-resource floor
        for _ in range(30):
            run.gained[g]["9"] += 1.0
        waste = D.resource_waste(run, g)
        self.assertEqual(waste[0][0], "Holy Power")
        self.assertLessEqual(waste[0][3], 1.0)

    def test_mana_overcap_is_not_reported(self):
        # A healer at full mana taking a Reclamation proc has not made a
        # mistake; there is nowhere to put it. Including it buried the Holy
        # Power figure under a meaningless 65%.
        g = PLAYER.format(1)
        run = self._run([combatant(g, 65)])
        run.gained[g]["0"] = 100000.0
        run.wasted[g]["0"] = 60000.0
        self.assertEqual(D.resource_waste(run, g), [])

    def test_refused_presses_are_reported_per_cast(self):
        # A raw count measures how often an ability is used as much as how hard
        # it is mashed: 478 refusals on Holy Shock is unremarkable against 200
        # casts, and 60 on a rarely-pressed button is the interesting one.
        g = PLAYER.format(1)
        lines = [combatant(g, 65)]
        for i in range(60):
            lines.append(line(f"20:0{i//60}:{i%60:02d}.000", "SPELL_CAST_FAILED",
                              *unit(g, "P", P_FLAGS), "0000000000000000", "nil",
                              "0x0", "0x0", "1", '"Divine Toll"', "0x2",
                              "Not yet recovered"))
        for i in range(6):
            lines.append(cast(f"20:0{i//60}:{i%60:02d}.500", g, "P", (1, "Divine Toll")))
        run = self._run(lines)
        got = D.mash(run, g)
        self.assertEqual(got[0][0], "Divine Toll")
        self.assertAlmostEqual(got[0][3], 10.0, delta=0.1)


class PullTests(unittest.TestCase):
    def test_a_long_clean_pull_costs_nothing_and_a_wipe_costs_everything(self):
        # Ranking pulls by duration put a six-minute pull that killed 39 enemies
        # without a death at the top of "most expensive", above the two pulls
        # that actually wiped the group.
        class R:
            players = {"a": 1, "b": 2, "c": 3, "d": 4, "e": 5}
            death_events = [(100.0, "a"), (101.0, "b"), (102.0, "c"), (103.0, "d")]
            taken_events = {}
            kill_times = []
            t0 = 0.0
        windows = [(0.0, 90.0), (95.0, 105.0)]
        pulls = D.per_pull(R(), windows, {"opportunities": []})
        clean, wipe = pulls
        self.assertEqual(clean["cost"], 0.0)
        self.assertTrue(wipe["wipe"])
        self.assertGreater(wipe["cost"], clean["cost"])
        self.assertGreater(wipe["cost"], 20.0)      # 10s fight + 4 deaths


class ExportTests(unittest.TestCase):
    def test_a_run_round_trips_through_json(self):
        import json
        from arbiter import export
        reg = K.Registry(path=_TmpPath())
        tank, heal = PLAYER.format(1), PLAYER.format(2)
        mob = MOB.format(500, 1)
        lines = [combatant(tank, 581), combatant(heal, 65)]
        for i in range(120):
            c = f"20:0{i // 60}:{i % 60:02d}.000"
            lines.append(damage(c, tank, "Tank", mob, "Mob", (100, "Hit"), 5000,
                                src_flags=P_FLAGS, dst_flags=HOSTILE_FLAGS))
            lines.append(damage(c, mob, "Mob", tank, "Tank", (200, "Swipe"), 3000))
            lines.append(heal_line(c, heal, "Heal", tank, "Tank", (300, "Flash"), 2000))
        path = write_log(lines)
        self.addCleanup(os.unlink, path)
        k = L.find_runs(path)[0]
        run = collect.collect(path, k.start_line, k.end_line, k.name, k.level,
                              k.timed, k.seconds, reg, k)
        rec = export.run_record(run, score.evaluate(run, reg, {}))
        blob = json.loads(json.dumps(rec))     # must be plain JSON-able data
        self.assertEqual(blob["dungeon"], run.name)
        self.assertEqual(len(blob["players"]), 2)
        self.assertIn("axes", blob["players"][0])
        self.assertIn("time", blob)


class IntegrationTests(unittest.TestCase):
    """One synthetic key, end to end through collect + evaluate."""

    def setUp(self):
        self.reg = K.Registry(path=_TmpPath())
        tank, heal = PLAYER.format(1), PLAYER.format(2)
        d1, d2, d3 = PLAYER.format(3), PLAYER.format(4), PLAYER.format(5)
        self.guids = [tank, heal, d1, d2, d3]
        mob = MOB.format(500, 1)
        lines = [combatant(tank, 581), combatant(heal, 65), combatant(d1, 62),
                 combatant(d2, 258), combatant(d3, 70)]
        # sustained combat so windows form
        for i in range(200):
            c = f"20:0{i // 60}:{i % 60:02d}.000"
            for g, n in ((tank, "Tank"), (d1, "Dps1"), (d2, "Dps2"), (d3, "Dps3")):
                lines.append(damage(c, g, n, mob, "Mob", (100, "Hit"), 5000,
                                    src_flags=P_FLAGS, dst_flags=HOSTILE_FLAGS))
            lines.append(damage(c, mob, "Mob", tank, "Tank", (200, "Swipe"), 3000))
            lines.append(cast(c, heal, "Heal", (300, "Holy Shock")))
        self.path = write_log(lines)

    def tearDown(self):
        os.unlink(self.path)

    def test_run_is_found_and_graded(self):
        runs = L.find_runs(self.path)
        self.assertEqual(len(runs), 1)
        i, nm, s, e, dur, lvl, timed = runs[0]
        run = collect.collect(self.path, s, e, nm, lvl, timed, dur, self.reg)
        self.assertEqual(len(run.players), 5)
        ev = score.evaluate(run, self.reg, {})
        self.assertEqual(len(ev["rows"]), 5)
        for g, row in ev["rows"].items():
            self.assertGreaterEqual(row["total"], 0)
            self.assertLessEqual(row["total"], 100)

    def test_inapplicable_axes_drop_and_weights_renormalise(self):
        # Rule 3: an axis that does not apply is dropped, never scored zero, and
        # the surviving weights must still sum to 1.
        i, nm, s, e, dur, lvl, timed = L.find_runs(self.path)[0]
        run = collect.collect(self.path, s, e, nm, lvl, timed, dur, self.reg)
        ev = score.evaluate(run, self.reg, {})
        for g, row in ev["rows"].items():
            self.assertAlmostEqual(sum(a.weight for a in row["live"]), 1.0, places=6)
            for a in row["axes"]:
                if a.score is None:
                    self.assertNotIn(a, row["live"])

    def test_no_kickable_casts_means_no_interrupt_axis(self):
        # A key with nothing kickable in it cannot be failed on interrupts.
        i, nm, s, e, dur, lvl, timed = L.find_runs(self.path)[0]
        run = collect.collect(self.path, s, e, nm, lvl, timed, dur, self.reg)
        ev = score.evaluate(run, self.reg, {})
        for g, row in ev["rows"].items():
            axis = next(a for a in row["axes"] if a.name == "interrupts")
            self.assertIsNone(axis.score)

    def test_healer_has_no_interrupt_axis(self):
        i, nm, s, e, dur, lvl, timed = L.find_runs(self.path)[0]
        run = collect.collect(self.path, s, e, nm, lvl, timed, dur, self.reg)
        ev = score.evaluate(run, self.reg, {})
        heal = next(g for g, p in run.players.items() if p["role"] == "heal")
        axis = next(a for a in ev["rows"][heal]["axes"] if a.name == "interrupts")
        self.assertIsNone(axis.score)


class BossSplitTests(unittest.TestCase):
    def test_encounter_lines_survive_the_length_guard(self):
        # ENCOUNTER_START carries six fields. Every combat event handler sits
        # behind a `len(f) < 8` guard, so parsing these late drops them silently
        # and the whole boss/trash split reports nothing.
        guids = [PLAYER.format(i) for i in range(1, 6)]
        lines = [combatant(g, sp) for g, sp in zip(guids, [581, 65, 62, 258, 70])]
        mob = MOB.format(500, 1)
        for i in range(60):
            c = f"20:00:{i:02d}.000"
            lines.append(damage(c, guids[0], "P", mob, "Mob", (100, "Hit"), 5000,
                                src_flags=P_FLAGS, dst_flags=HOSTILE_FLAGS))
        lines.append(line("20:00:20.000", "ENCOUNTER_START", 2609, '"Test Boss"', 8, 5, 1))
        lines.append(line("20:00:40.000", "ENCOUNTER_END", 2609, '"Test Boss"', 8, 5, 1,
                          20000))
        path = write_log(lines)
        try:
            reg = K.Registry(path=_TmpPath())
            i, nm, s2, e, dur, lvl, timed = L.find_runs(path)[0]
            run = collect.collect(path, s2, e, nm, lvl, timed, dur, reg)
            self.assertEqual(len(run.bosses), 1)
            self.assertEqual(run.bosses[0][2], "Test Boss")
            self.assertTrue(run.bosses[0][3])
            split = D.boss_split(run)
            self.assertEqual(split["boss_deaths"], 0)
        finally:
            os.unlink(path)

    def test_no_encounters_returns_none(self):
        self.assertIsNone(D.boss_split(_EmptyRun()))


class _EmptyRun:
    bosses = []


class CompositionTests(unittest.TestCase):
    """Odd group compositions must grade, not crash.

    A key is not always one tank, one healer and three damage dealers: people
    swap specs, somebody logs in as the wrong role, a spec id lands that this
    build has never heard of. None of that should take the tool down, and every
    one of these arrangements divides by something that can be zero somewhere in
    the model."""

    def _grade(self, specs, active=None):
        guids = [PLAYER.format(i) for i in range(1, len(specs) + 1)]
        active = guids if active is None else active
        lines = [combatant(g, sp) for g, sp in zip(guids, specs)]
        mob = MOB.format(500, 1)
        for i in range(120):
            c = f"20:0{i // 60}:{i % 60:02d}.000"
            for g in active:
                lines.append(damage(c, g, "P", mob, "Mob", (100, "Hit"), 5000,
                                    src_flags=P_FLAGS, dst_flags=HOSTILE_FLAGS))
            lines.append(damage(c, mob, "Mob", guids[0], "P", (200, "Swipe"), 3000))
        path = write_log(lines)
        try:
            reg = K.Registry(path=_TmpPath())
            i, nm, s, e, dur, lvl, timed = L.find_runs(path)[0]
            run = collect.collect(path, s, e, nm, lvl, timed, dur, reg)
            return run, score.evaluate(run, reg, {}), guids
        finally:
            os.unlink(path)

    def test_no_tank_and_no_healer(self):
        run, ev, _ = self._grade([62, 62, 258, 70, 103])
        self.assertEqual(len(ev["rows"]), 5)

    def test_two_tanks_and_two_healers(self):
        for specs in ([581, 66, 62, 258, 70], [581, 65, 105, 258, 70]):
            run, ev, _ = self._grade(specs)
            self.assertEqual(len(ev["rows"]), 5)

    def test_single_player(self):
        # Every "median of the other damage dealers" has an empty list here.
        run, ev, _ = self._grade([65])
        self.assertEqual(len(ev["rows"]), 1)

    def test_unknown_spec_id_falls_back_to_dps(self):
        run, ev, guids = self._grade([581, 65, 99999, 258, 70])
        self.assertEqual(run.players[guids[2]]["role"], "dps")

    def test_fully_idle_player_scores_badly_but_finitely(self):
        # Somebody who never acted at all should bottom out, not produce a
        # negative activity percentage or a NaN.
        guids = [PLAYER.format(i) for i in range(1, 6)]
        run, ev, _ = self._grade([581, 65, 62, 258, 70], active=guids[:4])
        row = ev["rows"][guids[4]]
        self.assertGreaterEqual(row["act"], 0.0)
        self.assertLess(row["act"], 10.0)
        self.assertGreater(row["total"], 0.0)


class TalentTests(unittest.TestCase):
    """The kit a player OWNS, versus the kit they happened to use.

    Every bug in this area has the same shape: a denominator built from presses
    shrinks when the player skips a button, so skipping it improves the grade.
    """

    def test_a_false_active_never_reaches_the_habit_line(self):
        # Auras of the Resolute is typed `active` in the Midnight tree dump and
        # is a passive aura upgrade. Reported as "never pressed" for two nights
        # before the Paladin who supposedly had it said he had never heard of
        # it. The tree's own flag is not evidence that something is a button.
        info = {"talents": {"Auras of the Resolute", "Blessing of Sacrifice"},
                "actives": {"Auras of the Resolute", "Blessing of Sacrifice"}}
        self.assertNotIn("Auras of the Resolute", K.buttons(info))
        self.assertIn("Blessing of Sacrifice", K.buttons(info))

    def test_a_spec_gated_ability_is_not_a_habit(self):
        # Sigil of Misery is a class-tree root every Demon Hunter's loadout
        # reports. Doryhunky pressed it 8 times across three Devourer keys and
        # zero across eleven Vengeance ones. He was told he had skipped it all
        # night; he does not have it in that spec.
        reg = K.Registry.__new__(K.Registry)
        reg.by_spec = {"Sigil of Misery": [1480], "Darkness": [581, 1480]}
        veng = {"talents": {"Sigil of Misery", "Darkness"}, "spec": 581}
        self.assertEqual(K.buttons(veng, reg), {"Darkness"})
        devourer = {"talents": {"Sigil of Misery", "Darkness"}, "spec": 1480}
        self.assertEqual(K.buttons(devourer, reg), {"Sigil of Misery", "Darkness"})

    def test_an_ability_nobody_presses_is_left_alone(self):
        # Proof runs one way. Excluding what has never been seen would let the
        # first Mage who ignores Ice Block all season exempt every Mage after.
        reg = K.Registry.__new__(K.Registry)
        reg.by_spec = {}
        info = {"talents": {"Ice Block"}, "spec": 62}
        self.assertEqual(K.buttons(info, reg), {"Ice Block"})

    def test_a_registry_without_the_table_does_not_raise(self):
        reg = K.Registry.__new__(K.Registry)   # no by_spec attribute at all
        info = {"talents": {"Darkness"}, "spec": 581}
        self.assertEqual(K.buttons(info, reg), {"Darkness"})

    def test_missing_talent_data_degrades_to_presses(self):
        self.assertEqual(K.buttons({}), set())
        self.assertEqual(K.buttons({"talents": None, "actives": None}), set())

    def test_an_unpressed_defensive_stays_in_the_mitigation_ceiling(self):
        # The self-fulfilling loop this closes: Fiery Brand talented and never
        # pressed used to leave the denominator, so the ratio was measured
        # against Demon Spikes alone and NOT pressing it raised the grade.
        reg = K.Registry.__new__(K.Registry)
        reg.cooldowns = {"Demon Spikes": 20.0, "Fiery Brand": 60.0}
        reg.durations = {"Demon Spikes": 8.0, "Fiery Brand": 12.0}
        run = _EmptyRun()
        run.casts = {"g": [(0.0, "Demon Spikes")]}
        run.players = {"g": {"talents": {"Fiery Brand"}, "actives": {"Fiery Brand"}}}
        with_brand = D.achievable_uptime(run, "g", 600, reg)
        run.players = {"g": {"talents": set(), "actives": set()}}
        without = D.achievable_uptime(run, "g", 600, reg)
        self.assertAlmostEqual(without, 8 / 20)
        self.assertAlmostEqual(with_brand, 8 / 20 + 12 / 60)
        self.assertGreater(with_brand, without)

    def test_an_untalented_defensive_stays_out(self):
        # Rule 2: nobody is scored against a button they do not have.
        reg = K.Registry.__new__(K.Registry)
        reg.cooldowns = {"Demon Spikes": 20.0, "Shield Wall": 180.0}
        reg.durations = {"Demon Spikes": 8.0, "Shield Wall": 12.0}
        run = _EmptyRun()
        run.casts = {"g": [(0.0, "Demon Spikes")]}
        run.players = {"g": {"talents": {"Frailty"}, "actives": set()}}
        self.assertAlmostEqual(D.achievable_uptime(run, "g", 600, reg), 8 / 20)

    def test_the_index_carries_type_and_spell_id(self):
        idx = K.load_talent_index()
        if not idx:
            self.skipTest("talents-live.json not present")
        name, kind, spell = next(iter(idx.values()))
        self.assertIsInstance(name, str)
        self.assertIn(kind, {"active", "passive", "tierrank", "subtree", None})
        # The old name-only map is still exactly that, for anything reading it.
        self.assertEqual(set(K.load_talents().values()),
                         {v[0] for v in idx.values()})


class AuraIntervalTests(unittest.TestCase):
    """Same-timestamp REMOVED/APPLIED pairs, which is how a refresh often logs."""

    def test_a_refresh_logged_as_remove_then_apply_is_continuous(self):
        # The bug: sorting (t, event) tuples breaks the tie alphabetically, and
        # APPLIED sorts before REMOVED, so the pair is silently reversed. That
        # closes the window at the instant it should have re-opened. A Vengeance
        # DH read 31% Demon Spikes uptime against a true 100%, and the report
        # published a mitigation collapse that never happened.
        evs = [(0.0, "SPELL_AURA_APPLIED"),
               (10.0, "SPELL_AURA_REMOVED"), (10.0, "SPELL_AURA_APPLIED"),
               (20.0, "SPELL_AURA_REMOVED"), (20.0, "SPELL_AURA_APPLIED"),
               (30.0, "SPELL_AURA_REMOVED")]
        iv = D.aura_intervals(evs, 100.0)
        self.assertEqual(iv, [(0.0, 30.0)])
        self.assertAlmostEqual(sum(b - a for a, b in iv), 30.0)

    def test_a_real_gap_is_still_a_gap(self):
        evs = [(0.0, "SPELL_AURA_APPLIED"), (10.0, "SPELL_AURA_REMOVED"),
               (25.0, "SPELL_AURA_APPLIED"), (30.0, "SPELL_AURA_REMOVED")]
        self.assertEqual(D.aura_intervals(evs, 100.0), [(0.0, 10.0), (25.0, 30.0)])

    def test_an_unclosed_aura_runs_to_the_cap(self):
        evs = [(0.0, "SPELL_AURA_APPLIED")]
        self.assertEqual(D.aura_intervals(evs, 42.0), [(0.0, 42.0)])

    def test_uptime_cannot_exceed_the_combat_it_is_divided_by(self):
        # A defensive held through a walk between packs is real, but the
        # denominator excludes those seconds, so the numerator must too.
        spans = [(0.0, 100.0)]
        windows = [(10.0, 20.0), (50.0, 60.0)]
        self.assertEqual(D.clip(spans, windows), [(10.0, 20.0), (50.0, 60.0)])
        combat = sum(b - a for a, b in windows)
        self.assertLessEqual(sum(b - a for a, b in D.clip(spans, windows)), combat)


class DispelCapacityTests(unittest.TestCase):
    """Overlapping debuffs against one dispel button.

    The denominator used to be a count of what the dungeon applied. A healer was
    charged for sixteen debuffs in a key where five were live at once and Cleanse
    recharges in nine seconds.
    """

    def _reg(self, cd=9.0):
        reg = K.Registry.__new__(K.Registry)
        reg.cooldowns = {"Cleanse": cd}
        return reg

    def test_simultaneous_debuffs_are_not_all_reachable(self):
        # Five land together and each lasts 6s. One presser at a 9s recharge
        # reaches exactly one before the rest expire.
        windows = [(0.0, 6.0)] * 5
        self.assertEqual(D.reachable_dispels(windows, self._reg()), 1)

    def test_spaced_debuffs_are_all_reachable(self):
        windows = [(t, t + 6.0) for t in (0.0, 20.0, 40.0, 60.0)]
        self.assertEqual(D.reachable_dispels(windows, self._reg()), 4)

    def test_a_long_debuff_can_wait_for_the_recharge(self):
        # Two land together but sit for 30s, so the second is reachable on the
        # next charge. Length is what decides it, not simultaneity.
        windows = [(0.0, 30.0), (0.0, 30.0)]
        self.assertEqual(D.reachable_dispels(windows, self._reg()), 2)

    def test_the_cap_never_exceeds_the_instances(self):
        windows = [(0.0, 500.0)]
        self.assertEqual(D.reachable_dispels(windows, self._reg()), 1)

    def test_no_debuffs_is_not_a_divide_by_zero(self):
        self.assertEqual(D.reachable_dispels([], self._reg()), 0)

    def test_a_missing_cooldown_falls_back_to_a_global(self):
        reg = K.Registry.__new__(K.Registry)
        reg.cooldowns = {}
        self.assertEqual(D.reachable_dispels([(0.0, 6.0)] * 5, reg), 1)


class _TmpPath:
    """A registry path that never exists and never writes."""

    def exists(self):
        return False

    def write_text(self, *a, **k):
        return None


if __name__ == "__main__":
    unittest.main(verbosity=2)
