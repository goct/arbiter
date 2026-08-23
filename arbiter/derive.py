"""Inferences drawn from the collected facts.

Every function here answers a question the log does not answer directly, and
each one exists because the naive version of the same question is misleading:

  gcd_set            "how busy were you" -- casts/min is a lie for any spec with
                     passive procs. One Ret key logs 769 Crusading Strikes, 563
                     Divine Hammer ticks and 491 Empyrean Hammer procs, none of
                     which is a button press, and the raw rate reads 117/min
                     against a GCD that physically caps near 50.
  interrupt_board    "kicks per minute" is mostly a fact about which dungeon you
                     ran. Casts that COULD have been stopped is the real
                     denominator.
  avoidable_damage   "damage taken" punishes whoever stood closest to the boss.
                     Damage from a spell that comparable teammates did NOT take
                     is the part worth asking about. Note the hedge: the log
                     cannot tell a puddle nobody else stepped in from a mob
                     choosing one target, so this is over-EXPOSURE, not proven
                     error, and the report says so. Melee are compared with
                     melee, tank-directed abilities are excluded, and stacking
                     debuffs are excluded, because each of those otherwise
                     produces a confident wrong answer.
  mitigation_uptime  a Veng DH presses Demon Spikes 126 times a key and a Fire
                     Mage presses Ice Block twice. Uptime and spike coverage are
                     about the player; press counts are about the class.
"""

import collections
import itertools
import statistics

from . import knowledge as K

GCD_FLOOR = 0.75          # hard floor in-game; nothing real lands closer


def combat_windows(stamps, gap=5.0):
    """Merge damage timestamps into [(start, end)] fight windows.

    A key is only 60-70% combat; the rest is walking. Scoring activity against
    wall-clock marks everybody idle and hands out straight Fs on a timed key."""
    if not stamps:
        return []
    stamps = sorted(stamps)
    out, s, prev = [], stamps[0], stamps[0]
    for t in stamps[1:]:
        if t - prev > gap:
            out.append((s, prev))
            s = t
        prev = t
    out.append((s, prev))
    return [(a, b) for a, b in out if b - a > 2.0]


def overlap(a, b, spans):
    return sum(max(0.0, min(b, y) - max(a, x)) for x, y in spans)


def dead_spans(dead_from, casts, end_cap=None):
    """guid -> [(died, back_up)]. No SPELL_RESURRECT is logged for a corpse run,
    so the next cast the player lands is the only evidence they are back.

    If they never cast again they never came back, and the span has to run to
    the end of the key. Closing it at the instant of death instead -- which is
    what a missing end cap does -- books a corpse as fully alive and then marks
    it down for being idle."""
    out = {}
    for g, times in dead_from.items():
        spans = []
        for d in times:
            nxt = next((c for c, _ in casts.get(g, []) if c > d), None)
            if nxt is None:
                nxt = end_cap if end_cap and end_cap > d else d
            spans.append((d, nxt))
        out[g] = spans
    return out


def gcd_set(seq, minutes, cast_start=()):
    """Spells to DROP from `seq` (a sorted [(t, spell)]) as not-a-button.

    The global cooldown is the one hard constraint the client cannot violate, so
    it is the only reliable discriminator available offline. Two casts closer
    than the floor cannot both have consumed a global; one of them was a proc.

    Blame is assigned to the MORE FREQUENT spell of each colliding pair, which
    matters: a button that triggers a proc storm collides just as often as the
    storm does, and blaming both drops the button. Hammer of Light collides with
    100% of its casts purely because Empyrean Hammer fires on top of it; once
    the proc is removed the button is exonerated, which only works if the proc
    loses the tie.

    Elimination stops as soon as the remainder is plausibly GCD-consistent, and
    refuses to drop a spell whose collisions are not systematic. Being too eager
    here strips a mage's entire rotation -- Arcane Blast, Missiles and Barrage
    all collide with each other under a naive rule -- and reports 5 casts/min
    for a player who never stopped casting.

    `cast_start` is the player's SPELL_CAST_START stream, and it is the one
    positive proof available: the client only emits a start for a spell with a
    cast bar, and a proc has no cast bar. Without it the most-frequent-loses tie
    break can eat the rotation's core button -- a Devourer DH's Consume is a
    1.4s hard cast pressed 224 times a key, more often than the instant procs it
    collides with, so it lost every tie and the spec reported 22 casts/min for a
    player who was hard-casting 26% of the fight."""
    hard_cast = collections.Counter(s for _, s in cast_start)
    n_all = collections.Counter(s for _, s in seq)
    proven = {s for s, c in hard_cast.items() if c >= 0.5 * n_all.get(s, 0)}
    banned = set(s for _, s in seq if s in K.KNOWN_PASSIVE) - proven
    n = collections.Counter(s for _, s in seq)
    # If the aggregate rate never violated the GCD in the first place, there is
    # nothing to detect and running elimination can only do harm. An Arcane Mage
    # casting 29/min is inside the physical cap; an earlier version stripped
    # Arcane Blast and Missiles anyway, because they collide with each other on
    # Clearcasting procs, and reported 5 casts/min for a player who never
    # stopped casting.
    if minutes > 0 and len(seq) / minutes <= 55:
        return banned
    while True:
        cur = [(t, s) for t, s in seq if s not in banned]
        if len(cur) < 2:
            break
        blame, total = collections.Counter(), 0
        for (t0, s0), (t1, s1) in zip(cur, cur[1:]):
            if t1 - t0 < GCD_FLOOR:
                total += 1
                blame[s0 if n[s0] >= n[s1] else s1] += 1
        if not blame or total <= 0.08 * len(cur):
            break
        cand = next((c for c, _ in blame.most_common() if c not in proven), None)
        if cand is None:
            break
        hits = blame[cand]
        if hits < 0.25 * n[cand]:
            break
        banned.add(cand)
    return banned


def sustained_gap(times, k=3):
    """Fastest SUSTAINED seconds-per-cast this spell was ever kept up.

    Not the minimum gap between two casts: an ability with charges gets dumped
    twice back to back, and a raw minimum reads that 1-second double-tap as a
    1-second cooldown, which then claims hundreds of presses were available.
    Measuring the tightest window covering k+1 casts and dividing by k prices
    the charges in without being fooled by them."""
    if len(times) < 2:
        return None
    t = sorted(times)
    if len(t) <= k:
        return min(b - a for a, b in zip(t, t[1:]))
    return min((t[i + k] - t[i]) / k for i in range(len(t) - k))


def learn_abilities(run, registry):
    """Teach the registry every cooldown and buff duration this key showed.

    Cooldowns are a fact about the game, not about the player, so they are
    pooled across every player and every run rather than re-derived per key."""
    for guid in run.players:
        spec = run.players[guid].get("spec")
        by_spell = collections.defaultdict(list)
        for t, spell in run.casts.get(guid, []):
            by_spell[spell].append(t)
        # Which specs have been SEEN pressing a thing. Proof runs one way: this
        # can only ever say "yes, that spec has it", never "no, it does not".
        if spec is not None:
            for spell in by_spell:
                seen = registry.by_spec.setdefault(spell, [])
                if spec not in seen:
                    seen.append(spec)
        for spell, times in by_spell.items():
            registry.learn_cooldown(spell, sustained_gap(times))
        for spell, evs in run.self_aura.get(guid, {}).items():
            # CLOSED intervals only, and the median of them. An aura with no
            # matching REMOVED gets closed at the end of the log, which measured
            # Demon Spikes -- a six-second buff -- at 120 seconds and handed
            # every tank a 100% mitigation ceiling regardless of their kit.
            closed, open_at = [], None
            for t, ev in sorted(evs, key=lambda x: x[0]):   # ties keep FILE order
                if ev in ("SPELL_AURA_APPLIED", "SPELL_AURA_REFRESH"):
                    if open_at is None:
                        open_at = t
                elif ev == "SPELL_AURA_REMOVED" and open_at is not None:
                    closed.append(t - open_at)
                    open_at = None
            if len(closed) >= 3:
                registry.learn_duration(spell, statistics.median(closed))


MIN_MEANINGFUL_CD = 15.0


def presses_available(spell, seconds, registry):
    """How many times this spell could have been pressed in `seconds`.

    Returns None when the registry has never seen the spell twice, which is the
    honest answer -- an ability with no measured cooldown cannot have a
    denominator, and inventing one is how a player gets marked down for a button
    nobody can prove they had.

    Also returns None for anything recharging faster than fifteen seconds.
    "Presses available" only means something for an ability gated by its
    cooldown; for a spammable one it is a fiction. Spellsteal has effectively no
    cooldown, and pricing it this way told an Arcane Mage he had 462 utility
    presses available and had used six of them."""
    cd = registry.cooldowns.get(spell)
    if not cd or cd < MIN_MEANINGFUL_CD:
        return None
    return seconds / cd


def shared_cooldown_pairs(run, registry, min_casts=5):
    """Spell pairs that never fire close together -- i.e. probably share a CD.

    The guard is the whole point. Two abilities pressed three times each will
    show a large gap between them by coincidence, and a detector without a
    sample-size floor invents shared cooldowns everywhere. Five casts each, and
    the observed separation has to hold against BOTH spells' own cooldowns
    before the pair is believed."""
    pairs = set()
    for guid in run.players:
        by_spell = collections.defaultdict(list)
        for t, spell in run.casts.get(guid, []):
            by_spell[spell].append(t)
        live = {s: sorted(v) for s, v in by_spell.items() if len(v) >= min_casts}
        for a, b in itertools.combinations(sorted(live), 2):
            cda, cdb = registry.cooldowns.get(a), registry.cooldowns.get(b)
            if not cda or not cdb:
                continue
            cross = min(abs(y - x) for x in live[a] for y in live[b])
            if cross >= 0.9 * max(cda, cdb):
                pairs.add((a, b))
    return pairs


def idle_seconds(times, windows, dead=(), thresh=2.5, gcd=1.5):
    """Seconds doing nothing WHILE IN COMBAT AND ALIVE.

    Time spent dead is removed: that is already paid for by the survival score,
    and charging it twice buries anyone who died once."""
    total = 0.0
    for a, b in windows:
        inside = [t for t in times if a <= t <= b]
        edges = [a] + inside + [b]
        for lo, hi in zip(edges, edges[1:]):
            if hi - lo > thresh:
                total += max(0.0, (hi - lo) - gcd - overlap(lo, hi, dead))
    return total


def aura_intervals(events, end_cap):
    """[(on, off)] from a stream of (t, APPLIED/REFRESH/REMOVED).

    Naively zipping applications against removals overcounts badly whenever a
    refresh lands without an intervening removal.

    Sorted by TIMESTAMP ONLY, and the stability of that sort is load-bearing.
    `sorted(events)` sorts the (t, event) tuples, so a tie breaks alphabetically
    and "SPELL_AURA_APPLIED" sorts before "SPELL_AURA_REMOVED" -- which silently
    reverses them. Many buffs log a refresh as REMOVED immediately followed by
    APPLIED at the same timestamp; reversing that pair closes the window at the
    instant it should have re-opened and opens a zero-length one in its place.

    It is not a rounding error. A Vengeance DH with 78 such pairs in one key read
    as 31% Demon Spikes uptime against a true 100%, and the mitigation axis
    published a collapse from 74 to 26 that never happened. Events arrive from
    the collector in file order, so sorting on `x[0]` alone keeps ties in the
    order the game wrote them."""
    out, open_at = [], None
    for t, ev in sorted(events, key=lambda x: x[0]):
        if ev in ("SPELL_AURA_APPLIED", "SPELL_AURA_REFRESH"):
            if open_at is None:
                open_at = t
        elif ev == "SPELL_AURA_REMOVED" and open_at is not None:
            out.append((open_at, t))
            open_at = None
    if open_at is not None:
        out.append((open_at, end_cap))
    return merge(out)


def merge(spans):
    if not spans:
        return []
    spans = sorted(spans)
    out = [list(spans[0])]
    for a, b in spans[1:]:
        if a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [tuple(x) for x in out]


def mitigation_spans(run, guid, windows, end_cap):
    """When ANY personal defensive was up on this player, DURING combat.

    Clipped to the combat windows because the uptime it feeds is divided by
    combat seconds. A defensive held through a pull timer or a walk between
    packs is real, but counting it against a denominator that excludes those
    seconds produced uptimes above 100%."""
    spans = []
    for spell, evs in run.self_aura.get(guid, {}).items():
        if spell in K.PERSONAL_DEFENSIVES:
            spans += aura_intervals(evs, end_cap)
    return clip(merge(spans), windows)


def clip(spans, windows):
    """The parts of `spans` that fall inside `windows`."""
    out = []
    for a, b in spans:
        for wa, wb in windows:
            lo, hi = max(a, wa), min(b, wb)
            if hi > lo:
                out.append((lo, hi))
    return merge(out)


def achievable_uptime(run, guid, combat, registry, shared=()):
    """The best mitigation uptime this player's KIT could have produced.

    Uptime scored against a flat band grades the class. Demon Spikes has charges
    and comes back every few seconds; a Warrior's Shield Wall is a two-minute
    wall, and no amount of skill closes that gap. Duration divided by cooldown
    gives the ceiling each button can hold on its own, and the sum of those
    ceilings is what the player was actually playing for.

    The kit is what they TALENTED plus what they pressed, and the talent half is
    the part that has to be there. Built from presses alone the denominator is
    self-fulfilling in the player's favour: skip a defensive all key and it
    leaves your own ceiling, the ratio is measured against the smaller number,
    and the grade goes UP for not pressing it. That is the same blind spot the
    utility axis was rebuilt to close, and it lived on in this function for
    another version. An untalented defensive still never enters the denominator
    -- rule 2, nobody is scored on a button they do not have."""
    total, seen = 0.0, set()
    owned = {s for _t, s in run.casts.get(guid, [])}
    owned |= K.buttons(run.players.get(guid) or {}, registry)
    for spell in owned:
        if spell not in K.PERSONAL_DEFENSIVES:
            continue
        cd = registry.cooldowns.get(spell)
        dur = registry.durations.get(spell)
        if not cd or not dur:
            continue
        if any(spell == b and a in seen for a, b in shared):
            continue          # its partner already paid for this slice of time
        seen.add(spell)
        total += min(dur / max(cd, 3.0), 1.0)
    return min(total, 1.0) if total else None


def worst_windows(events, width=5.0, top=8):
    """The player's heaviest incoming-damage windows, as [(start, end, amount)].

    Sliding a fixed window over the damage they took finds the moments a
    defensive was actually for, which is a different question from how much they
    took overall."""
    if not events:
        return []
    evs = sorted(events)
    out, j, run_sum = [], 0, 0
    for i, (t, amt, _, _, _) in enumerate(evs):
        run_sum += amt
        while evs[j][0] < t - width:
            run_sum -= evs[j][1]
            j += 1
        out.append((t - width, t, run_sum))
    out.sort(key=lambda x: -x[2])
    picked = []
    for a, b, v in out:
        if all(b < x or a > y for x, y, _ in picked):
            picked.append((a, b, v))
        if len(picked) >= top:
            break
    return picked


def interrupt_board(run, registry):
    """Every kickable cast the dungeon offered, and what happened to it.

    A spell counts as an opportunity only once some log has PROVEN it
    interruptible. Never having seen it kicked proves nothing -- the group may
    simply have had nothing up -- so unknown spells are left out rather than
    counted as misses. The board therefore understates early and sharpens as the
    registry fills."""
    kicked_at = collections.defaultdict(list)
    for t, guid, sid, _name in run.interrupts:
        kicked_at[sid].append((t, guid))

    cc_on = collections.defaultdict(list)
    for t, guid, _spell, mob in run.cc_on_enemy:
        cc_on[mob].append((t, guid))

    opps, by_spell = [], collections.defaultdict(lambda: [0, 0])
    kick_credit, cc_credit = collections.Counter(), collections.Counter()
    used, used_cc = set(), set()
    for t, mob, sid, name in run.enemy_start:
        if sid not in registry.interruptible:
            continue
        by_spell[name][0] += 1
        hit = None
        for i, (kt, kg) in enumerate(kicked_at.get(sid, [])):
            if (sid, i) in used or not (t <= kt <= t + 8.0):
                continue
            hit = (i, kg)
            break
        if hit:
            used.add((sid, hit[0]))
            kick_credit[hit[1]] += 1
            by_spell[name][1] += 1
            opps.append((t, name, hit[1]))
            continue
        # No kick. A stun landing on the caster mid-cast stopped it just as
        # dead, and is the only way a kickless spec ever shows up in this column.
        stop = None
        for i, (ct, cg) in enumerate(cc_on.get(mob, [])):
            if (mob, i) in used_cc or not (t <= ct <= t + 8.0):
                continue
            stop = (i, cg)
            break
        if stop:
            used_cc.add((mob, stop[0]))
            cc_credit[stop[1]] += 1
            by_spell[name][1] += 1
            opps.append((t, name, stop[1]))
            continue
        # Nobody stopped it -- but did it actually go off? A caster that died
        # mid-cast never landed the spell, and counting that as a leak both
        # inflates the group's failures and punishes them for killing the mob.
        # One key showed 33 Lightspore Shot starts, 11 kicked and only 11
        # landed; the other 11 were corpses.
        landed = any(t <= ct <= t + 15.0
                     for ct in run.enemy_success_t.get((mob, sid), ()))
        if landed:
            opps.append((t, name, None))
        else:
            by_spell[name][0] -= 1        # not an opportunity anyone failed
    total = len(opps)
    stopped = sum(1 for _, _, g in opps if g)
    credit = collections.Counter()
    credit.update(kick_credit)
    credit.update(cc_credit)
    return {"total": total, "stopped": stopped, "credit": credit,
            "kick_credit": kick_credit, "cc_credit": cc_credit,
            "by_spell": dict(by_spell), "opportunities": opps,
            "leaked": sorted(((n, v[0] - v[1]) for n, v in by_spell.items()),
                             key=lambda x: -x[1])}


def avoidable_damage(run, abilities):
    """Damage a player took that the rest of the party did not, per spell.

    This is the load-bearing idea for mechanics. Unavoidable raid damage lands
    on everyone roughly equally -- in one key, Spouting Floret hit all five for
    29-40 ticks each and is nobody's mistake. A spell that hit one player six
    times as often as the party median is that player standing in it.

    The tank is held to a looser threshold because standing in melee range is
    the job, and abilities BigWigs flags TANK are dropped for the tank outright."""
    per = {}
    tank = next((g for g, p in run.players.items() if p["role"] == "tank"), None)
    tank_flagged = {a["name"] for a in abilities.values() if "TANK" in a["flags"]}
    melee = {g for g, p in run.players.items() if p["spec"] in K.MELEE}

    spells = set()
    for g in run.players:
        spells |= set(run.ticks_by_spell.get(g, {}))
    spells.discard("Melee")

    for g in run.players:
        cohort = melee if g in melee else set(run.players) - melee
        peers = [o for o in cohort if o != g]
        if len(peers) < 2:                    # too small to be a fair reference
            peers = [o for o in run.players if o != g]
        excess_amt, detail = 0, []
        for sp in spells:
            mine = run.ticks_by_spell[g].get(sp, 0)
            if mine < 3:
                continue
            if g == tank and sp in tank_flagged:
                continue
            if g == tank:
                whole = sum(run.ticks_by_spell[o].get(sp, 0) for o in run.players)
                if whole and mine / whole >= 0.80:
                    # The tank ate essentially all of it. That is what a frontal
                    # or a tank swipe looks like from the log, and BigWigs only
                    # flags TANK on 8 abilities in the whole season, so the flag
                    # cannot be relied on to catch them. One boss barrage put 124
                    # of its 130 hits on the tank and read as 33 million damage
                    # of bad positioning.
                    continue
            if sp in run.stacking.get(g, ()):
                # Stacking debuff: the ticks follow from the application, not
                # from where the player was standing when they landed.
                continue
            others = [run.ticks_by_spell[o].get(sp, 0) for o in peers]
            others = [x for x in others if x > 0]
            if len(others) == 1:
                # One peer is not a baseline. This is how a tank-targeted bleed
                # that happened to splash onto one melee reads as the tank
                # standing in something. Skip outright -- emptying the list
                # instead routes into the med == 0 branch below, which attributes
                # half the damage, so "one peer took just as much as I did" scored
                # identically to "nobody comparable took it at all". In a 5-player
                # group split into two cohorts there are only two peers, so a
                # single peer at zero triggered this on every party-wide bleed.
                continue
            med = statistics.median(others) if others else 0
            factor = 3.0 if g == tank else 2.0
            if med == 0:
                # Nobody comparable took it at all. That is sometimes one player
                # standing somewhere alone, and sometimes a mob picking one
                # target -- an enemy leap that lands a bleed reads identically to
                # a puddle nobody else walked in, and the log does not
                # distinguish them. Attribute half rather than all, and demand
                # more repetitions before saying anything at all.
                if g == tank or mine < 8:
                    continue
                amt = run.taken_by_spell[g][sp] * 0.5
                excess_amt += amt
                detail.append((sp, mine, 0, amt))
                continue
            if mine > factor * med:
                share = (mine - med) / mine
                amt = run.taken_by_spell[g][sp] * share
                excess_amt += amt
                detail.append((sp, mine, med, amt))
        detail.sort(key=lambda x: -x[3])
        per[g] = {"amount": excess_amt, "detail": detail[:5]}
    return per


DISPEL_GCD = 8.0


def reachable_dispels(windows, registry):
    """How many of these debuffs ONE dispeller could actually have removed.

    Opportunities are counted per debuff instance, and instances overlap: in a
    single Blinding Vale key five were live at the same moment, against a Cleanse
    that recharges in about nine seconds. Scoring 7 of 23 there charges a healer
    for sixteen debuffs, most of which fell off on their own before the button
    was back. That is rule 2 -- never score a player on something they could not
    do -- being broken by the denominator rather than by the axis.

    So the denominator is capped at what one presser could reach: earliest
    deadline first, one press per recharge, and a press only counts if it lands
    while the debuff is still up. Measured on 2026-08-22 this moves a real key
    from 30% to 47% and another from 40% to 53% -- the gap is smaller than the
    raw count says and it does not vanish.

    Deliberately ONE dispeller, not the party's total. A healer is not graded on
    whether the Feral happened to also press Soothe."""
    if not windows:
        return 0
    cd = min([c for c in (registry.cooldowns.get(s) for s in K.DISPEL_SPELLS)
              if c] or [DISPEL_GCD])
    cd = max(cd, DISPEL_GCD)
    ready, taken = float("-inf"), 0
    for a, b in sorted(windows, key=lambda w: w[1]):
        press = max(a, ready + cd)
        if press <= b:
            taken += 1
            ready = press
    return taken


def dispel_board(run, registry):
    """Dispellable debuffs that landed, and how fast they came off.

    Thin by construction: LittleWigs ships no dispel metadata for Midnight, so
    the only debuffs known dispellable are ones somebody has already dispelled
    in a log this tool has read."""
    capable = any(K.DISPELS.get(p["spec"]) for p in run.players.values())
    if not capable:
        return {"capable": False, "opportunities": 0, "cleansed": 0, "latency": [],
                "worst": []}
    done = collections.defaultdict(list)
    for t, guid, sid, _n, dest in run.dispels:
        done[(sid, dest)].append(t)

    # Rebuild each debuff into the intervals it was actually ON someone. Counting
    # applications instead measures how often a mob pressed a button: in one
    # Blinding Vale key Spore Spines, Toxic Spew and Blight Resin logged 15, 15
    # and 12 applications, which says they are equivalent -- but the union of
    # intervals says Spore Spines sat on somebody for 273 seconds and Blight
    # Resin for 5 seconds of 1-second flickers. Only one of those is worth a GCD.
    starts = collections.defaultdict(list)
    for t, sid, name, dest in run.debuffs:
        starts[(sid, dest)].append((t, name))
    opps = cleansed = sustained = 0
    windows = []            # (start, end) of every counted opportunity
    lat, missed = [], collections.Counter()
    hurt, seen = collections.Counter(), collections.defaultdict(set)
    left_up = collections.Counter()
    for key, applied in starts.items():
        sid, dest = key
        name = applied[0][1]
        if not run.taken_by_spell.get(dest, {}).get(name):
            # Dispellable is not the same as worth dispelling. A debuff that
            # never dealt the target any damage is not a failure to cleanse, and
            # counting it drags a healer down for ignoring something harmless.
            continue
        known = sid in registry.dispellable
        ends = sorted(run.debuff_removed.get(key, []))
        open_until = -1.0
        for t, _n in sorted(applied):
            if t <= open_until:
                continue                      # a refresh inside a live instance
            end = next((e for e in ends if e > t), t + 30.0)
            open_until = end
            dur = end - t
            if dur < 4.0:
                # Too short to react to. Charging a healer for not cleansing a
                # one-second flicker measures reflexes, not decisions.
                continue
            # Counted whether or not the type is proven. This is the denominator
            # for "did you press dispel at all", which is answerable without
            # knowing which specific debuffs were cleansable.
            sustained += 1
            if not known:
                continue
            opps += 1
            windows.append((t, end))
            seen[name].add(dest)
            hit = next((d for d in done.get(key, []) if t <= d <= end), None)
            if hit:
                cleansed += 1
                lat.append(hit - t)
            else:
                missed[name] += 1
                left_up[name] += dur
    # The number that makes the case. A debuff left up is only worth arguing
    # about in terms of what it cost, and a dispellable one that did 22 million
    # damage over a key is a bigger finding than any letter grade.
    for name in missed:
        hurt[name] = sum(run.taken_by_spell[d].get(name, 0) for d in seen[name])
    worst = sorted(((n, missed[n], left_up[n], hurt[n]) for n in missed),
                   key=lambda x: -x[2])[:4]
    return {"capable": True, "opportunities": opps, "cleansed": cleansed,
            "reachable": reachable_dispels(windows, registry),
            "latency": lat, "worst": worst, "sustained": sustained,
            "attempts": collections.Counter(g for _t, g, *_ in run.dispels),
            "friendly_attempts": collections.Counter(
                g for _t, g, *_ in run.friendly_dispels),
            "seconds": sum(left_up.values()), "cost": sum(hurt.values())}


def healing_need(run):
    """How much health the party lost that somebody ELSE had to put back.

    This is the healer's real denominator, and getting it wrong is what made
    the report argue with itself -- saying very little healing was needed in a
    key and then marking the healer down for not doing much healing. The old
    version divided by every point of damage the party took.

    Book-keeping that has to stay straight, because two of these are easy to
    double-count:

      dmg_taken   is the damage that LANDED -- after mitigation and after
                  absorbs. Absorbed damage is therefore not in it, and must not
                  be subtracted from it.
      self_heal   a player healing themselves is health the healer did not have
                  to restore. A Vengeance DH puts back sixty million a key.
      overkill    damage past zero health. Never healable by anyone.
      absorb_taken a shield somebody else put on you prevented damage that
                  never landed, so it is not in dmg_taken -- but it IS work an
                  outside source did, so it is added back in.

    `per_sec` is carried so a caller can tell "the healer covered little" apart
    from "there was little to cover"."""
    gross = sum(run.dmg_taken.values())
    lost = sum(max(0, run.dmg_taken[p] - run.overkill[p] - run.self_heal[p])
               for p in run.players)
    need = lost + sum(run.absorb_taken[p] for p in run.players)
    return {"gross": gross, "self": max(gross - lost, 0), "need": need,
            "per_sec": need / max(run.dur, 1.0)}


def healer_output(run, guid):
    """Healing and absorbs this player put into SOMEBODY ELSE.

    Self-healing is excluded on both sides of the ratio: it is already out of
    the denominator, so leaving it in the numerator would pay a healer twice
    for keeping themselves alive."""
    return ((run.healed[guid] - run.self_heal[guid])
            + (run.absorb_given[guid] - run.self_absorb[guid]))


DEATH_WINDOW = 15.0       # longest stretch examined; the real one is derived
HEALTHY = 0.90            # at or above this, the death had not started yet


def death_forensics(run, windows, avoid):
    """For each death: what landed, what the player had up, and what arrived late.

    The interesting column is `own_cd`: a player who died with every personal
    cooldown unpressed died differently from one who pressed everything and was
    still short, and the two deserve different advice.

    Two things here used to make a death impossible to check against anything
    else:

    `at` is measured from the KEY, not from the first damage event of the run.
    Warcraft Logs and the in-game timer both count from the key, so every death
    in this report was printed about nineteen seconds earlier than the same
    death everywhere else, and cross-referencing one against the other silently
    failed.

    The window is derived rather than fixed at eight seconds. A death that
    arrived over eleven seconds got clipped and a two-second gib got padded
    with damage the player had already healed through, so the `burst` figure
    was answering a different question for every death. It now grows backwards
    only while damage keeps arriving, which is the same thing Warcraft Logs
    reports as "Over"."""
    out = []
    t0 = run.t0 if run.t0 is not None else min((a for a, _ in windows), default=0)
    for t, guid in sorted(run.death_events):
        evs = [e for e in run.taken_events.get(guid, [])
               if t - DEATH_WINDOW <= e[0] <= t + 0.5]
        evs.sort()
        # The window runs back to the last moment the player was near full
        # health, which is the stretch that actually killed them. Health comes
        # out of the advanced block on each damage event, so this is measured
        # rather than assumed; where the client did not write it, fall back to
        # walking back while hits keep arriving within a breath of each other.
        hp = {ht: frac for ht, frac in run.health.get(guid, [])}
        span, prev = [], None
        for e in reversed(evs):
            frac = hp.get(e[0])
            # Health is the reading AFTER the hit, so a healthy one means the
            # death had not started yet and the hit is NOT part of it. Stopping
            # after appending instead of before turned a genuine one-shot into
            # a 3.2-second window by dragging in the tick before it.
            if span and frac is not None and frac >= HEALTHY:
                break
            if prev is not None and prev - e[0] > 3.0 and frac is None:
                break
            span.append(e)
            prev = e[0]
        span.reverse()
        killer = next((e for e in reversed(span) if e[4] > 0),
                      span[-1] if span else None)
        burst = sum(e[1] for e in span)
        over = (span[-1][0] - span[0][0]) if len(span) > 1 else 0.0
        by = collections.Counter()
        for _tt, amt, sp, _src, _ov in span:
            by[sp] += amt
        lo = span[0][0] if span else t
        healed = sum(a for ht, a, _who in run.heal_events.get(guid, [])
                     if lo <= ht <= t + 0.5)
        healers = sorted({run.name_of(who)
                          for ht, a, who in run.heal_events.get(guid, [])
                          if lo <= ht <= t + 0.5 and a > 0 and who != guid})
        own = [sp for ct, sp in run.casts.get(guid, [])
               if t - 12 <= ct <= t and sp in K.PERSONAL_DEFENSIVES]
        # An external cast on YOURSELF is not an external received, and printing
        # it as "Blessing of Protection from Hyporock" under Hyporock's own
        # death read as though somebody had tried to save him. It belongs with
        # the buttons he pressed himself -- which is where the question "was it
        # the right button" gets asked.
        own += [sp for ct, cg, sp, dg in run.ext_casts
                if t - 12 <= ct <= t and dg == guid and cg == guid
                and sp not in own]
        ext = [(sp, run.name_of(cg)) for ct, cg, sp, dg in run.ext_casts
               if t - 12 <= ct <= t and dg == guid and cg != guid]
        out.append({
            "t": t, "at": t - t0, "guid": guid, "name": run.name_of(guid),
            "killer": killer, "burst": burst, "by": by.most_common(3),
            "over": over, "healed": healed, "healers": healers,
            "own_cd": own, "externals": ext,
            "in_avoidable": any(sp in dict((d[0], 1) for d in avoid[guid]["detail"])
                                for sp in by),
        })
    return out


def boss_split(run):
    """Deaths and damage taken, inside boss encounters versus on trash.

    In a key the bosses are the part everyone remembers and the trash is where
    the run is usually lost. Separating them turns "nine deaths" into a
    diagnosis: three bosses killed first pull without a single death, and every
    death in the key on the packs between them."""
    if not run.bosses:
        return None
    spans = [(a, b) for a, b, _n, _k in run.bosses]

    def on_boss(t):
        return any(a <= t <= b for a, b in spans)

    boss_deaths = sum(1 for t, _g in run.death_events if on_boss(t))
    boss_dmg = trash_dmg = 0
    for g in run.players:
        for t, amt, _sp, _src, _ov in run.taken_events.get(g, []):
            if on_boss(t):
                boss_dmg += amt
            else:
                trash_dmg += amt
    return {"bosses": [(n, b - a, k) for a, b, n, k in run.bosses],
            "boss_seconds": sum(b - a for a, b in spans),
            "boss_deaths": boss_deaths,
            "trash_deaths": len(run.death_events) - boss_deaths,
            "boss_damage": boss_dmg, "trash_damage": trash_dmg,
            "wipes": sum(1 for _a, _b, _n, k in run.bosses if not k)}


def pull_structure(run, windows):
    """Shape of the key: pulls, downtime, and where the deaths clustered."""
    if not windows:
        return {}
    lens = [b - a for a, b in windows]
    downs = [w2[0] - w1[1] for w1, w2 in zip(windows, windows[1:])]
    deaths_in = 0
    for t, _g in run.death_events:
        if any(a <= t <= b for a, b in windows):
            deaths_in += 1
    return {"pulls": len(windows), "combat": sum(lens), "longest": max(lens),
            "downtime": sum(downs), "median_down": statistics.median(downs) if downs else 0,
            "deaths_in_combat": deaths_in,
            "deaths_out": len(run.death_events) - deaths_in}


def per_pull(run, windows, board):
    """Every pull in the key, with what it cost.

    A key is not a smooth 26 minutes; it is a dozen fights with walking in
    between, and almost always one or two of them are the whole story. Reporting
    only the totals -- 11 deaths, 51% interrupt conversion -- averages the pull
    that wiped the group twice into eleven that went fine, which is exactly the
    detail somebody needs to change what they do next time.

    `cost` is TIME LOST, not time spent. A six-minute pull that killed 39
    enemies without a death is the job being done, and ranking on duration put
    it at the top of the list while the two pulls that actually wiped the group
    sat below it. A pull that wiped is wasted in full -- the fight, the walk
    back, and the timer penalty -- because the pack has to be killed again
    afterwards. A pull that held costs only the deaths it took."""
    if not windows:
        return []
    ends = {b: i for i, (_a, b) in enumerate(windows)}
    opp = collections.Counter()
    stop = collections.Counter()
    for t, _name, who in board.get("opportunities", ()):
        for i, (a, b) in enumerate(windows):
            if a <= t <= b:
                opp[i] += 1
                if who:
                    stop[i] += 1
                break
    out = []
    for i, (a, b) in enumerate(windows):
        deaths = [g for t, g in run.death_events if a <= t <= b + 3.0]
        taken = sum(amt for g in run.players
                    for t, amt, *_ in run.taken_events.get(g, []) if a <= t <= b)
        kills = sum(1 for t in run.kill_times if a <= t <= b + 3.0)
        # A wipe is charged the walk back as well as the fight.
        after = (windows[i + 1][0] - b) if i + 1 < len(windows) else 0.0
        wipe = len(set(deaths)) >= max(3, len(run.players) - 1)
        out.append({
            "n": i + 1, "start": a, "end": b, "seconds": b - a,
            "at": a - (run.t0 if run.t0 is not None else windows[0][0]),
            "deaths": len(deaths), "wipe": wipe, "taken": taken, "kills": kills,
            "opportunities": opp[i], "stopped": stop[i],
            "downtime_after": after,
            "cost": (b - a + after + 5.0 * len(deaths)) if wipe
                    else 5.0 * len(deaths),
        })
    return out


def time_ledger(run, windows, pulls):
    """Where the key's minutes actually went.

    The timer the game reports is not wall clock -- it is wall clock plus five
    seconds a death -- so a report that shows only the clear time hides a cost
    the group paid. Splitting it names the parts that are recoverable."""
    combat = sum(b - a for a, b in windows)
    penalty = 5.0 * len(run.death_events)
    wipe_walk = sum(p["downtime_after"] for p in pulls if p["wipe"])
    downtime = sum(p["downtime_after"] for p in pulls)
    total = run.key.timer_seconds if (run.key and run.key.timer_seconds) else run.dur
    lead_in = max(0.0, (windows[0][0] - run.t0) if run.t0 is not None else 0.0)
    return {"total": total, "combat": combat, "routing": max(0.0, downtime - wipe_walk),
            "wipe_recovery": wipe_walk, "death_penalty": penalty, "lead_in": lead_in,
            "unaccounted": max(0.0, total - combat - downtime - penalty - lead_in)}


def resource_waste(run, guid):
    """Resource generated and thrown away, as a share of what was generated.

    A Holy Paladin holding five Holy Power while Holy Shock refunds another one
    is spending globals to produce nothing, and it is invisible in HPS.

    Two things the naive version got wrong. `amount` is the resource actually
    RECEIVED and `overEnergize` is the part that spilled, so the total generated
    is the sum of the two -- dividing the spill by `amount` alone produced a
    229% waste figure for an Arcane Mage sitting at full charges. And some
    resources are excluded outright; see NOT_WASTE for which and why."""
    gained, wasted = run.gained.get(guid, {}), run.wasted.get(guid, {})
    out = []
    for pw in set(gained) | set(wasted):
        if pw in NOT_WASTE:
            continue
        got, lost = gained.get(pw, 0.0), wasted.get(pw, 0.0)
        total = got + lost
        if total >= 20 and lost:
            out.append((POWER.get(pw, f"power {pw}"), round(lost), round(total),
                        lost / total))
    out.sort(key=lambda x: -x[3])
    return out


# powerType ids. Anything not here prints as its raw id rather than being
# guessed at.
POWER = {"0": "Mana", "1": "Rage", "2": "Focus", "3": "Energy",
         "4": "Combo Points", "5": "Runes", "6": "Runic Power",
         "7": "Soul Shards", "8": "Astral Power", "9": "Holy Power",
         "11": "Maelstrom", "12": "Chi", "13": "Insanity",
         "16": "Arcane Charges", "17": "Fury", "18": "Pain", "19": "Essence"}

# Resources whose overflow is not a mistake, so reporting it is a false signal.
#
#   Mana            at full there is nowhere to put a proc.
#   Arcane Charges  the spec is DESIGNED to sit at cap -- the charges are a
#                   stacking multiplier held at four, not a currency spent
#                   down. An Arcane Mage reads 70% overcapped playing
#                   perfectly, and printing that under "habits" implies a
#                   fault. This one is a judgement call about a single
#                   resource whose design differs from the rest; Rage and Fury
#                   are NOT excluded, because overcapping those really does
#                   mean a spender was not pressed.
NOT_WASTE = {"0", "16"}


def mash(run, guid, min_ratio=1.5, min_n=40):
    """Presses the client REFUSED because the ability was still on cooldown.

    Reported per successful cast, not as a raw count. A raw count measures how
    often an ability is used as much as how hard it is mashed -- 478 refusals
    on Holy Shock looks alarming until you notice it was cast 200 times, and a
    rarely-pressed button with 60 refusals is the more interesting one.

    Be careful what this is: a refused press costs no global and no cast. It is
    an input habit, not a lost ability, and it is reported rather than graded.
    Its value is as a proxy for where the keyboard time is going."""
    per = run.failed_spell.get(guid)
    if not per:
        return []
    cast_n = collections.Counter(sp for _t, sp in run.casts.get(guid, []))
    out = []
    for sp, n in per.items():
        used = cast_n.get(sp, 0)
        ratio = n / used if used else float(n)
        if n >= min_n and ratio >= min_ratio:
            out.append((sp, n, used, ratio))
    out.sort(key=lambda x: -x[3])
    return out[:4]
