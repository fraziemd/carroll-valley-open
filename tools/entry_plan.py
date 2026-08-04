"""Print the extras and match play results to type into the admin pages.

These are the manual inputs the simulation can't supply, because they are
never scraped. Deterministic from the seed, so the list is stable if you run
it twice.
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.independent_score import load_event, roster_views  # noqa: E402

MATCH_TOTAL = 5.0


def round_2_matches(players):
    """Pair each Round 2 pair with the other pair in its foursome."""
    by_name = {p['name']: p for p in players.values()}
    pairs = set()
    for name, p in by_name.items():
        if p.get('round_2_partner'):
            pairs.add(tuple(sorted([name, p['round_2_partner']])))

    matches, seen = [], set()
    for pair in sorted(pairs):
        if pair in seen:
            continue
        others = by_name[pair[0]].get('round_2_foursome')
        if not others:
            continue
        opponent = tuple(sorted(others))
        if opponent in seen or opponent not in pairs:
            continue
        seen.add(pair)
        seen.add(opponent)
        matches.append((pair, opponent))
    return matches


def main():
    rng = random.Random(20260803)
    cfg, players, courses = load_event('event_2026.json')
    names = sorted(p['name'] for p in players.values())

    print("=" * 66)
    print("EXTRAS  —  Admin → Extras → Add an extra")
    print("=" * 66)
    print("Two closest to the pin and two longest drives per round is the "
          "minimum\nthe Progress tab looks for on rounds 1, 2, 3 and 5.\n")

    for rnd in (1, 2, 3, 5):
        picks = rng.sample(names, 5)
        print(f"Round {rnd}:")
        for who in picks[:2]:
            print(f"   {who:12s} Closest to the pin   1")
        for who in picks[2:4]:
            print(f"   {who:12s} Longest drive        1")
        print(f"   {picks[4]:12s} Chip-in              2")
        print()

    print("=" * 66)
    print("MATCH PLAY  —  Admin → Match play")
    print("=" * 66)
    print("Enter the points for the first pair; the app fills in the other "
          "side.\n")
    for a, b in round_2_matches(players):
        pts = rng.choice([0, 1, 1.5, 2, 2.5, 3, 4, 5])
        print(f"   {' & '.join(a):22s} vs {' & '.join(b):22s} "
              f"-> {pts:g}   (opponent gets {MATCH_TOTAL - pts:g})")


if __name__ == '__main__':
    main()
