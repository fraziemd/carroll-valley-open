"""Pure scoring functions for the JDCVO golf outing.

This is a faithful port of the scoring logic in outing-2025.ipynb. The goal of
this module is to reproduce the 2025 results EXACTLY, so all quirks of the
original notebook are preserved deliberately (and noted in comments). Scoring
behavior changes for 2026 should be made as explicit, separate edits after the
port is validated against the saved 2025 results.

All functions are pure: scores in, points out. No file, network, or Sheets I/O.

Intentional divergences from the 2025 notebook (do not affect 2025 results):
- Survival tiebreaker: the notebook keyed its tiebreaker data with int holes
  while the comparison used str holes, so the official "#1 handicap hole total
  foursome net score, then #2, ..." rule never actually ran and ties were
  silently split. Fixed to comply with the official rule. The 2025 survival
  tie path never fired, so the validation tests are unaffected.

Data conventions (matching the 2025 scraped-JSON format):
- ``scores``: list of {'name': str, 'hole_scores': {str(hole): int}}.
  A hole score of 0 means "not yet played" and is ignored.
- ``course_holes``: {str(hole): {'par': int, 'handicap': int}} where
  'handicap' is the hole's stroke index (1 = hardest).
- ``handicaps``: {player_name: int}
- ``partners``: {player_name: partner_name} (reciprocal entries expected)
- ``foursomes``: {player_name: [other_name, other_name]} - the two members of
  the player's foursome who are NOT the player or their partner.

Every calculate_* function returns a dict with:
- 'player_points' and/or entity-level points,
- a 'breakdown' with intermediate values (for drill-down display),
- 'details': list of human-readable log lines (replaces notebook prints).
"""


# ---------------------------------------------------------------------------
# Tiebreaker (port of notebook cell 11: break_tie)
# ---------------------------------------------------------------------------

def break_tie(teams, team_scores, hole_subset, category_name, details=None):
    """Break a tie between entities using hardest holes first.

    Only compares entities on holes they've ALL played. Returns the winning
    entity name, or a list of still-tied entity names if the tie cannot be
    broken.

    ``team_scores``: {entity_name: {'hole_scores': {hole_key: score}}}
    ``hole_subset``: {str(hole): {'par': int, 'handicap': int}}

    NOTE: hole keys are compared as-is, so callers must key ``hole_scores``
    and ``hole_subset`` the same way (all callers use str hole keys). The 2025
    notebook's survival caller mistakenly used int keys, which silently
    disabled the tiebreaker; that is fixed at the call site.
    """
    if details is None:
        details = []

    def log(msg):
        details.append(msg)

    teams = list(teams)
    log(f"TIEBREAKER for {category_name} - Teams tied: {', '.join(teams)}")

    # Hardest holes first (lowest stroke index)
    sorted_holes = sorted(hole_subset.items(), key=lambda x: x[1]['handicap'])
    log(f"Tiebreaker holes (hardest first): {[int(h) for h, _ in sorted_holes]}")

    for hole_num, hole_info in sorted_holes:
        hole_scores = {}
        teams_with_scores = []
        for team in teams:
            if hole_num in team_scores[team]['hole_scores']:
                hole_scores[team] = team_scores[team]['hole_scores'][hole_num]
                teams_with_scores.append(team)

        # Only compare if ALL tied entities have played this hole
        if len(teams_with_scores) == len(teams):
            log(f"  Hole {hole_num} (HCP {hole_info['handicap']}): {hole_scores}")
            best = min(hole_scores.values())
            winners = [t for t in teams if hole_scores[t] == best]
            if len(winners) == 1:
                log(f"  Winner: {winners[0]} (best on hole {hole_num})")
                return winners[0]
            teams = winners
            log(f"  Still tied: {teams}")
        else:
            log(f"  Hole {hole_num} (HCP {hole_info['handicap']}): skipped - not all have played")

    log(f"  Final tie - returning tied teams: {teams}")
    return teams


def _rank_tied_group(tied, tiebreaker_data, hole_subset, category_name,
                     current_rank, points_for_position, details):
    """Shared tie-resolution loop used by all round types.

    Repeatedly applies break_tie to the tied group. Returns
    {entity: points} for every entity in ``tied``.

    ``points_for_position(rank)`` maps a 1-based overall rank to points.
    """
    awarded = {}
    remaining = list(tied)
    position = current_rank

    while remaining:
        if len(remaining) == 1:
            awarded[remaining[0]] = points_for_position(position)
            details.append(
                f"{position}. {remaining[0]}: {awarded[remaining[0]]} points")
            break

        result = break_tie(remaining, tiebreaker_data, hole_subset,
                           category_name, details)
        if isinstance(result, list):
            # break_tie narrowed the field to these and could not separate
            # them, so they share the positions they hold. Whoever it
            # eliminated on the way is still ranked below and keeps going round
            # the loop: losing the hardest hole costs you the place, not every
            # point in the group. Dropping them here awarded nothing at all and
            # left part of the points undistributed.
            num_tied = len(result)
            total = sum(points_for_position(position + i)
                        for i in range(num_tied))
            avg = total / num_tied
            details.append(
                f"Tie cannot be broken - splitting {total} points between {num_tied} entries")
            for name in result:
                awarded[name] = avg
                details.append(
                    f"{position}-{position + num_tied - 1}. {name}: {avg} points")
            position += num_tied
            remaining = [e for e in remaining if e not in result]
        else:
            awarded[result] = points_for_position(position)
            details.append(f"{position}. {result}: {awarded[result]} points")
            position += 1
            remaining.remove(result)

    return awarded


# ---------------------------------------------------------------------------
# Best Ball Individual (Rounds 1 and 3) - port of notebook cell 12
# ---------------------------------------------------------------------------

# Individual hole points relative to par (on NET score)
BEST_BALL_HOLE_POINTS = {
    'double_eagle_or_better': 3.5,  # net <= par - 3
    'eagle': 1.5,                   # net == par - 2
    'birdie': 0.8,                  # net == par - 1
    'par': 0.4,                     # net == par
}
HOLE_IN_ONE_BONUS = 8.0
PAIR_POSITION_POINTS = [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]
SURVIVAL_POINTS = 2.0


# Stroke allocation rules. FULL is the standard allocation: every stroke the
# handicap says. CAPPED is the old notebook rule (at most 1 stroke/hole), kept
# only so tools can reproduce what was published in 2025 before the correction.
# Default and event configs use FULL.
ALLOCATION_CAPPED = 'capped'
ALLOCATION_FULL = 'full'


def handicap_strokes_for_hole(player_handicap, hole_stroke_index,
                              allocation=ALLOCATION_FULL):
    """Handicap strokes received on one hole.

    FULL: floor(H/18) strokes on every hole plus one more on the (H mod 18)
    hardest, totalling exactly H. A 30 gets 30 strokes.
    CAPPED: 1 stroke where the hole's stroke index <= the handicap, else 0, so
    at most 18 strokes total. Legacy notebook behaviour; not used by default.
    """
    if allocation == ALLOCATION_CAPPED:
        return 1 if hole_stroke_index <= player_handicap else 0
    if allocation == ALLOCATION_FULL:
        if player_handicap < 0:
            # Plus handicap gives strokes back on the hardest holes. No player
            # in this event is a plus, so this branch is untested in anger.
            return -1 if hole_stroke_index <= -player_handicap else 0
        base, extra = divmod(player_handicap, 18)
        return base + (1 if hole_stroke_index <= extra else 0)
    raise ValueError(f"unknown handicap allocation {allocation!r}; expected "
                     f"{ALLOCATION_CAPPED!r} or {ALLOCATION_FULL!r}")


def net_hole_score(gross, par, hole_stroke_index, player_handicap,
                   allocation=ALLOCATION_FULL):
    """Net score for a hole, capped at net double bogey."""
    strokes = handicap_strokes_for_hole(player_handicap, hole_stroke_index,
                                        allocation)
    net = gross - strokes
    cap = par + 2 + strokes  # net double bogey cap
    return min(net, cap)


def calculate_best_ball_individual(scores, course_holes, handicaps, partners,
                                   foursomes, allocation=ALLOCATION_FULL):
    """Score a best-ball individual round (2025 Rounds 1 and 3).

    Three point sources per player:
    1. Individual hole points (net par/birdie/eagle/better + hole-in-one bonus)
    2. Pair position points: partners' best net vs par, pairs ranked, 9..0
    3. Survival: 2 pts each to the foursome that survives the most consecutive
       holes from hole 1 (any member net par or better).

    Returns {'player_points', 'breakdown', 'details'} with player_points keyed
    by player name.
    """
    details = []
    player_net_scores = {}
    player_hole_points = {}
    player_total_points = {}

    # --- 1. Individual hole points ---
    for entry in scores:
        name = entry['name']
        if name not in handicaps:
            details.append(f"Warning: player '{name}' not found - skipped")
            continue
        player_handicap = handicaps[name]
        nets = {}
        hole_points_detail = {}
        total = 0.0
        for hole_key, gross in entry['hole_scores'].items():
            if gross == 0:  # not yet played
                continue
            hole = int(hole_key)
            info = course_holes[str(hole)]
            par = info['par']
            net = net_hole_score(gross, par, info['handicap'], player_handicap,
                                 allocation)
            nets[hole] = net

            pts = 0.0
            if net <= par - 3:
                pts += BEST_BALL_HOLE_POINTS['double_eagle_or_better']
            elif net == par - 2:
                pts += BEST_BALL_HOLE_POINTS['eagle']
            elif net == par - 1:
                pts += BEST_BALL_HOLE_POINTS['birdie']
            elif net == par:
                pts += BEST_BALL_HOLE_POINTS['par']
            if gross == 1:
                pts += HOLE_IN_ONE_BONUS
            if pts:
                hole_points_detail[hole] = pts
            total += pts

        player_net_scores[name] = nets
        # Preserved quirk: individual hole-point total is rounded to 1 decimal
        # BEFORE pair/survival points are added.
        player_total_points[name] = round(total, 1)
        player_hole_points[name] = hole_points_detail
        details.append(f"{name}: {round(total, 1)} individual points")

    # --- 2. Pair scoring (best net ball vs par) ---
    pair_scores = {}          # pair_name -> {hole: relative}
    pair_total_relative = {}  # pair_name -> int
    pair_members = {}         # pair_name -> (p1, p2)
    processed = set()

    for name in player_net_scores:
        partner = partners.get(name)
        if not partner or partner not in player_net_scores:
            continue
        key = tuple(sorted([name, partner]))
        if key in processed:
            continue
        processed.add(key)

        nets_a = player_net_scores[name]
        nets_b = player_net_scores[partner]
        common = set(nets_a) & set(nets_b)

        hole_rel = {}
        total_rel = 0
        for hole in common:
            best = min(nets_a[hole], nets_b[hole])
            rel = best - course_holes[str(hole)]['par']
            hole_rel[hole] = rel
            total_rel += rel

        pair_name = f"{name} & {partner}"
        pair_scores[pair_name] = hole_rel
        pair_total_relative[pair_name] = total_rel
        pair_members[pair_name] = (name, partner)

    # Group pairs by total relative score, award position points with ties.
    score_groups = {}
    for pair_name, rel in sorted(pair_total_relative.items(), key=lambda x: x[1]):
        score_groups.setdefault(rel, []).append(pair_name)

    def pair_points_for_position(rank):
        return PAIR_POSITION_POINTS[rank - 1] if rank <= len(PAIR_POSITION_POINTS) else 0

    pair_round_points = {}
    current_rank = 1
    details.append("Pair results (sorted by relative to par):")
    for rel in sorted(score_groups):
        group = score_groups[rel]
        if len(group) == 1:
            pts = pair_points_for_position(current_rank)
            pair_round_points[group[0]] = pts
            details.append(f"{current_rank}. {group[0]}: {rel:+} to par, {pts} pts")
            current_rank += 1
        else:
            details.append(
                f"TIE for rank {current_rank} at {rel:+} to par: {', '.join(group)}")
            tiebreaker_data = {
                p: {'hole_scores': {str(h): s for h, s in pair_scores[p].items()}}
                for p in group
            }
            hole_subset = {str(i): course_holes[str(i)] for i in range(1, 19)}
            awarded = _rank_tied_group(group, tiebreaker_data, hole_subset,
                                       "Pair Scoring", current_rank,
                                       pair_points_for_position, details)
            pair_round_points.update(awarded)
            current_rank += len(group)

    # --- 3. Survival scoring ---
    survival = {}         # foursome display name -> holes survived
    foursome_groups = {}  # frozen key -> member list
    processed_players = set()

    for name in player_net_scores:
        if name in processed_players:
            continue
        partner = partners.get(name)
        others = foursomes.get(name)
        if partner and others:
            members = [name, partner] + list(others)
            key = tuple(sorted(members))
            if key not in foursome_groups:
                foursome_groups[key] = members
                processed_players.update(members)

    survival_points = {}
    for key, members in foursome_groups.items():
        holes_survived = 0
        for hole in range(1, 19):
            par = course_holes[str(hole)]['par']
            survived = any(
                player_net_scores.get(m, {}).get(hole, 999) <= par
                for m in members
            )
            if survived:
                holes_survived += 1
            else:
                break
        survival[', '.join(members)] = holes_survived
        details.append(f"{', '.join(members)}: survived {holes_survived} holes")

    if foursome_groups:
        max_survival = max(survival.values())
        winners = [n for n, s in survival.items() if s == max_survival]

        if len(winners) == 1:
            details.append(f"Survival winner: {winners[0]} ({max_survival} holes)")
            for m in winners[0].split(', '):
                if m in player_total_points:
                    survival_points[m] = SURVIVAL_POINTS
        else:
            details.append(
                f"TIE for survival at {max_survival} holes: {len(winners)} foursomes")
            # Official rule: a survival tie goes to the foursome with the best
            # total net score on the #1 handicap hole, then the #2 handicap
            # hole, and so on. break_tie already walks holes hardest-first, so
            # we just need string hole keys here to match hole_subset.
            #
            # NOTE: the 2025 notebook keyed this data with INT holes while
            # hole_subset used str holes, so break_tie never matched a hole and
            # always split the points -- i.e. 2025 never actually applied the
            # official tiebreaker. This fix makes the code rule-compliant. It
            # does NOT change 2025 results: the survival tie path never fired
            # that year (both rounds had outright survival winners), so the
            # validation tests still pass.
            tb_data = {}
            for name in winners:
                members = name.split(', ')
                totals = {}
                for hole in range(1, 19):
                    totals[str(hole)] = sum(
                        player_net_scores.get(m, {}).get(hole, 999)
                        for m in members
                    )
                tb_data[name] = {'hole_scores': totals}
            hole_subset = {str(i): course_holes[str(i)] for i in range(1, 19)}
            result = break_tie(winners, tb_data, hole_subset, "Survival", details)

            if isinstance(result, list):
                per_player = SURVIVAL_POINTS / len(result)
                details.append(
                    f"Tie cannot be broken - splitting {SURVIVAL_POINTS} points "
                    f"between {len(result)} foursomes")
                for name in result:
                    for m in name.split(', '):
                        if m in player_total_points:
                            survival_points[m] = per_player
            else:
                for m in result.split(', '):
                    if m in player_total_points:
                        survival_points[m] = SURVIVAL_POINTS

    # --- Combine ---
    for name, pts in survival_points.items():
        player_total_points[name] += pts
    for pair_name, pts in pair_round_points.items():
        for member in pair_members[pair_name]:
            if member in player_total_points:
                player_total_points[member] += pts

    return {
        'player_points': player_total_points,
        'breakdown': {
            'hole_points': player_hole_points,
            'net_scores': player_net_scores,
            'pair_relative': pair_total_relative,
            'pair_hole_relative': pair_scores,
            'pair_position_points': pair_round_points,
            'pair_members': pair_members,
            'survival_holes': survival,
            'survival_points': survival_points,
        },
        'details': details,
    }


# ---------------------------------------------------------------------------
# Match Play (Round 2) - port of notebook cell 13 (scoring math only;
# interactive entry is replaced by an explicit argument)
# ---------------------------------------------------------------------------

def calculate_match_play(pair_points):
    """Score a match-play round from directly entered pair results.

    ``pair_points``: {(player1, player2): points} - each player in the pair
    receives the pair's points (0-5 in the 2025 format).

    Returns {'player_points', 'breakdown', 'details'}.
    """
    details = []
    player_points = {}
    pair_points_named = {}
    for (p1, p2), pts in pair_points.items():
        player_points[p1] = pts
        player_points[p2] = pts
        pair_points_named[f"{p1} & {p2}"] = pts
        details.append(f"{p1} & {p2}: {pts} points each")
    return {
        'player_points': player_points,
        'breakdown': {'pair_points': pair_points_named},
        'details': details,
    }


# ---------------------------------------------------------------------------
# Team Scramble (Round 4) - port of notebook cell 15
# ---------------------------------------------------------------------------

def _team_scramble_points_for_position(rank):
    """1st=3, 2nd=2, 3rd=1, 4th=0.5, 5th+=0."""
    if rank == 4:
        return 0.5
    return max(0, 4 - rank)


def calculate_team_scramble(scores, course_holes):
    """Score a team scramble round (2025 Round 4).

    ``scores``: one entry per team ({'name': team_name, 'hole_scores': ...}).
    Points are awarded independently for Front 9, Back 9, and Overall
    (3/2/1/0.5 for positions 1-4). Each player receives the sum of their
    team's three category points; that mapping is the caller's job (this
    function returns per-team points).

    Returns {'team_points', 'breakdown', 'details'} where team_points is
    {team: total_points_across_categories}.
    """
    details = []
    team_results = {}
    for entry in scores:
        team = entry['name']
        hole_scores = entry['hole_scores']

        front = sum(hole_scores.get(str(i), 999) for i in range(1, 10)
                    if hole_scores.get(str(i), 0) != 0)
        back = sum(hole_scores.get(str(i), 999) for i in range(10, 19)
                   if hole_scores.get(str(i), 0) != 0)
        front_played = [i for i in range(1, 10)
                        if str(i) in hole_scores and hole_scores[str(i)] != 0]
        back_played = [i for i in range(10, 19)
                       if str(i) in hole_scores and hole_scores[str(i)] != 0]
        front_par = sum(course_holes[str(i)]['par'] for i in front_played)
        back_par = sum(course_holes[str(i)]['par'] for i in back_played)

        team_results[team] = {
            'front_9_relative': front - front_par,
            'back_9_relative': back - back_par,
            'overall_relative': (front + back) - (front_par + back_par),
            'hole_scores': hole_scores,
        }

    categories = [
        ('Front 9', 'front_9_relative', range(1, 10)),
        ('Back 9', 'back_9_relative', range(10, 19)),
        ('Overall', 'overall_relative', range(1, 19)),
    ]

    category_points = {}
    for cat_name, score_key, hole_range in categories:
        details.append(f"--- {cat_name} ---")
        score_groups = {}
        for team in sorted(team_results, key=lambda t: team_results[t][score_key]):
            score_groups.setdefault(team_results[team][score_key], []).append(team)

        awarded = {}
        current_rank = 1
        for score in sorted(score_groups):
            group = score_groups[score]
            if len(group) == 1:
                pts = _team_scramble_points_for_position(current_rank)
                awarded[group[0]] = pts
                details.append(f"{group[0]}: {pts} points ({score:+d} to par)")
                current_rank += 1
            else:
                details.append(
                    f"TIE for rank {current_rank} at {score:+d} to par: {', '.join(group)}")
                hole_subset = {str(i): course_holes[str(i)] for i in hole_range}
                got = _rank_tied_group(group, team_results, hole_subset,
                                       cat_name, current_rank,
                                       _team_scramble_points_for_position, details)
                awarded.update(got)
                current_rank += len(group)

        for team in team_results:
            awarded.setdefault(team, 0)
        category_points[cat_name] = awarded

    team_points = {
        team: sum(category_points[c][team] for c in category_points)
        for team in team_results
    }

    return {
        'team_points': team_points,
        'breakdown': {
            'team_results': {
                t: {k: v for k, v in r.items() if k != 'hole_scores'}
                for t, r in team_results.items()
            },
            'category_points': category_points,
        },
        'details': details,
    }


# ---------------------------------------------------------------------------
# Two-Man Scramble (Round 5) - port of notebook cell 17
# ---------------------------------------------------------------------------

def calculate_two_man_scramble(scores, course_holes, pair_separator=" and "):
    """Score a two-man scramble round (2025 Round 5).

    ``scores``: one entry per pair; entry['name'] contains both player names
    joined by ``pair_separator`` (2025 scraped data used " and ").
    Pairs are ranked by overall relative-to-par; points = (#pairs - rank).

    NOTE (preserved 2025 behavior): scoring uses the relative-to-par of the
    entered scores as-is. In 2025 players entered NET scores on PlayThru, so
    no handicap is applied here.

    Returns {'player_points', 'pair_points', 'breakdown', 'details'} with
    player_points keyed by player name.
    """
    details = []
    pair_results = {}
    for entry in scores:
        name = entry['name']
        hole_scores = entry['hole_scores']
        overall = sum(s for s in hole_scores.values() if s != 0)
        played = [int(h) for h, s in hole_scores.items() if s != 0]
        par = sum(course_holes[str(i)]['par'] for i in played)
        pair_results[name] = {
            'overall_relative': overall - par,
            'hole_scores': hole_scores,
        }

    n_pairs = len(pair_results)

    def points_for_position(rank):
        return max(0, n_pairs - rank)

    score_groups = {}
    for pair in sorted(pair_results, key=lambda p: pair_results[p]['overall_relative']):
        score_groups.setdefault(pair_results[pair]['overall_relative'], []).append(pair)

    points_awarded = {}
    current_rank = 1
    for score in sorted(score_groups):
        group = score_groups[score]
        if len(group) == 1:
            pts = points_for_position(current_rank)
            points_awarded[group[0]] = pts
            details.append(f"{group[0]}: {pts} points ({score:+d} to par)")
            current_rank += 1
        else:
            details.append(
                f"TIE for rank {current_rank} at {score:+d} to par: {', '.join(group)}")
            hole_subset = {str(i): course_holes[str(i)] for i in range(1, 19)}
            got = _rank_tied_group(group, pair_results, hole_subset, "Overall",
                                   current_rank, points_for_position, details)
            points_awarded.update(got)
            current_rank += len(group)

    for pair in pair_results:
        points_awarded.setdefault(pair, 0)

    # Distribute pair points to individual players.
    player_points = {}
    for pair_name, pts in points_awarded.items():
        if pair_separator in pair_name:
            p1, p2 = [p.strip() for p in pair_name.split(pair_separator, 1)]
            player_points[p1] = player_points.get(p1, 0) + pts
            player_points[p2] = player_points.get(p2, 0) + pts
        else:
            details.append(
                f"Warning: could not split pair name '{pair_name}' "
                f"on '{pair_separator}' - no player points assigned")

    return {
        'player_points': player_points,
        'pair_points': points_awarded,
        'breakdown': {
            'pair_results': {
                p: {'overall_relative': r['overall_relative']}
                for p, r in pair_results.items()
            },
        },
        'details': details,
    }


# ---------------------------------------------------------------------------
# Round 5 pair handicaps - port of notebook cell 16
# ---------------------------------------------------------------------------

def calculate_round_5_handicaps(round_1_scores, round_3_scores,
                                round_1_course_holes, round_3_course_holes,
                                handicaps, round_5_partners,
                                allocation=ALLOCATION_FULL):
    """Compute Round 5 pair handicaps from Round 1 and Round 3 scores.

    Each player's gross scores are capped at net double bogey, totaled, and
    compared to par for both rounds; the two relative scores are averaged and
    truncated toward zero to give an individual handicap. Pair handicap =
    int((lower*2 + int(higher/2)) / 3).

    ``round_5_partners``: {player_name: partner_name}.
    Returns {'individual_handicaps', 'pair_handicaps', 'details'}.
    """
    details = []

    def adjusted_total(entry, course_holes, player_handicap):
        total = 0
        for hole_key, gross in entry['hole_scores'].items():
            info = course_holes[str(int(hole_key))]
            strokes = handicap_strokes_for_hole(player_handicap,
                                               info['handicap'], allocation)
            total += min(gross, info['par'] + 2 + strokes)
        return total

    r1_by_name = {e['name']: e for e in round_1_scores}
    r3_by_name = {e['name']: e for e in round_3_scores}
    r1_par = sum(h['par'] for h in round_1_course_holes.values())
    r3_par = sum(h['par'] for h in round_3_course_holes.values())

    individual = {}
    for name, player_handicap in handicaps.items():
        e1, e3 = r1_by_name.get(name), r3_by_name.get(name)
        if not e1 or not e3:
            details.append(f"Missing scores for {name}")
            continue
        t1 = adjusted_total(e1, round_1_course_holes, player_handicap)
        t3 = adjusted_total(e3, round_3_course_holes, player_handicap)
        rel1, rel3 = t1 - r1_par, t3 - r3_par
        avg = (rel1 + rel3) / 2
        individual[name] = {
            'round_1_adjusted_total': t1,
            'round_3_adjusted_total': t3,
            'round_1_relative': rel1,
            'round_3_relative': rel3,
            'avg_relative': avg,
            'handicap': int(avg),  # truncates toward zero, as in 2025
        }

    pairs = {}
    processed = set()
    for name, partner in round_5_partners.items():
        key = tuple(sorted([name, partner]))
        if key in processed:
            continue
        if name in individual and partner in individual:
            a, b = individual[name]['handicap'], individual[partner]['handicap']
            lower, higher = min(a, b), max(a, b)
            pair_handicap = int((lower * 2 + int(higher / 2)) / 3)
            pairs[f"{name} & {partner}"] = {
                'player_a': name,
                'player_b': partner,
                'player_a_handicap': a,
                'player_b_handicap': b,
                'pair_handicap': pair_handicap,
            }
            processed.add(key)
            details.append(f"{name} & {partner}: {pair_handicap} strokes")
        else:
            details.append(f"Missing handicaps for {name} or {partner}")

    return {
        'individual_handicaps': individual,
        'pair_handicaps': pairs,
        'details': details,
    }


# ---------------------------------------------------------------------------
# Leaderboard aggregation
# ---------------------------------------------------------------------------

def build_leaderboard(players, round_points, bonus_points=None):
    """Aggregate per-round points into individual and team leaderboards.

    ``players``: {player_id: {'name': str, 'team': str, ...}}
    ``round_points``: {round_key: {player_id: points}} where round_key is an
      int round number or a special key ('puttoff', 'extras', ...).
    ``bonus_points``: optional {player_id: points} stored under 'extras'.

    Team totals are the sum of the team's players' individual totals
    (matching 2025 behavior).

    Returns {'individual': {player_id: {...}}, 'team': {team: {...}}}.
    """
    individual = {}
    for pid, info in players.items():
        rounds = {}
        for round_key, pts_by_player in round_points.items():
            if pid in pts_by_player:
                rounds[round_key] = pts_by_player[pid]
        if bonus_points and pid in bonus_points:
            rounds['extras'] = rounds.get('extras', 0) + bonus_points[pid]
        individual[pid] = {
            'name': info['name'],
            'team': info['team'],
            'round_scores': rounds,
            'total_points': sum(rounds.values()),
        }

    team = {}
    for pid, entry in individual.items():
        t = entry['team']
        if t not in team:
            team[t] = {'team_name': t, 'total_points': 0, 'players': []}
        team[t]['players'].append(pid)
        team[t]['total_points'] += entry['total_points']

    return {'individual': individual, 'team': team}
