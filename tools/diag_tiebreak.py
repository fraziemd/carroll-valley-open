"""Trace the pair-position tiebreak on the simulated Round 1 four-way tie.

Prints the hole-by-hole data both implementations see, then walks each
algorithm by hand, to establish which step makes them disagree.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.independent_score import (load_event, roster_views,  # noqa: E402
                                     score_best_ball)

ROUND = 1


def main():
    cfg, players, courses = load_event('event_2026.json')
    holes = courses[cfg['rounds'][str(ROUND)]['course']]['holes']
    sim = json.load(open('simulated_scores.json'))['rounds'][str(ROUND)]
    cards = {e['name']: e['hole_scores'] for e in sim}
    handicaps, _, partners, foursomes = roster_views(players, ROUND)

    res = score_best_ball(cards, holes, handicaps, partners, foursomes)
    rel = res['pair_relative']

    groups = {}
    for pair, r in rel.items():
        groups.setdefault(r, []).append(pair)
    tied = max(groups.values(), key=len)
    print(f"Largest tie: {len(tied)} pairs at "
          f"{rel[tied[0]]:+} to par\n  " + "\n  ".join(tied))

    # Per-hole relative for each tied pair, hardest hole first.
    hardest = sorted(range(1, 19), key=lambda h: holes[str(h)]['handicap'])
    per_hole = {p: res['nets'] and {} for p in tied}
    pair_hole = {}
    for pair in tied:
        a, b = pair.split(' & ')
        pair_hole[pair] = {
            h: min(res['nets'][a][h], res['nets'][b][h]) - holes[str(h)]['par']
            for h in range(1, 19)}

    print("\nHole-by-hole pair relative, hardest hole first:")
    print("  idx hole  " + "  ".join(f"{p.split(' & ')[0][:6]:>6s}"
                                     for p in tied))
    for h in hardest[:8]:
        row = "  ".join(f"{pair_hole[p][h]:+6d}" for p in tied)
        print(f"  {holes[str(h)]['handicap']:3d} {h:4d}  {row}")

    print("\nWalking the two algorithms:")
    remaining_doc = list(tied)
    remaining_code = list(tied)
    for h in hardest:
        vals = {p: pair_hole[p][h] for p in remaining_code}
        best = min(vals.values())
        leaders = [p for p in remaining_code if vals[p] == best]
        vals_doc = {p: pair_hole[p][h] for p in remaining_doc}
        best_doc = min(vals_doc.values())
        leaders_doc = [p for p in remaining_doc if vals_doc[p] == best_doc]

        print(f"  hole {h:2d} (idx {holes[str(h)]['handicap']:2d})  "
              f"code-set leaders {len(leaders)}  doc-set leaders "
              f"{len(leaders_doc)} of {len(remaining_doc)}")
        if len(leaders) == 1:
            print(f"     CODE picks {leaders[0]} (narrowed field)")
            break
        remaining_code = leaders   # the narrowing step the app does
        if len(leaders_doc) == 1:
            print(f"     DOC picks {leaders_doc[0]} (full field)")
            break


if __name__ == '__main__':
    main()
