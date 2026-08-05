# Handoff — everything a new session needs

Written for an AI assistant picking this project up cold, on a different
machine, possibly mid-event with a problem to fix. Read this first, then
`SCORING.md` if the question is about how points are calculated.

---

## 1. What this is, in four sentences

A five-round golf outing for 20 players over one weekend. Players post hole
scores on golfplaythru.com; this app scrapes those pages, scores every round,
and publishes individual and team leaderboards to a Google Sheet and to a public
Streamlit site. A handful of things can't be scraped — match play results,
closest-to-the-pin awards, hole corrections — so they're typed into a
password-protected admin page and stored in the Sheet. The Sheet is the durable
store; the app holds nothing of its own.

**Live app:** https://carrollvalleyopen.streamlit.app
**Repo:** https://github.com/fraziemd/carroll-valley-open (public, branch `main`)

---

## 2. Nothing runs on the owner's computers

Worth being clear about, because it comes up.

The app runs on Streamlit Community Cloud, deployed from the GitHub repo, using
credentials held in Streamlit's own secrets store. No laptop, desktop or home
server needs to be switched on for the leaderboard to work or for scores to be
pulled. Editing code obviously needs a machine; running the event does not.

**There is no scheduled scrape.** A GitHub Actions cron exists as a file
(`.github/workflows/score-update.yml`) but is deliberately **not committed** —
see §4. Scores are therefore pulled when somebody loads or clicks around in the
app, cached for `REFRESH_SECONDS` (120) between pulls. That's fine: the
leaderboard is always current for whoever is looking at it.

**Streamlit sleeps a free app after 12 hours with no visitors.** The first
person to the URL then sees a wake-up button and waits about a minute. No data
is affected. Opening the app once each morning avoids it.

---

## 3. Switching machines — git identity and GitHub auth

This is the part that bites, so it's early.

### The account

The repo belongs to GitHub user **`fraziemd`**. The remote is HTTPS:

```
https://github.com/fraziemd/carroll-valley-open.git
```

### Authentication is per-machine

Pushes authenticate with a **personal access token** stored in the macOS
keychain (`credential.helper = osxkeychain`). That keychain entry is local to
each machine, so **a token working on one computer says nothing about another.**

On a machine that has never pushed, the first `git push` prompts for a username
and password. Enter:

- Username: `fraziemd`
- Password: **the personal access token**, not the GitHub account password.
  GitHub stopped accepting passwords over HTTPS years ago; entering the real
  password just fails with an unhelpful error.

The token then lands in the keychain and later pushes are silent. Tokens can be
reused across machines — the same token pasted on a second computer works fine.

### Commit author identity is a separate thing, and it is currently wrong

Authentication decides *whether* a push is allowed. The author name on a commit
is just text from `git config` and is not checked against anything. On the Mac
mini it is set to the **placeholder** values git ships with:

```
user.name  = Your Name
user.email = your@email.com
```

So 31 commits in this history read `Your Name <your@email.com>`. Harmless —
they pushed fine and the code is correct — but GitHub can't link them to the
`fraziemd` account, and the history is a mess of identities:

| Author on commits | Count |
|---|---|
| `Your Name <your@email.com>` | 31 |
| `cinghiale <cinghiale@cinghiales-Mini.home.local>` | 9 |
| `fraziemd <fraziemd@yahoo.com>` | 4 |
| `cinghiale <cinghiale@cinghiales-Mac-mini.local>` | 3 |
| `Crypto Tradz <cryptotradz99@gmail.com>` | 3 |

`cinghiale` is the macOS account name, which git falls back to when nothing is
configured. `Crypto Tradz` is a stale global config from some earlier setup.

**Do not "fix" this without asking.** The owner has a standing instruction never
to change git config on his behalf. Mention it, offer the one-liner, let him
decide. If he says yes, it is:

```
git config --global user.name "fraziemd"
git config --global user.email "fraziemd@yahoo.com"
```

That only affects future commits; it does not rewrite history, and rewriting
history on a pushed public branch is not worth it here.

### The workflow-scope trap

The token in use lacks the **`workflow`** scope. Any push that touches
`.github/workflows/` is rejected outright with a scope error, and the whole
push fails — not just that file. This already happened once and cost an hour.

`score-update.yml` is therefore untracked on purpose. It sits on disk, it is in
no commit, and GitHub Actions never runs it.

**So: never `git add .github/`.** If a cron scrape is genuinely wanted, the
owner has to mint a new token with `workflow` ticked first.

Prefer explicit paths over `git add -A` for this reason.

---

## 4. Repo layout

| Path | What it is |
|---|---|
| `app.py` | Three-line shim. Streamlit Cloud's entrypoint was fixed at deploy time and can't be changed, so this `runpy`s the real app. Don't put logic here. |
| `app_2026.py` | The whole UI — leaderboard, players, teams, rounds, admin. ~2200 lines. |
| `jdcvo/scoring.py` | Pure scoring. Scores in, points out. No I/O. **Every rule lives here.** |
| `jdcvo/pipeline.py` | One cycle: scrape → apply corrections → infer status → score → publish. |
| `jdcvo/store.py` | `LocalStore` (JSON files) and `SheetsStore` (Google Sheets), same interface. |
| `jdcvo/playthru.py` | HTML scraper for golfplaythru.com. |
| `jdcvo/state.py` | Round lifecycle: `not_started` / `live` / `complete`, override, lock. |
| `jdcvo/config.py` | Reads `event_2026.json` + the roster and course files. |
| `event_2026.json` | Rounds, courses, scraping URLs, sheet key, handicap rule. |
| `players_2026.json` | Roster: teams, handicaps, partners and foursomes per round. |
| `courses.json` | Par and stroke index per hole. **The only source** — PlayThru publishes neither. |
| `tools/` | Test, simulation and reset scripts. See §7 and §8. |
| `data_2026/` | Working output, gitignored. Regenerated; safe to delete. |

### Documentation

| File | Audience |
|---|---|
| `SCORING.md` | The full rules with derivations, history and code locations. |
| `RULES.html` + the `.docx` beside it | Standalone rules for a committee member or a rules official. Derived from the code, not from `SCORING.md`. |
| `PLAYER_GUIDE.html` + `.docx` | Two pages for players. What to post, and what not to do. |
| `README_2026.md` | Setup, local running, disaster recovery. |
| `HANDOFF.md` | This file. |

The `.docx` files are generated with `python3 tools/html_to_docx.py in.html out.docx`.
macOS `textutil` silently flattens HTML tables, which is why that script exists.

---

## 5. How data flows

```
PlayThru pages  ──scrape──┐
                          ├──► score ──► leaderboards ──► Google Sheet + app
Google Sheet tabs ────────┘
(typed in by the admin)
```

Manual tabs in the Sheet, all written and read by the app:

`Extras`, `Corrections`, `Match Play`, `Adjustments`, `Round Status`,
`Sunday Handicaps`.

Three things about this are load-bearing:

1. **The Sheet wins for manual data, PlayThru wins for scores.** Editing a
   leaderboard tab by hand is pointless — it's overwritten every cycle. Editing
   a manual tab by hand works and is respected.

2. **Corrections are a separate layer,** re-applied over the scraped card every
   cycle. That is what makes them reversible: delete the correction and the
   original score returns. Never write corrected values into `Raw Scores` — that
   bug existed once and made corrections permanent on locked rounds.

3. **Locking a round stops scraping it** and scores it from the saved cards
   instead. Reversible at any time. It is also how simulation works (§8).

---

## 6. Making and pushing a change

The whole loop:

```
# 1. edit
# 2. prove nothing broke — both of these, every time
python3 tools/run_2025_tests.py          # 13 tests, no pytest needed
python3 tools/compare_scoring.py         # app vs an independent implementation

# 3. commit specific paths (never `git add -A`, see §3)
git add app_2026.py jdcvo/scoring.py
git commit -m "..."
git push origin main
```

Pushing to `main` triggers a Streamlit redeploy, live in a minute or two.

### The redeploy trap

`st.cache_resource` objects can **outlive a redeploy**. A deployed app has been
seen holding a `SheetsStore` built from the previous deploy's class, then
crashing with `AttributeError` when new code called a method that object didn't
have.

`data_fingerprint()` in `app_2026.py` mitigates this: it keys the cached config,
Sheets client and results on the mtime and size of the config, roster, courses
and `store.py`, so changing any of them forces a rebuild. It is not a total
guarantee.

**If the app throws anything odd immediately after a deploy, reboot it** from
Manage app in the Streamlit Cloud console. That is the single most effective fix
in this project and it costs nothing.

### If the repo lives on a network share

The Mac mini reaches the project over SMB from the laptop, where git's lock
files are unreliable — `git add` and `git commit` intermittently fail on
`.git/index.lock`. Commands there are wrapped in a retry loop. **On the laptop
itself the files are local and no retry is needed.** Check with `pwd`: a path
under `/Volumes/` is the share, anything else is local.

---

## 7. Testing

| Command | What it proves |
|---|---|
| `python3 tools/run_2025_tests.py` | The engine still reproduces the real 2025 results exactly, plus 6 tiebreak unit tests. **Run after any scoring change.** |
| `python3 tools/compare_scoring.py` | Every player's round points *and* final total agree with `tools/independent_score.py`. |
| `python3 -m py_compile app_2026.py jdcvo/*.py` | Cheap syntax check. |

`tools/independent_score.py` is a second scoring implementation written from
`SCORING.md` alone. Keeping it independent is the point: **if you change a rule
in `jdcvo/scoring.py`, change it there too, separately, from the documentation.**
Copying one into the other destroys its value as a check.

Its limits are real. It once agreed with the app on every player while the two
disagreed about the net double bogey rule, because the difference only showed
when both partners of a pair blew up on the same hole. Agreement is evidence,
not proof.

---

## 8. Event-day playbook

### Scores aren't updating

Click **Pull latest scores now** on the admin page. That forces a full cycle and
a publish, bypassing the 120-second cache and the 60-second publish throttle.
Then check the round isn't **locked** — a locked round is not scraped at all.

### The app crashed

Reboot it from Manage app in the Streamlit console. If it crashes again, read the
traceback in the logs there. A `gspread.exceptions.APIError` usually means the
Google API quota was hit; wait a minute and reboot.

### "Rate limit" / quota errors while typing entries

The Sheets API allows 60 requests a minute. Publishing is throttled to once a
minute (`PUBLISH_MIN_INTERVAL`) and raw-score reads are deduplicated per cycle
precisely to stay inside that. If it still trips, slow down — let the page
finish redrawing between saves — and don't hammer **Pull latest scores now**.

### An entry saved with the wrong number

Every admin section has a row editor underneath it. Fix the number in place, or
delete the row. Match play has a **Start Round 2 over** control, and Extras,
Corrections and Adjustments are all fully editable tables.

### A round says LIVE but everyone has finished

Somebody didn't post. Look at **Card check** on the Progress tab: it names who
is short and how far behind his foursome he is. Either get the score posted, or
fix it under **Fix a score**, or force the round closed with the status override
on the **Round status** tab.

### A player is scoring zero for a round

Almost certainly an identity mismatch — a name on PlayThru that doesn't match
the roster, a missing card, a misspelt team, or a Sunday pair naming the wrong
two men. **Card check** on the Progress tab names all of these explicitly. This
class of failure used to be completely silent.

### Sunday handicaps

Calculated on the **Sunday handicaps** admin tab once Rounds 1 and 3 are
complete, then frozen. They must be frozen: a later correction to a Round 1 card
must not move a number already announced on the first tee. Both rounds need a
complete card for every player first, because an unplayed hole counts as zero
and understates the figure.

Round 5 scores are posted **net** by the players, so the handicaps are
information for them, not an input to scoring.

---

## 9. Landmines

**`tools/simulation.py load` must never run during the real event.** It writes
fabricated cards into the Sheet and locks the rounds. It is visible if it
happens — locked rounds wear a LOCKED badge, which is why it was built on the
lock rather than a hidden flag — but it would overwrite real scores.

To reset everything for play: `python3 tools/simulation.py off`. That clears
extras, match play, corrections, adjustments, all raw scores, the frozen Sunday
handicaps and the local cached cards, and unlocks every round. Test scores must
be deleted from the PlayThru pages separately; unlocked rounds are scraped from
there, so anything left comes straight back.

**Round 2 can never complete on its own.** It has no cards. It reads as
finished once all five matches have points, and its status has to be closed by
hand.

**`courses.json` is the only source of par and stroke index.** PlayThru
publishes neither. It was verified hole by hole against the paper scorecards. A
wrong stroke index silently misallocates handicap strokes and corrupts every
tiebreak.

**Never commit secrets.** `.streamlit/secrets.toml` and `golf-outing-*.json` are
gitignored, and `.gitignore` ignores all JSON by default and then re-allows the
specific config files — so a new key file can't be committed by accident. The
repo is **public**. It has been audited and contains no credentials; keep it
that way.

**Python here is 3.9.** No `tomllib`, no `match`, no `X | Y` type syntax.
`tools/simulation.py` hand-parses TOML for that reason.

---

## 10. Talking to the owner

He is not a programmer and does not want to be. He has asked, repeatedly and
with feeling, for plain answers rather than tutorials, and he does not want to
be quizzed or asked to choose between options he has no basis to judge. Tell him
what happened and what it means for the event.

He does want to be told the truth about problems, including ones he can't see —
his standing instruction is that hiding a problem to save face is the worst
thing an assistant can do here. If something is broken or uncertain, say so
plainly, say how sure you are, and say what you checked.

Two hard rules from him: **do not change code he didn't ask to have changed**,
and **do not touch git config**.
