"""Parsing primitives for a retail WoW combat log.

Everything in here is about the *file*, not about Mythic+. The field offsets
below cost real debugging time to get right and are wrong in the obvious
guesses, so they are written down once rather than re-derived per script.

  event name is separated from the timestamp by TWO SPACES, not a comma, so
                        `grep ",SPELL_HEAL,"` silently matches nothing.
  SPELL_HEAL tail       amount, baseAmount, overhealing, absorbed, critical
                        -> effective = f[-5] - f[-3]
  damage (any)          NOT a fixed offset. See `damage_fields` -- the tail is
                        amount, baseAmount, overkill, school, resisted, blocked,
                        absorbed, critical, glancing, crushing, and SPELL_* /
                        RANGE_* events carry ONE MORE field after that ("ST",
                        "AOE", or the supporting player's GUID on _SUPPORT)
                        which SWING_* and ENVIRONMENTAL_DAMAGE do not.
                        Counting back from the end with a single offset
                        therefore reads a DIFFERENT column depending on the
                        event, and both wrong answers look plausible:
                          - f[-10] on a SPELL_DAMAGE is baseAmount, the damage
                            BEFORE armour and mitigation. Every damage number
                            in the pipeline was inflated -- one Zul'jan
                            Axegrinder read 1,282,592 against the 918,057 it
                            actually did.
                          - f[-9] on a SWING is baseAmount too, and it is read
                            as OVERKILL, so every melee swing in the log was
                            flagged as a killing blow: 52,547 of them in one
                            night against 920 real ones.
                        The invariant that catches both: overkill can never
                        exceed the damage of the hit that caused it. The old
                        offsets violate it 36,406 times in one log; the ones
                        below, zero.
  COMBATANT_INFO        specID f[25] (f[24] is armor and parses fine, which is
                        how you get silently nonsense spec labels); gear blob
                        f[28], a bracketed list of
                        (itemID, ilvl, (enchants), (bonusIDs), (gems)).
  SPELL_INTERRUPT       the INTERRUPTED spell is f[13], not f[14]. f[10] is the
                        interrupting spell. Getting this wrong reads as "nothing
                        in this dungeon is kickable", which is a very
                        convincing-looking wrong answer.
  SPELL_CAST_SUCCESS    advanced tail powerType f[-9], currentPower f[-8],
                        maxPower f[-7], powerCost f[-6], posX f[-5], posY f[-4].
                        powerType reports the class's PRIMARY resource, not
                        anything about the cast -- it does not distinguish a
                        button press from a proc. Do not try; see derive.gcd_set.

UNIT FLAGS are the reliable way to tell friend from foe -- much better than
testing whether a GUID starts with "Player-". A mage's Mirror Images are
`Creature-...` and cast Frostbolt; counting those as enemy casts invented 107
phantom interrupt opportunities in one key before this was flagged on reaction
instead. Hostility is a bit, so use it.
"""

import datetime
import re
import io

# COMBATLOG_OBJECT_* bits, from FrameXML. Only the ones actually used.
AFFILIATION_MINE = 0x00000001
AFFILIATION_PARTY = 0x00000002
AFFILIATION_RAID = 0x00000004
REACTION_FRIENDLY = 0x00000010
REACTION_HOSTILE = 0x00000040
CONTROL_PLAYER = 0x00000100
TYPE_PLAYER = 0x00000400

DAMAGE_EVENTS = ("SPELL_DAMAGE", "SPELL_PERIODIC_DAMAGE", "SWING_DAMAGE",
                 "SWING_DAMAGE_LANDED", "RANGE_DAMAGE", "SPELL_DAMAGE_SUPPORT",
                 "SPELL_PERIODIC_DAMAGE_SUPPORT", "ENVIRONMENTAL_DAMAGE")
HEAL_EVENTS = ("SPELL_HEAL", "SPELL_PERIODIC_HEAL", "SPELL_HEAL_SUPPORT")
# Swing events carry no spellId/spellName/school, so the advanced block starts
# three fields earlier. Anything reading f[10] as a spell name must special-case
# these or it will read a GUID and produce a spell called "Player-3684-...".
SWING_EVENTS = ("SWING_DAMAGE", "SWING_DAMAGE_LANDED", "SWING_MISSED")


_SPECIAL = re.compile(r'["\[\]()]')


def split(s):
    """Split a log line on commas, respecting quotes and [ ( ) ] nesting.

    Hot enough to be worth the shape it is in: this runs on every line of a
    million-line file and a character-at-a-time version with a list append per
    character was 85% of total runtime -- 58 million appends for one key. The
    work is done per comma-separated PIECE instead, and the character loop only
    runs for the pieces that actually contain a quote or a bracket, which is a
    small minority. Plain numeric fields never enter it at all."""
    if '"' not in s and "[" not in s and "(" not in s:
        return s.split(",")
    out, buf, quoted, depth = [], None, False, 0
    for piece in s.split(","):
        buf = piece if buf is None else buf + "," + piece
        if _SPECIAL.search(piece):
            for ch in piece:
                if ch == '"':
                    quoted = not quoted
                elif not quoted:
                    if ch in "[(":
                        depth += 1
                    elif ch in "])":
                        depth -= 1
        if not quoted and depth == 0:
            out.append(buf.replace('"', "") if '"' in buf else buf)
            buf = None
    if buf is not None:
        out.append(buf.replace('"', "") if '"' in buf else buf)
    return out


def body_of(line):
    """The event body, with the line ending removed.

    The `\\r` matters. These logs are CRLF, and reading the file in binary --
    which is what seeking to a key's byte offset requires -- hands back a line
    that text mode would have cleaned up. Left on, it rides along on the LAST
    field of every event, so `f[12] == "BUFF"` quietly became False for 27,797
    of 30,256 aura events in one key and every tank in the report scored 0%
    mitigation uptime. Strip both, not just the newline."""
    parts = line.split("  ", 1)
    return parts[1].rstrip("\r\n") if len(parts) > 1 else None


_DAY = {}


def stamp(line):
    """Absolute seconds. Parsing the DATE as well as the clock is not padding:
    a key started at 23:50 rolls the clock over, and a clock-only parser reports
    a negative duration and divides by it.

    The date is memoised because this runs on every line of a million-line file
    and a log spans one or two distinct dates -- building a `datetime.date` per
    line to compute a constant is most of what this function used to cost."""
    head = line.split("  ", 1)[0]
    date, clock = head.split(" ", 1)
    day = _DAY.get(date)
    if day is None:
        mo, da, yr = (int(x) for x in date.split("/"))
        day = _DAY[date] = datetime.date(yr, mo, da).toordinal() * 86400.0
    h, mi, se = clock.split("-")[0].split("+")[0].split(":")
    return day + int(h) * 3600 + int(mi) * 60 + float(se)


def flags(field):
    try:
        return int(field, 16)
    except (ValueError, TypeError):
        return 0


def hostile(field):
    return bool(flags(field) & REACTION_HOSTILE)


def friendly(field):
    return bool(flags(field) & REACTION_FRIENDLY)


def spell_fields(ev, f):
    """(spellId, spellName) for any event, or (None, 'Melee') for a swing."""
    if ev.startswith("SWING"):
        return None, "Melee"
    if len(f) > 10:
        return f[9], f[10]
    return None, "?"


# The last three fields of every damage event are critical / glancing /
# crushing, which the client writes as "nil" or "1". Anything else sitting in
# the final column is the trailing marker, so the shift is detected rather than
# hardcoded per event name -- RANGE_DAMAGE and any future _SUPPORT variant then
# come out right without this table having to know about them.
_BOOLISH = ("nil", "1", "0")


def _tail_shift(f):
    return 0 if f[-1] in _BOOLISH else 1


def damage_fields(f):
    """(amount, overkill, absorbed) for any damage event.

    `amount` is what the target actually lost, which is the only one of the
    three damage columns that belongs in a total. `baseAmount` next to it is
    pre-mitigation and is deliberately not returned: nothing here wants it, and
    returning it is how it gets summed by mistake.

    overkill is -1 on an ordinary hit and > 0 only on a killing blow."""
    s = _tail_shift(f)
    return (_int(f, -10 - s), _int(f, -8 - s, 0) or 0, _int(f, -4 - s, 0) or 0)


def _int(f, i, default=None):
    """One field, parsed independently of its neighbours. Independence is the
    point: an unreadable `absorbed` used to take the whole damage event down
    with it, so a single odd field silently deleted the hit from every total."""
    try:
        return int(f[i])
    except (ValueError, IndexError, TypeError):
        return default


def heal_fields(f):
    """(effective, overhealing, absorbed). Heals carry no trailing marker."""
    amt, over = _int(f, -5), _int(f, -3, 0) or 0
    if amt is None:
        return None, 0, 0
    return amt - over, over, _int(f, -2, 0) or 0


def owner_guid(ev, f):
    """The owner GUID out of the advanced-parameter block, or None.

    SPELL_SUMMON is the obvious way to learn who a pet belongs to and it is not
    enough on its own: a warlock summons the felhunter in town, so the summon
    sits OUTSIDE the key's line range and the pet arrives already orphaned.
    Every damage, heal and cast the pet does carries its owner in the second
    advanced field, so ownership can be learned from the pet simply acting.

    The block starts after school on a SPELL_* event and immediately after the
    destination on a SWING_*, which is the same three-field shift documented at
    the top of this file. Events without an advanced block (auras, summons)
    return None rather than reading a spell name as a GUID."""
    i = 9 if ev.startswith("SWING") else 12
    if len(f) <= i + 1:
        return None
    guid = f[i + 1]
    return guid if guid.startswith("Player-") else None


def health_fields(ev, f):
    """(currentHP, maxHP) of the unit the advanced block describes, or None.

    On a damage event that unit is the TARGET, and the value is its health
    AFTER the hit -- the killing blow on a player reads 0. This is what makes a
    death window answerable properly: the interesting stretch is the one since
    the player was last near full, which is what Warcraft Logs shows as "Over",
    rather than a fixed number of seconds guessed in advance."""
    i = 9 if ev.startswith("SWING") else 12
    cur, mx = _int(f, i + 2), _int(f, i + 3)
    if cur is None or not mx:
        return None
    return cur, mx


def absorb_fields(f):
    """(shield caster GUID, damage the shield ate) from SPELL_ABSORBED.

    The caster GUID is f[-10] and its NAME is f[-9]. Reading f[-9] as the GUID
    tests a display name against "Player-", which never matches, so every
    absorb silently scored zero -- the whole shielding half of a Disc Priest or
    a Preservation Evoker landed on the floor. f[-2] is the shield's size,
    which is not the same question as what it stopped."""
    if len(f) < 10:
        return None, 0
    try:
        return f[-10], int(f[-3])
    except ValueError:
        return f[-10], 0


class Key:
    """One CHALLENGE_MODE_START/END pair.

    CHALLENGE_MODE_END is two different events wearing one name. A real
    completion carries the instance, the level and the timer; the client also
    fires an ALL-ZERO one every time the challenge-mode UI resets, which in
    practice lands a few milliseconds before every START. Reading the level off
    the END therefore prints an abandoned key as "+0 depleted" -- and worse,
    reads `success` off an event that says nothing about success.

    The level comes from START, which always has it. `finished` says whether a
    real END was seen at all, which is what separates a DEPLETED key (played to
    the end, timer missed) from an ABANDONED one (everyone left).

    Fields 6 and 7 of a real END are the run's Mythic+ dungeon score and the
    character's TOTAL M+ rating afterwards. Neither is a par time, and there is
    no par time anywhere in a combat log -- see `arbiter/dungeons.py`. Confirmed
    against the in-game Raider.IO readout on 2026-08-21: rating 2108 against a
    logged 2107.587, and every per-dungeon best score matching field 6 of the
    run that set it."""

    __slots__ = ("index", "name", "start_line", "end_line", "seconds", "level",
                 "timed", "finished", "score", "rating", "timer_seconds",
                 "start_t", "end_t", "start_byte", "affixes")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def __iter__(self):
        """Legacy 7-tuple unpacking, so existing callers keep working."""
        return iter((self.index, self.name, self.start_line, self.end_line,
                     self.seconds, self.level, self.timed))


DEATH_PENALTY = 5.0       # seconds the M+ timer adds per counted death
COUNTDOWN = 8.4           # START fires this long before the timer actually runs

# Affix ids from the bracketed field 6 of CHALLENGE_MODE_START. Only the ones
# that can be confirmed from this corpus are named; anything else prints as its
# raw id rather than being guessed at, because a wrong affix label changes how
# a whole key reads. The confirmation here is structural: every key at +7, +8
# and +9 carries [162,10] and the +10 carries [162,10,9], which is exactly the
# level at which a second base affix is added.
AFFIXES = {"9": "Tyrannical", "10": "Fortified", "11": "Bursting",
           "12": "Grievous", "13": "Explosive", "14": "Quaking",
           "3": "Volcanic", "4": "Necrotic", "6": "Raging", "7": "Bolstering",
           "8": "Sanguine", "122": "Inspiring", "123": "Spiteful",
           "124": "Storming", "134": "Incorporeal", "135": "Afflicted",
           "136": "Challenger's Peril"}


def affix_names(ids):
    return [AFFIXES.get(i, f"affix {i}") for i in ids]


def find_runs(path):
    """[Key]. Line numbers are 1-based and inclusive.

    Pre-filtering on the literal substring before doing any splitting is what
    makes this bearable on a 1 GB log."""
    found, open_at, n, at = [], None, 0, 0
    with io.open(path, "rb") as fh:
        for raw in fh:
            n += 1
            here, at = at, at + len(raw)
            if b"CHALLENGE_MODE_" not in raw:
                continue
            line = raw.decode("utf-8", "replace")
            b = body_of(line)
            if not b:
                continue
            f = split(b)
            if b.startswith("CHALLENGE_MODE_START"):
                if open_at is not None:
                    # A START with a START already open: the previous key was
                    # abandoned and its reset END was consumed as the opener of
                    # this one. Close it at the line before this START.
                    found.append(_close(open_at, n - 1, stamp(line), None,
                                        len(found) + 1))
                lvl = f[4] if len(f) > 4 and f[4].lstrip("-").isdigit() else "0"
                aff = []
                if len(f) > 5 and f[5].startswith("["):
                    aff = [x.strip() for x in f[5][1:-1].split(",") if x.strip()]
                open_at = (n, f[1], stamp(line), int(lvl), here, aff)
            elif b.startswith("CHALLENGE_MODE_END") and open_at:
                real = len(f) > 4 and f[1] not in ("0", "") and f[3] not in ("0", "")
                # A reset END closes the key only in the sense that the group
                # left; nothing about it describes the run, so `f` is dropped.
                found.append(_close(open_at, n, stamp(line), f if real else None,
                                    len(found) + 1))
                open_at = None
    return found


def _close(open_at, end_line, end_t, f, index):
    start_line, name, start_t, level, start_byte, aff = open_at
    return Key(index=index, name=name, start_line=start_line, end_line=end_line,
               level=level, start_t=start_t, end_t=end_t, start_byte=start_byte,
               affixes=aff,
               seconds=end_t - start_t,
               finished=f is not None,
               timed=bool(f) and f[2] == "1",
               timer_seconds=(int(f[4]) / 1000.0) if f and f[4].isdigit() else None,
               score=_f(f, 5), rating=_f(f, 6))


def _f(f, i):
    if not f or len(f) <= i:
        return None
    try:
        return float(f[i])
    except ValueError:
        return None


def counted_deaths(key):
    """How many deaths the GAME counted, from its own timer. Independent of
    UNIT_DIED entirely, which is the point: it is the only cross-check on the
    parser that does not come from the parser.

    The M+ timer is wall-clock plus five seconds per death, and START fires
    about 8.4s before it starts running. Rearranged, the death count falls out.
    Across sixteen keys on disk this lands within 0.15 of an integer every
    time, which is what makes it trustworthy enough to check against."""
    if key.timer_seconds is None or key.seconds is None:
        return None
    return (key.timer_seconds - key.seconds + COUNTDOWN) / DEATH_PENALTY


def events(path, start, end, start_byte=None, keep=None):
    """(t, event, fields) for each parsed line in [start, end].

    `start_byte` turns this from a scan into a seek. It matters more than it
    looks: grading a night of six keys re-read the whole file twelve times --
    once per key to learn the registry, once per key to grade -- so a 365 MB log
    cost four and a half gigabytes of I/O to answer a question about six
    stretches of it. find_runs already knows where each key begins, so it
    records the offset and this skips straight there."""
    with io.open(path, "rb") as fh:
        if start_byte is not None:
            fh.seek(start_byte)
            n = start - 1
        else:
            n = 0
        for raw in fh:
            n += 1
            if n < start:
                continue
            if n > end:
                break
            line = raw.decode("utf-8", "replace")
            b = body_of(line)
            if not b:
                continue
            ev = b.split(",", 1)[0]
            # `keep` is checked BEFORE the full split, which is where the time
            # goes -- `split` walks the line character by character to respect
            # quoting and bracket nesting. Damage and healing are ~70% of a
            # key's lines, and the registry pre-pass does not read either.
            if keep is not None and ev not in keep:
                continue
            yield stamp(line), ev, split(b)


def talent_entries(blob):
    """traitEntryIDs out of the COMBATANT_INFO talent field.

    The blob is a bracketed list of (traitNodeID, traitEntryID, rank). Only the
    entry id is useful: it is what `data/talents-live.json` keys its spell names on."""
    inner = blob.strip()
    if not (inner.startswith("[") and inner.endswith("]")):
        return []
    out = []
    for item in split(inner[1:-1]):
        item = item.strip()
        if not item.startswith("("):
            continue
        parts = split(item[1:-1])
        if len(parts) >= 2 and parts[1].strip().isdigit():
            out.append(parts[1].strip())
    return out


def item_levels(gear_blob):
    """Equipped item levels out of the COMBATANT_INFO gear field.

    Empty slots (shirt, tabard) log as ilvl 1 and would drag a 290 average down
    to 266 if averaged in."""
    inner = gear_blob.strip()
    if not (inner.startswith("[") and inner.endswith("]")):
        return []
    out = []
    for item in split(inner[1:-1]):
        item = item.strip()
        if not item.startswith("("):
            continue
        parts = split(item[1:-1])
        try:
            lvl = int(parts[1])
        except (ValueError, IndexError):
            continue
        if lvl > 1:
            out.append(lvl)
    return out
