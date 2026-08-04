"""Count Google Sheets API requests for one admin save.

Google's quota is counted in requests per minute, not bytes or seconds, so
sizing the rate-limit problem means counting calls. This stands a fake
spreadsheet in front of SheetsStore and tallies every call gspread would turn
into an HTTP request, then runs the exact sequence an admin save triggers:
write the entry, then re-run the pipeline the way refresh_now() forces.

Run: python3 tools/count_sheet_calls.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gspread  # noqa: E402
from google.oauth2 import service_account  # noqa: E402

from jdcvo import pipeline  # noqa: E402
from jdcvo.config import EventConfig  # noqa: E402
from jdcvo.store import SheetsStore, MANUAL_WORKSHEETS  # noqa: E402

CALLS = []


def record(kind, what):
    CALLS.append((kind, what))


class FakeWorksheet:
    def __init__(self, title, headers):
        self.title = title
        self._headers = headers
        self._rows = []

    def get_all_records(self):
        record('READ', f'{self.title}.get_all_records')
        return list(self._rows)

    def append_row(self, values):
        record('WRITE', f'{self.title}.append_row')
        self._rows.append(values)

    def clear(self):
        record('WRITE', f'{self.title}.clear')
        self._rows = []

    def update(self, rows, *a, **k):
        record('WRITE', f'{self.title}.update')

    def resize(self, *a, **k):
        record('WRITE', f'{self.title}.resize')


class FakeSpreadsheet:
    def __init__(self):
        self._ws = {}
        for title, (headers, _kind) in MANUAL_WORKSHEETS.items():
            self._ws[title] = FakeWorksheet(title, headers)
        for title in ('Individual Leaderboard', 'Team Leaderboard', 'Raw Scores'):
            self._ws[title] = FakeWorksheet(title, [])

    def worksheets(self):
        record('READ', 'spreadsheet.worksheets (metadata)')
        return list(self._ws.values())

    def add_worksheet(self, title, rows, cols):
        record('WRITE', f'add_worksheet({title})')
        self._ws[title] = FakeWorksheet(title, [])
        return self._ws[title]

    def values_batch_get(self, ranges, params=None):
        record('READ', f'values_batch_get ({len(ranges)} tabs in ONE request)')
        return {'valueRanges': [{'values': []} for _ in ranges]}


def main():
    cfg_path = os.environ.get('JDCVO_CONFIG', 'event_2026.json')
    cfg = EventConfig(cfg_path)

    service_account.Credentials.from_service_account_info = staticmethod(
        lambda info, scopes=None: None)
    gspread.authorize = lambda creds: type(
        'GC', (), {'open_by_key': lambda self, k: FakeSpreadsheet()})()

    store = SheetsStore('FAKEKEY', {})
    print(f"config: {cfg_path}")
    print(f"rounds: {len(list(cfg.round_numbers()))}, "
          f"scrape URLs: {sum(1 for n in cfg.round_numbers() if cfg.round_config(n).get('scrape_url'))}")
    print()

    CALLS.clear()
    store.append_extra('1', 'Frazier', 'chip_in', 2.0, '')
    save_only = list(CALLS)

    CALLS.clear()
    pipeline.run_pipeline(cfg_path, scrape=False, log=lambda m: None,
                          sheets=store, publish=True)
    cycle = list(CALLS)

    CALLS.clear()
    pipeline.run_pipeline(cfg_path, scrape=False, log=lambda m: None,
                          sheets=store, publish=False)
    cycle_nopub = list(CALLS)

    def report(name, calls):
        reads = sum(1 for k, _ in calls if k == 'READ')
        writes = sum(1 for k, _ in calls if k == 'WRITE')
        print(f"--- {name} ---")
        print(f"  {reads} reads, {writes} writes, {reads + writes} requests total")
        for k, what in calls:
            print(f"     {k:6s} {what}")
        print()
        return reads + writes

    report("the save itself (append one extra)", save_only)
    report("the re-score cycle it triggers (publish ON)", cycle)
    report("same cycle with publish OFF", cycle_nopub)

    # A real burst reuses one store, so the one-off metadata read isn't paid
    # again, and the publish throttle lets only the first cycle write.
    for n in (10, 20):
        CALLS.clear()
        for i in range(n):
            store.append_extra('1', f'P{i}', 'chip_in', 2.0, '')
            publish = (i == 0)   # throttle: one publish per interval
            pipeline.run_pipeline(cfg_path, scrape=False, log=lambda m: None,
                                  sheets=store, publish=publish)
        reads = sum(1 for k, _ in CALLS if k == 'READ')
        writes = sum(1 for k, _ in CALLS if k == 'WRITE')
        print("=" * 62)
        print(f"{n} entries back to back, one session:")
        print(f"   {reads} reads  ({'OVER' if reads > 60 else 'under'} the 60/min read limit)")
        print(f"   {writes} writes ({'OVER' if writes > 60 else 'under'} the 60/min write limit)")


if __name__ == '__main__':
    main()
