"""Round lifecycle: auto-inferred status with manual override/lock.

Statuses:
- ``not_started``: no non-zero hole score exists for the round yet.
- ``live``: at least one non-zero hole score exists.
- ``complete``: every entry has a non-zero score on all 18 holes.

Only rounds that are ``live`` or ``complete`` contribute points, so an
upcoming round whose PlayThru page is all zeros (or empty) can never award
points or split-tie its way onto the leaderboard.

Manual controls (set from the admin UI, stored per round):
- ``override_status``: force a status regardless of the data.
- ``locked``: freeze the round; the pipeline stops re-scraping it and keeps
  using the last saved raw scores, so later edits on the PlayThru site can't
  silently change finalized standings.
"""

NOT_STARTED = 'not_started'
LIVE = 'live'
COMPLETE = 'complete'


def infer_round_status(scores):
    """Infer a round's status from its scraped score entries."""
    if not scores:
        return NOT_STARTED

    any_nonzero = False
    all_complete = True
    for entry in scores:
        hole_scores = entry.get('hole_scores', {})
        nonzero = [h for h, s in hole_scores.items() if s != 0]
        if nonzero:
            any_nonzero = True
        if len(nonzero) < 18:
            all_complete = False

    if not any_nonzero:
        return NOT_STARTED
    return COMPLETE if all_complete else LIVE


def effective_status(scores, round_state=None):
    """Combine inferred status with a manual override.

    ``round_state``: optional {'override_status': str|None, 'locked': bool}.
    """
    if round_state and round_state.get('override_status'):
        return round_state['override_status']
    return infer_round_status(scores)


def is_locked(round_state=None):
    return bool(round_state and round_state.get('locked'))
