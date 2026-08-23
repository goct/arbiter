"""The grading model.

Three rules hold everywhere in this file, and every axis obeys them:

  1. Score against a reference the RUN provides, not an absolute band. A +2 and
     a +9 are different games; a healer who pushed 60k HPS in a key where the
     party took 200k/s did a harder job than one who pushed 90k in a key where
     it took 500k/s. Absolute bands mostly measure key level.
  2. Never score a player on something they could not do. A Holy Paladin has no
     interrupt; the tank is SUPPOSED to be taking the damage; a dungeon with no
     dispellable debuff cannot be failed on dispels.
  3. When an axis does not apply, DROP it and renormalise the remaining weights.
     Scoring an inapplicable axis as zero is the single most common way a
     grading model ends up measuring group composition instead of play.

Rule 3 is why axes carry `None` rather than 0. `Axis(None)` disappears and the
rest of the weights are rescaled, so a key with nothing to kick grades on what
actually happened in it.

The letter curve is shifted down from school grades on purpose: 100 means
"nothing left on the table", not "fine". An even, competent performance lands
around 75 by construction, which is a B-. Read the sub-scores, not the letter.
"""

import collections
import statistics

from . import derive as D
from . import knowledge as K
from . import logfile as K_L


class Axis:
    """`raw` is the underlying measurement before banding. It is carried so the
    bands can be re-derived from a corpus of real keys rather than guessed at;
    `calibrate.py` reads exactly this."""

    __slots__ = ("name", "score", "weight", "note", "raw")

    def __init__(self, name, score, weight, note="", raw=None):
        self.name, self.score, self.weight, self.note = name, score, weight, note
        self.raw = raw


def band(v, lo, hi):
    if hi == lo:
        return 50.0
    return max(0.0, min(100.0, 100.0 * (v - lo) / (hi - lo)))


def letter(s):
    for cut, name in ((88, "A"), (84, "A-"), (80, "B+"), (76, "B"), (72, "B-"),
                      (68, "C+"), (64, "C"), (60, "C-"), (56, "D+"), (52, "D"),
                      (48, "D-")):
        if s >= cut:
            return name
    return "F"


WEIGHTS = {
    "tank": dict(survival=.20, mitigation=.18, mechanics=.14, interrupts=.18,
                 damage=.12, activity=.10, utility=.08),
    "heal": dict(survival=.16, throughput=.22, response=.16, mechanics=.14,
                 interrupts=.12, dispel=.10, activity=.10, utility=.06),
    # No `utility` for damage dealers. Externals and dispels are things a tank
    # and a healer carry; measured across nine keys a DPS scores a flat ~0 on it
    # regardless of how they played, so all it did was dock every damage dealer
    # the same ten points for the class they logged in on.
    "dps": dict(survival=.18, damage=.26, mechanics=.20, interrupts=.20,
                activity=.16),
}

# Activity means something different per role: a healer who is not casting is
# often waiting on damage, a tank is never not doing something. One band across
# all three grades the role, not the player.
ACTIVITY_BAND = {"tank": (82, 99), "heal": (65, 95), "dps": (70, 98)}

# Utility is situational -- nobody presses crowd control on cooldown -- so the
# ceiling is nowhere near 100% of what the kit offers. Bands set from the corpus
# so an ordinary key lands near 75. Tanks and healers differ because the kits do.
UTILITY_BAND = {"tank": (0.04, 0.31), "heal": (0.06, 0.41), "dps": (0.02, 0.25)}


def party_spikes(run, top=20, width=8.0):
    """The party's worst incoming-damage windows, party-wide.

    Twenty windows of eight seconds, not six of six. Measured across nine keys
    the underlying response ratio is stable either way, but the small sample
    swung it between 1.8 and 3.7 where the wide one holds 2.0 to 2.5 -- and a
    narrow band on top of that turned sampling noise into two-letter grade
    swings for a healer whose behaviour barely changed."""
    evs = []
    for g in run.players:
        evs += [(t, amt) for t, amt, _s, _src, _o in run.taken_events.get(g, [])]
    if not evs:
        return []
    evs.sort()
    out, j, tot = [], 0, 0
    for i, (t, amt) in enumerate(evs):
        tot += amt
        while evs[j][0] < t - width:
            tot -= evs[j][1]
            j += 1
        out.append((t - width, t, tot))
    out.sort(key=lambda x: -x[2])
    picked = []
    for a, b, v in out:
        if all(b < x or a > y for x, y, _ in picked):
            picked.append((a, b, v))
        if len(picked) >= top:
            break
    return picked


def effective_deaths(run):
    """Deaths, discounting the ones that were the whole group going down.

    Dying alone at a pull the rest of the party survived is an individual
    mistake. Dying in the cluster of four that arrived in the same six seconds
    is one group event, and charging every member full price for it grades the
    same failure five times."""
    times = sorted(t for t, _g in run.death_events)
    clusters, cur = [], []
    for t in times:
        # Measured from the FIRST death of the cluster, not the last. Chaining
        # off the last one lets a cluster grow without limit: a player who died
        # alone and thirteen seconds later took the group with him was folded
        # into the wipe he caused and charged half price for it.
        if cur and t - cur[0] > 15:
            clusters.append(cur)
            cur = []
        cur.append(t)
    if cur:
        clusters.append(cur)
    weight_at = {}
    for c in clusters:
        w = 1.0 if len(c) < 3 else 0.5
        for t in c:
            weight_at[t] = w
    out = collections.Counter()
    for t, g in run.death_events:
        out[g] += weight_at.get(t, 1.0)
    return out


DEATH_CURVE = (100.0, 72.0, 50.0, 28.0)


def survival_score(deaths):
    """Interpolate the death curve instead of indexing it.

    Indexing meant rounding, and Python rounds 0.5 to 0 -- so a player whose one
    death was discounted as part of a group wipe came out at exactly 0.5 deaths
    and scored a clean 100. Two players in the calibration corpus died and were
    graded as though they had not."""
    d = max(0.0, deaths)
    if d >= len(DEATH_CURVE) - 1:
        return DEATH_CURVE[-1]
    lo = int(d)
    return DEATH_CURVE[lo] + (DEATH_CURVE[lo + 1] - DEATH_CURVE[lo]) * (d - lo)


def evaluate(run, registry, abilities):
    P = run.players
    # A key whose START and END share a timestamp would divide by zero in every
    # per-second figure below. Rare, but it comes free.
    run.dur = max(run.dur, 1.0)
    windows = D.combat_windows(run.fight)
    combat = sum(b - a for a, b in windows) or run.dur
    end_cap = max((b for _a, b in windows), default=0)
    D.learn_abilities(run, registry)
    # Deliberately NOT wired into scoring. The detector needs far more than nine
    # keys to be trustworthy: at this corpus size it confidently pairs Beacon of
    # Light with Judgment and Fracture with Glide, and feeding those to the
    # availability denominators would silently delete real abilities from them.
    # A wrong correction is worse than no correction. Run it from
    # `derive.shared_cooldown_pairs` as a diagnostic until the corpus is bigger.
    shared = ()
    dead = D.dead_spans(run.dead_from, run.casts, end_cap)

    alive = {g: max(1.0, combat - sum(D.overlap(a, b, dead.get(g, []))
                                      for a, b in windows)) for g in P}
    banned = {g: D.gcd_set(sorted(run.casts.get(g, [])), alive[g] / 60,
                       run.cast_start.get(g, ())) for g in P}
    gcd_casts = {g: [t for t, s in run.casts.get(g, []) if s not in banned[g]]
                 for g in P}
    busy = {g: sorted([t for t, _ in run.casts.get(g, [])]
                      + [t for t, _ in run.cast_start.get(g, [])]) for g in P}
    idle = {g: D.idle_seconds(busy[g], windows, dead.get(g, [])) for g in P}
    act = {g: max(0.0, 100 * (1 - idle[g] / alive[g])) for g in P}
    cpm = {g: 60 * len(gcd_casts[g]) / alive[g] for g in P}

    avoid = D.avoidable_damage(run, abilities)
    board = D.interrupt_board(run, registry)
    disp = D.dispel_board(run, registry)
    deaths = effective_deaths(run)
    spikes = party_spikes(run)
    forensics = D.death_forensics(run, windows, avoid)
    pulls = D.pull_structure(run, windows)
    bypull = D.per_pull(run, windows, board)
    ledger = D.time_ledger(run, windows, bypull)
    bosses = D.boss_split(run)

    dps = {g: run.dmg_done[g] / run.dur for g in P}
    hps = {g: run.healed[g] / run.dur for g in P}
    taken = {g: run.dmg_taken[g] / run.dur for g in P}
    incoming = {g: (run.dmg_taken[g] - run.self_dmg[g]) / run.dur for g in P}

    dps_g = [g for g in P if P[g]["role"] == "dps"]
    dps_total = sum(run.dmg_done[g] for g in dps_g) or 1
    party_taken = sum(run.dmg_taken.values()) or 1

    kickers = [g for g in P if P[g]["spec"] not in K.KICKLESS]
    stopped = board["stopped"]

    rows = {}
    for g, info in P.items():
        r = info["role"]
        ax = []

        d = deaths.get(g, 0)
        ax.append(Axis("survival", survival_score(d), 0,
                       f"{run.deaths[g]} death(s)", d))

        net = max(run.dmg_taken[g] - run.self_dmg[g], 1)
        frac = avoid[g]["amount"] / net
        ax.append(Axis("mechanics", band(1 - frac, 0.76, 1.0), 0,
                       f"{100*frac:.0f}% of intake was damage comparable players "
                       f"did not take", frac))

        if not board["total"]:
            ax.append(Axis("interrupts", None, 0, "nothing kickable in this key"))
        elif info["spec"] in K.KICKLESS:
            ax.append(Axis("interrupts", None, 0, "no interrupt on the bar"))
        else:
            # Two questions, deliberately weighted apart. Whether the group
            # controlled casts at all is largely a route and pull-size decision
            # and belongs to all five; whether YOU pulled your weight inside
            # that is individual. Scoring only the first hands the tank a free
            # 100 and every DPS a zero, because in a key the tank is standing on
            # every pack and gets first crack at everything.
            # The tank is counted for two shares. Not a favour -- it is where
            # they stand. The tank is on top of every pack from the pull, so
            # they get first crack at every cast in the key, and splitting the
            # stops evenly five ways sets a target no ranged DPS can reach and
            # one the tank clears without trying.
            units = sum((2.0 if P[k]["role"] == "tank" else 1.0)
                        * (alive[k] / max(combat, 1)) for k in kickers)
            mine = (2.0 if r == "tank" else 1.0) * (alive[g] / max(combat, 1))
            even = max(stopped * mine / max(units, 1e-9), 1.0)
            share = board["credit"][g] / even
            conv = board["stopped"] / board["total"]
            sub = 0.6 * band(share, 0.15, 1.0) + 0.4 * band(conv, 0.30, 0.85)
            ax.append(Axis("interrupts", sub, 0,
                           f"{board['credit'][g]} stops vs {even:.0f} expected; "
                           f"group stopped {100*conv:.0f}%", share))

        ax.append(Axis("activity", band(act[g], *ACTIVITY_BAND[r]), 0,
                       f"{act[g]:.0f}% engaged while alive in combat", act[g]))

        # Scored against what this player's kit could actually produce, not a
        # flat rate. A Veng DH carries five sigils and a Prot Warrior does not,
        # and banding both against the same presses-per-minute graded the class.
        #
        # CC only enters here for specs with no interrupt. For everyone else a
        # stun that stopped a cast is already paid for under `interrupts`, and
        # counting it twice made the tank's entire utility score a duplicate of
        # a column he had already been graded on.
        cc_here = info["spec"] in K.KICKLESS
        # The kit is what they TALENTED plus what they pressed, not just what
        # they pressed. Deriving it from casts alone meant never using a button
        # exempted you from being graded on it -- one tank in this corpus
        # talented Darkness and Sigil of Misery and pressed neither all night.
        # Baseline abilities never appear in the talent tree, so pressing still
        # counts them in.
        owned = K.buttons(info) | {s for _t, s in run.casts.get(g, [])}
        kit = {s for s in owned
               if s in K.EXTERNALS or s in K.RAID_COOLDOWNS or s in K.DISPEL_SPELLS
               or (cc_here and s in K.CC)}
        avail, priced = 0.0, set()
        for spell in sorted(kit):
            if any(spell == b and a in priced for a, b in shared):
                continue
            n = D.presses_available(spell, alive[g], registry)
            if n:
                priced.add(spell)
                avail += n
        util = (sum(1 for _t, cg, _s, _d in run.ext_casts if cg == g)
                + sum(1 for _t, cg, *_ in run.dispels if cg == g)
                + (board["cc_credit"][g] if cc_here else 0))
        if avail >= 2:
            used = min(util / avail, 1.5)
            ax.append(Axis("utility", band(used, *UTILITY_BAND[r]), 0,
                           f"{util} used of ~{avail:.0f} the kit offered", used))
        else:
            ax.append(Axis("utility", None, 0,
                           "no utility cooldown with a measured recharge yet"))

        if r == "tank":
            spans = D.mitigation_spans(run, g, end_cap)
            up = sum(b - a for a, b in spans) / max(combat, 1)
            worst = D.worst_windows(run.taken_events.get(g, []))
            cov = (sum(1 for a, b, _v in worst if D.overlap(a, b, spans) > 0.5)
                   / len(worst)) if worst else 0
            ceiling = D.achievable_uptime(run, g, combat, registry, shared)
            if ceiling:
                rel = up / ceiling
                ax.append(Axis("mitigation",
                               0.5 * band(rel, .45, .95) + 0.5 * band(cov, .60, 1.0), 0,
                               f"{100*up:.0f}% uptime of the {100*ceiling:.0f}% this kit "
                               f"can hold, {100*cov:.0f}% of worst windows covered", rel))
            else:
                ax.append(Axis("mitigation", 0.5 * band(up, .55, .98)
                               + 0.5 * band(cov, .60, 1.0), 0,
                               f"{100*up:.0f}% uptime, {100*cov:.0f}% of worst windows "
                               f"covered (no measured recharge yet)", up))
            avg = dps_total / max(len(dps_g), 1)
            ratio = run.dmg_done[g] / max(avg, 1)
            ax.append(Axis("damage", band(ratio, .36, .80), 0,
                           f"{dps[g]:,.0f} dps, {100*ratio:.0f}% of the average DPS",
                           ratio))
        elif r == "heal":
            # Absorbs count. A Disc Priest or Preservation Evoker prevents damage
            # rather than healing it back, and a numerator of raw healing alone
            # would score the whole shielding half of the role as zero.
            #
            # The DENOMINATOR is the part that was wrong, and it produced a
            # report that argued with itself: it said very little healing was
            # needed in a key and then marked the healer down for not doing
            # much healing. It was every point of damage the party took --
            # including the 61 million a Vengeance DH healed back on himself,
            # every self-shield the DPS put up, and every point of overkill on
            # a corpse. None of that was ever the healer's to cover, so a party
            # that looked after itself mechanically lowered the healer's grade.
            #
            # What is left is the damage that actually required an outside
            # heal, and the healer is scored on their share OF THAT.
            covered = D.healing_need(run)
            output = D.healer_output(run, g)
            ratio = output / max(covered["need"], 1)
            shielded = run.absorb_given[g] - run.self_absorb[g]
            if covered["need"] <= 0 or covered["per_sec"] < 4000:
                # Nothing to do is not a failure to do it. Below this the key
                # never asked the healer a question, and a ratio computed on
                # noise is worse than no ratio -- rule 3, drop it.
                ax.append(Axis("throughput", None, 0,
                               f"only {covered['need']:,} damage in the whole key "
                               f"needed an outside heal; nothing to score"))
            else:
                # Band set from every key on disk: sixteen runs, +2 to +10,
                # coverage 58% to 82% with a median of 73%, which is what puts
                # an ordinary key at a 75. Worth knowing that all sixteen are
                # the SAME healer, so this encodes one player's range and will
                # want re-cutting the first time a second healer is fed
                # through. It is still a far better reference than the figure
                # it replaces, which moved with how self-sufficient the party
                # happened to be and barely moved with the healing.
                ax.append(Axis("throughput", band(ratio, 0.45, 0.82), 0,
                               f"covered {100*ratio:.0f}% of the {covered['need']:,} "
                               f"that needed an outside heal "
                               f"({100*covered['self']/max(covered['gross'], 1):.0f}% of "
                               f"the party's intake was self-covered or overkill)"
                               + (f"; {shielded:,} absorbed" if shielded else ""),
                               ratio))
            hs = run.heal_times.get(g, [])
            sw = sum(b - a for a, b, _v in spikes) or 1
            inspike = sum(v for t, v in hs if any(a <= t <= b for a, b, _ in spikes))
            rate_all = run.healed[g] / max(combat, 1)
            resp = (inspike / sw) / rate_all if rate_all else 0
            ax.append(Axis("response", band(resp, 1.16, 2.60), 0,
                           f"{resp:.1f}x baseline output during the worst windows", resp))
            if disp["capable"] and disp["opportunities"] >= 5:
                # Against what ONE dispeller could reach, not the raw instance
                # count -- see `derive.reachable_dispels`. Overlapping debuffs
                # against a nine-second recharge made the old denominator a
                # count of what the dungeon applied rather than of what anybody
                # could have removed.
                reach = max(disp.get("reachable") or 0, disp["cleansed"], 1)
                dr = disp["cleansed"] / reach
                note = f"{disp['cleansed']}/{reach} of what one dispeller could reach"
                if reach < disp["opportunities"]:
                    note += (f" ({disp['opportunities']} landed; the rest overlapped "
                             f"a debuff already being cleansed)")
                ax.append(Axis("dispel", band(dr, .05, .70), 0, note, dr))
            elif (K.DISPELS.get(info["spec"]) and disp["sustained"] >= 20
                  and not disp["friendly_attempts"].get(g)):
                # The registry learns a debuff is dispellable by watching someone
                # dispel it, which means a player who never dispels never
                # generates the evidence that would have graded them. Left alone
                # that blind spot is self-fulfilling and rewards the habit it
                # should catch. Pressing the button zero times across a key with
                # this many sustained damaging debuffs is a fact about the
                # player, not about the dungeon, and does not require knowing
                # which specific ones were cleansable.
                ax.append(Axis("dispel", 0.0, 0,
                               f"0 dispels cast in a key with {disp['sustained']} "
                               f"sustained damaging debuffs", 0.0))
            else:
                ax.append(Axis("dispel", None, 0,
                               "too few known-dispellable debuffs to judge"))
        else:
            # Compared against the MEDIAN dps, not the mean and not the best.
            # With three damage dealers the mean is dragged by whoever the
            # dungeon happened to suit, and scoring against the best guarantees
            # two thirds of the field are marked down for existing.
            # Support damage is included. An Augmentation Evoker's entire
            # contribution logs as SPELL_DAMAGE_SUPPORT credited to them, so
            # counting only direct damage scores the spec a flat zero.
            def out(x, _a=alive):
                return (run.dmg_done[x] + run.dmg_support[x]) / max(_a[x], 1)
            med = statistics.median([out(x) for x in dps_g]) if dps_g else 1
            ratio = out(g) / max(med, 1)
            ax.append(Axis("damage", band(ratio, .40, 1.20), 0,
                           f"{dps[g]:,.0f} dps, {100*ratio:.0f}% of the median DPS",
                           ratio))

        w = WEIGHTS[r]
        live = [a for a in ax if a.score is not None and a.name in w]
        tot_w = sum(w[a.name] for a in live) or 1
        for a in live:
            a.weight = w[a.name] / tot_w
        total = sum(a.score * a.weight for a in live)

        # Talented and never pressed. Not scored -- an ability can be talented
        # for one pull in the key and correctly held the rest of the night --
        # but a button that never came off the bar all run is worth naming.
        #
        # Only abilities `knowledge.buttons` will vouch for -- see there for why
        # a wider list drawn from the tree's own `active` flag was tried, shipped
        # and withdrawn inside a day. It filled this line with free root-node
        # talents nobody chose and passives nobody can press.
        pressed = {sp for _t, sp in run.casts.get(g, [])}
        unused = sorted(K.buttons(info) - pressed)

        rows[g] = {
            "axes": ax, "live": live, "total": total, "letter": letter(total),
            "unused": unused,
            "dps": dps[g], "hps": hps[g], "taken": taken[g], "incoming": incoming[g],
            "act": act[g], "cpm": cpm[g], "alive": alive[g],
            "avoid": avoid[g], "kicks": board["kick_credit"][g],
            "cc": board["cc_credit"][g],
            "dispels": sum(1 for _t, cg, *_ in run.dispels if cg == g),
            "dropped": banned[g],
            "waste": D.resource_waste(run, g),
            "mash": D.mash(run, g),
            "rezzes": sum(1 for _t, cg, _dg, _s in run.resurrects if cg == g),
            "mana_low": (run.mana_low.get(g) / run.mana_max[g]
                         if run.mana_max.get(g) else None),
            "overheal": (run.overheal[g] / (run.healed[g] + run.overheal[g])
                         if r == "heal" and run.healed[g] else None),
        }

    return {"rows": rows, "windows": windows, "combat": combat, "board": board,
            "dispel": disp, "spikes": spikes, "forensics": forensics,
            "pulls": pulls, "party_taken": party_taken, "bosses": bosses,
            "by_pull": bypull, "ledger": ledger,
            "need": D.healing_need(run),
            "death_check": {"counted": len(run.death_events),
                            "expected": K_L.counted_deaths(run.key) if run.key
                            else None}}
