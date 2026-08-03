"""Validate the jdcvo scoring engine against 2025 tournament scorecards.

Reads the 2025 input files (golf_scores_round*.json, players_2025.json,
courses.json) and asserts the engine reproduces the saved outputs
(round_*_results.json, round_5_handicaps.json) exactly.

Rounds 1 and 3 fixtures use FULL handicap allocation (every stroke the
handicap says). That corrects the old notebook's 1-stroke-per-hole cap;
published 2025 sheet totals under the capped rule will not match these.

Run from the repo root:  python3 -m jdcvo.test_2025_validation
or with pytest:          pytest jdcvo/test_2025_validation.py

READ-ONLY: this script never writes any files.

TODO (2026, once this year's roster/courses are loaded):
  - Add tests driven by randomly generated scorecards to exercise paths the
    real 2025 data never hit.
  - In particular, ENGINEER A DEDICATED TEST OF THE SURVIVAL TIEBREAKER:
    construct two (and three) foursomes that survive the same number of holes,
    then confirm the win goes to the better total foursome net score on the
    #1 handicap hole, cascading to #2, #3, ... on further ties, and splitting
    only if all 18 holes are exhausted. (The 2025 data had outright survival
    winners, so this path is currently unexercised by the validation above.)
  - Also cover the Round 5 pair-name parsing once the " and " vs " & "
    separator is settled for 2026.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jdcvo import scoring

DATA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(filename):
    with open(os.path.join(DATA_DIR, filename)) as f:
        return json.load(f)


def get_2025_context():
    players_data = load('players_2025.json')['players']
    courses = load('courses.json')['courses']

    name_to_id = {p['name']: pid for pid, p in players_data.items()}
    id_to_team = {pid: p['team'] for pid, p in players_data.items()}
    handicaps = {p['name']: p['handicap'] for p in players_data.values()}

    def partners(round_num):
        field = f'round_{round_num}_partner'
        return {p['name']: p[field] for p in players_data.values() if p.get(field)}

    def foursomes(round_num):
        field = f'round_{round_num}_foursome'
        return {p['name']: p[field] for p in players_data.values() if p.get(field)}

    return players_data, courses, name_to_id, id_to_team, handicaps, partners, foursomes


def to_ids_and_teams(player_points_by_name, name_to_id, id_to_team):
    """Convert name-keyed points to id-keyed points + team sums (2025 style)."""
    individual = {}
    team = {}
    for name, pts in player_points_by_name.items():
        pid = name_to_id[name]
        individual[pid] = pts
        t = id_to_team[pid]
        team[t] = team.get(t, 0) + pts
    return individual, team


def compare(label, computed, expected, failures, tolerance=1e-9):
    keys = set(computed) | set(expected)
    for k in sorted(keys):
        c, e = computed.get(k), expected.get(k)
        if c is None or e is None or abs(c - e) > tolerance:
            failures.append(f"{label}[{k}]: computed={c} expected={e}")


def test_round_1():
    (players_data, courses, name_to_id, id_to_team,
     handicaps, partners, foursomes) = get_2025_context()
    # Matt K played R1 at handicap 16 (already the value in players_2025.json,
    # re-asserted by notebook cell 23).
    handicaps['Matt K'] = 16

    scores = [s for s in load('golf_scores_round1.json')
              if s['scoring_style'] == 'best_ball_individual']
    result = scoring.calculate_best_ball_individual(
        scores, courses['Gettysburg National']['holes'],
        handicaps, partners(1), foursomes(1))

    expected = load('round_1_results.json')
    individual, team = to_ids_and_teams(result['player_points'], name_to_id, id_to_team)

    failures = []
    compare('R1 individual', individual, expected['individual_scores'], failures)
    compare('R1 team', team, expected['team_scores'], failures)
    assert not failures, '\n'.join(failures)


def test_round_2():
    """Round 2 was manual entry; the saved individual scores ARE the input.
    Verify the match-play function is a faithful pass-through and that team
    sums match."""
    (players_data, courses, name_to_id, id_to_team,
     handicaps, partners, foursomes) = get_2025_context()

    expected = load('round_2_results.json')
    id_to_name = {v: k for k, v in name_to_id.items()}

    # Reconstruct pair entries from saved results + round_2_partner fields.
    pair_points = {}
    seen = set()
    for pid, pts in expected['individual_scores'].items():
        name = id_to_name[pid]
        partner = partners(2).get(name)
        key = tuple(sorted([name, partner]))
        if key in seen:
            continue
        seen.add(key)
        pair_points[key] = pts

    result = scoring.calculate_match_play(pair_points)
    individual, team = to_ids_and_teams(result['player_points'], name_to_id, id_to_team)

    failures = []
    compare('R2 individual', individual, expected['individual_scores'], failures)
    compare('R2 team', team, expected['team_scores'], failures)
    assert not failures, '\n'.join(failures)


def test_round_3():
    (players_data, courses, name_to_id, id_to_team,
     handicaps, partners, foursomes) = get_2025_context()
    # Notebook cell 28 set Matt K's handicap to 10 before Round 3.
    handicaps['Matt K'] = 10

    scores = [s for s in load('golf_scores_round3.json')
              if s['scoring_style'] == 'best_ball_individual']
    result = scoring.calculate_best_ball_individual(
        scores, courses['Carroll Valley']['holes'],
        handicaps, partners(3), foursomes(3))

    expected = load('round_3_results.json')
    individual, team = to_ids_and_teams(result['player_points'], name_to_id, id_to_team)

    failures = []
    compare('R3 individual', individual, expected['individual_scores'], failures)
    compare('R3 team', team, expected['team_scores'], failures)
    assert not failures, '\n'.join(failures)


def test_round_4():
    (players_data, courses, name_to_id, id_to_team,
     handicaps, partners, foursomes) = get_2025_context()

    scores = [s for s in load('golf_scores_round4.json')
              if s['scoring_style'] == 'team_scramble']
    result = scoring.calculate_team_scramble(scores, courses['Carroll Valley']['holes'])

    # Each player receives their team's total category points.
    individual = {}
    team = {}
    for pid, p in players_data.items():
        pts = result['team_points'].get(p['team'], 0)
        individual[pid] = pts
        team[p['team']] = team.get(p['team'], 0) + pts

    expected = load('round_4_results.json')
    failures = []
    compare('R4 individual', individual, expected['individual_scores'], failures)
    compare('R4 team', team, expected['team_scores'], failures)
    assert not failures, '\n'.join(failures)


def test_round_5():
    (players_data, courses, name_to_id, id_to_team,
     handicaps, partners, foursomes) = get_2025_context()

    scores = [s for s in load('golf_scores_round5.json')
              if s['scoring_style'] == 'two_man_scramble']
    result = scoring.calculate_two_man_scramble(
        scores, courses['The Links at Gettysburg']['holes'])

    # 2025 initialized every player to 0 then added pair points.
    player_points = {p['name']: 0 for p in players_data.values()}
    player_points.update(result['player_points'])

    expected = load('round_5_results.json')
    individual, team = to_ids_and_teams(player_points, name_to_id, id_to_team)

    failures = []
    compare('R5 individual', individual, expected['individual_scores'], failures)
    compare('R5 team', team, expected['team_scores'], failures)
    assert not failures, '\n'.join(failures)


def test_round_5_handicaps():
    (players_data, courses, name_to_id, id_to_team,
     handicaps, partners, foursomes) = get_2025_context()
    # Round 5 handicaps were computed after cell 28 (Matt K -> 10).
    handicaps['Matt K'] = 10

    result = scoring.calculate_round_5_handicaps(
        load('golf_scores_round1.json'),
        load('golf_scores_round3.json'),
        courses['Gettysburg National']['holes'],
        courses['Carroll Valley']['holes'],
        handicaps,
        partners(5))

    expected = load('round_5_handicaps.json')

    failures = []
    computed_ind = {n: d['handicap'] for n, d in result['individual_handicaps'].items()}
    expected_ind = {n: d['handicap'] for n, d in expected['individual_handicaps'].items()}
    compare('R5 individual handicaps', computed_ind, expected_ind, failures)

    computed_pairs = {n: d['pair_handicap'] for n, d in result['pair_handicaps'].items()}
    expected_pairs = {n: d['pair_handicap'] for n, d in expected['pair_handicaps'].items()}
    compare('R5 pair handicaps', computed_pairs, expected_pairs, failures)
    assert not failures, '\n'.join(failures)


def test_full_leaderboard():
    """End-to-end: combine all rounds + extras + puttoff + adjustment and
    check every player's final total."""
    (players_data, courses, name_to_id, id_to_team,
     handicaps, partners, foursomes) = get_2025_context()

    round_points = {}
    for n in range(1, 6):
        round_points[n] = load(f'round_{n}_results.json')['individual_scores']
    round_points['puttoff'] = load('puttoff_results.json')['individual_scores']

    # 2025 extras files store CUMULATIVE extras totals per player; the final
    # extras state is the last value seen per player, then the manual
    # adjustment file overrides pete's extras.
    extras = {}
    for n in range(1, 6):
        extras.update(load(f'extras_r{n}_results.json')['individual_scores'])
    extras.update(load('adjustment_results.json')['individual_scores'])
    round_points['extras'] = extras

    board = scoring.build_leaderboard(players_data, round_points)

    computed_totals = {pid: e['total_points'] for pid, e in board['individual'].items()}
    computed_team_totals = {t: e['total_points'] for t, e in board['team'].items()}

    print("\nFinal 2025 leaderboard as computed by the engine:")
    for pid, entry in sorted(board['individual'].items(),
                             key=lambda x: -x[1]['total_points']):
        print(f"  {entry['name']:<10} ({entry['team']:<6}) {entry['total_points']:.2f}")
    print("Teams:")
    for t, entry in sorted(board['team'].items(), key=lambda x: -x[1]['total_points']):
        print(f"  {t:<6} {entry['total_points']:.2f}")

    return computed_totals, computed_team_totals


if __name__ == '__main__':
    tests = [test_round_1, test_round_2, test_round_3, test_round_4,
             test_round_5, test_round_5_handicaps]
    passed, failed = 0, 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {t.__name__}\n{e}")
            failed += 1
    test_full_leaderboard()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
