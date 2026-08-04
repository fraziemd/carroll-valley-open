# JDCVO Scoring Rules

The complete rules of the "Jimmy D" Carroll Valley Open as actually implemented
in `jdcvo/scoring.py`. This is the reference for *what the numbers mean*;
`README_2026.md` covers how the software is built, run and recovered.

Everything here was verified against the real 2024 and 2025 scorecards. Where a
rule is a deliberate house convention rather than standard golf, it says so.

---

## 1. The event

Five rounds over three days. Each round has its own format and its own way of
turning scores into points. Points accumulate all weekend into one individual
leaderboard and one team leaderboard.

| Round | When | Format | Scoring style | Scores come from |
|---|---|---|---|---|
| 1 | Friday morning | Best ball, individual cards | `best_ball_individual` | Scraped |
| 2 | Friday afternoon | Skins / match play within the foursome | `match_play` | Entered by hand |
| 3 | Saturday morning | Best ball, individual cards | `best_ball_individual` | Scraped |
| 4 | Saturday afternoon | Four-man team scramble | `team_scramble` | Scraped |
| 5 | Sunday morning | Two-man scramble | `two_man_scramble` | Scraped |

Five teams of four. Courses and pairings are defined per year in the event
config (`event_2026.json`) and the roster file (`players_2026.json`).

---

## 2. Handicaps

Two entirely separate handicap calculations exist. Don't confuse them.

- The **event handicap** (§2.1) is set once a year and used for net scoring in
  rounds 1 and 3.
- The **Sunday pair handicap** (§2.5) is computed *during* the event from
  rounds 1 and 3, and used only for round 5.

### 2.1 The yearly event handicap

Each player's handicap for the year comes from how he actually played the two
best-ball rounds (rounds 1 and 3) over the **previous two years** — four rounds
in total.

The window is **exactly two years and nothing more**. It rolls: 2026 uses 2024
and 2025; 2027 will use 2025 and 2026 and will *drop* 2024. History does not
accumulate beyond two years.

For every one of those rounds:

1. Cap each hole's gross score at **net double bogey**: `par + 2 + strokes`,
   where `strokes` is the handicap strokes he received on that hole *that year*,
   under full allocation (§2.3). This stops one blow-up hole from inflating a
   handicap.
2. Total the capped card.
3. Subtract course par. Call this the round's **relative-to-par** figure.

Then average those figures, and **normalize the field so the best player plays
off scratch**:

```
raw      = mean(relative-to-par of all available rounds)
offset   = min(raw) over the players in THIS year's field
handicap = floor( raw − offset )
```

The offset is **not a constant** — it is recomputed every year, and it is the
lowest raw figure among **this year's participants**. Players who aren't in the
field don't set the scale no matter how low they are, and newcomers without
history can't set it either since they have no raw figure. For 2026 the offset is
7.50, Joe M's figure.

Every handicap is therefore strokes *relative to the best player in the field*,
not an absolute number, and the low man is 0 by definition rather than by
coincidence.

`floor` rounds down, so a player is never given more strokes than he has earned.

**Worked example — Ben, 2026.**

| Year | Course | Relative to par |
|---|---|---|
| 2024 | Gettysburg National | +10 |
| 2024 | Carroll Valley | +15 |
| 2025 | Gettysburg National | +9 |
| 2025 | Carroll Valley | +16 |

Ben's raw figure is 12.50. The 2026 field offset is 7.50, so
`floor(12.50 − 7.50)` = **5**. Ben plays off 5 — five strokes worse than the
best player in the field.

**Players with less than two years of history** use the mean of whatever rounds
exist. Joe M had only 2025 (+11 and +4, raw 7.50). His raw figure *is* the field
minimum, so `floor(7.50 − 7.50)` = **0** — he sets the scale.

**Brand-new players cannot be computed** and must be assigned by hand. For 2026
that is Chris G, Patrick, Dave and Jim F.

A worked caution on the population rule: John K's raw figure is also 7.50 but he
isn't playing in 2026, so he is irrelevant to the offset — it coincidentally
matches Joe M's this year. Had John K been the sole low man, the offset would
still have been Joe M's 7.50 and every handicap in the field would differ from a
naive calculation over everyone with history.

This formula reproduces all 22 players in `handicaps_2026_detail.csv` exactly,
and 15 of the 16 rostered players who have prior rounds. The exception is Pete,
who missed a 2024 round; his handicap was fudged by double-counting and is not
formula-derived.

### 2.2 Why the numbers jumped in 2026

Worth recording so it isn't mistaken for an error. Across the 15 returning men
with a handicap in both years, the average went from 8.40 to 14.07 — while Joe M
stayed at 0.

**The field gained strokes; Joe didn't.** That's the whole story, and it falls
straight out of normalizing to the best player.

Joe played 2025 off 0 and was far better than 0 relative to everyone else's net
scoring — he won the event outright. Measured over 2024–25, the field sits an
average of **14.28 strokes behind him**, but 2025's handicaps had them only
**8.40** behind. The spacing was too tight by about six strokes.

Because Joe *defines* zero, he can't be moved down to absorb that — he's already
the floor. The entire correction therefore lands on everyone else. His 0 was
right as a label and wrong as a scale, and the fix can only appear as the field
gaining strokes. Spread widened from 20 to 30 for the same reason.

(Consistent with this: 2025's handicaps can't be reproduced by this rule from
the 2024 cards — 3 of 15 match — and Joe M has no 2024 rounds at all, so his
2025 zero was almost certainly a label rather than a computed figure.)

### 2.3 Stroke allocation — how many strokes on which holes

Standard **full allocation**:

```
strokes on a hole = floor(H / 18) + (1 if hole's stroke index ≤ H mod 18 else 0)
```

A player receives exactly `H` strokes in total. A 30-handicap gets **two**
strokes on the twelve hardest holes and one on the rest. A 12-handicap gets one
stroke on the twelve hardest holes and none on the other six.

In `courses.json`, each hole's `handicap` field is its **stroke index**
(1 = hardest), *not* a player handicap. Easy to misread.

> **History — corrected in 2026.** The original notebook used
> `1 if stroke_index ≤ H else 0`, capping everyone at one stroke per hole and
> therefore at 18 strokes total. Any handicap above 18 silently played as an 18.
> This was invisible in 2024 (nobody above 18) and nearly invisible in 2025
> (only Trock, at 20, losing 2 strokes). With eight players above 18 in 2026 and
> Trock at 30 losing 12, it was untenable and was fixed.
>
> The fix changes history. Re-scoring 2025 under full allocation moves the team
> title **from White to Green**: White had won 125.30 to 124.90, but the
> correction costs White 2.00 and gives Green nothing, so Green takes it 124.90
> to 123.30. (Blue gains 5.20 and Black loses 2.00, neither changing the top
> two.) Joe M still wins individually at 38.05 either way. The 2025 fixtures in
> this repo reflect the corrected rule, so they no longer match what was
> published to the 2025 sheet at the time.
>
> **Full allocation is the rule everywhere, including the §2.1 derivation.** It
> happens to change no 2026 handicap: there the stroke count only sets the
> net-double-bogey cap, which bites only on blow-up holes. Trock's four-round mean
> shifts 37.75 → 38.00, the offset stays 7.50, and all 22 handicaps come out
> identical. `handicaps_2026_detail.csv` was produced with the old capped rule
> and is therefore still valid, but anything computed from here on should use full
> allocation.
>
> The legacy rule survives only as `"handicap_allocation": "capped"`, purely for
> reproducing what the 2025 sheet published at the time. Don't use it for
> anything else.

### 2.4 Net score and the net double bogey cap

```
net = gross − strokes,  capped at  par + 2 + strokes
```

The cap applies to both the handicap derivation (§2.1) and to net scoring in
rounds 1 and 3.

### 2.5 The Sunday pair handicap

Computed after rounds 1 and 3 are complete, for round 5 only.

**Step one — each man's individual Sunday figure.** Exactly the §2.1 method but
using *this year's* rounds 1 and 3 only: cap each hole at net double bogey,
total, subtract par, average the two rounds, truncate toward zero. **No field
normalization here** — these stay as raw strokes over par, which is why they run
much higher than event handicaps (Trock's was 35).

**Step two — combine the partners.**

```
pair handicap = int( (lower × 2 + int(higher ÷ 2)) ÷ 3 )
```

where `lower` and `higher` are the two partners' step-one figures. Both `int()`
calls truncate downward.

**How it weights the partners.** Expanded, the formula is:

```
⅔ × lower  +  ⅙ × higher
```

The better player counts exactly **four times** the worse player. Two
consequences follow, both deliberate:

- The weights sum to ⅚, not 1, so there is a built-in haircut. Two identical
  24s get a pair handicap of 20, not 24.
- The worse partner contributes nothing at all until his figure exceeds
  **double** his partner's. Below that he actively pulls the pair number down.
  (`pair − lower = ⅙×higher − ⅓×lower`, positive only when `higher > 2 × lower`.)

**Real 2025 examples:**

| Pair | Better | Worse | Pair handicap | Simple average would be |
|---|---|---|---|---|
| Tim & Trock | 16 | 35 | 16 | 25 |
| Ben & Harvey | 12 | 33 | 13 | 22 |
| Tbone & Pete | 19 | 20 | 16 | 19 |
| Oaks & Andrew | 24 | 25 | 20 | 24 |

Trock played to 35 and added nothing — the pair got 16, exactly Tim's own
number. This is aggressively anti-sandbagging: pairing a good player with a weak
one yields close to the good player's allowance and no more. It is also very
flat — a partner can vary 28 strokes and move the pair number only 5. That is
considered appropriate for a scramble, where you play the better ball and the
weaker partner genuinely matters less.

**How it is applied:** as a reference number only. Players subtract it
themselves and enter **net** scores. The engine applies no handicap in round 5
(§4.4).

---

## 3. Points: rounds 1 and 3 (best ball individual)

Three independent point sources. A player's round total is the sum of all three.

### 3.1 Individual hole points

Awarded on **net** score against par, hole by hole:

| Net result | Points |
|---|---|
| 3 or more under par | 3.5 |
| 2 under (eagle) | 1.5 |
| 1 under (birdie) | 0.8 |
| Par | 0.4 |
| Over par | 0 |
| **Gross** hole-in-one | +8.0 bonus |

The hole-in-one bonus is on the *gross* score of 1 and stacks with the net
award.

> Preserved quirk: the individual hole-point total is rounded to one decimal
> place *before* pair and survival points are added.

### 3.2 Pair position points

For each pair, on every hole both partners played, take the **better of the two
net scores** and compare to par. Sum across holes to get the pair's
relative-to-par. Rank all pairs, lowest first:

| Position | 1st | 2nd | 3rd | 4th | 5th | 6th | 7th | 8th | 9th | 10th | 11th+ |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Points | 9 | 8 | 7 | 6 | 5 | 4 | 3 | 2 | 1 | 0 | 0 |

**Both partners receive the pair's points.**

### 3.3 Survival points

Walking from hole 1, a foursome "survives" a hole if **at least one** of its
four members made net par or better. Counting stops at the first hole where
nobody did. The foursome surviving the most consecutive holes gets **2.0 points
to each member**.

Ties go to the best *total foursome net score* on the number 1 stroke-index
hole, then the number 2, and so on. If all 18 are exhausted, the 2.0 points are
split between the tied foursomes.

> The 2025 notebook had a key-type bug that silently disabled this tiebreaker
> and always split. It is fixed here. 2025 results are unaffected because both
> rounds had outright survival winners.

---

## 4. Points: the other rounds

### 4.1 Round 2 — skins / match play

Points are agreed at the table and **typed in by hand** after the round
(Admin → Match play). Nothing is scraped and no handicap is applied by the
software. Each player receives his pair's points; the 2025 range was 0–5.

Because there is nothing to scrape, this round can never auto-detect as
complete — it shows `live` as soon as any points are entered and must be closed
manually.

### 4.2 Round 4 — four-man team scramble

One card per team. Points are awarded **three times independently** — Front 9,
Back 9, and Overall — each on relative-to-par:

| Position | 1st | 2nd | 3rd | 4th | 5th |
|---|---|---|---|---|---|
| Points | 3 | 2 | 1 | 0.5 | 0 |

A team's round total is the sum of its three category results (maximum 9).
**Every player on the team receives the full team total.**

### 4.3 Round 5 — two-man scramble

Pairs are ranked by overall relative-to-par of the scores **as entered**:

```
points = number of pairs − rank
```

With ten pairs that is 9 for 1st down to 0 for 10th. Both players receive the
pair's points.

**No handicap is applied by the engine.** Players subtract their pair handicap
(§2.5) themselves and enter net scores. If that convention ever changes, the
scoring function must change with it.

---

## 5. Extras and adjustments

Two manual buckets, both added straight onto a player's total:

- **Extras** — chip-ins, greenies, closest-to-pin and similar side awards,
  entered per round.
- **Adjustments** — a manual override for anything else (corrections,
  penalties, one-off rulings). Requires a note.

Both accumulate into a single `extras` bucket on the leaderboard.

Separately, **corrections** fix a wrong hole score on a scorecard. These are
stored apart from the scraped data and re-applied after every scrape, so a
refresh can never wipe them.

---

## 6. Tiebreakers

Every positional tie in every round uses the same routine. Compare the tied
entities hole by hole, **hardest hole first** by stroke index. Whoever is
outright best on a hole takes the position. Where several share the best score,
**everyone else is eliminated** and the comparison continues among those
leaders alone on the next hole — losing the hardest hole puts you out of
contention for the position, and you cannot win it back later.

Only holes that *all* the entities still in contention have played are
compared.

If the survivors are never separated, they share the positions they hold and
the points for those positions are pooled and split evenly. Everyone eliminated
along the way is then ranked for the positions **below** them, by the same
routine. Losing the hardest hole costs you the place, not every point in the
group.

> Fixed in 2026: entities eliminated during the narrowing were dropped from the
> result and received nothing at all, so part of the points on offer went
> undistributed. Four entities tied for positions worth 9+8+7+6 handed out only
> 17 of the 30. It needed the surviving leaders to be identical on every
> commonly-played hole, so it was unlikely over a full round but quite possible
> mid-round, when few holes are shared. `test_tiebreak.py` covers it.

---

## 7. Round status

A round is:

- **not started** — no non-zero scores yet. Awards no points, so future rounds
  can't create phantom ties.
- **live** — some scores in.
- **complete** — every card full (all 18 holes non-zero for every entrant).

Statuses are inferred automatically, but can be overridden and **locked** from
the admin side. Locking stops re-scraping, so late edits on PlayThru can't
silently change a finalized round.

A round with a player who forgot to post will sit at `live` indefinitely — that
is intended, and is closed manually.

---

## 8. Where each rule lives in the code

| Rule | Location |
|---|---|
| Stroke allocation | `jdcvo/scoring.py` → `handicap_strokes_for_hole` |
| Net score / double bogey cap | `jdcvo/scoring.py` → `net_hole_score` |
| Rounds 1 & 3 | `jdcvo/scoring.py` → `calculate_best_ball_individual` |
| Round 2 | `jdcvo/scoring.py` → `calculate_match_play` |
| Round 4 | `jdcvo/scoring.py` → `calculate_team_scramble` |
| Round 5 | `jdcvo/scoring.py` → `calculate_two_man_scramble` |
| Sunday pair handicaps | `jdcvo/scoring.py` → `calculate_round_5_handicaps` |
| Tiebreakers | `jdcvo/scoring.py` → `break_tie`, `_rank_tied_group` |
| Round status | `jdcvo/state.py` |
| Point values / constants | top of `jdcvo/scoring.py` |
| Regression tests | `jdcvo/test_2025_validation.py` |

Point values are named constants at the top of `scoring.py`
(`BEST_BALL_HOLE_POINTS`, `HOLE_IN_ONE_BONUS`, `PAIR_POSITION_POINTS`,
`SURVIVAL_POINTS`). Change them there, not inline.

**After any change to scoring, re-run the regression suite:**

```
python3 tools/run_2025_tests.py        # no pytest needed
python3 -m jdcvo.test_2025_validation  # equivalent
```

It recomputes all five 2025 rounds plus the Sunday handicaps from raw hole
scores and asserts exact agreement with the saved fixtures.

---

## 9. Open items

- **The yearly handicap formula isn't implemented in this codebase.** The rule in
  §2.1 is confirmed and reproduces all 22 players in
  `handicaps_2026_detail.csv` exactly, along with every underlying round total
  from the raw 2024 and 2025 scorecards — but handicaps are still computed
  outside and typed into the roster by hand. Worth porting. The two parts most
  easily got wrong by hand are the offset's population (this year's field only,
  §2.1) and the rolling two-year window (drop the third year, don't accumulate).
- ~~**The Sunday pair handicap has no UI.**~~ Done. Admin → *Sunday handicaps*
  calculates and previews, then freezes on save. Saved figures go to
  `round_5_handicaps.json` and the *Sunday Handicaps* tab of the sheet, and the
  pair strokes appear on the public Round 5 page. Frozen deliberately: the pairs
  are told their strokes on the first tee, so a later Round 1 or 3 correction
  must not move them. Recalculating is an explicit act and warns before
  overwriting a locked figure.
- **2024 scores live only in a spreadsheet**
  (`Carroll Valley Aug 23 2024 (2).xlsx`, sheet `Golf`). Section one is
  Gettysburg National, section two is Carroll Valley; par and stroke index in
  both match `courses.json` exactly. Note the date cells wrongly say 2022, the
  yardage row in section two was copy-pasted from Gettysburg, and Pete's
  Carroll Valley card is blank.
