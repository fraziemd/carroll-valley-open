# JDCVO 2026 Scoring System

A rebuild of the 2025 notebook-based scoring for the "Jimmy D" Carroll Valley
Open. No laptop babysitting, no Selenium, no black box: an always-available
web app with a public leaderboard (with full drill-downs) and a
password-protected admin side, backed by human-readable data that can always
be recovered by hand.

Nothing from 2025 was modified: the notebooks, `app.py`, and all
`*_results.json` / `golf_scores_*.json` files are untouched and now serve as
the validation fixtures for the new engine.

## Start here

| If you want to | Read |
|---|---|
| Pick this project up cold, or fix something during the event | **`HANDOFF.md`** — what runs where, how to push a change, GitHub auth when switching machines, and an event-day playbook |
| Know exactly how a point is earned | **`SCORING.md`** — the rules with derivations and code locations |
| Hand the rules to a committee member | **`RULES.html`** and the `.docx` beside it — standalone, derived from the code |
| Hand something to the players | **`PLAYER_GUIDE.html`** and its `.docx` — two pages for the bag |

## What's here

| Path | Purpose |
|---|---|
| `HANDOFF.md` | Orientation for a new session: machines, auth, deploy, event-day fixes |
| `SCORING.md` | **The rules of the event** — every point value, the yearly handicap formula, and the Sunday pair handicap formula |
| `RULES.html`, `PLAYER_GUIDE.html` | Shareable rules documents; `tools/html_to_docx.py` regenerates the Word versions |
| `jdcvo/scoring.py` | Pure scoring engine (best ball, match play, scrambles, survival, tiebreakers, R5 handicaps) |
| `jdcvo/test_2025_validation.py` | Proves the engine reproduces the saved 2025 results exactly |
| `jdcvo/playthru.py` | PlayThru scraper — plain HTTP + BeautifulSoup (the page is server-rendered; Selenium was never needed) |
| `jdcvo/state.py` | Round lifecycle: auto-inferred `not_started` / `live` / `complete`, plus manual override and lock |
| `jdcvo/config.py` | Loads the event config (rounds, courses, roster, sheet key). Defaults to `event_2026.json`; set `JDCVO_CONFIG` to run against another (e.g. a draft) without touching the deployment |
| `jdcvo/store.py` | Local JSON storage + Google Sheets storage/publishing |
| `jdcvo/pipeline.py` | One cycle: scrape → apply corrections → infer status → score → publish |
| `app_2026.py` | The Streamlit app (public + admin) |
| `event_2026.json` | The event definition — edit this for 2026 |
| `data_2026/` | Working data: raw scores, manual inputs, results + timestamped history |
| `.github/workflows/score-update.yml` | A 5-minute cron that would publish to Sheets — **on disk only, deliberately not committed.** The token in use lacks GitHub's `workflow` scope, so any push touching `.github/` is rejected entirely. Never `git add` it. There is consequently no scheduled scrape: scores are pulled when somebody opens the app. See `HANDOFF.md` §3 |

## How scores flow

1. **Scrape**: each round with a `scrape_url` is fetched from PlayThru
   (simple HTTP GET). Locked rounds are never re-scraped.
2. **Corrections**: admin-entered hole-score fixes are stored separately and
   re-applied after every scrape, so a refresh can never wipe them.
3. **Status**: a round is `not_started` until any non-zero score appears
   (so future rounds award zero points — no phantom tie-splitting),
   `live` during play, `complete` when all cards are full. Admin can
   override and can **lock** a finished round so later edits on the
   PlayThru site can't silently change finalized standings.
4. **Score**: the tested engine computes every point (rules in `SCORING.md`);
   extras and adjustments are layered on top.
5. **Publish**: results are written to `data_2026/results.json` plus a
   timestamped snapshot in `data_2026/history/`, and to the Google Sheet
   when configured.

The Streamlit app runs this cycle itself (cached ~2 minutes), so the public
leaderboard self-updates with no external scheduler. The GitHub Actions cron
is an independent backup path that keeps the Sheet fresh.

## Validation

```
python3 tools/run_2025_tests.py        # no pytest needed
python3 -m jdcvo.test_2025_validation  # equivalent
```

Recomputes rounds 1–5 and the Round 5 handicaps from the raw 2025 hole
scores and asserts exact agreement with the saved 2025 results, then prints
the reconstructed final leaderboard.

The engine started as a faithful port that reproduced 2025 exactly, quirks and
all. Two rules have since been deliberately changed, both documented in
`SCORING.md`: the handicap stroke allocation was corrected (it had silently
capped everyone at 18 strokes) and a broken survival tiebreaker was fixed.
Because of the first, the 2025 fixtures in this repo have been regenerated and
no longer match the totals published to the 2025 sheet at the time. Any further
rule change should be an explicit edit plus a fixture update, never a silent
drift.

## Running locally

The fastest way, and the panic button if the cloud is down: **double-click
`run_local.command`** (in Finder) or run `./run_local.command`. It installs
dependencies on first use, then starts the full app (public + admin) on this
machine at http://localhost:8501.

Equivalently, by hand:

```
pip install -r requirements_2026.txt
streamlit run app_2026.py
```

Admin password goes in `.streamlit/secrets.toml`:

```toml
admin_password = "choose-something"
```

Without a Google Sheet configured, everything works against local JSON in
`data_2026/` — that's the current state, seeded with the 2025 data so you
can explore the app.

## Deploying for the event

1. **Push this repo to GitHub** (see security note below first).
2. **Streamlit Community Cloud**: new app → this repo → `app_2026.py`.
   Add secrets: `admin_password`, and `[gcp_service_account]` (the service
   account key as TOML) if using Sheets.
3. **Create a NEW Google Sheet for 2026** (do not reuse the 2025 sheet),
   share it with the service account email, and put its key in
   `event_2026.json` → `google_sheet_key`.
4. **GitHub Actions** (optional): add repo secret `GCP_SERVICE_ACCOUNT_JSON`.
5. **Before the event**: update `event_2026.json` with this year's rounds and
   PlayThru event ids, and create `players_2026.json` when the roster/teams/
   handicaps are set (then point `players_file` at it).

## If something breaks (disaster recovery)

The whole system is designed so that **no single service is a point of
failure**. Keep this in mind: the *data* lives in three independent places and
the *compute* runs anywhere Python does.

**Your data is in three places, none depending on the others:**

1. **Google Sheets** — human-readable; export any time with `File → Download`.
2. **Local snapshots** — every pipeline run writes `data_2026/results.json`
   plus a timestamped copy in `data_2026/history/`. The 2025 data is also in
   the repo.
3. **The Git repo** — code, config, and data cloned on your machine.

**The compute is portable:** the engine (`jdcvo/`) and pipeline are plain
Python. Streamlit is just the front door; GitHub Actions is just a timer.

### The one move that fixes almost everything

Double-click **`run_local.command`** (or run `./run_local.command`). This runs
the entire app — public leaderboard and admin — on your own computer,
independent of Streamlit Cloud and GitHub. It's exactly how 2025 worked, minus
the notebook. Keep the window open while you use it.

### By scenario

| What went wrong | What still works | What to do |
|---|---|---|
| **Streamlit Cloud is down / your account got suspended** | Everything else. | Run `run_local.command`. It reaches the same Google Sheet, so no data is lost. Optionally redeploy to another host later (below). |
| **GitHub is down / Actions disabled** | The app self-refreshes on its own (~2-min cache); the cron is only a backup. | Nothing urgent. Just keep the app open, or use the admin **Pull latest now** button. |
| **Google Sheets unreachable / Google account issue** | The app auto-falls back to local JSON in `data_2026/`; results + history snapshots still save locally. | Run locally; you keep scoring against the last snapshot. Re-sync to a Sheet once access returns. |
| **Everything cloud is down / you're offline** | Local data in `data_2026/`. | Run `run_local.command` on any machine that has this repo. Scraping needs internet, but you can still enter/fix scores by hand and score them. |
| **You lost the leaderboard entirely** | The Sheet and `data_2026/history/`. | Open the Sheet, or copy the newest file from `data_2026/history/` over `data_2026/results.json`. |

### Moving to a different host (if you want cloud back, not Streamlit)

Because the app reads only `event_2026.json` + the Sheet, redeploying is just
pointing a new host at this repo:

- **Render / Railway / Fly.io / Hugging Face Spaces**: new web service from the
  repo, start command `streamlit run app_2026.py --server.port $PORT`, and set
  the same secrets (`admin_password`, `GCP_SERVICE_ACCOUNT_JSON`).

### Avoiding the suspension in the first place

The earlier suspension looked like a false "crypto" flag from filenames. To
minimize the chance: **keep the repo private**, and don't ship credential-named
files — `golf-leaderboard-key.pem` and the service-account JSON are already
gitignored, so they never reach GitHub/Streamlit. If a key ever *does* leak,
rotate it in Google Cloud Console.

## Security note (do this before pushing to GitHub)

`golf-outing-468018-a5bee528ee1c.json` (Google service-account key) and
`golf-leaderboard-key.pem` are credentials sitting in the working directory.
They must not be committed: keep them in `.gitignore`, and put the key JSON
into Streamlit/GitHub secrets instead. If the repo was ever public with the
key in it, rotate the key in Google Cloud Console.

## During the event — cheat sheet

- **Where am I up to?**: Admin → Progress lists every step in order, ticks off
  the ones the stored data shows are done, and calls out the next one. It is
  derived from the data, not from a checklist you tick, so it can't drift.
- **Nothing to start/stop per round**: statuses flip automatically as scores
  appear. Glance at Admin → Round status if you want to confirm.
- **After each round**: enter extras (chip-ins etc.) in Admin → Extras;
  lock the round in Admin → Round status.
- **Round 2 (skins/match play)**: enter pair points in Admin → Match play. It
  can't auto-complete, so close it manually when the points are in.
- **Before Sunday**: once Rounds 1 and 3 are final, run Admin → Sunday
  handicaps, check the numbers, then save to lock them. The pair strokes then
  show on the public Round 5 page for everyone. Saved figures don't move if a
  Round 1 or 3 score is corrected later — recalculate deliberately if you want
  them to.
- **Bad score on a card**: Admin → Fix a score (survives re-scrapes).
- **Rounds scored out of order**: allowed on purpose. Gating a later round on
  an earlier one would stop scoring mid-event over a single unfinished card,
  which is worse than the problem it prevents. An untouched round is
  `not_started` and awards nothing, and anything out of sequence shows up as a
  note in Admin → Progress.
- **Anything weird**: every computed point is visible in Rounds → "Full
  scoring log", and every publish leaves a snapshot in `data_2026/history/`.
