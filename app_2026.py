"""JDCVO 2026 tournament app.

Public side: live leaderboards with drill-downs into players, teams, and
rounds showing exactly where every point came from.

Admin side (password-protected, in the sidebar): pull latest scores, fix hole
scores, enter extras / match play / adjustments, and control each
round's status (auto-inferred, with manual override and lock).

Data flow:
- The app itself runs the scrape+score pipeline (cached, ~2 min TTL), so the
  public leaderboard stays fresh with no laptop and no external scheduler.
- Manual inputs live in Google Sheets when configured (durable, shared,
  human-readable), else local JSON under the event data dir.
- The GitHub Actions cron additionally publishes results to Sheets as an
  independent backup path.

Run locally:  streamlit run app_2026.py
Secrets (Streamlit Cloud or .streamlit/secrets.toml):
  admin_password = "..."
  [gcp_service_account]  # service-account key, only if a sheet is configured
"""

import streamlit as st

st.set_page_config(
    page_title="The 'Jimmy D' Carroll Valley Open",
    page_icon="jdcvo.png",
    layout="wide",
)

import base64
import json
import os
from datetime import datetime

import pandas as pd
import streamlit.components.v1 as components

from jdcvo import pipeline, scoring, state
from jdcvo.config import EventConfig
from jdcvo.store import LocalStore

# Set JDCVO_CONFIG to run against a different event config (e.g. a 2026 draft
# while the deployed app stays on the current one). Unset everywhere but a dev
# shell, so the deployment is always the default below.
CONFIG_PATH = os.environ.get('JDCVO_CONFIG', 'event_2026.json')
REFRESH_SECONDS = 120

TEAM_COLORS = {
    'Red': '#e74c3c', 'Blue': '#3498db', 'Green': '#2ecc71',
    'Yellow': '#f1c40f', 'Purple': '#9b59b6', 'Orange': '#e67e22',
    'Pink': '#e91e63', 'Teal': '#1abc9c', 'Brown': '#8b4513',
    'Gray': '#95a5a6', 'Black': '#2d2d2d', 'White': '#ecf0f1',
}

ROUND_STATUS_LABELS = {
    state.NOT_STARTED: 'Not started',
    state.LIVE: 'LIVE',
    state.COMPLETE: 'Complete',
}

# Extras categories: stored key -> (display label, usual points).
# The key is what goes in the sheet and stays stable; only the label is shown.
# Storing the label instead would mean old rows fragmenting into separate
# columns in the summary table the day anyone reworded one.
EXTRA_CATEGORIES = {
    'chip_in': ('Chip-in', 2.0),
    'longest_drive': ('Longest drive', 1.0),
    'closest_to_pin': ('Closest to the pin', 1.0),
    'hole_in_one': ('Hole-in-one', 8.0),
    'adjustment': ('Adjustment', 0.0),
}


def extra_category_label(key):
    """Readable name for a stored extras category.

    Unknown keys (hand-entered in the sheet, or from an older season) are
    prettified rather than dropped, so nothing ever renders as raw snake_case.
    """
    key = str(key or '')
    if key in EXTRA_CATEGORIES:
        return EXTRA_CATEGORIES[key][0]
    return key.replace('_', ' ').strip().capitalize() or '(none)'

# Optional retro 80s/90s arcade skin, toggled from the sidebar. Pixel font on
# headings/buttons; a larger, legible retro font ("VT323") on body and tables
# so the leaderboard stays readable. Fully reversible (off = normal theme).
#
# Palette: classic arcade-cabinet "attract mode" screen (black background,
# amber/orange hero color, white body text, a muted red as the one secondary
# accent/shadow color) rather than a synthwave/vaporwave look - matched to
# real reference screens. Kept deliberately subdued (not neon-hot) per
# feedback on the first pass.
ARCADE_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Press+Start+2P&family=VT323&display=swap');

/* rem units (used throughout Streamlit's own CSS, and ours) are always
   measured against the page ROOT, not against .stApp - so the base-size bump
   has to happen here to actually scale everything, including Streamlit's own
   text, together. */
html {
    font-size: 21px !important;
}
.stApp {
    background:
        repeating-linear-gradient(0deg, rgba(230,57,70,0.045) 0px, rgba(230,57,70,0.045) 1px, transparent 1px, transparent 48px),
        repeating-linear-gradient(90deg, rgba(230,57,70,0.045) 0px, rgba(230,57,70,0.045) 1px, transparent 1px, transparent 48px),
        #060606 !important;
    color: #f2f2f2 !important;
    font-family: 'VT323', monospace !important;
}
.stApp p, .stApp label, .stApp li,
[data-testid="stCaptionContainer"], [data-testid="stMarkdownContainer"] {
    font-family: 'VT323', monospace !important;
    letter-spacing: 0.3px;
}
/* Icons (expander arrows, checkboxes, etc.) are literal ligature text like
   "keyboard_arrow_right" rendered via a special icon font - excluded here so
   they don't turn into garbled overlapping text.
   Heading-internal spans (h1-h4) are excluded too: Streamlit Cloud's build
   wraps heading text in an inner <span> (locally the text is a direct child
   of the h tag), and without the exclusion this rule hijacked every deployed
   heading's text into VT323 - thin and small - while the h1-h4 rule below
   only got to style the color/shadow. That was the "headings look tiny/wrong
   on the live site but fine locally" bug. */
.stApp span:not([aria-hidden="true"]):not([data-testid="stIconMaterial"]):not(.lb-name):not(.lb-points):not(h1 span):not(h2 span):not(h3 span):not(h4 span) {
    font-family: 'VT323', monospace !important;
    letter-spacing: 0.3px;
}
h1, h2, h3, h4 {
    font-family: 'Press Start 2P', cursive !important;
    color: #ffa629 !important;
    text-shadow:
        3px 3px 0 #e63946,
        1px 1px 0 #000, -1px -1px 0 #000, 1px -1px 0 #000, -1px 1px 0 #000,
        0 0 12px rgba(255,166,41,0.45);
    line-height: 1.55 !important;
}
h1 { font-size: 1.35rem !important; }
h2 { font-size: 1.1rem !important; }
h3 { font-size: 0.9rem !important; }
/* Explicit (not just excluded-from-VT323 above): heading text on Streamlit
   Cloud lives in an inner span, and it must render in the heading font. */
h1 span, h2 span, h3 span, h4 span {
    font-family: 'Press Start 2P', cursive !important;
    letter-spacing: normal !important;
}

/* Leaderboard name/points: classic arcade high-score tables don't jump
   between wildly different type sizes/fonts the way the default theme's
   small "card" text does, so these pick up the same pixel font as the
   headings (just smaller, and without the full glow/outline treatment,
   so a whole list of them doesn't turn into a wall of neon). The small
   sub-labels (team/roster line, "points" caption) stay close to their
   original size on purpose - they're meant to read as secondary detail. */
.lb-name {
    font-family: 'Press Start 2P', cursive !important;
    font-size: 0.85rem !important;
    line-height: 1.5 !important;
    color: #f2f2f2 !important;
}
.lb-points {
    font-family: 'Press Start 2P', cursive !important;
    font-size: 1.05rem !important;
    line-height: 1.5 !important;
    color: #ffa629 !important;
    text-shadow: 2px 2px 0 #e63946 !important;
}
.lb-sub {
    font-size: 0.78rem !important;
}
.lb-points-sub {
    font-size: 0.5rem !important;
}

.stButton > button, .stFormSubmitButton > button, [data-testid="stBaseButton-secondary"] {
    font-family: 'Press Start 2P', cursive !important;
    font-size: 0.68rem !important;
    color: #ffa629 !important;
    background: #000 !important;
    border: 3px solid #ffa629 !important;
    border-radius: 0 !important;
    box-shadow: 3px 3px 0 #e63946 !important;
    transition: none !important;
}
.stButton > button:hover, .stFormSubmitButton > button:hover {
    color: #000 !important;
    background: #ffa629 !important;
    box-shadow: 3px 3px 0 #f2f2f2 !important;
}

section[data-testid="stSidebar"] {
    background: #000 !important;
    border-right: 3px solid #ffa629 !important;
}

[data-testid="stDataFrame"], [data-testid="stTable"] {
    border: 2px solid #e63946 !important;
    box-shadow: 0 0 8px rgba(230,57,70,0.3);
}
[data-testid="stDataFrame"] * {
    font-family: 'VT323', monospace !important;
}

hr { border-color: #ffa629 !important; box-shadow: 0 0 8px #ffa629; }

/* CRT scanline overlay (clicks pass through) */
.stApp::before {
    content: "";
    position: fixed;
    inset: 0;
    pointer-events: none;
    z-index: 99999;
    background: repeating-linear-gradient(
        to bottom,
        rgba(0,0,0,0) 0px, rgba(0,0,0,0) 2px,
        rgba(0,0,0,0.16) 3px, rgba(0,0,0,0.16) 4px);
    mix-blend-mode: multiply;
}
</style>
"""

# Purchased/evaluation sound-effect samples (see sfx_preview/), embedded as
# base64 data URIs directly in the iframe's HTML so no separate static file
# server is needed. NOTE: these are still watermarked preview clips from
# AudioJungle-style packs - fine for testing/prototyping the wiring, but
# should be swapped for the purchased, unwatermarked files before real event
# use.
def _audio_data_uri(rel_path):
    try:
        with open(rel_path, 'rb') as f:
            data = f.read()
    except FileNotFoundError:
        return ''
    return 'data:audio/mpeg;base64,' + base64.b64encode(data).decode('ascii')


# Each interaction TYPE gets its own dedicated clip (never shared with a
# different type of interaction, even within the same sample "category"),
# so the sound itself tells you what kind of thing just happened.
_ARCADE_SFX = {
    'radio': _audio_data_uri(os.path.join('sfx_preview', 'up_2.mp3')),
    'dropdown_open': _audio_data_uri(os.path.join('sfx_preview', 'drink_2.mp3')),
    'dropdown_select': _audio_data_uri(os.path.join('sfx_preview', 'power_up_2.mp3')),
    'expander': _audio_data_uri(os.path.join('sfx_preview', 'drink_1.mp3')),
    'button': _audio_data_uri(os.path.join('sfx_preview', 'coins_2.mp3')),
    'admin_submit': _audio_data_uri(os.path.join('sfx_preview', 'coins_1.mp3')),
    'round_live': _audio_data_uri(os.path.join('sfx_preview', 'power_up_1.mp3')),
    'leader_change': _audio_data_uri(os.path.join('sfx_preview', 'victory_2.mp3')),
}

# Fixed historical high score shown in the HI-SCORE display (2025 champion's
# final total), per explicit user confirmation - not derived from any data
# file, and not meant to update automatically as future seasons complete.
ARCADE_HI_SCORE = 38

# Sound effects for arcade mode, rendered via components.html (a real
# <iframe>) rather than st.markdown, because browsers never execute <script>
# tags injected through innerHTML/dangerouslySetInnerHTML - only a real
# document (iframe) will run them.
#
# There is deliberately NO background music. It was removed on request; only
# the per-action effects remain. Each is a one-off HTML5 Audio clip fired
# straight from a click - the click is itself the user gesture browsers
# require before audio may play, so nothing needs an AudioContext, an
# autoplay workaround, or the mobile <audio> fallback the old looping track
# needed. The panel is just a volume control for these effects.
#
# As long as this exact HTML string keeps rendering at the same spot on every
# Streamlit rerun, Streamlit reuses the same underlying iframe instead of
# recreating it, so its JS state and parent-page listeners survive reruns
# instead of being torn down and rebuilt every refresh.
#
# The iframe triggers effects for two kinds of moments:
#   1. Clicks on real Streamlit widgets, which live in the PARENT page, not
#      this iframe. Since components.html's iframe uses srcdoc without a
#      `sandbox` attribute, it's same-origin with the parent, so a listener
#      here can reach across via window.parent.document. That's simpler and
#      more robust than trying to inject anything into Streamlit's own React
#      app directly.
#   2. Score-driven events (round going live, leader changing), detected by
#      polling a small hidden marker div that main() re-renders each rerun
#      with the current leader/live-round state. Polling (rather than baking
#      the event directly into this HTML) is what keeps this iframe's content
#      byte-identical across reruns, so it isn't recreated and its listeners
#      aren't lost.
ARCADE_AUDIO_HTML_TEMPLATE = """
<div id="arcade-audio-panel" style="
    font-family: 'Courier New', monospace;
    background: #000;
    border: 2px solid #ffa629;
    border-radius: 4px;
    padding: 6px 10px;
    display: flex;
    align-items: center;
    gap: 8px;
    color: #f2f2f2;
    font-size: 12px;
    box-sizing: border-box;
">
  <label style="display:flex; align-items:center; gap:6px; white-space:nowrap; flex:1; min-width:0;">
    SFX VOL
    <input id="arcade-vol" type="range" min="0" max="100" value="35" style="flex:1; min-width:0;">
  </label>
</div>
<script>
(function() {
  // Sample-based SFX (real audio clips, embedded as data URIs by Python).
  // Each interaction TYPE has its own dedicated clip - never shared with a
  // different type of interaction - so the sound itself is a consistent cue
  // for what just happened.
  const SFX_SOURCES = {
    radio: "__SFX_RADIO__",
    dropdown_open: "__SFX_DROPDOWN_OPEN__",
    dropdown_select: "__SFX_DROPDOWN_SELECT__",
    expander: "__SFX_EXPANDER__",
    button: "__SFX_BUTTON__",
    admin_submit: "__SFX_ADMIN_SUBMIT__",
    round_live: "__SFX_ROUND_LIVE__",
    leader_change: "__SFX_LEADER_CHANGE__",
  };
  const sfxTemplates = {};
  function playSample(name) {
    // One-off clips fired directly from a click, which is itself the user
    // gesture browsers require, so they can just always play.
    const src = SFX_SOURCES[name];
    if (!src) return;
    if (!sfxTemplates[name]) {
      sfxTemplates[name] = new Audio(src);
    }
    // Clone so rapid/overlapping triggers of the same sound don't cut each
    // other off.
    const el = sfxTemplates[name].cloneNode(true);
    el.volume = getVol();
    el.play().catch(function() { /* ignore - e.g. no gesture yet */ });
  }

  // Read fresh on every play, so moving the slider affects the next effect
  // without needing a change listener.
  function getVol() {
    const el = document.getElementById('arcade-vol');
    return el ? (parseInt(el.value, 10) / 100) : 0.35;
  }

  // --- Hooks into the parent Streamlit page (see module docstring above) ---
  function setupParentHooks() {
    let parentDoc;
    try {
      parentDoc = window.parent.document;
    } catch (e) {
      return; // not same-origin for some reason - fail silently
    }

    // Click sounds on real widgets in the parent page.
    parentDoc.addEventListener('click', function(e) {
      const target = e.target;
      // Any radio button, anywhere (sidebar page nav, in-page radio groups).
      if (target.closest('input[type="radio"], [role="radiogroup"] label')) {
        playSample('radio');
        return;
      }
      // Selecting an OPTION from an already-open dropdown's popover list.
      // BaseWeb (Streamlit's select widget) renders the open list with
      // role="listbox"/"option" - checked before the "open a dropdown" case
      // below since these are two different clicks on two different
      // elements, never the same click.
      if (target.closest('[role="option"], [role="listbox"] li')) {
        playSample('dropdown_select');
        return;
      }
      // Clicking a closed dropdown/select control to open it (player/round/
      // category pickers, etc). Matches both the BaseWeb select itself and
      // Streamlit's wrapper, so clicks landing on the label/icon/chevron
      // still count.
      if (target.closest('[data-baseweb="select"], [data-testid="stSelectbox"], '
                        + '[data-testid="stMultiSelect"]')) {
        playSample('dropdown_open');
        return;
      }
      // Expander headers (Round detail boxes, "Full scoring log", etc.) -
      // Streamlit renders these as native <summary> elements, both for
      // expanding AND collapsing.
      if (target.closest('[data-testid="stExpander"] summary')) {
        playSample('expander');
        return;
      }
      // Any other button click in the app (refresh, save, admin actions...)
      if (target.closest('button')) {
        playSample('button');
        return;
      }
    }, true);

    // 2. Score-driven moments: poll a hidden marker div (re-rendered by
    // main() every rerun) for the current leader, which rounds are LIVE,
    // and whether an admin-password attempt was just submitted (a text
    // input's Enter-key submission doesn't fire a normal bubbling 'click'
    // the way widgets above do, so it needs this same polling approach).
    let lastLeader = null;
    let lastLive = new Set();
    let lastAuthAttempt = null;
    let firstPoll = true;
    setInterval(function() {
      const marker = parentDoc.getElementById('arcade-state-marker');
      if (!marker) return;
      const leader = marker.getAttribute('data-leader') || '';
      const liveCsv = marker.getAttribute('data-live') || '';
      const authAttempt = marker.getAttribute('data-auth') || '0';
      const liveSet = new Set(liveCsv.split(',').filter(Boolean));

      if (!firstPoll) {
        if (leader && lastLeader !== null && leader !== lastLeader) {
          playSample('leader_change'); // the individual leader changed
        }
        liveSet.forEach(function(r) {
          if (!lastLive.has(r)) {
            playSample('round_live'); // a round just went LIVE
          }
        });
        if (lastAuthAttempt !== null && authAttempt !== lastAuthAttempt) {
          playSample('admin_submit'); // admin password submitted (right or wrong)
        }
      }
      firstPoll = false;
      lastLeader = leader;
      lastLive = liveSet;
      lastAuthAttempt = authAttempt;
    }, 1500);
  }

  setupParentHooks();
})();
</script>
"""


def build_arcade_audio_html():
    html = ARCADE_AUDIO_HTML_TEMPLATE
    for key, placeholder in [('radio', '__SFX_RADIO__'),
                              ('dropdown_open', '__SFX_DROPDOWN_OPEN__'),
                              ('dropdown_select', '__SFX_DROPDOWN_SELECT__'),
                              ('expander', '__SFX_EXPANDER__'),
                              ('button', '__SFX_BUTTON__'),
                              ('admin_submit', '__SFX_ADMIN_SUBMIT__'),
                              ('round_live', '__SFX_ROUND_LIVE__'),
                              ('leader_change', '__SFX_LEADER_CHANGE__')]:
        html = html.replace(placeholder, _ARCADE_SFX[key])
    return html


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------

def data_fingerprint():
    """Identity of the data AND code the caches depend on.

    st.cache_resource can outlive a redeploy. That bit us twice today: the
    app kept a SheetsStore built from a class that predated
    read_round_5_handicaps, and it kept a 2025 EventConfig after the roster
    switched to 2026. Keying every cache on this fingerprint means a change
    to the event config, the roster, the courses, or the store class itself
    forces a fresh object.
    """
    paths = [CONFIG_PATH, 'jdcvo/store.py']
    try:
        with open(CONFIG_PATH) as f:
            raw = json.load(f)
        paths += [raw.get('players_file'), raw.get('courses_file')]
    except (OSError, ValueError):
        pass

    parts = []
    for path in paths:
        if not path:
            continue
        try:
            s = os.stat(path)
            parts.append(f"{path}:{s.st_mtime_ns}:{s.st_size}")
        except OSError:
            parts.append(f"{path}:missing")
    return '|'.join(parts)


@st.cache_resource
def _load_config(fingerprint):
    return EventConfig(CONFIG_PATH)


def get_config():
    return _load_config(data_fingerprint())


@st.cache_resource
def _load_sheets(fingerprint):
    """SheetsStore when a sheet key and credentials are available, else None."""
    cfg = get_config()
    if not cfg.google_sheet_key:
        return None
    if 'gcp_service_account' not in st.secrets:
        return None
    from jdcvo.store import SheetsStore
    return SheetsStore(cfg.google_sheet_key, dict(st.secrets['gcp_service_account']))


def get_sheets():
    """SheetsStore for this fingerprint, rebuilt if a stale cached instance
    is missing methods the current code expects."""
    fp = data_fingerprint()
    store = _load_sheets(fp)
    if store is not None and not hasattr(store, 'read_round_5_handicaps'):
        _load_sheets.clear()
        store = _load_sheets(fp)
    return store


def get_writable_store():
    """Where admin edits go: the sheet if configured, else local JSON."""
    sheets = get_sheets()
    return sheets if sheets is not None else LocalStore(get_config().data_dir)


@st.cache_data(ttl=REFRESH_SECONDS, show_spinner="Updating scores...")
def _run_cycle(fingerprint):
    """Run one scrape+score cycle (publish only when Sheets is configured)."""
    logs = []
    results = pipeline.run_pipeline(CONFIG_PATH, scrape=True,
                                    log=logs.append, sheets=get_sheets())
    return results, logs


def get_results():
    return _run_cycle(data_fingerprint())


def refresh_now():
    _run_cycle.clear()
    # Also drop the memoized store/config so the next cycle cannot reuse an
    # object built against a previous deploy's class or roster.
    _load_sheets.clear()
    _load_config.clear()


# ---------------------------------------------------------------------------
# Shared UI helpers
# ---------------------------------------------------------------------------

def card(title_html, right_html, color):
    st.markdown(f"""
    <div style="background: linear-gradient(90deg, #2c3e50 0%, #34495e 100%);
                border-radius: 8px; padding: 8px 16px; margin: 6px 0;
                border-left: 5px solid {color};">
      <div style="display: flex; justify-content: space-between; align-items: center;">
        <div>{title_html}</div>
        <div style="text-align: right;">{right_html}</div>
      </div>
    </div>""", unsafe_allow_html=True)


def round_label(cfg, round_key):
    if round_key == 'extras':
        return 'Extras'
    return cfg.round_config(round_key).get('name', f'Round {round_key}')


def fmt_pts(x):
    # Round first: summing points yields values like 32.099999999999994, which
    # compare unequal to 32.1 and would otherwise render as "32.10" next to a
    # column of single-decimal totals.
    return f"{round(x, 2):g}"


# Hand-built 7x7 sparkle/star made of solid blocks - not a font character.
# ("Press Start 2P" doesn't include a star glyph, so text stars silently fall
# back to a smooth system font despite the styling; a real pixel-block shape
# sidesteps that entirely and looks genuinely 8-bit at any size.)
_PIXEL_STAR_CELLS = [
    (3, 0), (3, 1),
    (2, 2), (3, 2), (4, 2),
    (0, 3), (2, 3), (3, 3), (4, 3), (6, 3),
    (2, 4), (3, 4), (4, 4),
    (3, 5), (3, 6),
]
def _pixel_star_svg(color='#e8b400'):
    rects = "".join(
        f'<rect x="{x}" y="{y}" width="1" height="1" fill="{color}"/>'
        for x, y in _PIXEL_STAR_CELLS)
    return (
        '<svg viewBox="0 0 7 7" width="0.9em" height="0.9em" '
        'style="vertical-align:-0.1em;shape-rendering:crispEdges;" '
        f'xmlns="http://www.w3.org/2000/svg">{rects}</svg>')


def champion_marker(player):
    """Champion badge for the leaderboard: emoji normally, a hand-built pixel
    sparkle in arcade mode (real emoji are smooth color pictures that ignore
    any font, so they never look 8-bit no matter how they're styled)."""
    if not (player.get('current_champion') or player.get('past_champion')):
        return ""
    if st.session_state.get('arcade_mode'):
        return _pixel_star_svg()
    return "👑" if player.get('current_champion') else "🏆"


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------

def page_leaderboard(cfg, results):
    individual = results['leaderboard']['individual']
    team = results['leaderboard']['team']

    statuses = results['round_statuses']
    status_bits = []
    for n in cfg.round_numbers():
        s = statuses.get(str(n), state.NOT_STARTED)
        if s != state.NOT_STARTED:
            status_bits.append(f"R{n}: {ROUND_STATUS_LABELS[s]}")
    if status_bits:
        st.caption(" | ".join(status_bits) +
                   f" &nbsp;&nbsp;·&nbsp;&nbsp; updated {results['generated_at']}")

    col1, col2 = st.columns(2)

    with col1:
        st.header("Individual Leaderboard")
        standings = sorted(individual.items(), key=lambda x: -x[1]['total_points'])
        for rank, (pid, e) in enumerate(standings, 1):
            color = TEAM_COLORS.get(e['team'], '#3498db')
            # A player present in the results but not the roster means the two
            # disagree. Show the row without a champion marker rather than
            # taking the whole public leaderboard down over an icon.
            champion = (champion_marker(cfg.players[pid])
                        if pid in cfg.players else '')
            card(
                f"<span class='lb-name' style='font-size:1.2em;font-weight:bold;color:#ecf0f1;'>"
                f"#{rank} {e['name']} {champion}</span>",
                f"<span class='lb-points' style='font-size:1.5em;font-weight:bold;color:#f39c12;'>"
                f"{fmt_pts(e['total_points'])}</span>",
                color)

    with col2:
        st.header("Team Leaderboard")
        id_names = {pid: e['name'] for pid, e in individual.items()}
        for rank, (t, e) in enumerate(
                sorted(team.items(), key=lambda x: -x[1]['total_points']), 1):
            color = TEAM_COLORS.get(t, '#e74c3c')
            names = ', '.join(id_names.get(p, p) for p in e['players'])
            card(
                f"<span class='lb-name' style='font-size:1.2em;font-weight:bold;color:#ecf0f1;'>"
                f"#{rank} {t}</span><br>"
                f"<span class='lb-sub' style='color:#bdc3c7;font-size:0.8em;'>{names}</span>",
                f"<span class='lb-points' style='font-size:1.5em;font-weight:bold;color:#f39c12;'>"
                f"{fmt_pts(e['total_points'])}</span><br>"
                f"<span class='lb-points-sub' style='color:#bdc3c7;font-size:0.8em;'>points</span>",
                color)

    st.markdown("---")
    st.subheader("Round-by-round breakdown")
    rows = []
    for pid, e in sorted(individual.items(), key=lambda x: -x[1]['total_points']):
        row = {'Player': e['name'], 'Team': e['team']}
        for n in cfg.round_numbers():
            row[f'R{n}'] = e['round_scores'].get(str(n), 0)
        row['Extras'] = e['round_scores'].get('extras', 0)
        row['Total'] = e['total_points']
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)


def page_player(cfg, results):
    individual = results['leaderboard']['individual']
    names = sorted(e['name'] for e in individual.values())
    name = st.selectbox("Player", names)
    pid = cfg.name_to_id()[name]
    entry = individual[pid]

    st.title(f"{name} — {fmt_pts(entry['total_points'])} points")
    st.caption(f"Team {entry['team']}")

    for round_key, pts in sorted(entry['round_scores'].items(),
                                 key=lambda x: str(x[0])):
        with st.expander(
                f"{round_label(cfg, round_key)}: {fmt_pts(pts)} points",
                expanded=False):
            _player_round_detail(cfg, results, round_key, name, pid)


def _player_round_detail(cfg, results, round_key, name, pid):
    breakdowns = results['breakdowns']

    if round_key == 'extras':
        rows = [r for r in results['extras_detail'] if r['player'] == name]
        if rows:
            st.dataframe(pd.DataFrame([
                {'Round': (round_label(cfg, str(r['round']))
                           if str(r['round']) else '—'),
                 'What': extra_category_label(r['category']),
                 'Points': fmt_pts(float(r['points'])),
                 'Note': r.get('note', '') or ''}
                for r in rows]), width='stretch', hide_index=True)
        return

    b = breakdowns.get(str(round_key), {})
    style = cfg.round_config(round_key)['scoring_style']

    if style == 'best_ball_individual':
        holes = cfg.course_holes(round_key)
        nets = b.get('net_scores', {}).get(name, {})
        hole_pts = b.get('hole_points', {}).get(name, {})
        if nets:
            rows = []
            for h in sorted(nets, key=int):
                rows.append({
                    'Hole': int(h), 'Par': holes[str(h)]['par'],
                    'Net': nets[h], 'Points': hole_pts.get(h, hole_pts.get(str(h), 0)),
                })
            st.markdown("**Individual hole points (net):**")
            st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)

        for pair_name, members in b.get('pair_members', {}).items():
            if name in members:
                rel = b['pair_relative'][pair_name]
                pos_pts = b['pair_position_points'].get(pair_name, 0)
                st.markdown(f"**Pair ({pair_name}):** {rel:+} to par → "
                            f"{fmt_pts(pos_pts)} position points")
        for foursome, holes_survived in b.get('survival_holes', {}).items():
            if name in foursome.split(', '):
                spts = b.get('survival_points', {}).get(name, 0)
                st.markdown(f"**Survival ({foursome}):** {holes_survived} holes"
                            + (f" → {fmt_pts(spts)} points" if spts else ""))

    elif style == 'match_play':
        for pair_name, pts in b.get('pair_points', {}).items():
            if name in pair_name:
                st.markdown(f"**{pair_name}:** {fmt_pts(pts)} match-play points each")

    elif style == 'team_scramble':
        team = cfg.players[pid]['team']
        cat = b.get('category_points', {})
        rows = [{'Category': c, 'Points': cat[c].get(team, 0)} for c in cat]
        st.markdown(f"**Team {team} scramble results:**")
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)

    elif style == 'two_man_scramble':
        for pair_name, pts in b.get('pair_points', {}).items():
            if name in pair_name:
                rel = b['pair_results'][pair_name]['overall_relative']
                st.markdown(f"**{pair_name}:** {rel:+} to par → {fmt_pts(pts)} points")


def page_team(cfg, results):
    individual = results['leaderboard']['individual']
    team_board = results['leaderboard']['team']
    team = st.selectbox("Team", sorted(team_board))
    entry = team_board[team]

    st.title(f"Team {team} — {fmt_pts(entry['total_points'])} points")

    rows = []
    for pid in entry['players']:
        e = individual[pid]
        row = {'Player': e['name']}
        for n in cfg.round_numbers():
            row[f'R{n}'] = e['round_scores'].get(str(n), 0)
        row['Extras'] = e['round_scores'].get('extras', 0)
        row['Total'] = e['total_points']
        rows.append(row)
    df = pd.DataFrame(sorted(rows, key=lambda r: -r['Total']))
    st.dataframe(df, width='stretch', hide_index=True)

    totals = {f'R{n}': df[f'R{n}'].sum() for n in cfg.round_numbers()}
    totals['Extras'] = df['Extras'].sum()
    st.markdown("**Points by round:** " + " · ".join(
        f"{k}: {fmt_pts(v)}" for k, v in totals.items()))


def render_pair_handicaps(results):
    """Public view of the frozen Sunday pair handicaps: the strokes each pair
    gets, and nothing about how they were derived."""
    saved = results.get('round_5_handicaps')
    pairs = (saved or {}).get('pairs') or {}
    if not pairs:
        st.info("Pair handicaps haven't been set yet. They're calculated from "
                "Round 1 and Round 3 once both are in the books.")
        return

    st.subheader("Pair handicaps")
    st.dataframe(pd.DataFrame(
        [{'Pair': label, 'Strokes': p['pair_handicap']}
         for label, p in sorted(pairs.items(),
                                key=lambda kv: kv[1]['pair_handicap'])]),
        width='stretch', hide_index=True)
    if saved.get('calculated_at'):
        st.caption(f"Set {saved['calculated_at']}. Enter your net score — "
                   f"subtract your pair's strokes from your gross total.")


def page_round(cfg, results):
    options = [str(n) for n in cfg.round_numbers()]
    round_key = st.selectbox(
        "Round", options, format_func=lambda k: round_label(cfg, k))
    rcfg = cfg.round_config(round_key)
    status = results['round_statuses'].get(round_key, state.NOT_STARTED)

    st.title(round_label(cfg, round_key))
    course = rcfg.get('course')
    st.caption(f"{rcfg['scoring_style']}"
               + (f" · {course}" if course else "")
               + f" · {ROUND_STATUS_LABELS[status]}")

    # Shown before the round starts as well as during it: the pairs need their
    # stroke allocation on the first tee, which is the whole point of it.
    if rcfg['scoring_style'] == 'two_man_scramble':
        render_pair_handicaps(results)

    if status == state.NOT_STARTED:
        st.info("This round hasn't started yet.")
        return

    pts = results['round_points'].get(round_key, {})
    id_to_name = cfg.id_to_name()
    rows = [{'Player': id_to_name[pid], 'Team': cfg.players[pid]['team'],
             'Points': p} for pid, p in pts.items()]
    st.subheader("Points")
    st.dataframe(pd.DataFrame(sorted(rows, key=lambda r: -r['Points'])),
                 width='stretch', hide_index=True)

    raw = results['raw_scores'].get(round_key, [])
    if raw:
        st.subheader("Scorecards (as scraped, with corrections applied)")
        srows = []
        for e in raw:
            row = {'Name': e['name']}
            for h in range(1, 19):
                v = e['hole_scores'].get(str(h), 0)
                row[str(h)] = str(v) if v else ''
            row['Total'] = str(e.get('total_score', '') or '')
            srows.append(row)
        st.dataframe(pd.DataFrame(srows), width='stretch', hide_index=True)

    details = results['details'].get(round_key)
    if details:
        with st.expander("Full scoring log (how every point was computed)"):
            st.text("\n".join(details))


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

def admin_authenticated():
    """True once logged in this session. Doesn't render any UI itself - see
    render_admin_login() for the actual password box, which is rendered
    separately (below the main nav radio) so this can be checked earlier,
    while deciding whether "Admin" belongs in that radio's page list."""
    return bool(st.session_state.get('is_admin'))


def render_admin_login():
    """Sidebar admin password box. Renders nothing once already logged in
    for this session."""
    if st.session_state.get('is_admin'):
        return
    pw = st.sidebar.text_input("Admin password", type="password", key="admin_pw")
    if pw:
        # Counter (not a bool) so the arcade audio panel's poller - which
        # can only detect a CHANGE in this value, not a one-off event - can
        # tell a fresh submission apart from "still showing the last result".
        st.session_state['admin_attempt'] = st.session_state.get('admin_attempt', 0) + 1
        if pw == st.secrets.get('admin_password', ''):
            st.session_state['is_admin'] = True
            st.rerun()  # so "Admin" shows up in the nav radio right away
        else:
            st.sidebar.error("Wrong password")


def page_admin(cfg, results, logs):
    st.title("Admin")
    store = get_writable_store()
    is_sheets = not isinstance(store, LocalStore)
    st.caption("Edits are saved to "
               + ("the Google Sheet" if is_sheets else f"{cfg.data_dir}/ (local JSON)")
               + ". Every change triggers a re-score.")

    if st.button("🔄 Pull latest scores now", type="primary"):
        refresh_now()
        st.rerun()

    with st.expander("Last pipeline log"):
        st.text("\n".join(logs))

    (tab_status, tab_fix, tab_extras, tab_match, tab_adjust,
     tab_sunday) = st.tabs(
        ["Round status", "Fix a score", "Extras", "Match play", "Adjustments",
         "Sunday handicaps"])

    local = LocalStore(cfg.data_dir)
    round_states = (store.read_round_state() if is_sheets
                    else local.read_round_state())

    with tab_status:
        st.markdown("Status is auto-inferred from the scores. Use overrides only "
                    "if the inference is wrong; **lock** a round once it's final "
                    "so later edits on PlayThru can't change it.")
        new_states = {}
        for n in cfg.round_numbers():
            k = str(n)
            rs = round_states.get(k, {})
            inferred = results['round_statuses'].get(k, state.NOT_STARTED)
            c1, c2, c3 = st.columns([2, 2, 1])
            c1.markdown(f"**{round_label(cfg, k)}**  \ninferred: "
                        f"{ROUND_STATUS_LABELS[inferred]}")
            override = c2.selectbox(
                "Override", ['(auto)', state.NOT_STARTED, state.LIVE, state.COMPLETE],
                index=([None, state.NOT_STARTED, state.LIVE, state.COMPLETE]
                       .index(rs.get('override_status'))
                       if rs.get('override_status') else 0),
                key=f"ovr_{k}", label_visibility="collapsed")
            locked = c3.checkbox("Locked", value=rs.get('locked', False),
                                 key=f"lock_{k}")
            new_states[k] = {
                'override_status': None if override == '(auto)' else override,
                'locked': locked,
            }
        if st.button("Save round status"):
            if is_sheets:
                store.write_round_state(new_states, results['round_statuses'])
            else:
                local.write_round_state(new_states)
            refresh_now()
            st.success("Saved.")
            st.rerun()

    with tab_fix:
        st.markdown("Corrections are stored separately and re-applied on every "
                    "scrape, so they can never be overwritten by a refresh.")
        round_options = [str(n) for n in cfg.round_numbers()
                         if cfg.round_config(n).get('scrape_url')]
        rn = st.selectbox("Round", round_options,
                          format_func=lambda k: round_label(cfg, k), key="fix_round")
        entities = [e['name'] for e in results['raw_scores'].get(rn, [])]
        entity = st.selectbox("Player / team / pair", entities, key="fix_entity")
        hole = st.selectbox("Hole", [str(h) for h in range(1, 19)], key="fix_hole")
        current = ''
        for e in results['raw_scores'].get(rn, []):
            if e['name'] == entity:
                current = e['hole_scores'].get(hole, '(none)')
        st.caption(f"Current score on hole {hole}: {current}")
        score = st.number_input("Corrected score", min_value=1, max_value=20,
                                value=4, key="fix_score")
        note = st.text_input("Note (why)", key="fix_note")
        if st.button("Save correction"):
            if is_sheets:
                store.append_correction(rn, entity, hole, int(score), note)
            else:
                rows = local.read_corrections()
                rows.append({'round': rn, 'entity': entity, 'hole': hole,
                             'score': int(score), 'note': note})
                local.write_corrections(rows)
            refresh_now()
            st.success(f"Saved: {entity}, hole {hole} → {int(score)}")
            st.rerun()

        existing = store.read_corrections()

        st.divider()
        st.markdown("**Edit or remove corrections**")
        if not existing:
            st.caption("No corrections entered yet.")
        else:
            st.caption("Change any value directly in a cell; delete a row with "
                       "the checkbox on its left, then the trash/❌ that appears. "
                       "Click **Save corrections changes** to apply. Deleting a "
                       "correction reverts that hole to whatever PlayThru says "
                       "on the next refresh.")
            # Entities seen in any round's scores, plus any already stored, so
            # an existing row for a player who has since dropped out still has
            # a valid option and isn't silently rewritten.
            entity_opts = sorted({e['name']
                                  for rows in results['raw_scores'].values()
                                  for e in rows}
                                 | {str(c['entity']) for c in existing
                                    if c.get('entity')})
            round_editor_opts = list(dict.fromkeys(
                round_options + [str(c['round']) for c in existing]))
            edit_rows = []
            for c in existing:
                try:
                    sc = int(float(c['score']))
                except (TypeError, ValueError):
                    sc = 0
                edit_rows.append({
                    'Round': str(c['round']), 'Entity': str(c['entity']),
                    'Hole': str(c['hole']), 'Score': sc,
                    'Note': str(c.get('note', '') or '')})
            edited = st.data_editor(
                pd.DataFrame(edit_rows), key="fix_editor", width='stretch',
                hide_index=True, num_rows="dynamic",
                column_config={
                    'Round': st.column_config.SelectboxColumn(
                        options=round_editor_opts, required=True),
                    'Entity': st.column_config.SelectboxColumn(
                        options=entity_opts, required=True),
                    'Hole': st.column_config.SelectboxColumn(
                        options=[str(h) for h in range(1, 19)], required=True),
                    'Score': st.column_config.NumberColumn(
                        min_value=1, max_value=20, step=1, required=True),
                    'Note': st.column_config.TextColumn(),
                })
            if st.button("Save corrections changes"):
                new_rows = []
                for _, r in edited.iterrows():
                    if pd.isna(r['Entity']) or not str(r['Entity']).strip():
                        continue
                    if pd.isna(r['Round']) or not str(r['Round']).strip():
                        continue
                    if pd.isna(r['Hole']) or not str(r['Hole']).strip():
                        continue
                    try:
                        sc = int(float(r['Score']))
                    except (TypeError, ValueError):
                        continue
                    new_rows.append({
                        'round': str(r['Round']), 'entity': str(r['Entity']),
                        'hole': str(r['Hole']), 'score': sc,
                        'note': '' if pd.isna(r['Note']) else str(r['Note'])})
                store.write_corrections(new_rows)
                refresh_now()
                st.success(f"Saved {len(new_rows)} correction(s).")
                st.rerun()

    with tab_extras:
        players = sorted(p['name'] for p in cfg.players.values())
        round_opts = [str(n) for n in cfg.round_numbers()]

        st.markdown("**Add an extra**")
        st.caption("Pick a category for the usual amount, or type any points "
                   "you like.")
        player = st.selectbox("Player", players, key="ex_player")
        rn = st.selectbox("Round", round_opts, key="ex_round")
        cat_keys = [k for k in EXTRA_CATEGORIES if k != 'adjustment']

        def cat_option(k):
            label, pts = EXTRA_CATEGORIES[k]
            unit = 'pt' if pts == 1 else 'pts'
            return f"{label} ({fmt_pts(pts)} {unit})"

        cat = st.selectbox("Category", cat_keys, format_func=cat_option,
                           key="ex_cat")
        points = st.number_input("Points", value=EXTRA_CATEGORIES[cat][1],
                                 step=0.5, key="ex_pts")
        note = st.text_input("Note", key="ex_note")
        if st.button("Add extras"):
            if is_sheets:
                store.append_extra(rn, player, cat, points, note)
            else:
                rows = local.read_extras()
                rows.append({'round': rn, 'player': player, 'category': cat,
                             'points': points, 'note': note})
                local.write_extras(rows)
            refresh_now()
            st.success(f"Added {points:g} points to {player}")
            st.rerun()

        existing = store.read_extras()

        st.divider()
        st.markdown("**Edit or remove extras**")
        if not existing:
            st.caption("No extras entered yet.")
        else:
            st.caption("Change any value directly in a cell; delete a row with "
                       "the checkbox on its left, then the trash/❌ that appears. "
                       "Click **Save extras changes** to apply.")
            player_editor_opts = sorted(
                set(players) | {str(e['player']) for e in existing if e.get('player')})
            round_editor_opts = list(dict.fromkeys(
                round_opts + [str(e['round']) for e in existing]))
            # The editor shows labels, not the stored keys, so map back on save.
            # Any category already stored but not in EXTRA_CATEGORIES still gets
            # an option, so an unrecognised row isn't silently rewritten.
            cat_label_to_key = {extra_category_label(k): k
                                for k in EXTRA_CATEGORIES}
            for e in existing:
                cat_label_to_key.setdefault(
                    extra_category_label(e['category']), str(e['category']))
            edit_rows = []
            for e in existing:
                try:
                    pts = float(e['points'])
                except (TypeError, ValueError):
                    pts = 0.0
                edit_rows.append({
                    'Round': str(e['round']), 'Player': str(e['player']),
                    'Category': extra_category_label(e['category']),
                    'Points': pts,
                    'Note': str(e.get('note', '') or '')})
            edited = st.data_editor(
                pd.DataFrame(edit_rows), key="ex_editor", width='stretch',
                hide_index=True, num_rows="dynamic",
                column_config={
                    'Round': st.column_config.SelectboxColumn(
                        options=round_editor_opts, required=True),
                    'Player': st.column_config.SelectboxColumn(
                        options=player_editor_opts, required=True),
                    'Category': st.column_config.SelectboxColumn(
                        options=sorted(cat_label_to_key), required=True),
                    'Points': st.column_config.NumberColumn(step=0.5,
                                                            required=True),
                    'Note': st.column_config.TextColumn(),
                })
            if st.button("Save extras changes"):
                new_rows = []
                for _, r in edited.iterrows():
                    if pd.isna(r['Player']) or not str(r['Player']).strip():
                        continue
                    if pd.isna(r['Round']) or not str(r['Round']).strip():
                        continue
                    try:
                        pts = float(r['Points'])
                    except (TypeError, ValueError):
                        pts = 0.0
                    label = '' if pd.isna(r['Category']) else str(r['Category'])
                    new_rows.append({
                        'round': str(r['Round']), 'player': str(r['Player']),
                        'category': cat_label_to_key.get(label, label),
                        'points': pts,
                        'note': '' if pd.isna(r['Note']) else str(r['Note'])})
                store.write_extras(new_rows)
                refresh_now()
                st.success("Saved extras.")
                st.rerun()

        st.divider()
        st.markdown("**Visual summary — counts per round × category**")
        summary_player = st.selectbox("Show player", players,
                                      key="ex_summary_player")
        std_cats = [k for k in EXTRA_CATEGORIES if k != 'adjustment']
        other_cats = [c for c in dict.fromkeys(str(e['category'])
                                               for e in existing)
                      if c and c not in std_cats]
        cats = std_cats + other_cats
        counts = {rk: {c: 0 for c in cats} for rk in round_opts}
        for e in existing:
            if e['player'] != summary_player:
                continue
            rk = str(e['round'])
            if rk in counts:
                c = str(e['category'])
                counts[rk][c] = counts[rk].get(c, 0) + 1
        summary_df = pd.DataFrame([
            {'Round': round_label(cfg, rk),
             **{extra_category_label(c): counts[rk][c] for c in cats}}
            for rk in round_opts])
        st.dataframe(summary_df, width='stretch', hide_index=True)
        st.caption("Each cell is how many of that extra the player recorded in "
                   "that round. Edit the table above to change them.")

        adj_rows = [a for a in store.read_adjustments()
                    if a['player'] == summary_player]
        extras_pts = sum(float(e['points']) for e in existing
                         if e['player'] == summary_player)
        adj_pts = sum(float(a['points']) for a in adj_rows)
        if adj_rows:
            st.markdown("**Adjustments for this player** — one-off points, "
                        "*not* included in the counts above:")
            st.dataframe(
                pd.DataFrame([{'Points': a['points'], 'Reason': a.get('note', '')}
                              for a in adj_rows]),
                width='stretch', hide_index=True)
        st.caption(
            f"**Extras total for {summary_player}: {fmt_pts(extras_pts + adj_pts)} "
            f"pts** — {fmt_pts(extras_pts)} from the tallies above"
            + (f", plus {fmt_pts(adj_pts)} from adjustments."
               if adj_rows else "."))

    with tab_match:
        render_match_play_admin(cfg, store, local, is_sheets)

    with tab_adjust:
        st.markdown("One-off point adjustments (positive or negative). "
                    "Shows up under Extras.")
        players = sorted(p['name'] for p in cfg.players.values())
        player = st.selectbox("Player", players, key="adj_player")
        pts = st.number_input("Points (+/-)", value=0.0, step=0.5, key="adj_pts")
        note = st.text_input("Reason", key="adj_note")
        if st.button("Save adjustment"):
            if is_sheets:
                store.append_adjustment(player, pts, note)
            else:
                rows = local.read_adjustments()
                rows.append({'player': player, 'points': pts, 'note': note})
                local.write_adjustments(rows)
            refresh_now()
            st.success(f"Saved: {player} {pts:+g}")
            st.rerun()

        existing = store.read_adjustments()

        st.divider()
        st.markdown("**Edit or remove adjustments**")
        if not existing:
            st.caption("No adjustments entered yet.")
        else:
            st.caption("Change any value directly in a cell; delete a row with "
                       "the checkbox on its left, then the trash/❌ that appears. "
                       "Click **Save adjustments changes** to apply.")
            player_editor_opts = sorted(
                set(players) | {str(a['player']) for a in existing
                                if a.get('player')})
            edit_rows = []
            for a in existing:
                try:
                    p = float(a['points'])
                except (TypeError, ValueError):
                    p = 0.0
                edit_rows.append({'Player': str(a['player']), 'Points': p,
                                  'Reason': str(a.get('note', '') or '')})
            edited = st.data_editor(
                pd.DataFrame(edit_rows), key="adj_editor", width='stretch',
                hide_index=True, num_rows="dynamic",
                column_config={
                    'Player': st.column_config.SelectboxColumn(
                        options=player_editor_opts, required=True),
                    'Points': st.column_config.NumberColumn(step=0.5,
                                                            required=True),
                    'Reason': st.column_config.TextColumn(),
                })
            if st.button("Save adjustments changes"):
                new_rows = []
                for _, r in edited.iterrows():
                    if pd.isna(r['Player']) or not str(r['Player']).strip():
                        continue
                    try:
                        p = float(r['Points'])
                    except (TypeError, ValueError):
                        continue
                    new_rows.append({
                        'player': str(r['Player']), 'points': p,
                        'note': '' if pd.isna(r['Reason']) else str(r['Reason'])})
                store.write_adjustments(new_rows)
                refresh_now()
                st.success(f"Saved {len(new_rows)} adjustment(s).")
                st.rerun()

    with tab_sunday:
        render_sunday_handicap_admin(cfg, results, store, local, is_sheets)


MATCH_PLAY_POINTS = 5.0


def round_2_matches(cfg):
    """The Round 2 matches, as ((pair A), (pair B)) tuples.

    A match is the two pairs sharing a foursome — that is the unit the points
    belong to, since the five points are split between them. Derived from the
    roster rather than entered by hand so the app cannot disagree with it.
    """
    partners, foursomes = cfg.partners(2), cfg.foursomes(2)
    matches, seen = [], set()
    for name in sorted(partners):
        partner = partners.get(name)
        others = foursomes.get(name) or []
        if not partner or len(others) != 2:
            continue
        a = tuple(sorted((name, partner)))
        b = tuple(sorted(others))
        # Both pairs must be genuine partnerships, or the roster disagrees
        # with itself and we should not invent a match from it.
        if partners.get(b[0]) != b[1]:
            continue
        key = frozenset((a, b))
        if key in seen:
            continue
        seen.add(key)
        matches.append(tuple(sorted((a, b))))
    matches.sort()
    return matches


def render_match_play_admin(cfg, store, local, is_sheets):
    """Round 2 entry, one match at a time.

    The five points are always split between the two pairs in a match, so only
    one number is ever typed: the other pair gets the remainder. Entering the
    match rather than the pair makes a split that doesn't total five
    impossible to record in the first place, which a check after the fact
    could only detect, not resolve — it can tell you a foursome is wrong but
    not which of the two numbers to fix.
    """
    st.markdown(f"Enter one pair's points; the other pair automatically gets "
                f"the rest of the {fmt_pts(MATCH_PLAY_POINTS)}. Both players "
                f"in a pair receive their pair's amount.")

    existing = store.read_match_play() if is_sheets else local.read_match_play()
    # Later rows supersede earlier ones for the same pair, matching how the
    # scoring engine keys pair points, so a correction never reads as a second
    # award.
    entered = {}
    for r in existing:
        entered[frozenset(r['players'])] = float(r['points'])

    matches = round_2_matches(cfg)
    if not matches:
        st.warning("No Round 2 matches could be built from the roster file. "
                   "Every player needs round_2_partner and a two-player "
                   "round_2_foursome.")
        return

    def pair_name(pair):
        return ' & '.join(pair)

    def match_label(match):
        a, b = match
        got_a, got_b = entered.get(frozenset(a)), entered.get(frozenset(b))
        label = f"{pair_name(a)}  vs  {pair_name(b)}"
        if got_a is None and got_b is None:
            return label
        return (f"{label}   ✓ {fmt_pts(got_a or 0)} – "
                f"{fmt_pts(got_b or 0)}")

    match = st.selectbox("Match", matches, format_func=match_label,
                         key="mp_match")
    pair_a, pair_b = match

    prior = entered.get(frozenset(pair_a))
    pts_a = st.number_input(
        f"Points for {pair_name(pair_a)}",
        min_value=0.0, max_value=MATCH_PLAY_POINTS, step=0.25,
        value=float(prior) if prior is not None else 0.0,
        key=f"mp_pts_{'_'.join(pair_a)}")
    pts_b = MATCH_PLAY_POINTS - pts_a
    st.info(f"**{pair_name(pair_a)}** get {fmt_pts(pts_a)}  ·  "
            f"**{pair_name(pair_b)}** get {fmt_pts(pts_b)}")

    if st.button("Save match result"):
        # Replace any existing rows for either pair, then write both, so the
        # stored split always totals five even when correcting an entry.
        drop = {frozenset(pair_a), frozenset(pair_b)}
        rows = [r for r in existing if frozenset(r['players']) not in drop]
        rows.append({'players': list(pair_a), 'points': pts_a})
        rows.append({'players': list(pair_b), 'points': pts_b})
        if is_sheets:
            store.write_match_play(rows)
        else:
            local.write_match_play(rows)
        refresh_now()
        st.success(f"Saved: {pair_name(pair_a)} {fmt_pts(pts_a)}, "
                   f"{pair_name(pair_b)} {fmt_pts(pts_b)}")
        st.rerun()

    done = [m for m in matches
            if frozenset(m[0]) in entered and frozenset(m[1]) in entered]
    if len(done) < len(matches):
        todo = [m for m in matches if m not in done]
        st.caption(f"{len(done)} of {len(matches)} matches entered. Still to "
                   f"come: " + '; '.join(match_label(m) for m in todo))
    else:
        st.caption(f"All {len(matches)} matches entered.")

    rows = []
    for a, b in matches:
        got_a, got_b = entered.get(frozenset(a)), entered.get(frozenset(b))
        if got_a is None and got_b is None:
            continue
        total = (got_a or 0) + (got_b or 0)
        rows.append({
            'Pair': pair_name(a), 'Points': fmt_pts(got_a or 0),
            'Opponent': pair_name(b), 'Their points': fmt_pts(got_b or 0),
            'Match total': fmt_pts(total),
            # Only reachable by editing the sheet by hand; the UI can't
            # produce it. Worth surfacing rather than silently scoring it.
            'OK': '✓' if abs(total - MATCH_PLAY_POINTS) < 1e-9 else '✗',
        })
    if rows:
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
        bad = [r for r in rows if r['OK'] == '✗']
        if bad:
            st.error(f"{len(bad)} match(es) don't total "
                     f"{fmt_pts(MATCH_PLAY_POINTS)}. That can only come from "
                     f"a hand edit in the sheet — re-save the match here to "
                     f"fix it.")

    orphans = sorted(
        (pair_name(sorted(k)) for k in entered
         if not any(frozenset(p) == k for m in matches for p in m)),)
    if orphans:
        st.warning("These stored pairs aren't Round 2 matches in the current "
                   "roster, so their points are being credited to nobody's "
                   "match: " + ', '.join(orphans))


def render_sunday_handicap_admin(cfg, results, store, local, is_sheets):
    """Calculate, review and freeze the Round 5 two-man pair handicaps.

    Deliberately a two-step flow. Calculating shows a preview only; nothing is
    stored until Save. Once saved, the numbers are what the app and the sheet
    report, even if a Round 1 or 3 score is corrected afterwards, because the
    pairs are told their strokes on the first tee and can't be moved after.
    """
    st.markdown("Pair handicaps for the Sunday two-man scramble, derived from "
                "Round 1 and Round 3. Calculate, check them, then save to "
                "lock them in.")

    # Same guard as the pipeline: a cache_resource store instance can outlive
    # the deploy that defined these methods, and a bad read must not take the
    # admin page down with it.
    try:
        if is_sheets and hasattr(store, 'read_round_5_handicaps'):
            saved = store.read_round_5_handicaps()
        else:
            saved = local.read_round_5_handicaps()
    except Exception as e:
        saved = None
        st.warning(f"Couldn't read the saved handicaps ({e}). Calculating still "
                   f"works, but check the sheet before you rely on a save.")

    if saved and saved.get('pairs'):
        st.success(f"Saved and locked — calculated "
                   f"{saved.get('calculated_at') or 'at an unknown time'}. "
                   f"These are the numbers the app and the sheet are showing.")
        st.dataframe(pd.DataFrame([
            {'Pair': label,
             'Player A': p['player_a'], 'A': p['player_a_handicap'],
             'Player B': p['player_b'], 'B': p['player_b_handicap'],
             'Pair handicap': p['pair_handicap']}
            for label, p in sorted(saved['pairs'].items())
        ]), width='stretch', hide_index=True)

    # Round 1 and 3 gross scores are the only inputs. Without both, there is
    # nothing to derive from.
    raw = results.get('raw_scores', {})
    r1 = [e for e in raw.get('1', raw.get(1, []))
          if e.get('scoring_style') != 'team_scramble']
    r3 = [e for e in raw.get('3', raw.get(3, []))
          if e.get('scoring_style') != 'team_scramble']
    holes1, holes3 = cfg.course_holes(1), cfg.course_holes(3)

    def complete(entries):
        return [e for e in entries
                if len(e.get('hole_scores', {})) == 18
                and all(v for v in e['hole_scores'].values())]

    c1, c3 = complete(r1), complete(r3)
    st.caption(f"Inputs: Round 1 has {len(c1)} of {len(cfg.players)} complete "
               f"cards, Round 3 has {len(c3)}.")

    if not holes1 or not holes3:
        st.warning("Round 1 and Round 3 need courses set in the event config "
                   "before handicaps can be derived.")
        return
    if len(c1) < len(cfg.players) or len(c3) < len(cfg.players):
        st.warning("Both Round 1 and Round 3 need a complete card for every "
                   "player. Finish or correct the missing cards first — a "
                   "partial card would understate that player's handicap.")

    label = "Recalculate" if saved else "Calculate pair handicaps"
    if st.button(label, type="primary"):
        try:
            st.session_state['sunday_preview'] = scoring.calculate_round_5_handicaps(
                c1, c3, holes1, holes3, cfg.handicaps(), cfg.partners(5),
                allocation=cfg.handicap_allocation)
        except Exception as e:
            st.session_state.pop('sunday_preview', None)
            st.error(f"Could not calculate: {e}")

    preview = st.session_state.get('sunday_preview')
    if not preview:
        return

    ind, pairs = preview['individual_handicaps'], preview['pair_handicaps']
    if not pairs:
        if not ind:
            st.error("No player could be scored — Round 1 and Round 3 have no "
                     "usable cards yet, so there is nothing to derive from.")
        else:
            st.error("Individual figures came out, but no pairs could be "
                     "built. Check that round_5_partner is set for every "
                     "player in the roster file.")
        return

    st.markdown("**Preview — not saved yet**")
    st.dataframe(pd.DataFrame([
        {'Pair': label_,
         'Player A': p['player_a'], 'A': p['player_a_handicap'],
         'Player B': p['player_b'], 'B': p['player_b_handicap'],
         'Pair handicap': p['pair_handicap']}
        for label_, p in sorted(pairs.items())
    ]), width='stretch', hide_index=True)

    with st.expander("How each player's Sunday figure was derived"):
        st.dataframe(pd.DataFrame([
            {'Player': n,
             'R1 adj. total': d['round_1_adjusted_total'],
             'R1 vs par': f"{d['round_1_relative']:+d}",
             'R3 adj. total': d['round_3_adjusted_total'],
             'R3 vs par': f"{d['round_3_relative']:+d}",
             'Average': f"{d['avg_relative']:+.1f}",
             'Sunday handicap': d['handicap']}
            for n, d in sorted(ind.items())
        ]), width='stretch', hide_index=True)
        notes = [d for d in preview.get('details', []) if d.startswith('Missing')]
        if notes:
            st.warning("\n\n".join(notes))

    if saved and saved.get('pairs'):
        changes = [f"{k}: {saved['pairs'][k]['pair_handicap']} → "
                   f"{v['pair_handicap']}"
                   for k, v in sorted(pairs.items())
                   if k in saved['pairs']
                   and saved['pairs'][k]['pair_handicap'] != v['pair_handicap']]
        if changes:
            st.warning("Saving would change already-locked handicaps:\n\n"
                       + "\n\n".join(f"- {c}" for c in changes))
        else:
            st.info("Identical to what is already saved.")

    if st.button("Save and lock these handicaps"):
        payload = {
            'calculated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'pairs': pairs,
        }
        local.write_round_5_handicaps(payload)
        # The sheet is the copy that survives a restart, so say so plainly if
        # only the ephemeral local copy was written.
        if is_sheets:
            try:
                store.write_round_5_handicaps(payload)
            except Exception as e:
                st.error(f"Saved locally but NOT to the Google Sheet ({e}). The "
                         f"local copy is lost if the app restarts — reboot the "
                         f"app and save again.")
                return
        st.session_state.pop('sunday_preview', None)
        refresh_now()
        st.success(f"Locked in {len(pairs)} pair handicaps.")
        st.rerun()


# Arcade-mode "attract screen" shown once per session before the real app.
# Any click/tap anywhere on it advances past it - implemented as one real
# st.button() CSS-stretched to cover the full viewport (with the decorative
# title/HI-SCORE/PRESS START text drawn on top via a pointer-events:none
# overlay, so clicks pass through to the button underneath). Dismissal is
# tracked in session_state so it only ever shows once per session, even
# though Streamlit re-runs this whole function on every interaction.
def render_arcade_title_screen(cfg):
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"] { display: none !important; }
        header[data-testid="stHeader"] { display: none !important; }
        .block-container { padding: 0 !important; }

        .arcade-title-screen {
            position: fixed; inset: 0; z-index: 1000000;
            display: flex; flex-direction: column; align-items: center;
            justify-content: center; gap: 32px;
            pointer-events: none; text-align: center; padding: 0 24px;
        }
        .arcade-title-screen .game-title {
            font-family: 'Press Start 2P', cursive;
            font-size: 1.5rem; max-width: 90vw; line-height: 1.7;
            color: #ffa629;
            text-shadow:
                3px 3px 0 #e63946,
                1px 1px 0 #000, -1px -1px 0 #000, 1px -1px 0 #000, -1px 1px 0 #000,
                0 0 16px rgba(255,166,41,0.5);
        }
        .arcade-title-screen .hi-score-label {
            font-family: 'Press Start 2P', cursive;
            font-size: 0.55rem; letter-spacing: 3px; color: #f2f2f2;
        }
        .arcade-title-screen .hi-score-value {
            font-family: 'Press Start 2P', cursive;
            font-size: 1.3rem; color: #ffa629; margin-top: 10px;
            text-shadow:
                3px 3px 0 #e63946,
                1px 1px 0 #000, -1px -1px 0 #000, 1px -1px 0 #000, -1px 1px 0 #000,
                0 0 12px rgba(255,166,41,0.45);
        }
        @keyframes arcade-blink { 0%, 55% { opacity: 1; } 56%, 100% { opacity: 0; } }
        .arcade-title-screen .press-start {
            font-family: 'Press Start 2P', cursive;
            font-size: 0.85rem; color: #f2f2f2;
            animation: arcade-blink 1.1s steps(1) infinite;
        }

        /* The actual click target: a real button, invisible, stretched to
           fill the screen and sitting UNDER the decorative layer above
           (which has pointer-events: none, so every click/tap falls
           through to this). */
        .st-key-arcade_title_gate {
            position: fixed !important; inset: 0 !important; z-index: 999999 !important;
        }
        .st-key-arcade_title_gate button,
        .st-key-arcade_title_gate button:hover,
        .st-key-arcade_title_gate button:focus,
        .st-key-arcade_title_gate button:active,
        .st-key-arcade_title_gate.st-key-arcade_title_gate button:hover,
        .st-key-arcade_title_gate.st-key-arcade_title_gate button:focus,
        .st-key-arcade_title_gate.st-key-arcade_title_gate button:active {
            position: fixed !important; inset: 0 !important;
            width: 100vw !important; height: 100vh !important;
            background: #060606 !important;
            border: none !important; box-shadow: none !important;
            border-radius: 0 !important;
            font-size: 0 !important;
            color: transparent !important;
        }
        .st-key-arcade_title_gate button * {
            visibility: hidden !important;
        }
        </style>
        <div class="arcade-title-screen">
        """ + f"""
          <div class="game-title">{cfg.event_name}</div>
          <div>
            <div class="hi-score-label">HI-SCORE</div>
            <div class="hi-score-value">{ARCADE_HI_SCORE:05d}</div>
          </div>
          <div class="press-start">PRESS START</div>
        </div>
        """,
        unsafe_allow_html=True)

    with st.container(key="arcade_title_gate"):
        if st.button("Press start", key="arcade_title_start_btn"):
            st.session_state['arcade_title_dismissed'] = True
            st.rerun()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    st.markdown("""
    <style>
        .stApp { background-color: #0E1117; color: #FAFAFA; }
        h1, h2, h3 { color: #FAFAFA; }
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
    </style>""", unsafe_allow_html=True)

    if st.sidebar.toggle("🕹️ Arcade mode", help="Retro 80s/90s skin",
                        key="arcade_mode", value=True):
        st.markdown(ARCADE_CSS, unsafe_allow_html=True)
        with st.sidebar:
            components.html(build_arcade_audio_html(), height=50)

    cfg = get_config()
    arcade_on = st.session_state.get('arcade_mode')

    # Arcade "attract screen" - shown once per session, before anything
    # else (including the sidebar), and dismissed by a single click/tap
    # anywhere on it. Skipped entirely once dismissed, and never used at
    # all when Arcade mode is off.
    if arcade_on and not st.session_state.get('arcade_title_dismissed'):
        render_arcade_title_screen(cfg)
        return

    pages = ["Leaderboard", "Players", "Teams", "Rounds"]
    if admin_authenticated():
        pages.append("Admin")
    page = st.sidebar.radio("View", pages)
    render_admin_login()

    results, logs = get_results()
    individual = results['leaderboard']['individual']
    leader_pid = (max(individual.items(), key=lambda kv: kv[1]['total_points'])[0]
                  if individual else '')

    if arcade_on:
        # Hidden marker the audio panel's persistent iframe polls (see its
        # comment above) to detect a changed leader, a newly-LIVE round, or
        # an admin-password submission, without changing the iframe's own
        # content (which would tear down and rebuild it).
        live_rounds = ','.join(
            n for n, s in sorted(results['round_statuses'].items())
            if s == state.LIVE)
        st.markdown(
            f'<div id="arcade-state-marker" data-leader="{leader_pid}" '
            f'data-live="{live_rounds}" '
            f'data-auth="{st.session_state.get("admin_attempt", 0)}" '
            f'style="display:none;"></div>',
            unsafe_allow_html=True)

        # HI-SCORE: fixed historical record (2025 champion's total), per
        # user confirmation - NOT the live 2026 leader's running total.
        # Plain divs (not real h1-h4) so it doesn't inherit the page-title
        # sizing, but styled to match.
        hi_score = ARCADE_HI_SCORE
        st.markdown(
            "<div style='text-align:center; margin-bottom:4px;'>"
            "<div style='font-family:\"Press Start 2P\",cursive; font-size:0.5rem; "
            "color:#f2f2f2; letter-spacing:2px;'>HI-SCORE</div>"
            "<div style='font-family:\"Press Start 2P\",cursive; font-size:1rem; "
            "color:#ffa629; text-shadow:3px 3px 0 #e63946,1px 1px 0 #000,"
            "-1px -1px 0 #000,1px -1px 0 #000,-1px 1px 0 #000,"
            f"0 0 12px rgba(255,166,41,0.45);'>{hi_score:05d}</div>"
            "</div>",
            unsafe_allow_html=True)

    col1, col2 = st.columns([1, 8])
    with col1:
        st.image("jdcvo.png", width=110)
    with col2:
        st.markdown(f"<h1 style='margin-top:10px'>{cfg.event_name}</h1>",
                    unsafe_allow_html=True)

    if page == "Leaderboard":
        page_leaderboard(cfg, results)
    elif page == "Players":
        page_player(cfg, results)
    elif page == "Teams":
        page_team(cfg, results)
    elif page == "Rounds":
        page_round(cfg, results)
    elif page == "Admin":
        page_admin(cfg, results, logs)

    st.markdown("---")
    st.caption(f"Scores update automatically every ~{REFRESH_SECONDS // 60} minutes. "
               f"Last computed: {results['generated_at']}")


if __name__ == '__main__':
    main()
