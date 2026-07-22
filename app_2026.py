"""JDCVO 2026 tournament app.

Public side: live leaderboards with drill-downs into players, teams, and
rounds showing exactly where every point came from.

Admin side (password-protected, in the sidebar): pull latest scores, fix hole
scores, enter extras / match play / putt-off / adjustments, and control each
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
import os

import pandas as pd
import streamlit.components.v1 as components

from jdcvo import pipeline, state
from jdcvo.config import EventConfig
from jdcvo.store import LocalStore

CONFIG_PATH = 'event_2026.json'
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
   they don't turn into garbled overlapping text. */
.stApp span:not([aria-hidden="true"]):not([data-testid="stIconMaterial"]):not(.lb-name):not(.lb-points) {
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

# Purchased/evaluation sound-effect samples (see sfx_preview/ and
# music_preview/), embedded as base64 data URIs directly in the iframe's HTML
# so no separate static file server is needed. NOTE: these are still
# watermarked preview clips from AudioJungle-style packs - fine for
# testing/prototyping the wiring, but should be swapped for the purchased,
# unwatermarked files before real event use.
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

_ARCADE_MUSIC_LOOP = _audio_data_uri(os.path.join('music_preview', 'loop_2.mp3'))

# Fixed historical high score shown in the HI-SCORE display (2025 champion's
# final total), per explicit user confirmation - not derived from any data
# file, and not meant to update automatically as future seasons complete.
ARCADE_HI_SCORE = 38

# Background music + SFX for arcade mode, rendered via components.html (a
# real <iframe>) rather than st.markdown, because browsers never execute
# <script> tags injected through innerHTML/dangerouslySetInnerHTML - only a
# real document (iframe) will run them.
#
# The background loop is a real sample track (music_preview/loop_1.mp3,
# still a watermarked preview - swap for the purchased file before real
# event use), decoded once into an AudioBuffer and looped sample-accurately
# via Web Audio API rather than an <audio loop> tag, to avoid the small
# gap/click most browsers introduce at the loop point with plain HTML5 audio
# looping.
#
# Music defaults ON, but does NOT autoplay on load - browsers block audio
# until the user interacts with the page anyway. Instead it kicks off on the
# first click in the app, which in a fresh arcade session is the click that
# dismisses the intro/attract screen (see startMusicOnGesture below). The
# panel's toggle can still turn it off (e.g. while developing). The per-action
# SFX are NOT gated by the toggle at all - they're one-off clips triggered
# directly by a click, so they always just play.
#
# As long as this exact HTML string keeps rendering at the same spot on
# every Streamlit rerun, Streamlit reuses the same underlying iframe instead
# of recreating it, so the loop and its JS state (audio context, whether
# music is on, etc.) survive reruns instead of restarting every time the
# page refreshes.
#
# This same persistent iframe is also used to trigger the sample SFX above,
# for two different kinds of moments:
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
#      byte-identical across reruns, so it doesn't get recreated and the
#      music loop doesn't restart.
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
  <button id="arcade-music-toggle" title="Turn background music on/off (on by default)" style="
      font-size: 16px;
      line-height: 1;
      color: #ffa629;
      background: #000;
      border: 2px solid #ffa629;
      border-radius: 0;
      padding: 3px 7px;
      cursor: pointer;
      flex-shrink: 0;
  ">&#128266;</button>
  <label style="display:flex; align-items:center; gap:6px; white-space:nowrap; flex:1; min-width:0;">
    VOL
    <input id="arcade-vol" type="range" min="0" max="100" value="35" style="flex:1; min-width:0;">
  </label>
</div>
<script>
(function() {
  let audioCtx = null;
  let masterGain = null;
  let musicGain = null;
  let musicBuffer = null;
  let musicSource = null;
  let musicOn = true; // background loop defaults ON, started on first click (see startMusicOnGesture)

  const MUSIC_LOOP_SRC = "__MUSIC_LOOP__";

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
    // Not gated by the music toggle - these are one-off clips fired
    // directly from a click, which is itself the user gesture browsers
    // require, so they can just always play.
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

  function getVol() {
    const el = document.getElementById('arcade-vol');
    return el ? (parseInt(el.value, 10) / 100) : 0.35;
  }

  function updateMusicVolume() {
    if (musicGain) musicGain.gain.value = getVol();
  }

  function base64ToArrayBuffer(dataUri) {
    const comma = dataUri.indexOf(',');
    const raw = atob(dataUri.slice(comma + 1));
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    return bytes.buffer;
  }

  // Starts the loop, decoding the track into an AudioBuffer the first time
  // (cached in musicBuffer after that, so later starts are instant).
  function startMusicLoop() {
    if (!MUSIC_LOOP_SRC || musicSource) return;
    const finish = function(buffer) {
      musicBuffer = buffer;
      if (!musicOn || musicSource) return; // turned off, or already started meanwhile
      musicSource = audioCtx.createBufferSource();
      musicSource.buffer = musicBuffer;
      musicSource.loop = true;
      musicGain = audioCtx.createGain();
      musicGain.gain.value = getVol();
      musicSource.connect(musicGain).connect(masterGain);
      musicSource.start(0);
    };
    if (musicBuffer) {
      finish(musicBuffer);
      return;
    }
    audioCtx.decodeAudioData(base64ToArrayBuffer(MUSIC_LOOP_SRC))
      .then(finish)
      .catch(function() { /* ignore - e.g. malformed/missing data */ });
  }

  // Stops and tears down the current source node. musicBuffer (the decoded
  // track) stays cached, so turning the loop back on afterwards is instant
  // - just a fresh AudioBufferSourceNode, no re-decoding.
  function stopMusicLoop() {
    if (musicSource) {
      try { musicSource.stop(); } catch (e) { /* already stopped */ }
      try { musicSource.disconnect(); } catch (e) { /* already disconnected */ }
      musicSource = null;
    }
    if (musicGain) {
      try { musicGain.disconnect(); } catch (e) { /* already disconnected */ }
      musicGain = null;
    }
  }

  function setMusicIcon() {
    document.getElementById('arcade-music-toggle').innerHTML =
      musicOn ? '&#128266;' : '&#128263;';
  }

  document.getElementById('arcade-music-toggle').addEventListener('click', function() {
    musicOn = !musicOn;
    setMusicIcon();
    if (musicOn) {
      if (!audioCtx) {
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        masterGain = audioCtx.createGain();
        masterGain.gain.value = 1.0;
        masterGain.connect(audioCtx.destination);
      }
      audioCtx.resume();
      startMusicLoop();
    } else {
      stopMusicLoop();
    }
  });

  // Kick the background loop off on a user gesture. Browsers won't let audio
  // start without one, so this is wired to the first click in the app (which,
  // in a fresh arcade session, is the click that dismisses the intro/attract
  // screen). Idempotent and cheap to call on every click: it no-ops when music
  // is toggled off, only ever builds the audio context once, and startMusicLoop
  // itself no-ops once a source is already running.
  function startMusicOnGesture() {
    if (!musicOn) return;
    if (!audioCtx) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      masterGain = audioCtx.createGain();
      masterGain.gain.value = 1.0;
      masterGain.connect(audioCtx.destination);
    }
    audioCtx.resume();
    startMusicLoop();
  }

  const volSlider = document.getElementById('arcade-vol');
  if (volSlider) volSlider.addEventListener('input', updateMusicVolume);

  // --- Hooks into the parent Streamlit page (see module docstring above) ---
  function setupParentHooks() {
    let parentDoc;
    try {
      parentDoc = window.parent.document;
    } catch (e) {
      return; // not same-origin for some reason - fail silently, music still works
    }

    // Click sounds on real widgets in the parent page.
    parentDoc.addEventListener('click', function(e) {
      const target = e.target;
      // Start the background loop on the first real click (the intro-screen
      // dismissal in a fresh session). No-op after it's running / when music
      // is off, so it's safe to call on every click.
      startMusicOnGesture();
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

  setMusicIcon(); // reflect the default (music on) in the toggle icon
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
    html = html.replace('__MUSIC_LOOP__', _ARCADE_MUSIC_LOOP)
    return html


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------

@st.cache_resource
def get_config():
    return EventConfig(CONFIG_PATH)


@st.cache_resource
def get_sheets():
    """SheetsStore when a sheet key and credentials are available, else None."""
    cfg = get_config()
    if not cfg.google_sheet_key:
        return None
    if 'gcp_service_account' not in st.secrets:
        return None
    from jdcvo.store import SheetsStore
    return SheetsStore(cfg.google_sheet_key, dict(st.secrets['gcp_service_account']))


def get_writable_store():
    """Where admin edits go: the sheet if configured, else local JSON."""
    sheets = get_sheets()
    return sheets if sheets is not None else LocalStore(get_config().data_dir)


@st.cache_data(ttl=REFRESH_SECONDS, show_spinner="Updating scores...")
def get_results():
    """Run one scrape+score cycle (publish only when Sheets is configured)."""
    logs = []
    results = pipeline.run_pipeline(CONFIG_PATH, scrape=True,
                                    log=logs.append, sheets=get_sheets())
    return results, logs


def refresh_now():
    get_results.clear()


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
    if round_key == 'puttoff':
        return 'Putt-Off'
    if round_key == 'extras':
        return 'Extras'
    return cfg.round_config(round_key).get('name', f'Round {round_key}')


def fmt_pts(x):
    return f"{x:g}" if x == round(x, 2) else f"{x:.2f}"


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
            champion = champion_marker(cfg.players[pid])
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
            names = ', '.join(id_names[p] for p in e['players'])
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
        row['Putt-Off'] = e['round_scores'].get('puttoff', 0)
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
            st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
        return

    if round_key == 'puttoff':
        st.write("Putt-Off team result points.")
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
        row['Putt-Off'] = e['round_scores'].get('puttoff', 0)
        row['Extras'] = e['round_scores'].get('extras', 0)
        row['Total'] = e['total_points']
        rows.append(row)
    df = pd.DataFrame(sorted(rows, key=lambda r: -r['Total']))
    st.dataframe(df, width='stretch', hide_index=True)

    totals = {f'R{n}': df[f'R{n}'].sum() for n in cfg.round_numbers()}
    totals['Putt-Off'] = df['Putt-Off'].sum()
    totals['Extras'] = df['Extras'].sum()
    st.markdown("**Points by round:** " + " · ".join(
        f"{k}: {fmt_pts(v)}" for k, v in totals.items()))


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

    tab_status, tab_fix, tab_extras, tab_match, tab_putt, tab_adjust = st.tabs(
        ["Round status", "Fix a score", "Extras", "Match play", "Putt-off",
         "Adjustments"])

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
        existing = (store.read_corrections() if is_sheets
                    else local.read_corrections())
        if existing:
            st.markdown("**Existing corrections:**")
            st.dataframe(pd.DataFrame(existing), width='stretch',
                         hide_index=True)

    with tab_extras:
        players = sorted(p['name'] for p in cfg.players.values())
        round_opts = [str(n) for n in cfg.round_numbers()]

        st.markdown("**Add an extra**")
        st.caption("Chip-ins (2), longest drive (1), closest to pin (1), "
                   "hole-in-one bonus (8), or any custom amount.")
        player = st.selectbox("Player", players, key="ex_player")
        rn = st.selectbox("Round", round_opts, key="ex_round")
        category = st.selectbox("Category", [
            'chip_in (2)', 'longest_drive (1)', 'closest_to_pin (1)',
            'hole_in_one (8)'], key="ex_cat")
        default_pts = {'chip_in (2)': 2.0, 'longest_drive (1)': 1.0,
                       'closest_to_pin (1)': 1.0, 'hole_in_one (8)': 8.0,
                       }[category]
        points = st.number_input("Points", value=default_pts, step=0.5,
                                 key="ex_pts")
        note = st.text_input("Note", key="ex_note")
        if st.button("Add extras"):
            cat = category.split(' ')[0]
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
            edit_rows = []
            for e in existing:
                try:
                    pts = float(e['points'])
                except (TypeError, ValueError):
                    pts = 0.0
                edit_rows.append({
                    'Round': str(e['round']), 'Player': str(e['player']),
                    'Category': str(e['category']), 'Points': pts,
                    'Note': str(e.get('note', '') or '')})
            edited = st.data_editor(
                pd.DataFrame(edit_rows), key="ex_editor", width='stretch',
                hide_index=True, num_rows="dynamic",
                column_config={
                    'Round': st.column_config.SelectboxColumn(
                        options=round_editor_opts, required=True),
                    'Player': st.column_config.SelectboxColumn(
                        options=player_editor_opts, required=True),
                    'Category': st.column_config.TextColumn(required=True),
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
                    new_rows.append({
                        'round': str(r['Round']), 'player': str(r['Player']),
                        'category': ('' if pd.isna(r['Category'])
                                     else str(r['Category'])),
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
        std_cats = ['chip_in', 'longest_drive', 'closest_to_pin',
                    'hole_in_one']
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
            {'Round': round_label(cfg, rk), **{c: counts[rk][c] for c in cats}}
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
        st.markdown("Enter each pair's match-play points (both players in the "
                    "pair receive the amount).")
        players = sorted(p['name'] for p in cfg.players.values())
        p1 = st.selectbox("Player 1", players, key="mp_p1")
        p2 = st.selectbox("Player 2", players, key="mp_p2")
        pts = st.number_input("Points (0-5)", min_value=0.0, max_value=5.0,
                              step=0.25, key="mp_pts")
        if st.button("Save match-play result"):
            if p1 == p2:
                st.error("Pick two different players.")
            else:
                if is_sheets:
                    store.append_match_play(p1, p2, pts)
                else:
                    rows = local.read_match_play()
                    rows.append({'players': [p1, p2], 'points': pts})
                    local.write_match_play(rows)
                refresh_now()
                st.success(f"Saved: {p1} & {p2} → {pts:g} each")
                st.rerun()
        existing = store.read_match_play() if is_sheets else local.read_match_play()
        if existing:
            st.dataframe(pd.DataFrame(
                [{'Pair': ' & '.join(r['players']), 'Points': r['points']}
                 for r in existing]), width='stretch', hide_index=True)

    with tab_putt:
        teams = cfg.teams()
        first = st.selectbox("Winning team (2 pts/player)", teams, key="po_1")
        second = st.selectbox("Second place (1 pt/player)", teams, key="po_2")
        if st.button("Save putt-off result"):
            if first == second:
                st.error("Pick two different teams.")
            else:
                if is_sheets:
                    store.set_puttoff(first, second)
                else:
                    local.write_puttoff({'first': first, 'second': second})
                refresh_now()
                st.success(f"Saved: 1st {first}, 2nd {second}")
                st.rerun()
        current = store.read_puttoff() if is_sheets else local.read_puttoff()
        if current.get('first'):
            st.caption(f"Current: 1st {current['first']}, "
                       f"2nd {current.get('second', '-')}")

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
        existing = (store.read_adjustments() if is_sheets
                    else local.read_adjustments())
        if existing:
            st.dataframe(pd.DataFrame(existing), width='stretch',
                         hide_index=True)


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
        # content (which would restart the background music loop).
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
