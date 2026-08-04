"""Unit tests for the positional tiebreak shared by every round type.

Plain functions with asserts, same style as test_2025_validation.py, collected
by tools/run_2025_tests.py.

The case that matters here is an unbreakable tie *after* narrowing. break_tie
eliminates whoever loses the hardest hole and keeps comparing the leaders; if
those leaders never separate, the eliminated entities used to be dropped from
the result and receive nothing, silently leaving part of the points on offer
undistributed.
"""

from .scoring import _rank_tied_group

# Stroke index equals hole number, so hole 1 is the hardest.
HOLES = {str(h): {'par': 4, 'handicap': h} for h in range(1, 19)}
POINTS = [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]


def points_for(rank):
    return POINTS[rank - 1] if rank <= len(POINTS) else 0


def flat(value, holes=range(1, 19)):
    return {'hole_scores': {str(h): value for h in holes}}


def test_tiebreak_separable_group_gets_sequential_positions():
    data = {'A': flat(0), 'B': flat(1), 'C': flat(2)}
    awarded = _rank_tied_group(['A', 'B', 'C'], data, HOLES, 'test', 1,
                              points_for, [])
    assert awarded == {'A': 9, 'B': 8, 'C': 7}, awarded


def test_tiebreak_starts_from_the_group_s_own_rank():
    data = {'A': flat(0), 'B': flat(1)}
    awarded = _rank_tied_group(['A', 'B'], data, HOLES, 'test', 4,
                              points_for, [])
    assert awarded == {'A': 6, 'B': 5}, awarded


def test_tiebreak_splits_a_wholly_unbreakable_tie():
    data = {'A': flat(0), 'B': flat(0), 'C': flat(0)}
    awarded = _rank_tied_group(['A', 'B', 'C'], data, HOLES, 'test', 1,
                              points_for, [])
    share = (9 + 8 + 7) / 3
    assert awarded == {'A': share, 'B': share, 'C': share}, awarded


def test_eliminated_entities_still_take_the_positions_below():
    """The regression. A and B beat C and D everywhere and never separate.

    A and B share 1st and 2nd; C and D must still get 3rd and 4th rather than
    nothing, and every point on offer must be handed out.
    """
    data = {'A': flat(0), 'B': flat(0), 'C': flat(1), 'D': flat(1)}
    awarded = _rank_tied_group(['A', 'B', 'C', 'D'], data, HOLES, 'test', 1,
                               points_for, [])
    assert set(awarded) == {'A', 'B', 'C', 'D'}, awarded
    assert awarded['A'] == awarded['B'] == (9 + 8) / 2, awarded
    assert awarded['C'] == awarded['D'] == (7 + 6) / 2, awarded
    assert sum(awarded.values()) == 9 + 8 + 7 + 6, awarded


def test_narrowing_can_cascade_and_still_distribute_everything():
    """Three distinct standings, the best two of which cannot be separated."""
    data = {'A': flat(0), 'B': flat(0), 'C': flat(1), 'D': flat(2)}
    awarded = _rank_tied_group(['A', 'B', 'C', 'D'], data, HOLES, 'test', 1,
                               points_for, [])
    assert awarded['A'] == awarded['B'] == (9 + 8) / 2, awarded
    assert awarded['C'] == 7, awarded
    assert awarded['D'] == 6, awarded
    assert sum(awarded.values()) == 9 + 8 + 7 + 6, awarded


def test_holes_not_played_by_everyone_are_skipped():
    """A is behind on hole 1 but hasn't played it, so hole 2 decides."""
    data = {
        'A': {'hole_scores': {str(h): 0 for h in range(2, 19)}},   # no hole 1
        'B': {'hole_scores': {'1': 0, **{str(h): 1 for h in range(2, 19)}}},
    }
    awarded = _rank_tied_group(['A', 'B'], data, HOLES, 'test', 1,
                               points_for, [])
    assert awarded == {'A': 9, 'B': 8}, awarded
