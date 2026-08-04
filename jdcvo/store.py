"""Storage for the JDCVO scoring system.

Two layers, used together:

- LocalStore: JSON files under the event data dir (e.g. data_2026/). Always
  written. Human-readable, git-committable, and the fallback when no Google
  Sheet is configured. Every leaderboard publish also appends a timestamped
  snapshot under data_2026/history/ so no state is ever lost.

- SheetsStore: Google Sheets, the shared human-readable store in production.
  Manual inputs (extras, corrections, match play, round status
  overrides) live in dedicated worksheets so the admin app, the pipeline, and
  any committee member looking at the sheet all see the same data.

IMPORTANT: the 2025 sheet must never be written. The sheet key comes from the
event config and stays empty until a NEW 2026 sheet exists.

Manual-input schemas (JSON files and worksheet columns):
- extras.json:      [{"round": 1, "player": "Pete", "category": "chip_in",
                      "points": 2, "note": ""}]
- corrections.json: [{"round": 1, "entity": "Pete", "hole": "7", "score": 5,
                      "note": ""}]  # entity = player, team, or pair name
- match_play.json:  [{"players": ["Andrew", "Tom K"], "points": 3.75}]
- adjustments.json: [{"player": "Pete", "points": 4.5, "note": ""}]
- round_state.json: {"1": {"override_status": null, "locked": false}, ...}
- round_5_handicaps.json:
      {"calculated_at": "2026-08-09 07:30:00",
       "pairs": {"Joe M & Trock": {"player_a": "Joe M", "player_b": "Trock",
                                   "player_a_handicap": 3,
                                   "player_b_handicap": 22,
                                   "pair_handicap": 5}}}
  Saved deliberately by an admin, not recomputed each cycle: the pair
  handicaps are announced on the first tee Sunday, so a late Round 1 or 3
  correction must not silently move them. Recalculating is an explicit act.
"""

import json
import os
from datetime import datetime


class LocalStore:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(os.path.join(data_dir, 'history'), exist_ok=True)

    def _path(self, name):
        return os.path.join(self.data_dir, name)

    def _read_json(self, name, default):
        try:
            with open(self._path(name)) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    def _write_json(self, name, data):
        tmp = self._path(name + '.tmp')
        with open(tmp, 'w') as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self._path(name))

    # --- raw scraped scores ---

    def read_raw_scores(self, round_number):
        return self._read_json(f'raw_scores_round_{round_number}.json', [])

    def write_raw_scores(self, round_number, scores):
        self._write_json(f'raw_scores_round_{round_number}.json', scores)

    # --- manual inputs ---

    def read_extras(self):
        return self._read_json('extras.json', [])

    def write_extras(self, extras):
        self._write_json('extras.json', extras)

    def read_corrections(self):
        return self._read_json('corrections.json', [])

    def write_corrections(self, corrections):
        self._write_json('corrections.json', corrections)

    def read_match_play(self):
        return self._read_json('match_play.json', [])

    def write_match_play(self, results):
        self._write_json('match_play.json', results)

    def read_adjustments(self):
        return self._read_json('adjustments.json', [])

    def write_adjustments(self, adjustments):
        self._write_json('adjustments.json', adjustments)

    def read_round_state(self):
        return self._read_json('round_state.json', {})

    def write_round_state(self, state):
        self._write_json('round_state.json', state)

    def read_round_5_handicaps(self):
        return self._read_json('round_5_handicaps.json', None)

    def write_round_5_handicaps(self, data):
        self._write_json('round_5_handicaps.json', data)

    # --- computed output ---

    def write_results(self, results):
        """Write the full computed results and a timestamped history snapshot."""
        self._write_json('results.json', results)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        with open(os.path.join(self.data_dir, 'history', f'results_{stamp}.json'), 'w') as f:
            json.dump(results, f, indent=2)

    def read_results(self):
        return self._read_json('results.json', None)


# ---------------------------------------------------------------------------
# Google Sheets
# ---------------------------------------------------------------------------

MANUAL_WORKSHEETS = {
    # worksheet title -> (header row, kind)
    'Extras': (['Round', 'Player', 'Category', 'Points', 'Note'], 'rows'),
    'Corrections': (['Round', 'Entity', 'Hole', 'Score', 'Note'], 'rows'),
    'Match Play': (['Player 1', 'Player 2', 'Points'], 'rows'),
    'Adjustments': (['Player', 'Points', 'Note'], 'rows'),
    'Round Status': (['Round', 'Inferred Status', 'Override Status', 'Locked'], 'rows'),
    # Computed by the admin, then frozen. Lives here rather than with the
    # published leaderboards because it must be read back unchanged every
    # cycle, and because the local data dir is ephemeral on Streamlit Cloud.
    'Sunday Handicaps': (['Pair', 'Player A', 'A Sunday Handicap',
                          'Player B', 'B Sunday Handicap', 'Pair Handicap',
                          'Calculated At'], 'rows'),
}


def _values_to_records(values):
    """Turn a raw value matrix (header row + data rows) into record dicts,
    mirroring gspread's get_all_records() so callers are unaffected."""
    if not values:
        return []
    headers = [str(h) for h in values[0]]
    records = []
    for row in values[1:]:
        row = list(row) + [''] * (len(headers) - len(row))
        records.append({h: row[i] for i, h in enumerate(headers)})
    return records


class SheetsStore:
    """Reads manual inputs from and publishes results to a Google Sheet.

    Credentials come from a service-account info dict (e.g. Streamlit
    secrets or a JSON key file loaded by the caller).
    """

    def __init__(self, sheet_key, service_account_info):
        import gspread
        from google.oauth2 import service_account

        scopes = ['https://www.googleapis.com/auth/spreadsheets']
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info, scopes=scopes)
        self.gc = gspread.authorize(credentials)
        # Opening the sheet is a metadata read like any other, so it can be
        # rate-limited too. Unretried it took the whole app down with a raw
        # APIError, while every later call quietly rode the limit out.
        self.sheet = self._retry(self.gc.open_by_key, sheet_key)
        self._ws_map = None       # title -> Worksheet (fetched once, reused)
        self._rows_cache = None   # title -> [record dicts], primed per cycle
        self._raw_cache = None    # Raw Scores rows, fetched once and filtered

    @staticmethod
    def _retry(fn, *args, **kwargs):
        """Run a gspread call, retrying transient 429/5xx with backoff.

        Google caps reads/writes per minute; a burst of admin actions or
        reruns can trip it. Rather than surface a 429 to the user, wait and
        retry a few times so brief spikes heal themselves.
        """
        import time
        import gspread

        delay = 1.0
        for attempt in range(5):
            try:
                return fn(*args, **kwargs)
            except gspread.exceptions.APIError as e:
                status = getattr(getattr(e, 'response', None), 'status_code', None)
                transient = status == 429 or (status is not None and 500 <= status < 600)
                if not transient or attempt == 4:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 20)

    def _worksheet_map(self):
        """All worksheets by title, fetched once (one metadata read) and reused."""
        if self._ws_map is None:
            self._ws_map = {ws.title: ws
                            for ws in self._retry(self.sheet.worksheets)}
        return self._ws_map

    def _worksheet(self, title, headers):
        ws_map = self._worksheet_map()
        if title not in ws_map:
            ws = self._retry(self.sheet.add_worksheet, title=title,
                             rows=200, cols=len(headers) + 2)
            self._retry(ws.update, [headers])
            ws_map[title] = ws
        return ws_map[title]

    def prime(self):
        """Load every manual-input tab in a single batch read.

        Called once at the start of a scoring cycle so the ~six read_* calls
        that follow cost one API request instead of a dozen (each of which
        previously also re-fetched sheet metadata). This is the main defense
        against Google's per-minute read quota.
        """
        # The store now outlives a single cycle, so last cycle's scores must go
        # or a locked round would be served stale ones.
        self._raw_cache = None
        titles = list(MANUAL_WORKSHEETS)
        for title in titles:
            self._worksheet(title, MANUAL_WORKSHEETS[title][0])  # ensure exists
        ranges = [f"'{t}'!A1:Z" for t in titles]
        resp = self._retry(self.sheet.values_batch_get, ranges,
                           params={'valueRenderOption': 'UNFORMATTED_VALUE'})
        value_ranges = resp.get('valueRanges', [])
        self._rows_cache = {}
        for title, vr in zip(titles, value_ranges):
            self._rows_cache[title] = _values_to_records(vr.get('values', []))

    def _read_rows(self, title):
        if self._rows_cache is not None and title in self._rows_cache:
            return self._rows_cache[title]
        headers = MANUAL_WORKSHEETS[title][0]
        ws = self._worksheet(title, headers)
        return self._retry(ws.get_all_records)

    def _invalidate_cache(self):
        """Drop the batch-read cache after a write so later reads are fresh."""
        self._rows_cache = None
        self._raw_cache = None

    # --- manual inputs (read from sheet; admins may edit sheet directly) ---

    def read_extras(self):
        rows = self._read_rows('Extras')
        return [{'round': r['Round'], 'player': r['Player'],
                 'category': r['Category'], 'points': r['Points'],
                 'note': r.get('Note', '')} for r in rows if r.get('Player')]

    def read_corrections(self):
        rows = self._read_rows('Corrections')
        return [{'round': r['Round'], 'entity': r['Entity'],
                 'hole': str(r['Hole']), 'score': r['Score'],
                 'note': r.get('Note', '')} for r in rows if r.get('Entity')]

    def read_match_play(self):
        rows = self._read_rows('Match Play')
        return [{'players': [r['Player 1'], r['Player 2']], 'points': r['Points']}
                for r in rows if r.get('Player 1')]

    def read_adjustments(self):
        rows = self._read_rows('Adjustments')
        return [{'player': r['Player'], 'points': r['Points'],
                 'note': r.get('Note', '')} for r in rows if r.get('Player')]

    def read_round_state(self):
        rows = self._read_rows('Round Status')
        state = {}
        for r in rows:
            if r.get('Round') != '':
                override = str(r.get('Override Status', '')).strip().lower()
                locked = str(r.get('Locked', '')).strip().lower() in ('true', 'yes', '1', 'x')
                state[str(r['Round'])] = {
                    'override_status': override or None,
                    'locked': locked,
                }
        return state

    def read_round_5_handicaps(self):
        """Read the frozen Sunday pair handicaps, or None if not yet saved."""
        rows = [r for r in self._read_rows('Sunday Handicaps') if r.get('Pair')]
        if not rows:
            return None

        def num(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        pairs = {}
        for r in rows:
            pairs[r['Pair']] = {
                'player_a': r.get('Player A', ''),
                'player_b': r.get('Player B', ''),
                'player_a_handicap': num(r.get('A Sunday Handicap')),
                'player_b_handicap': num(r.get('B Sunday Handicap')),
                'pair_handicap': num(r.get('Pair Handicap')),
            }
        return {'calculated_at': rows[0].get('Calculated At', ''),
                'pairs': pairs}

    def write_round_5_handicaps(self, data):
        headers = MANUAL_WORKSHEETS['Sunday Handicaps'][0]
        ws = self._worksheet('Sunday Handicaps', headers)
        rows = [headers]
        stamp = (data or {}).get('calculated_at', '')
        for label, p in sorted((data or {}).get('pairs', {}).items()):
            rows.append([label, p['player_a'], p['player_a_handicap'],
                         p['player_b'], p['player_b_handicap'],
                         p['pair_handicap'], stamp])
        self._retry(ws.clear)
        self._retry(ws.update, rows)
        self._invalidate_cache()

    def append_extra(self, round_number, player, category, points, note=''):
        ws = self._worksheet('Extras', MANUAL_WORKSHEETS['Extras'][0])
        self._retry(ws.append_row, [round_number, player, category, points, note])
        self._invalidate_cache()

    def write_extras(self, extras):
        """Replace the whole Extras worksheet (used for edits/deletions)."""
        headers = MANUAL_WORKSHEETS['Extras'][0]
        ws = self._worksheet('Extras', headers)
        rows = [headers]
        for e in extras:
            rows.append([e.get('round', ''), e.get('player', ''),
                         e.get('category', ''), e.get('points', ''),
                         e.get('note', '')])
        self._retry(ws.clear)
        self._retry(ws.update, rows)
        self._invalidate_cache()

    def append_correction(self, round_number, entity, hole, score, note=''):
        ws = self._worksheet('Corrections', MANUAL_WORKSHEETS['Corrections'][0])
        self._retry(ws.append_row, [round_number, entity, hole, score, note])
        self._invalidate_cache()

    def write_corrections(self, corrections):
        """Replace the whole Corrections worksheet (used for edits/deletions)."""
        headers = MANUAL_WORKSHEETS['Corrections'][0]
        ws = self._worksheet('Corrections', headers)
        rows = [headers]
        for c in corrections:
            rows.append([c.get('round', ''), c.get('entity', ''),
                         c.get('hole', ''), c.get('score', ''),
                         c.get('note', '')])
        self._retry(ws.clear)
        self._retry(ws.update, rows)
        self._invalidate_cache()

    def append_match_play(self, player1, player2, points):
        ws = self._worksheet('Match Play', MANUAL_WORKSHEETS['Match Play'][0])
        self._retry(ws.append_row, [player1, player2, points])
        self._invalidate_cache()

    def write_match_play(self, results):
        """Replace the whole Match Play worksheet.

        Used when saving a match, which writes both pairs at once and must be
        able to correct an earlier entry. Appending would leave the superseded
        rows behind: the scoring engine tolerates that (last row wins) but a
        human reading the sheet would see a pair credited twice.
        """
        headers = MANUAL_WORKSHEETS['Match Play'][0]
        ws = self._worksheet('Match Play', headers)
        rows = [headers]
        for r in results:
            players = list(r['players'])
            rows.append([players[0], players[1], r['points']])
        self._retry(ws.clear)
        self._retry(ws.update, rows)
        self._invalidate_cache()

    def append_adjustment(self, player, points, note=''):
        ws = self._worksheet('Adjustments', MANUAL_WORKSHEETS['Adjustments'][0])
        self._retry(ws.append_row, [player, points, note])
        self._invalidate_cache()

    def write_adjustments(self, adjustments):
        """Replace the whole Adjustments worksheet (used for edits/deletions)."""
        headers = MANUAL_WORKSHEETS['Adjustments'][0]
        ws = self._worksheet('Adjustments', headers)
        rows = [headers]
        for a in adjustments:
            rows.append([a.get('player', ''), a.get('points', ''),
                         a.get('note', '')])
        self._retry(ws.clear)
        self._retry(ws.update, rows)
        self._invalidate_cache()

    def write_round_state(self, round_states, inferred):
        headers = MANUAL_WORKSHEETS['Round Status'][0]
        ws = self._worksheet('Round Status', headers)
        rows = [headers]
        for rn in sorted(round_states, key=int):
            s = round_states[rn]
            rows.append([rn, inferred.get(rn, ''),
                         s.get('override_status') or '',
                         'TRUE' if s.get('locked') else ''])
        self._retry(ws.clear)
        self._retry(ws.update, rows)
        self._invalidate_cache()

    # --- published output ---

    def publish_leaderboards(self, results, round_numbers):
        """Write Individual/Team Leaderboard worksheets (2025-compatible layout)."""
        individual = results['leaderboard']['individual']
        team = results['leaderboard']['team']

        ind_rows = [['Rank', 'Player', 'Team', 'Total'] +
                    [f'R{n}' for n in round_numbers] + ['Extras']]
        standings = sorted(individual.values(), key=lambda e: -e['total_points'])
        for rank, entry in enumerate(standings, 1):
            rs = entry['round_scores']
            ind_rows.append(
                [rank, entry['name'], entry['team'], round(entry['total_points'], 2)] +
                [rs.get(str(n), rs.get(n, 0)) for n in round_numbers] +
                [rs.get('extras', 0)])

        team_rows = [['Rank', 'Team', 'Total', 'Players']]
        id_names = {pid: e['name'] for pid, e in individual.items()}
        for rank, (t, entry) in enumerate(
                sorted(team.items(), key=lambda x: -x[1]['total_points']), 1):
            team_rows.append([rank, t, round(entry['total_points'], 2),
                              ', '.join(id_names[p] for p in entry['players'])])

        ws = self._worksheet('Individual Leaderboard', ind_rows[0])
        self._retry(ws.clear)
        self._retry(ws.update, ind_rows)
        ws = self._worksheet('Team Leaderboard', team_rows[0])
        self._retry(ws.clear)
        self._retry(ws.update, team_rows)

    def read_raw_scores(self, round_number):
        """Read one round's raw scores back from the 'Raw Scores' worksheet.

        Used for locked rounds so finalized scores are served from the sheet
        (the durable copy) instead of being re-scraped.

        The whole tab is fetched once and kept, because every round lives in
        it: asking per round meant five identical full-tab reads per cycle,
        which is pure waste against a per-minute request quota.
        """
        headers = ['Round', 'Name'] + [str(h) for h in range(1, 19)] + ['Total']
        if self._raw_cache is None:
            ws = self._worksheet('Raw Scores', headers)
            self._raw_cache = self._retry(ws.get_all_records)
        entries = []
        for r in self._raw_cache:
            if str(r.get('Round')) != str(round_number) or not r.get('Name'):
                continue
            hole_scores = {}
            for h in range(1, 19):
                v = r.get(str(h), '')
                if v != '':
                    hole_scores[str(h)] = int(v)
            entries.append({
                'name': r['Name'],
                'hole_scores': hole_scores,
                'total_score': int(r['Total']) if r.get('Total') != '' else 0,
            })
        return entries

    def publish_raw_scores(self, all_raw):
        """Write a human-readable backup of raw hole scores per round.

        Must be given the SCRAPED scores, not corrected ones: read_raw_scores
        reads this tab back as the source for locked rounds and failed scrapes,
        so a corrected value written here would become indistinguishable from a
        real score and could never be undone. See the call site in pipeline.py.
        """
        headers = ['Round', 'Name'] + [str(h) for h in range(1, 19)] + ['Total']
        rows = [headers]
        for round_number, scores in sorted(all_raw.items(), key=lambda x: int(x[0])):
            for entry in scores:
                hs = entry.get('hole_scores', {})
                rows.append([round_number, entry['name']] +
                            [hs.get(str(h), '') for h in range(1, 19)] +
                            [entry.get('total_score', '')])
        ws = self._worksheet('Raw Scores', headers)
        self._retry(ws.clear)
        self._retry(ws.update, rows)
        self._raw_cache = None
