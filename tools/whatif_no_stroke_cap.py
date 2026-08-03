"""Diagnostic: would 2025's results have changed under standard stroke allocation?

The engine gives at most 1 stroke per hole (1 if the hole's stroke index <= the
player's handicap), so nobody can receive more than 18. Standard golf allocation
gives floor(H/18) strokes on every hole plus one more on the (H mod 18) hardest,
so a 20-handicap gets 20 and a 30-handicap gets 30.

This re-scores 2025 Rounds 1 and 3 (the only rounds where handicaps affect
points) under both rules and diffs everything. Rounds 2, 4 and 5 take no
handicap input, so they're carried over from the saved 2025 results unchanged.

This is why 2026 switched to full allocation while 2025 stays capped: the 2025
event was scored under the cap and its results are already in the books.

Read-only: writes nothing.

Usage: python3 tools/whatif_no_stroke_cap.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jdcvo import scoring
from jdcvo import test_2025_validation as t

CAPPED = scoring.ALLOCATION_CAPPED
FULL = scoring.ALLOCATION_FULL


def score_r1_r3(allocation):
    """Return (r1_points_by_name, r3_points_by_name) under ``allocation``."""
    (players, courses, name_to_id, id_to_team, handicaps,
     partners, foursomes) = t.get_2025_context()

    handicaps['Matt K'] = 16  # notebook cell 23, before R1
    r1 = scoring.calculate_best_ball_individual(
        [s for s in t.load('golf_scores_round1.json')
         if s['scoring_style'] == 'best_ball_individual'],
        courses['Gettysburg National']['holes'],
        handicaps, partners(1), foursomes(1), allocation=allocation)

    handicaps['Matt K'] = 10  # notebook cell 28, before R3
    r3 = scoring.calculate_best_ball_individual(
        [s for s in t.load('golf_scores_round3.json')
         if s['scoring_style'] == 'best_ball_individual'],
        courses['Carroll Valley']['holes'],
        handicaps, partners(3), foursomes(3), allocation=allocation)

    return r1['player_points'], r3['player_points']


def r5_handicaps(allocation):
    (players, courses, name_to_id, id_to_team, handicaps,
     partners, foursomes) = t.get_2025_context()
    handicaps['Matt K'] = 10
    return scoring.calculate_round_5_handicaps(
        t.load('golf_scores_round1.json'), t.load('golf_scores_round3.json'),
        courses['Gettysburg National']['holes'],
        courses['Carroll Valley']['holes'], handicaps, partners(5),
        allocation=allocation)


def leaderboard(r1_by_name, r3_by_name):
    """Full 2025 leaderboard, substituting recomputed R1/R3 points."""
    (players, courses, name_to_id, id_to_team, handicaps,
     partners, foursomes) = t.get_2025_context()

    round_points = {}
    for n in (2, 4, 5):
        round_points[n] = t.load(f'round_{n}_results.json')['individual_scores']
    round_points[1] = {name_to_id[n]: p for n, p in r1_by_name.items()}
    round_points[3] = {name_to_id[n]: p for n, p in r3_by_name.items()}
    round_points['puttoff'] = t.load('puttoff_results.json')['individual_scores']

    extras = {}
    for n in range(1, 6):
        extras.update(t.load(f'extras_r{n}_results.json')['individual_scores'])
    extras.update(t.load('adjustment_results.json')['individual_scores'])
    round_points['extras'] = extras

    return scoring.build_leaderboard(players, round_points)


def standings(board):
    rows = sorted(board['individual'].values(), key=lambda e: -e['total_points'])
    return [(e['name'], e['team'], round(e['total_points'], 2)) for e in rows]


def team_standings(board):
    rows = sorted(board['team'].items(), key=lambda x: -x[1]['total_points'])
    return [(name, round(e['total_points'], 2)) for name, e in rows]


def main():
    (players, courses, name_to_id, id_to_team, handicaps,
     partners, foursomes) = t.get_2025_context()
    handicaps['Matt K'] = 16

    print("Who is affected at all (handicap > 18):")
    affected = {n: h for n, h in handicaps.items() if h > 18}
    for n, h in sorted(affected.items(), key=lambda x: -x[1]):
        now = sum(scoring.handicap_strokes_for_hole(h, i, CAPPED)
                  for i in range(1, 19))
        then = sum(scoring.handicap_strokes_for_hole(h, i, FULL)
                   for i in range(1, 19))
        print(f"  {n}: handicap {h} -> {now} strokes capped, {then} full "
              f"(+{then - now})")
    if not affected:
        print("  nobody")
    print()

    # --- baseline ---
    base_r1, base_r3 = score_r1_r3(CAPPED)
    base_h = r5_handicaps(CAPPED)
    base_board = leaderboard(base_r1, base_r3)

    # Sanity: baseline must reproduce the saved 2025 fixtures exactly.
    fail = []
    for n, pts in ((1, base_r1), (3, base_r3)):
        exp = t.load(f'round_{n}_results.json')['individual_scores']
        got = {name_to_id[k]: v for k, v in pts.items()}
        t.compare(f'baseline R{n}', got, exp, fail)
    print("baseline reproduces saved 2025 R1/R3 exactly:",
          "YES" if not fail else f"NO -> {fail[:3]}")
    print()

    # --- counterfactual ---
    alt_r1, alt_r3 = score_r1_r3(FULL)
    alt_h = r5_handicaps(FULL)
    alt_board = leaderboard(alt_r1, alt_r3)

    print("=" * 72)
    print("ROUND 1 / ROUND 3 POINT CHANGES")
    print("=" * 72)
    any_change = False
    for label, base, alt in (('R1', base_r1, alt_r1), ('R3', base_r3, alt_r3)):
        diffs = {n: (base[n], alt[n]) for n in base
                 if abs(base[n] - alt.get(n, 0)) > 1e-9}
        if not diffs:
            print(f"{label}: no change for anyone")
            continue
        any_change = True
        print(f"{label}:")
        for n, (b, a) in sorted(diffs.items(), key=lambda x: x[1][1] - x[1][0]):
            print(f"   {n:<11} {b:>6.2f} -> {a:>6.2f}  ({a - b:+.2f})")
    print()

    print("=" * 72)
    print("FINAL LEADERBOARD")
    print("=" * 72)
    b_st, a_st = standings(base_board), standings(alt_board)
    b_rank = {n: i for i, (n, _, _) in enumerate(b_st, 1)}
    print(f"{'#':>3} {'player':<11}{'team':<8}{'now':>8}{'no cap':>9}{'delta':>8}"
          f"{'was':>6}")
    alt_pts = {n: p for n, _, p in a_st}
    base_pts = {n: p for n, _, p in b_st}
    for i, (n, team, p) in enumerate(a_st, 1):
        d = alt_pts[n] - base_pts[n]
        moved = '' if b_rank[n] == i else f"  #{b_rank[n]}"
        print(f"{i:>3} {n:<11}{team:<8}{base_pts[n]:>8.2f}{p:>9.2f}"
              f"{d:>+8.2f}{moved:>6}")
    print()
    print(f"Winner now:      {b_st[0][0]} ({b_st[0][2]})")
    print(f"Winner no cap:   {a_st[0][0]} ({a_st[0][2]})")
    print(f"Order changed:   {[n for n, _, _ in b_st] != [n for n, _, _ in a_st]}")
    print()

    print("=" * 72)
    print("TEAM STANDINGS")
    print("=" * 72)
    b_team, a_team = team_standings(base_board), team_standings(alt_board)
    b_tp = dict(b_team)
    print(f"{'team':<8}{'now':>9}{'no cap':>9}{'delta':>8}")
    for name, p in a_team:
        print(f"{name:<8}{b_tp[name]:>9.2f}{p:>9.2f}{p - b_tp[name]:>+8.2f}")
    print(f"Team winner now:    {b_team[0][0]}")
    print(f"Team winner no cap: {a_team[0][0]}")
    print(f"Team order changed: {[n for n, _ in b_team] != [n for n, _ in a_team]}")
    print()

    print("=" * 72)
    print("ROUND 5 HANDICAPS (informational in 2025 - no effect on points)")
    print("=" * 72)
    for n in sorted(base_h['individual_handicaps']):
        b = base_h['individual_handicaps'][n]['handicap']
        a = alt_h['individual_handicaps'][n]['handicap']
        if b != a:
            print(f"  individual {n}: {b} -> {a}")
    for k in sorted(base_h['pair_handicaps']):
        b = base_h['pair_handicaps'][k]['pair_handicap']
        a = alt_h['pair_handicaps'][k]['pair_handicap']
        if b != a:
            print(f"  pair {k}: {b} -> {a}")
    print("  (nothing listed above = no change)")


if __name__ == '__main__':
    main()
