"""Load, inspect and remove simulated scores for pre-event testing.

Simulation works through the round lock, not through a code flag. A locked
round stops being scraped and is scored from the 'Raw Scores' tab instead, so
writing cards there and locking the round makes the app treat them exactly as
if PlayThru had served them. Nothing in the app knows a simulation is running.

That is deliberate: a locked round wears a LOCKED badge on its page, so the
simulation is visible in the UI and cannot be left switched on unnoticed. A
config flag would have been invisible and would have needed a deploy to
change in either direction.

    python3 tools/simulation.py status   # what is stored right now
    python3 tools/simulation.py clear    # wipe scores and manual entries
    python3 tools/simulation.py load     # write simulated_scores.json + lock
    python3 tools/simulation.py off      # unlock everything and wipe scores

BEFORE THE REAL EVENT run `off`, then delete any test scores from PlayThru
itself. PlayThru is the source of truth for an unlocked round, so anything
left there comes straight back on the next scrape.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jdcvo.config import EventConfig  # noqa: E402
from jdcvo.store import SheetsStore  # noqa: E402

SIM_FILE = 'simulated_scores.json'


def load_secrets(path='.streamlit/secrets.toml'):
    """Minimal TOML read for the service-account block (no tomllib on 3.9)."""
    info, in_block = {}, False
    for line in open(path):
        line = line.strip()
        if line.startswith('['):
            in_block = line == '[gcp_service_account]'
            continue
        if not in_block or '=' not in line:
            continue
        k, v = line.split('=', 1)
        info[k.strip()] = v.strip().strip('"').replace('\\n', '\n')
    return info


def connect():
    cfg = EventConfig(os.environ.get('JDCVO_CONFIG', 'event_2026.json'))
    return cfg, SheetsStore(cfg.google_sheet_key, load_secrets())


def cmd_status(cfg, store):
    store.prime()
    states = store.read_round_state()
    print("Round state:")
    for n in cfg.round_numbers():
        s = states.get(str(n), {})
        lock = 'LOCKED (simulated or frozen)' if s.get('locked') else 'unlocked'
        entries = store.read_raw_scores(n)
        filled = sum(1 for e in entries
                     for v in e.get('hole_scores', {}).values() if v)
        print(f"  R{n}: {lock:28s} {len(entries):2d} rows, {filled:3d} scores")
    print(f"\nExtras: {len(store.read_extras())}   "
          f"Match play: {len(store.read_match_play())}   "
          f"Corrections: {len(store.read_corrections())}   "
          f"Adjustments: {len(store.read_adjustments())}")
    r5 = store.read_round_5_handicaps()
    print(f"Sunday handicaps: "
          f"{len(r5.get('pairs', {})) if r5 else 0} pairs saved")


def _wipe(cfg, store, unlock):
    store.write_extras([])
    store.write_match_play([])
    store.write_corrections([])
    store.write_adjustments([])
    store.publish_raw_scores({n: [] for n in cfg.round_numbers()})
    # Frozen Sunday handicaps outlive the cards they were derived from, by
    # design, so they have to be cleared explicitly or a set computed from
    # test scores would be showing on the tee.
    store.write_round_5_handicaps(None)
    # The local copies are the fallback when a scrape fails, so leaving
    # simulated cards there means a bad network moment could put them back on
    # the board.
    local_dir = cfg.data_dir
    for name in os.listdir(local_dir) if os.path.isdir(local_dir) else []:
        if name.startswith('raw_scores_round_') or name in (
                'extras.json', 'match_play.json', 'corrections.json',
                'adjustments.json', 'results.json',
                'round_5_handicaps.json'):
            os.remove(os.path.join(local_dir, name))
    if unlock:
        states = {str(n): {'override_status': None, 'locked': False}
                  for n in cfg.round_numbers()}
        store.write_round_state(states, {})


def cmd_clear(cfg, store):
    _wipe(cfg, store, unlock=True)
    print("Cleared extras, match play, corrections, adjustments and all raw "
          "scores; every round unlocked.")
    print("\nNote: unlocked rounds are scraped again, so any scores still in "
          "PlayThru will reappear. Delete them there for a true reset.")


def cmd_off(cfg, store):
    _wipe(cfg, store, unlock=True)
    print("Simulation off. Rounds unlocked and raw scores cleared; the app is "
          "scraping PlayThru again.")
    print("\nStill to do by hand: delete any test scores from the PlayThru "
          "pages, or the next scrape brings them straight back.")


def cmd_load(cfg, store):
    if not os.path.exists(SIM_FILE):
        sys.exit(f"{SIM_FILE} not found - generate it first.")
    with open(SIM_FILE) as f:
        sim = json.load(f)
    rounds = {int(k): v for k, v in sim['rounds'].items()}

    # Lock first. Writing the cards while a round is still unlocked risks a
    # scoring cycle landing in between and republishing scraped (empty) data
    # over the top of them.
    states = {}
    for n in cfg.round_numbers():
        states[str(n)] = {'override_status': None, 'locked': n in rounds}
    store.write_round_state(states, {})

    all_raw = {n: rounds.get(n, []) for n in cfg.round_numbers()}
    store.publish_raw_scores(all_raw)

    for n in sorted(rounds):
        entries = rounds[n]
        filled = sum(1 for e in entries
                     for v in e['hole_scores'].values() if v)
        print(f"  R{n}: {len(entries)} cards, {filled} hole scores, locked")
    print(f"\nLoaded from {SIM_FILE}. Run `simulation.py off` to undo.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command', choices=['status', 'clear', 'load', 'off'])
    args = ap.parse_args()
    cfg, store = connect()
    {'status': cmd_status, 'clear': cmd_clear,
     'load': cmd_load, 'off': cmd_off}[args.command](cfg, store)


if __name__ == '__main__':
    main()
