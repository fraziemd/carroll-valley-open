"""Generate a full synthetic 2026 event into simulated_scores.json.

NOTHING HERE IS REAL. Every score is invented for testing the scoring engine
before the event. The file it writes is loaded by tools/simulation.py, which
locks the rounds so the app reads these cards instead of scraping PlayThru.

Scoring shape is calibrated on 2025: the field averaged roughly handicap + 11
over par in the best-ball rounds, team scrambles came in around 6 under, and
two-man scrambles between 10 under and level.

    python3 tools/make_simulation.py --rounds 1,3,4
    python3 tools/make_simulation.py --rounds 5 --pair-handicaps r5_handicaps.json

Usage note: rounds 1, 3 and 4 come first. Round 5 needs the Sunday pair
handicaps, which are calculated in the app from rounds 1 and 3, so it is
generated separately afterwards.
"""
import argparse
import json
import os
import random
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.independent_score import (load_event, roster_views,  # noqa: E402
                                     score_best_ball, strokes_on_hole)

OUT = 'simulated_scores.json'


def make_card(holes, over_par, rng):
    """An 18-hole card finishing ``over_par`` over (negative for under).

    Dropped shots land preferentially on hard holes and gained shots on easy
    ones, so cards look like golf rather than noise.
    """
    card = {h: holes[h]['par'] for h in holes}
    hard_first = sorted(holes, key=lambda h: holes[h]['handicap'])
    easy_first = list(reversed(hard_first))

    if over_par >= 0:
        birdies = rng.randint(0, 2) if over_par > 3 else 0
        for h in rng.sample(easy_first[:9], birdies):
            card[h] -= 1
        remaining = over_par + birdies
        weights = [19 - holes[h]['handicap'] for h in hard_first]
        while remaining > 0:
            h = rng.choices(hard_first, weights=weights)[0]
            if card[h] - holes[h]['par'] >= 4:   # no single hole balloons
                continue
            card[h] += 1
            remaining -= 1
    else:
        remaining = -over_par
        weights = [holes[h]['handicap'] for h in easy_first]
        while remaining > 0:
            h = rng.choices(easy_first, weights=weights)[0]
            par_h = holes[h]['par']
            floor = par_h - 2 if par_h >= 5 else par_h - 1   # eagles on par 5s
            if card[h] <= floor or card[h] <= 2:
                continue
            card[h] -= 1
            remaining -= 1

    return {str(h): card[h] for h in sorted(card, key=int)}


def entry(name, style, hole_scores, idx):
    return {
        'name': name,
        'scoring_style': style,
        'golfer_id': f'SIM{idx:06d}',
        'hole_scores': hole_scores,
        'total_score': sum(hole_scores.values()),
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def force_survival_tie(cards, holes, handicaps, foursomes_of, survived):
    """Make the runner-up foursome match the best survival streak.

    The survival tiebreak has never executed against real data — 2025 had an
    outright winner both rounds — so a tie is manufactured rather than waited
    for. The runner-up is given whatever it needs to reach hole ``best``, and
    is then forced to fail hole ``best + 1`` so its streak stops in the same
    place rather than running past it.

    Returns the holes it touched, so the pair-tie pass can avoid them.
    """
    best = max(survived.values())
    leaders = [l for l, c in survived.items() if c == best]
    if len(leaders) > 1:
        return set(), leaders, best

    runner_up = max((l for l in survived if l not in leaders),
                    key=lambda l: survived[l])
    members = runner_up.split(', ')
    touched = set()

    def par_and_strokes(member, hole):
        info = holes[str(hole)]
        return info['par'], strokes_on_hole(handicaps[member], info['handicap'])

    for hole in range(1, best + 1):
        # Survives iff someone is at or under par + his strokes on the hole.
        if any(cards[m][str(hole)] <= sum(par_and_strokes(m, hole))
               for m in members):
            continue
        best_placed = max(members, key=lambda m: par_and_strokes(m, hole)[1])
        cards[best_placed][str(hole)] = sum(par_and_strokes(best_placed, hole))
        touched.add(hole)

    if best + 1 <= 18:
        for m in members:
            need = sum(par_and_strokes(m, best + 1)) + 1
            if cards[m][str(best + 1)] < need:
                cards[m][str(best + 1)] = need
        touched.add(best + 1)

    return touched, leaders + [runner_up], best


def force_pair_tie(cards, holes, handicaps, partners, foursomes, rng,
                   avoid_holes=()):
    """Nudge one pair's card until two pairs tie on relative-to-par.

    The pair-position tiebreak has to run at least once before the event and a
    natural tie can't be relied on. Adds a stroke at a time to the weaker
    partner of the second-placed pair until it falls back to the third.

    ``avoid_holes`` keeps it away from the stretch that decides survival, so
    manufacturing this tie can't quietly undo the survival one.
    """
    for _ in range(400):
        rel = score_best_ball(cards, holes, handicaps, partners,
                              foursomes)['pair_relative']
        order = sorted(rel, key=lambda p: rel[p])
        if len(order) < 3:
            return None
        if rel[order[2]] == rel[order[1]]:
            return (order[1], order[2])
        target = order[1]
        member = max(target.split(' & '), key=lambda m: handicaps[m])
        options = [h for h in cards[member]
                   if int(h) not in avoid_holes and cards[member][h] < 9]
        if not options:
            return None
        cards[member][rng.choice(options)] += 1
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default='event_2026.json')
    ap.add_argument('--rounds', default='1,3,4')
    ap.add_argument('--seed', type=int, default=20260803)
    ap.add_argument('--pair-handicaps', default=None,
                    help='JSON {pair name: handicap}, required for round 5')
    args = ap.parse_args()

    rng = random.Random(args.seed)
    cfg, players, courses = load_event(args.config)
    wanted = [int(r) for r in args.rounds.split(',') if r.strip()]

    existing = {}
    if os.path.exists(OUT):
        existing = json.load(open(OUT)).get('rounds', {})

    out = dict(existing)
    notes = []

    for n in wanted:
        rcfg = cfg['rounds'][str(n)]
        style = rcfg['scoring_style']
        holes = courses[rcfg['course']]['holes']
        par = sum(h['par'] for h in holes.values())
        handicaps, teams, partners, foursomes = roster_views(players, n)

        if style == 'best_ball_individual':
            cards = {}
            for name, hcp in sorted(handicaps.items()):
                over = max(2, int(rng.gauss(hcp + 11, 4.0)))
                cards[name] = make_card(holes, over, rng)
            # Survival first: it can only be arranged over holes 1..best+1,
            # whereas the pair tie can be arranged anywhere, so it is the one
            # that gets to pick its ground.
            surv = score_best_ball(cards, holes, handicaps, partners,
                                   foursomes)['survived']
            touched, _, best = force_survival_tie(cards, holes, handicaps,
                                                  foursomes, surv)
            tie = force_pair_tie(cards, holes, handicaps, partners, foursomes,
                                 rng, avoid_holes=set(range(1, best + 2)))
            res = score_best_ball(cards, holes, handicaps, partners, foursomes)
            surv = res['survived']
            best = max(surv.values())
            tied_surv = [l for l, c in surv.items() if c == best]
            notes.append(
                f"R{n} {rcfg['course']} (par {par}): {len(cards)} cards. "
                f"Pair tie: {' = '.join(tie) if tie else 'NONE'}. "
                f"Survival best {best} holes, {len(tied_surv)} foursome(s) "
                f"tied ({'tiebreak will run' if len(tied_surv) > 1 else 'NO TIE'}).")
            out[str(n)] = [entry(nm, style, c, i)
                           for i, (nm, c) in enumerate(sorted(cards.items()))]

        elif style == 'team_scramble':
            team_names = sorted({p['team'] for p in players.values()})
            rows = []
            for i, team in enumerate(team_names):
                rows.append(entry(team, style,
                                  make_card(holes, int(round(rng.gauss(-6, 1.8))),
                                            rng), i))
            out[str(n)] = rows
            tot = [r['total_score'] for r in rows]
            notes.append(f"R{n} {rcfg['course']} (par {par}): {len(rows)} teams, "
                         f"{min(tot)}-{max(tot)}")

        elif style == 'two_man_scramble':
            if not args.pair_handicaps:
                sys.exit("Round 5 needs --pair-handicaps (the frozen Sunday "
                         "numbers from the app).")
            pair_hcp = json.load(open(args.pair_handicaps))
            sep = rcfg.get('pair_separator', ' and ')
            rows = []
            for i, (pair, hcp) in enumerate(sorted(pair_hcp.items())):
                # Players enter NET scores for Sunday (§4.3), so generate the
                # net card directly: winners a few over, tail around +10.
                rows.append(entry(pair, style,
                                  make_card(holes, max(-2, int(round(rng.gauss(5, 4)))),
                                            rng), i))
            out[str(n)] = rows
            tot = [r['total_score'] - par for r in rows]
            notes.append(f"R{n} {rcfg['course']} (par {par}): {len(rows)} pairs, "
                         f"net {min(tot):+} to {max(tot):+}")
        else:
            notes.append(f"R{n} {style}: skipped (entered by hand)")

    with open(OUT, 'w') as f:
        json.dump({'_warning': 'SYNTHETIC TEST DATA - not real scores',
                   'seed': args.seed,
                   'generated': datetime.now().isoformat(timespec='seconds'),
                   'rounds': out}, f, indent=1)

    print(f"Wrote {OUT} (seed {args.seed})")
    for line in notes:
        print('  ' + line)


if __name__ == '__main__':
    main()
