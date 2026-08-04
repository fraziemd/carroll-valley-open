"""Check what happens to entities eliminated during tiebreak narrowing.

break_tie() narrows the field to each hole's leaders. If the survivors then
never separate, it returns that narrowed list, and _rank_tied_group splits
points among it. The question this asks is what the entities dropped along the
way receive.

Constructs four tied entities where A and B beat C and D on the hardest hole
and are then identical to each other everywhere, so the narrowed pair cannot
be separated.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jdcvo.scoring import _rank_tied_group  # noqa: E402

# Stroke index 1 is hole 1, index 2 is hole 2, and so on.
HOLES = {str(h): {'par': 4, 'handicap': h} for h in range(1, 19)}

# A and B: 0 on every hole. C and D: 1 on every hole. So A/B win the hardest
# hole outright as a group, and are then indistinguishable from each other.
DATA = {
    'A': {'hole_scores': {str(h): 0 for h in range(1, 19)}},
    'B': {'hole_scores': {str(h): 0 for h in range(1, 19)}},
    'C': {'hole_scores': {str(h): 1 for h in range(1, 19)}},
    'D': {'hole_scores': {str(h): 1 for h in range(1, 19)}},
}

POINTS = [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]


def points_for(rank):
    return POINTS[rank - 1] if rank <= len(POINTS) else 0


def main():
    details = []
    awarded = _rank_tied_group(['A', 'B', 'C', 'D'], DATA, HOLES,
                               "Pair Scoring", 1, points_for, details)

    print("Four entities tied for ranks 1-4, worth 9 + 8 + 7 + 6 = 30 points.")
    print("A and B beat C and D on every hole; A and B never separate.\n")
    print("Awarded by the app's routine:")
    for name in 'ABCD':
        print(f"  {name}: {awarded.get(name, 'NOTHING - absent from result')}")
    print(f"\nTotal distributed: {sum(awarded.values())} of 30")

    print("\nTiebreak log:")
    for line in details:
        print("  " + line)


if __name__ == '__main__':
    main()
