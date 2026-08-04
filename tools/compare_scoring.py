"""Compare the app's scoring against the independent implementation.

Runs the real pipeline against whatever is stored (locked rounds read from the
sheet, exactly as the deployed app does) and scores the same cards again using
tools/independent_score.py, which shares no code with it. Any difference means
one of the two is wrong.

Read-only: publishes nothing and writes nothing.

    python3 tools/compare_scoring.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jdcvo import pipeline  # noqa: E402
from jdcvo.config import EventConfig  # noqa: E402
from tools.independent_score import (load_event, roster_views,  # noqa: E402
                                     score_best_ball, score_team_scramble,
                                     score_two_man_scramble)
from tools.simulation import load_secrets  # noqa: E402
from jdcvo.store import SheetsStore  # noqa: E402

TOL = 1e-9
BEST_BALL = 'best_ball_individual'


def cards_from(raw_entries):
    return {e['name']: e['hole_scores'] for e in raw_entries}


def main():
    cfg_path = os.environ.get('JDCVO_CONFIG', 'event_2026.json')
    app_cfg = EventConfig(cfg_path)
    store = SheetsStore(app_cfg.google_sheet_key, load_secrets())

    logs = []
    results = pipeline.run_pipeline(cfg_path, scrape=True, publish=False,
                                    log=logs.append, sheets=store)

    cfg, players, courses = load_event(cfg_path)
    raw = results['raw_scores']
    id_to_name = app_cfg.id_to_name()
    problems = []

    for n in app_cfg.round_numbers():
        rcfg = cfg['rounds'][str(n)]
        style = rcfg['scoring_style']
        entries = raw.get(n) or raw.get(str(n)) or []
        # An all-zero card set is a round nobody has played: PlayThru lists the
        # names as soon as the event exists. It awards nothing.
        if not any(v for e in entries for v in e['hole_scores'].values()):
            print(f"R{n} {style}: not started, awards nothing")
            continue
        holes = courses[rcfg['course']]['holes']
        cards = cards_from(entries)
        # The pipeline keys points by player id; this module works in names.
        app_round = {id_to_name.get(pid, pid): pts for pid, pts
                     in results['round_points'].get(str(n), {}).items()}

        if style == BEST_BALL:
            handicaps, _, partners, foursomes = roster_views(players, n)
            mine = score_best_ball(cards, holes, handicaps, partners,
                                   foursomes)
            expected = mine['points']
            surv = mine['survived']
            best = max(surv.values())
            print(f"\nR{n} best ball ({rcfg['course']}):")
            print(f"  pair relatives: "
                  f"{sorted(mine['pair_relative'].values())}")
            print(f"  survival: best {best} holes, "
                  f"{sum(1 for c in surv.values() if c == best)} tied, "
                  f"awarded to {list(mine['survival_points']) or 'nobody'}")

        elif style == 'team_scramble':
            mine = score_team_scramble(cards, holes)
            team_of = {p['name']: p['team'] for p in players.values()}
            expected = {name: mine['team_points'][team]
                        for name, team in team_of.items()}
            print(f"\nR{n} team scramble ({rcfg['course']}):")
            for team, d in sorted(mine['detail'].items()):
                print(f"  {team:7s} " + '  '.join(
                    f"{c} {rel:+d}->{pts:g}" for c, (rel, pts) in d.items()))

        elif style == 'two_man_scramble':
            mine = score_two_man_scramble(cards, holes)
            sep = rcfg.get('pair_separator', ' and ')
            expected = {}
            for pair, pts in mine['pair_points'].items():
                for member in pair.split(sep):
                    expected[member.strip()] = pts
            print(f"\nR{n} two-man scramble ({rcfg['course']}):")
            for pair, rel in sorted(mine['pair_relative'].items(),
                                    key=lambda x: x[1]):
                print(f"  {rel:+3d}  {pair}  -> "
                      f"{mine['pair_points'][pair]:g}")
        else:
            print(f"\nR{n} {style}: entered by hand, nothing to re-derive")
            continue

        for name in sorted(set(expected) | set(app_round)):
            a, b = app_round.get(name, 0.0), expected.get(name, 0.0)
            if abs(a - b) > TOL:
                problems.append(f"R{n} {name}: app {a:g}, independent {b:g}")

    print("\n" + "=" * 60)
    if problems:
        print(f"{len(problems)} DISAGREEMENT(S) — one side is wrong:")
        for p in problems:
            print("  " + p)
    else:
        print("Every player's round points agree between the two "
              "implementations.")
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
