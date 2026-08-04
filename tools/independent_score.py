"""A second, independent implementation of the event's scoring rules.

Written from SCORING.md alone. It deliberately imports nothing from jdcvo —
not the scoring engine, not even the config loader — and reads the JSON files
itself. The point is to have two implementations that can disagree: comparing
the app against a checker built on the same code would pass no matter what,
because a bug would appear identically on both sides.

Where this disagrees with the app, one of the two is wrong (or SCORING.md
describes something neither does). Investigate; don't assume.

Section numbers in comments refer to SCORING.md.
"""
import json
import os

HOLE_POINTS = [               # §3.1, checked in order
    (-3, 3.5),                # 3 or more under net par
    (-2, 1.5),                # eagle
    (-1, 0.8),                # birdie
    (0, 0.4),                 # par
]
PAIR_POSITION = [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]   # §3.2
SURVIVAL = 2.0                                    # §3.3
SCRAMBLE_POSITION = [3, 2, 1, 0.5, 0]             # §4.2


# --- §2.3 / §2.4 strokes and net score -------------------------------------

def strokes_on_hole(handicap, stroke_index):
    """§2.3 full allocation: floor(H/18) plus one on the H mod 18 hardest."""
    return handicap // 18 + (1 if stroke_index <= handicap % 18 else 0)


def net_score(gross, par, stroke_index, handicap):
    """§2.4 net = gross - strokes, with the net double bogey cap.

    Net double bogey is par + 2 + strokes in gross terms, so once the strokes
    come off the net can never be worse than par + 2.
    """
    s = strokes_on_hole(handicap, stroke_index)
    return min(gross - s, par + 2)


# --- §6 the one tiebreak routine -------------------------------------------

def rank_with_ties(entities, totals, per_hole, holes, first_rank, points_for):
    """Award points to ``entities``, resolving ties per §6.

    ``totals`` ranks them (lowest first); ``per_hole`` maps entity -> {hole:
    value} for the hole-by-hole comparison. Returns {entity: points}.
    """
    awarded = {}
    groups = {}
    for e in entities:
        groups.setdefault(totals[e], []).append(e)

    rank = first_rank
    for total in sorted(groups):
        group = groups[total]
        if len(group) == 1:
            awarded[group[0]] = points_for(rank)
            rank += 1
        else:
            resolved = _break_tie(group, per_hole, holes, rank, points_for)
            awarded.update(resolved)
            rank += len(group)
    return awarded


def _break_tie(group, per_hole, holes, first_rank, points_for):
    """§6: hardest hole first, eliminating whoever loses each hole.

    Losing the hardest hole puts you out of contention for the position, so
    the comparison narrows to that hole's leaders and continues among them.
    Only holes every entity still in contention has played are compared.

    If the survivors never separate, they share the positions they hold and
    the points for those positions are split. Everyone eliminated on the way
    is then ranked for the positions below — being beaten on the hardest hole
    costs you the place, not every point in the group.
    """
    awarded = {}
    remaining = list(group)
    rank = first_rank
    hardest_first = sorted(range(1, 19),
                           key=lambda h: holes[str(h)]['handicap'])

    while remaining:
        if len(remaining) == 1:
            awarded[remaining[0]] = points_for(rank)
            break

        field = list(remaining)
        winner = None
        for hole in hardest_first:
            if not all(hole in per_hole[e] for e in field):
                continue
            vals = {e: per_hole[e][hole] for e in field}
            best = min(vals.values())
            leaders = [e for e in field if vals[e] == best]
            if len(leaders) == 1:
                winner = leaders[0]
                break
            field = leaders

        if winner is None:
            pool = sum(points_for(rank + i) for i in range(len(field)))
            share = pool / len(field)
            for e in field:
                awarded[e] = share
            rank += len(field)
            remaining = [e for e in remaining if e not in field]
            continue

        awarded[winner] = points_for(rank)
        remaining.remove(winner)
        rank += 1

    return awarded


# --- §3 rounds 1 and 3 ------------------------------------------------------

def score_best_ball(cards, holes, handicaps, partners, foursomes):
    """§3: individual hole points, pair position points, survival points."""
    points = {}
    nets = {}
    detail = {}

    # §3.1
    for name, card in cards.items():
        h = handicaps[name]
        player_nets = {}
        total = 0.0
        for hole_str, gross in card.items():
            if not gross:
                continue
            hole = int(hole_str)
            par = holes[str(hole)]['par']
            n = net_score(gross, par, holes[str(hole)]['handicap'], h)
            player_nets[hole] = n
            rel = n - par
            if rel <= -3:
                total += 3.5
            elif rel == -2:
                total += 1.5
            elif rel == -1:
                total += 0.8
            elif rel == 0:
                total += 0.4
            # §3.1: an ace earns nothing extra here; the 8-point bonus is an
            # event-wide extra, entered by hand.
        nets[name] = player_nets
        # §3.1 preserved quirk: rounded to 1dp before anything else is added.
        points[name] = round(total, 1)
        detail[name] = {'hole_points': round(total, 1)}

    # §3.2
    pair_rel = {}
    pair_hole_rel = {}
    pair_of = {}
    for name in nets:
        partner = partners.get(name)
        if not partner or partner not in nets:
            continue
        key = tuple(sorted([name, partner]))
        if key in pair_of.values():
            continue
        label = f"{name} & {partner}"
        pair_of[label] = key
        shared = set(nets[name]) & set(nets[partner])
        hole_rel = {}
        for hole in shared:
            best = min(nets[name][hole], nets[partner][hole])
            hole_rel[hole] = best - holes[str(hole)]['par']
        pair_hole_rel[label] = hole_rel
        pair_rel[label] = sum(hole_rel.values())

    pair_points = rank_with_ties(
        list(pair_rel), pair_rel, pair_hole_rel, holes, 1,
        lambda r: PAIR_POSITION[r - 1] if r <= len(PAIR_POSITION) else 0)

    for label, pts in pair_points.items():
        for member in pair_of[label]:
            points[member] += pts
            detail[member]['pair'] = pts
            detail[member]['pair_relative'] = pair_rel[label]

    # §3.3
    groups = {}
    for name in nets:
        partner = partners.get(name)
        others = foursomes.get(name)
        if not partner or not others:
            continue
        members = sorted([name, partner] + list(others))
        groups[tuple(members)] = members

    survived = {}
    for key, members in groups.items():
        label = ', '.join(members)
        count = 0
        for hole in range(1, 19):
            par = holes[str(hole)]['par']
            if any(nets.get(m, {}).get(hole, 999) <= par for m in members):
                count += 1
            else:
                break
        survived[label] = count

    survival_points = {}
    if survived:
        best = max(survived.values())
        winners = [l for l, c in survived.items() if c == best]
        if len(winners) == 1:
            survival_points[winners[0]] = SURVIVAL
        else:
            # §3.3 tie: best TOTAL foursome net on the hardest hole, then next.
            per_hole = {}
            for label in winners:
                members = label.split(', ')
                per_hole[label] = {
                    hole: sum(nets.get(m, {}).get(hole, 999) for m in members)
                    for hole in range(1, 19)}
            survival_points = _break_tie(
                winners, per_hole, holes, 1,
                lambda r: SURVIVAL if r == 1 else 0)

    for label, pts in survival_points.items():
        if not pts:
            continue
        for m in label.split(', '):
            if m in points:
                points[m] += pts
                detail[m]['survival'] = pts

    return {'points': points, 'detail': detail, 'pair_relative': pair_rel,
            'pair_points': pair_points, 'survived': survived,
            'survival_points': survival_points, 'nets': nets}


# --- §4.2 round 4 team scramble --------------------------------------------

def score_team_scramble(cards, holes):
    """§4.2: Front 9, Back 9 and Overall scored independently, then summed."""
    categories = {'front': range(1, 10), 'back': range(10, 19),
                  'overall': range(1, 19)}
    totals = {t: 0.0 for t in cards}
    detail = {t: {} for t in cards}

    for cat, hole_range in categories.items():
        rel, per_hole = {}, {}
        for team, card in cards.items():
            played = [h for h in hole_range if card.get(str(h))]
            rel[team] = (sum(card[str(h)] for h in played)
                         - sum(holes[str(h)]['par'] for h in played))
            per_hole[team] = {h: card[str(h)] for h in played}
        awarded = rank_with_ties(
            list(rel), rel, per_hole, holes, 1,
            lambda r: SCRAMBLE_POSITION[r - 1] if r <= len(SCRAMBLE_POSITION) else 0)
        for team, pts in awarded.items():
            totals[team] += pts
            detail[team][cat] = (rel[team], pts)

    return {'team_points': totals, 'detail': detail}


# --- §4.3 round 5 two-man scramble -----------------------------------------

def score_two_man_scramble(cards, holes):
    """§4.3: rank by relative to par of the scores AS ENTERED (already net)."""
    rel, per_hole = {}, {}
    for pair, card in cards.items():
        played = [int(h) for h, s in card.items() if s]
        rel[pair] = (sum(card[str(h)] for h in played)
                     - sum(holes[str(h)]['par'] for h in played))
        per_hole[pair] = {h: card[str(h)] for h in played}
    n = len(rel)
    return {
        'pair_points': rank_with_ties(list(rel), rel, per_hole, holes, 1,
                                      lambda r: max(0, n - r)),
        'pair_relative': rel,
    }


# --- §2.5 Sunday pair handicaps --------------------------------------------

def sunday_individual(card, holes, handicap):
    """§2.5 step one, for one round: net-double-bogey capped total minus par."""
    total = 0
    par_total = 0
    for hole_str, gross in card.items():
        if not gross:
            continue
        info = holes[str(int(hole_str))]
        s = strokes_on_hole(handicap, info['handicap'])
        total += min(gross, info['par'] + 2 + s)
        par_total += info['par']
    return total - par_total


def sunday_pair_handicap(figure_a, figure_b):
    """§2.5 step two: int((lower*2 + int(higher/2)) / 3), both truncating."""
    lower, higher = min(figure_a, figure_b), max(figure_a, figure_b)
    return int((lower * 2 + int(higher / 2)) / 3)


# --- loading ----------------------------------------------------------------

def load_event(config_path='event_2026.json'):
    root = os.path.dirname(os.path.abspath(config_path)) or '.'
    cfg = json.load(open(config_path))
    players = json.load(open(os.path.join(root, cfg['players_file'])))['players']
    courses = json.load(open(os.path.join(root, cfg['courses_file'])))['courses']
    return cfg, players, courses


def roster_views(players, round_number):
    by_name = {p['name']: p for p in players.values()}
    handicaps = {n: p['handicap'] for n, p in by_name.items()}
    teams = {n: p['team'] for n, p in by_name.items()}
    partners, foursomes = {}, {}
    for n, p in by_name.items():
        pk, fk = f'round_{round_number}_partner', f'round_{round_number}_foursome'
        if p.get(pk):
            partners[n] = p[pk]
        if p.get(fk):
            foursomes[n] = p[fk]
    return handicaps, teams, partners, foursomes
