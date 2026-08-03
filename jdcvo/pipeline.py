"""The scrape -> correct -> score -> publish pipeline.

Run one cycle with run_pipeline(). Used by the GitHub Actions cron, the
admin app's "Pull latest now" button, and locally from the command line:

    python3 -m jdcvo.pipeline               # uses event_2026.json
    python3 -m jdcvo.pipeline --no-scrape   # re-score saved data only

Flow per cycle:
1. For every scrapeable round that is not locked, fetch the PlayThru page and
   save the raw scores locally (and never touch locked rounds).
2. Apply manual hole-score corrections on top of the raw scores.
3. Infer each round's status (not_started / live / complete) and apply any
   manual override. Only live/complete rounds score points.
4. Score every active round with the pure engine, add extras and adjustments,
   and build the leaderboards.
5. Write results.json + a timestamped history snapshot locally; publish to
   Google Sheets when a sheet key is configured.
"""

import argparse
import sys
from datetime import datetime

from . import scoring, state
from .config import EventConfig
from .store import LocalStore


def apply_corrections(scores, corrections, round_number):
    """Overlay manual hole-score corrections onto scraped scores.

    Corrections win over scraped data and survive every re-scrape.
    """
    applied = []
    for entry in scores:
        entry = {**entry, 'hole_scores': dict(entry['hole_scores'])}
        applied.append(entry)

    by_name = {e['name']: e for e in applied}
    for c in corrections:
        if str(c['round']) != str(round_number):
            continue
        entry = by_name.get(c['entity'])
        if entry is None:
            continue
        entry['hole_scores'][str(c['hole'])] = int(c['score'])
        entry['total_score'] = sum(v for v in entry['hole_scores'].values() if v != 0)
    return applied


def score_round(cfg, round_number, scores, manual):
    """Score one round. Returns (player_points_by_name, breakdown, details)."""
    rcfg = cfg.round_config(round_number)
    style = rcfg['scoring_style']

    if style == 'best_ball_individual':
        handicaps = cfg.handicaps()
        handicaps.update(rcfg.get('handicap_overrides', {}))
        result = scoring.calculate_best_ball_individual(
            scores, cfg.course_holes(round_number), handicaps,
            cfg.partners(round_number), cfg.foursomes(round_number),
            allocation=cfg.handicap_allocation)
        return result['player_points'], result['breakdown'], result['details']

    if style == 'match_play':
        pair_points = {tuple(r['players']): float(r['points'])
                       for r in manual['match_play']}
        result = scoring.calculate_match_play(pair_points)
        return result['player_points'], result['breakdown'], result['details']

    if style == 'team_scramble':
        result = scoring.calculate_team_scramble(scores, cfg.course_holes(round_number))
        player_points = {p['name']: result['team_points'].get(p['team'], 0)
                         for p in cfg.players.values()}
        return player_points, result['breakdown'], result['details']

    if style == 'two_man_scramble':
        result = scoring.calculate_two_man_scramble(
            scores, cfg.course_holes(round_number),
            pair_separator=rcfg.get('pair_separator', ' and '))
        player_points = {p['name']: 0 for p in cfg.players.values()}
        player_points.update(result['player_points'])
        breakdown = dict(result['breakdown'])
        breakdown['pair_points'] = result['pair_points']
        return player_points, breakdown, result['details']

    raise ValueError(f"Unknown scoring style: {style}")


def compute_results(cfg, raw, manual, round_states, log=print):
    """Pure computation step: corrected scores -> statuses -> points -> boards.

    ``raw``: {round_number(int): [score entries]}
    ``manual``: dict with extras/corrections/match_play/adjustments
    ``round_states``: {str(round): {'override_status', 'locked'}}
    Returns the full results dict (JSON-serializable).
    """
    name_to_id = cfg.name_to_id()

    # --- 2 & 3. Corrections + status ---
    corrected = {}
    statuses = {}
    for n in cfg.round_numbers():
        corrected[n] = apply_corrections(raw[n], manual['corrections'], n)
        rstate = round_states.get(str(n), {})
        style = cfg.round_config(n)['scoring_style']
        if style == 'match_play':
            inferred = state.LIVE if manual['match_play'] else state.NOT_STARTED
            statuses[n] = rstate.get('override_status') or inferred
        else:
            statuses[n] = state.effective_status(corrected[n], rstate)
        log(f"Round {n}: status={statuses[n]}"
            + (" (locked)" if state.is_locked(rstate) else ""))

    # --- 4. Score active rounds ---
    round_points = {}   # round key -> {player_id: points}
    breakdowns = {}
    details = {}
    for n in cfg.round_numbers():
        if statuses[n] == state.NOT_STARTED:
            continue
        points_by_name, breakdown, round_details = score_round(
            cfg, n, corrected[n], manual)
        round_points[str(n)] = {name_to_id[name]: pts
                                for name, pts in points_by_name.items()
                                if name in name_to_id}
        breakdowns[str(n)] = breakdown
        details[str(n)] = round_details

    # Extras + adjustments (both accumulate into the 'extras' bucket)
    extras = {}
    extras_detail = []
    for row in manual['extras']:
        pid = name_to_id.get(row['player'])
        if pid is None:
            continue
        extras[pid] = extras.get(pid, 0) + float(row['points'])
        extras_detail.append(row)
    for row in manual['adjustments']:
        pid = name_to_id.get(row['player'])
        if pid is None:
            continue
        extras[pid] = extras.get(pid, 0) + float(row['points'])
        extras_detail.append({'round': '', 'player': row['player'],
                              'category': 'adjustment',
                              'points': row['points'],
                              'note': row.get('note', '')})

    leaderboard = scoring.build_leaderboard(cfg.players, round_points,
                                            bonus_points=extras or None)

    return {
        'event_name': cfg.event_name,
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'round_statuses': {str(n): statuses[n] for n in statuses},
        'round_states': round_states,
        'round_points': round_points,
        'breakdowns': breakdowns,
        'details': details,
        'extras_detail': extras_detail,
        'raw_scores': {str(n): corrected[n] for n in corrected},
        'leaderboard': leaderboard,
    }


def run_pipeline(config_path='event_2026.json', scrape=True, log=print,
                 sheets=None, publish=True):
    """Run one full cycle.

    When a Google Sheet is configured, manual inputs (extras, corrections,
    match play, adjustments) and round state come FROM THE SHEET -
    that's the durable, shared store that the admin app and committee edit.
    Local JSON files are the store in dev/no-sheet mode, and results/raw
    scores are always also written locally as backup.
    """
    cfg = EventConfig(config_path)
    local = LocalStore(cfg.data_dir)

    if sheets is None and cfg.google_sheet_key:
        sheets = _make_sheets_store(cfg)

    source = sheets if sheets is not None else local
    if sheets is not None:
        # One batched read of all manual tabs, so the many read_* calls below
        # cost a single API request instead of tripping Google's read quota.
        sheets.prime()
    manual = {
        'extras': source.read_extras(),
        'corrections': source.read_corrections(),
        'match_play': source.read_match_play(),
        'adjustments': source.read_adjustments(),
    }
    round_states = source.read_round_state()
    log(f"Manual inputs from {'Google Sheets' if sheets is not None else 'local JSON'}: "
        f"{len(manual['extras'])} extras, {len(manual['corrections'])} corrections, "
        f"{len(manual['match_play'])} match-play rows, "
        f"{len(manual['adjustments'])} adjustments")

    def saved_scores(n):
        """Last known raw scores for a round: prefer the sheet, else local."""
        if sheets is not None:
            entries = sheets.read_raw_scores(n)
            if entries:
                return entries
        return local.read_raw_scores(n)

    # --- 1. Scrape unlocked rounds ---
    raw = {}
    for n in cfg.round_numbers():
        rcfg = cfg.round_config(n)
        rstate = round_states.get(str(n), {})
        if not rcfg.get('scrape_url'):
            raw[n] = []
            continue
        if state.is_locked(rstate):
            raw[n] = saved_scores(n)
            log(f"Round {n}: locked - using saved scores ({len(raw[n])} entries)")
            continue
        if scrape:
            from . import playthru
            try:
                raw[n] = playthru.scrape(rcfg['scrape_url'], rcfg['scoring_style'])
                local.write_raw_scores(n, raw[n])
                log(f"Round {n}: scraped {len(raw[n])} entries")
            except Exception as e:
                raw[n] = saved_scores(n)
                log(f"Round {n}: SCRAPE FAILED ({e}) - using saved scores "
                    f"({len(raw[n])} entries)")
        else:
            raw[n] = saved_scores(n)

    # --- 2-4. Compute ---
    results = compute_results(cfg, raw, manual, round_states, log=log)

    # Frozen Sunday pair handicaps, if an admin has calculated them. Read
    # rather than recomputed so a late R1/R3 correction can't move a number
    # that has already been announced on the tee.
    r5h = source.read_round_5_handicaps()
    if r5h is None and source is not local:
        r5h = local.read_round_5_handicaps()
    results['round_5_handicaps'] = r5h
    if r5h:
        log(f"Sunday pair handicaps: {len(r5h.get('pairs', {}))} pairs, "
            f"calculated {r5h.get('calculated_at') or 'unknown'}")

    # --- 5. Persist ---
    local.write_results(results)
    log(f"Results written to {cfg.data_dir}/results.json (+ history snapshot)")

    if sheets is not None and publish:
        try:
            sheets.publish_leaderboards(results, cfg.round_numbers())
            sheets.publish_raw_scores(results['raw_scores'])
            sheets.write_round_state(
                {str(n): round_states.get(str(n), {}) for n in cfg.round_numbers()},
                results['round_statuses'])
            log("Published to Google Sheets")
        except Exception as e:
            log(f"SHEETS PUBLISH FAILED ({e}) - local results are still saved")
    else:
        log("No google_sheet_key configured - skipped Sheets publish")

    return results


def _make_sheets_store(cfg):
    """Build a SheetsStore using credentials from env var or local key file."""
    import json as _json
    import os
    from .store import SheetsStore

    info = None
    env_json = os.environ.get('GCP_SERVICE_ACCOUNT_JSON')
    if env_json:
        info = _json.loads(env_json)
    else:
        key_file = os.environ.get('GCP_SERVICE_ACCOUNT_FILE',
                                  'golf-outing-468018-a5bee528ee1c.json')
        with open(key_file) as f:
            info = _json.load(f)
    return SheetsStore(cfg.google_sheet_key, info)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Run one scoring cycle.')
    parser.add_argument('--config', default='event_2026.json')
    parser.add_argument('--no-scrape', action='store_true',
                        help='Re-score saved data without fetching PlayThru')
    args = parser.parse_args()
    try:
        run_pipeline(args.config, scrape=not args.no_scrape)
    except Exception as e:
        print(f"PIPELINE FAILED: {e}", file=sys.stderr)
        raise
