"""Read-only dump of what is currently stored for the event.

Reads only; writes nothing and triggers no publish. Use it to see what test
data is sitting in the sheet before deciding what to clear.

Run: python3 tools/inspect_sheet.py
"""
import os
import sys
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jdcvo.config import EventConfig  # noqa: E402
from jdcvo.store import SheetsStore  # noqa: E402


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
        v = v.strip().strip('"')
        info[k.strip()] = v.replace('\\n', '\n')
    return info


def main():
    cfg = EventConfig(os.environ.get('JDCVO_CONFIG', 'event_2026.json'))
    store = SheetsStore(cfg.google_sheet_key, load_secrets())
    store.prime()

    print("=== ROUND STATE ===")
    states = store.read_round_state()
    for n in cfg.round_numbers():
        s = states.get(str(n), {})
        print(f"  R{n}: {cfg.round_config(n)['name']}")
        print(f"      {s if s else '(no stored state)'}")

    print("\n=== RAW SCORES (per round) ===")
    for n in cfg.round_numbers():
        entries = store.read_raw_scores(n)
        if not entries:
            print(f"  R{n}: empty")
            continue
        filled = sum(1 for e in entries
                     for v in e.get('hole_scores', {}).values() if v)
        print(f"  R{n}: {len(entries)} rows, {filled} non-zero hole scores")
        for e in entries[:3]:
            hs = e.get('hole_scores', {})
            got = {h: v for h, v in sorted(hs.items(), key=lambda x: int(x[0])) if v}
            print(f"      {e['name']}: {got}")
        if len(entries) > 3:
            print(f"      ... {len(entries) - 3} more rows")

    print("\n=== MANUAL INPUTS ===")
    for label, rows in (('Extras', store.read_extras()),
                        ('Corrections', store.read_corrections()),
                        ('Match Play', store.read_match_play()),
                        ('Adjustments', store.read_adjustments())):
        print(f"  {label}: {len(rows)} rows")
        for r in rows:
            print(f"      {r}")

    r5 = store.read_round_5_handicaps()
    print(f"\n=== SUNDAY HANDICAPS ===\n  {r5 if r5 else '(none saved)'}")


if __name__ == '__main__':
    main()
