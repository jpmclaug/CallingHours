from __future__ import annotations

import http.server
import socketserver
import os
import sys
import urllib.parse
import webbrowser
import threading
import re
import html
import json
import secrets
import difflib
from datetime import datetime, timezone
from typing import Any, Optional, Dict, List
import traceback

import requests
try:
    from curl_cffi import requests as c_requests
    HAS_CURL_CFFI = True
except ImportError:
    c_requests = None
    HAS_CURL_CFFI = False

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    BeautifulSoup = None
    HAS_BS4 = False

from google import genai
import database
import lastfm
import theaudiodb
import setlistfm
import spotify

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

def html_escape(text: Any) -> str:
    if text is None:
        return ""
    return html.escape(str(text))

# Initialize database
try:
    database.init_db()
    backend = database.get_backend_name()
    print(f"Database initialized successfully using {backend}.")
except Exception as e:
    print(f"Warning: Database initialization failed ({e}). Falling back to local SQLite.")
    try:
        database.init_db(database.DEFAULT_SQLITE_PATH)
    except Exception as sqle:
        print(f"SQLite fallback error: {sqle}")


DATABASE_PATH = os.environ.get('DATABASE_PATH')
PROMPTS_FILE = os.environ.get('PROMPTS_FILE_PATH') or os.path.join(SCRIPT_DIR, 'prompts.json')

def load_prompts():
    if os.path.exists(PROMPTS_FILE):
        try:
            with open(PROMPTS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return [{"name": "Default Analysis", "text": "Analyze the following lyrics for the song '{song}' by '{artist}'.\n\nLyrics:\n{lyrics_text}\n\nProvide an in-depth analysis of the themes, meaning, and poetic devices. Format your response with clear Markdown headings (e.g. ## Core Themes, ## Meaning & Interpretation, ## Poetic Devices) and bullet points so it is structured and easy to read."}]

def save_prompt(name, text):
    prompts = load_prompts()
    prompts.append({"name": name, "text": text})
    try:
        with open(PROMPTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(prompts, f, indent=4)
    except (OSError, IOError) as e:
        print(f"Warning: Could not save prompt to file ({e}).")

AVAILABLE_GEMINI_MODELS = [
    {
        "id": "gemini-3.8-flash",
        "name": "Gemini 3.8 Flash (Recommended - Most Intelligent)",
    },
    {
        "id": "gemini-3.5-flash-lite",
        "name": "Gemini 3.5 Flash-Lite (Fastest)",
    }
]
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"

# Load local secrets file if present (for local development)
_local_secrets = {}
try:
    import calling_hours_secrets
    for attr in ('GENIUS_CLIENT_ID', 'GENIUS_CLIENT_SECRET', 'GENIUS_ACCESS_TOKEN', 'GEMINI_API_KEY', 'GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GOOGLE_REDIRECT_URI', 'LASTFM_API_KEY', 'THEAUDIODB_API_KEY', 'SETLIST_FM_API_KEY', 'SPOTIFY_CLIENT_ID', 'SPOTIFY_CLIENT_SECRET', 'SPOTIFY_REDIRECT_URI'):
        if hasattr(calling_hours_secrets, attr):
            _local_secrets[attr] = getattr(calling_hours_secrets, attr)
except ImportError:
    pass

# Environment variables take precedence (standard for Cloud Run/Docker), falling back to local secrets
GENIUS_CLIENT_ID = os.environ.get('GENIUS_CLIENT_ID') or _local_secrets.get('GENIUS_CLIENT_ID')
GENIUS_CLIENT_SECRET = os.environ.get('GENIUS_CLIENT_SECRET') or _local_secrets.get('GENIUS_CLIENT_SECRET')
GENIUS_ACCESS_TOKEN = os.environ.get('GENIUS_ACCESS_TOKEN') or _local_secrets.get('GENIUS_ACCESS_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY') or _local_secrets.get('GEMINI_API_KEY')
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID') or _local_secrets.get('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET') or _local_secrets.get('GOOGLE_CLIENT_SECRET', '')
GOOGLE_REDIRECT_URI = os.environ.get('GOOGLE_REDIRECT_URI') or _local_secrets.get('GOOGLE_REDIRECT_URI', '')
LASTFM_API_KEY = os.environ.get('LASTFM_API_KEY') or _local_secrets.get('LASTFM_API_KEY', '')
THEAUDIODB_API_KEY = os.environ.get('THEAUDIODB_API_KEY') or _local_secrets.get('THEAUDIODB_API_KEY', '123')
SETLIST_FM_API_KEY = os.environ.get('SETLIST_FM_API_KEY') or _local_secrets.get('SETLIST_FM_API_KEY', '')
SPOTIFY_CLIENT_ID = os.environ.get('SPOTIFY_CLIENT_ID') or _local_secrets.get('SPOTIFY_CLIENT_ID', '')
SPOTIFY_CLIENT_SECRET = os.environ.get('SPOTIFY_CLIENT_SECRET') or _local_secrets.get('SPOTIFY_CLIENT_SECRET', '')
SPOTIFY_REDIRECT_URI = os.environ.get('SPOTIFY_REDIRECT_URI') or _local_secrets.get('SPOTIFY_REDIRECT_URI', '')

def build_theaudiodb_widget(
    artist: str,
    song: str,
    theaudiodb_data: Optional[Dict[str, Any]] = None
) -> str:
    if not artist and not song:
        return ""

    if not theaudiodb_data:
        return f'''
        <div class="audiodb-card" id="audiodb-intelligence-widget" style="margin-bottom: 20px;">
            <div class="audiodb-header">
                <div style="display: flex; align-items: center; gap: 8px;">
                    <span style="font-size: 1.2rem;">🎵</span>
                    <span style="font-weight: 700; color: #E1E8F0; font-family: 'Montserrat', sans-serif;">TheAudioDB Track Insights</span>
                </div>
            </div>
            <div style="font-size: 0.82rem; color: rgba(225, 232, 240, 0.5); font-style: italic; padding: 8px 0;">
                No additional track details found on TheAudioDB.
            </div>
        </div>
        '''

    data = theaudiodb_data
    track_title = html_escape(data.get("track") or song)
    album = html_escape(data.get("album") or "")
    genre = html_escape(data.get("genre") or "")
    style = html_escape(data.get("style") or "")
    mood = html_escape(data.get("mood") or "")
    theme = html_escape(data.get("theme") or "")
    tempo = data.get("tempo")
    key = html_escape(data.get("key") or "")
    open_key = html_escape(data.get("open_key") or "")
    time_sig = html_escape(data.get("time_signature") or "")
    duration_fmt = html_escape(data.get("duration_formatted") or "")
    description = html_escape(data.get("description") or "")
    thumb_url = html_escape(data.get("thumbnail_url") or "")
    music_vid_url = html_escape(data.get("music_vid_url") or "")
    vid_views = html_escape(data.get("music_vid_views_formatted") or "")
    vid_director = html_escape(data.get("music_vid_director") or "")
    spotify_id = html_escape(data.get("spotify_id") or "")

    # Attribute pills
    pills = []
    if tempo:
        pills.append(f'<span class="audiodb-pill tempo" title="Tempo / Beats per minute">⏱️ {tempo} BPM</span>')
    if key:
        key_label = f"🎹 Key: {key}" + (f" ({open_key})" if open_key else "")
        pills.append(f'<span class="audiodb-pill key" title="Musical Key">{key_label}</span>')
    if time_sig:
        pills.append(f'<span class="audiodb-pill" title="Time Signature">🎼 {time_sig}</span>')
    if duration_fmt:
        pills.append(f'<span class="audiodb-pill" title="Duration">⏳ {duration_fmt}</span>')
    if genre:
        pills.append(f'<span class="audiodb-pill" title="Genre">🎸 {genre}</span>')
    if mood:
        pills.append(f'<span class="audiodb-pill mood" title="Mood">🎨 {mood}</span>')
    if theme:
        pills.append(f'<span class="audiodb-pill" title="Theme">💡 {theme}</span>')
    if style and style.lower() != genre.lower():
        pills.append(f'<span class="audiodb-pill" title="Style">✨ {style}</span>')

    pills_html = " ".join(pills) if pills else ""

    # Audio Feature Meters
    meters = []
    feature_labels = [
        ("energy", "⚡ Energy", "#3B82F6"),
        ("danceability", "💃 Danceability", "#EC4899"),
        ("valence", "😊 Valence", "#10B981"),
        ("acousticness", "🎻 Acousticness", "#F59E0B"),
        ("liveness", "🎤 Liveness", "#8B5CF6"),
        ("speechiness", "🗣️ Speechiness", "#06B6D4"),
    ]
    for key_name, label, color in feature_labels:
        val = data.get(key_name)
        if val is not None and isinstance(val, (int, float)) and val > 0:
            meters.append(f'''
                <div class="audiodb-meter-item">
                    <div class="audiodb-meter-label">
                        <span>{label}</span>
                        <span style="font-weight: 700; color: #E1E8F0;">{int(val)}%</span>
                    </div>
                    <div class="audiodb-meter-bar-bg">
                        <div class="audiodb-meter-bar-fill" style="width: {min(max(int(val), 0), 100)}%; background: {color};"></div>
                    </div>
                </div>
            ''')
    meters_html = f'<div class="audiodb-meter-container">{"".join(meters)}</div>' if meters else ""

    # Story / Background
    story_html = ""
    if description:
        story_html = f'''
        <div style="margin-top: 14px;">
            <div style="font-size: 0.76rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: #93C5FD; margin-bottom: 6px;">📖 Story &amp; Background</div>
            <div class="audiodb-story-box">{description}</div>
        </div>
        '''

    # Media / Action Links
    media_links = []
    if music_vid_url:
        clean_vid_url = music_vid_url
        if clean_vid_url.lower().startswith("http://"):
            clean_vid_url = "https://" + clean_vid_url[7:]
        view_note = f" ({vid_views})" if vid_views else ""
        director_note = f" &bull; Dir: {vid_director}" if vid_director else ""
        media_links.append(f'<a href="{clean_vid_url}" target="_blank" rel="noopener noreferrer" class="pill-btn secondary" style="font-size: 0.76rem; padding: 4px 10px; display: inline-flex; align-items: center; gap: 6px; text-decoration: none;" title="Watch Music Video{director_note}">▶ Official Video{view_note}</a>')
    if spotify_id:
        media_links.append(f'<a href="https://open.spotify.com/track/{spotify_id}" target="_blank" rel="noopener noreferrer" class="pill-btn secondary" style="font-size: 0.76rem; padding: 4px 10px; text-decoration: none;" title="Open in Spotify">🎧 Spotify</a>')
    media_links_html = f'<div style="display: flex; gap: 8px; flex-wrap: wrap;">{" ".join(media_links)}</div>' if media_links else ""

    thumb_html = f'<img src="{thumb_url}" alt="{track_title}" style="width: 48px; height: 48px; border-radius: 8px; object-fit: cover; border: 1px solid rgba(165, 200, 255, 0.3); flex-shrink: 0;">' if thumb_url else ''
    album_info = f'<div style="font-size: 0.8rem; color: rgba(225, 232, 240, 0.7); margin-top: 2px;">Album: <strong style="color: #E1E8F0;">{album}</strong></div>' if album else ''

    return f'''
    <div class="audiodb-card" id="audiodb-intelligence-widget" style="margin-bottom: 20px;">
        <div class="audiodb-header">
            <div style="display: flex; align-items: center; gap: 12px;">
                {thumb_html}
                <div>
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <span style="font-size: 1.1rem;">🎵</span>
                        <span style="font-weight: 700; color: #E1E8F0; font-family: 'Montserrat', sans-serif;">TheAudioDB Track Insights</span>
                    </div>
                    {album_info}
                </div>
            </div>
            {media_links_html}
        </div>

        {f'<div style="margin-top: 12px; display: flex; flex-wrap: wrap; gap: 6px;">{pills_html}</div>' if pills_html else ''}
        {meters_html}
        {story_html}
    </div>
    '''

def build_lastfm_widget(
    artist: str,
    song: str,
    track_tags: Optional[List[Dict[str, Any]]] = None,
    artist_metadata: Optional[Dict[str, Any]] = None,
    has_api_key: bool = True
) -> str:
    if not artist and not song:
        return ""

    artist_esc = html_escape(artist)
    song_esc = html_escape(song)

    tags = track_tags or []
    art_meta = artist_metadata or {}
    art_tags = art_meta.get("tags") or []
    top_tracks = art_meta.get("top_tracks") or []

    if not has_api_key and not tags and not art_tags and not top_tracks:
        return f'''
        <div class="lastfm-card" style="margin-bottom: 20px;">
            <div class="lastfm-header">
                <div style="display: flex; align-items: center; gap: 8px;">
                    <span style="font-size: 1.2rem;">📻</span>
                    <span style="font-weight: 700; color: #E1E8F0; font-family: 'Montserrat', sans-serif;">Last.fm Music Intelligence</span>
                </div>
            </div>
            <div style="font-size: 0.85rem; color: rgba(225, 232, 240, 0.7); line-height: 1.5; padding: 6px 0;">
                Connect community tags and artist top tracks by adding <code>LASTFM_API_KEY</code> to <code>calling_hours_secrets.py</code> or environment variables.
                <a href="https://www.last.fm/api/account/create" target="_blank" rel="noopener noreferrer" style="color: #A5C8FF; text-decoration: underline; margin-left: 6px;">Get a free API key &rarr;</a>
            </div>
        </div>
        '''

    # 1. Track Tags Chips
    if tags:
        track_chips = []
        for t in tags[:10]:
            raw_name = str(t.get("name", "") or "").strip()
            name = html_escape(raw_name)
            count = t.get("count", 0)
            url = html_escape(t.get("url") or f"https://www.last.fm/tag/{urllib.parse.quote_plus(raw_name)}")
            badge = f'<span class="lastfm-chip-count">{count}</span>' if count > 0 else ''
            track_chips.append(f'<a href="{url}" target="_blank" rel="noopener noreferrer" class="lastfm-tag-chip track-tag" title="Last.fm tag: {name}">#{name}{badge}</a>')
        track_tags_html = " ".join(track_chips)
    else:
        track_tags_html = '<span style="font-size: 0.82rem; color: rgba(225, 232, 240, 0.5); font-style: italic;">No track tags found on Last.fm.</span>'

    # 2. Artist Tags Chips
    if art_tags:
        art_chips = []
        for t in art_tags[:8]:
            raw_name = str(t.get("name", "") or "").strip()
            name = html_escape(raw_name)
            url = html_escape(t.get("url") or f"https://www.last.fm/tag/{urllib.parse.quote_plus(raw_name)}")
            art_chips.append(f'<a href="{url}" target="_blank" rel="noopener noreferrer" class="lastfm-tag-chip artist-tag" title="Artist genre: {name}">#{name}</a>')
        artist_tags_html = " ".join(art_chips)
    else:
        artist_tags_html = '<span style="font-size: 0.82rem; color: rgba(225, 232, 240, 0.5); font-style: italic;">No artist genres found.</span>'

    # 3. Artist Top Tracks List
    top_tracks_rows = []
    if top_tracks:
        for t in top_tracks[:5]:
            rank = t.get("rank", 1)
            t_name = html_escape(t.get("name", ""))
            t_name_attr = html.escape(t.get("name", ""), quote=True)
            playcount = t.get("playcount", 0)
            listeners = t.get("listeners", 0)
            meta_parts = []
            if listeners:
                meta_parts.append(f"{listeners:,} listeners")
            elif playcount:
                meta_parts.append(f"{playcount:,} plays")
            meta_str = " &bull; ".join(meta_parts)
            top_tracks_rows.append(f'''
                <div class="lastfm-track-row">
                    <span class="lastfm-track-rank">{rank}</span>
                    <span class="lastfm-track-name" title="{t_name}">{t_name}</span>
                    <span class="lastfm-track-meta">{meta_str}</span>
                    <button type="button" class="lastfm-quick-load-btn" data-artist="{artist_esc}" data-track="{t_name_attr}" onclick="quickLoadTrack(this.dataset.artist, this.dataset.track)" title="Load lyrics for {t_name}">⚡ Load</button>
                </div>
            ''')
        top_tracks_html = "".join(top_tracks_rows)
    else:
        top_tracks_html = '<div style="font-size: 0.82rem; color: rgba(225, 232, 240, 0.5); font-style: italic; padding: 6px 0;">No top tracks found for artist.</div>'

    view_more_btn = f'''<button type="button" class="pill-btn secondary" style="font-size: 0.72rem; padding: 2px 8px;" data-artist="{artist_esc}" onclick="openArtistModal(this.dataset.artist)">View All Top Tracks &rarr;</button>''' if len(top_tracks) > 5 else ''

    return f'''
    <div class="lastfm-card" id="lastfm-intelligence-widget" style="margin-bottom: 20px;">
        <div class="lastfm-header">
            <div style="display: flex; align-items: center; gap: 8px;">
                <span style="font-size: 1.2rem;">📻</span>
                <span style="font-weight: 700; color: #E1E8F0; font-family: 'Montserrat', sans-serif;">Last.fm Music Intelligence</span>
            </div>
            <button type="button" class="pill-btn secondary" style="font-size: 0.75rem; padding: 4px 10px;" data-artist="{artist_esc}" onclick="openArtistModal(this.dataset.artist)">
                👤 {artist_esc} Profile
            </button>
        </div>

        <div style="margin-top: 10px;">
            <div style="font-size: 0.76rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: #A5C8FF; margin-bottom: 4px;">Track Top Tags</div>
            <div class="lastfm-tag-cloud">{track_tags_html}</div>
        </div>

        <div style="margin-top: 12px;">
            <div style="font-size: 0.76rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: #C5B8FF; margin-bottom: 4px;">Artist Genres &amp; Tags</div>
            <div class="lastfm-tag-cloud">{artist_tags_html}</div>
        </div>

        <div style="margin-top: 14px; border-top: 1px solid rgba(165, 200, 255, 0.12); padding-top: 10px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <span style="font-size: 0.76rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: #A5C8FF;">Top Tracks by {artist_esc}</span>
                {view_more_btn}
            </div>
            <div class="lastfm-tracks-list">
                {top_tracks_html}
            </div>
        </div>
    </div>
    '''


ACCESS_TOKEN = GENIUS_ACCESS_TOKEN
SERVER_PORT = None

GENIUS_AUTH_URL = 'https://api.genius.com/oauth/authorize'
GENIUS_TOKEN_URL = 'https://api.genius.com/oauth/token'
GENIUS_API_SEARCH_URL = 'https://api.genius.com/search'
GENIUS_WEB_SEARCH_URL = 'https://genius.com/api/search/multi'

GOOGLE_AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN_URL = 'https://oauth2.googleapis.com/token'
GOOGLE_USERINFO_URL = 'https://www.googleapis.com/oauth2/v3/userinfo'

# Cloud Run injects K_SERVICE and PORT (default 8080)
IS_CLOUD_RUN = bool(os.environ.get('K_SERVICE'))
PORT = int(os.environ.get('PORT', 8080 if IS_CLOUD_RUN else 8000))
HOST = os.environ.get('HOST', '0.0.0.0')
OAUTH_PORT = PORT

def get_genius_missing_message(action='authorize') -> str:
    if IS_CLOUD_RUN or not os.path.exists(os.path.join(SCRIPT_DIR, 'calling_hours_secrets.py')):
        return (
            '<div class="message">Genius credentials not found. '
            'In production, configure <code>GENIUS_ACCESS_TOKEN</code> (recommended) '
            'or <code>GENIUS_CLIENT_ID</code> and <code>GENIUS_CLIENT_SECRET</code> as environment variables.</div>'
        )
    verb = 'before authorizing' if action == 'authorizing' else 'and reload to authorize Genius'
    return f'<div class="message">Set your Genius credentials in calling_hours_secrets.py {verb}.</div>'

def get_gemini_missing_message() -> str:
    if IS_CLOUD_RUN or not os.path.exists(os.path.join(SCRIPT_DIR, 'calling_hours_secrets.py')):
        return (
            '<div class="message">Gemini API key not found. '
            'In production, configure <code>GEMINI_API_KEY</code> as an environment variable.</div>'
        )
    return '<div class="message">Set your GEMINI_API_KEY in calling_hours_secrets.py and reload to use analysis.</div>'

def get_google_redirect_uri(host_header: str | None = None) -> str:
    if GOOGLE_REDIRECT_URI:
        return GOOGLE_REDIRECT_URI
    if host_header:
        scheme = 'https' if (IS_CLOUD_RUN or 'run.app' in host_header) else 'http'
        return f'{scheme}://{host_header}/auth/google/callback'
    display_host = '127.0.0.1' if HOST == '0.0.0.0' else HOST
    return f'http://{display_host}:{SERVER_PORT or PORT}/auth/google/callback'

def parse_cookies(headers) -> Dict[str, str]:
    cookie_header = headers.get('Cookie') or headers.get('cookie') or ''
    cookies = {}
    for item in cookie_header.split(';'):
        if '=' in item:
            name, val = item.strip().split('=', 1)
            cookies[name.strip()] = val.strip()
    return cookies

def build_cookie_header(name: str, value: str, max_age: Optional[int] = 2592000, path: str = '/', http_only: bool = True, same_site: str = 'Lax', secure: bool = False) -> str:
    parts = [f"{name}={value}", f"Path={path}", f"SameSite={same_site}"]
    if max_age is not None:
        parts.append(f"Max-Age={max_age}")
    if http_only:
        parts.append("HttpOnly")
    if secure:
        parts.append("Secure")
    return "; ".join(parts)

def build_loading_overlay_html() -> str:
    return '''
    <!-- Action Loading & Anti-Interruption Overlay -->
    <div id="analysis-loading-overlay" class="analysis-loading-overlay" style="display: none;" aria-hidden="true" role="dialog" aria-modal="true" aria-labelledby="analysis-loading-title">
        <div class="analysis-loading-backdrop"></div>
        <div class="analysis-loading-modal">
            <div class="analysis-cosmic-spinner" aria-hidden="true">
                <div class="spinner-ring ring-1"></div>
                <div class="spinner-ring ring-2"></div>
                <div class="spinner-core" id="analysis-loading-icon">✦</div>
            </div>
            <h2 id="analysis-loading-title" class="analysis-loading-title">Analyzing with Gemini...</h2>
            <div class="analysis-loading-song" id="analysis-loading-song"></div>
            
            <div class="analysis-loading-bar-wrapper">
                <div class="analysis-loading-bar-inner"></div>
            </div>
            
            <div class="analysis-loading-status" id="analysis-loading-status">Connecting to Gemini AI...</div>
            
            <div class="analysis-loading-notice">
                <span class="notice-lock-icon">🔒</span>
                <span id="analysis-loading-notice-text">Please keep this page open. Leaving or navigating away will cancel the analysis.</span>
            </div>
            <div id="analysis-loading-dismiss-wrap" style="display: none; margin-top: 14px;">
                <button type="button" id="analysis-loading-dismiss-btn" onclick="hideActionLoadingOverlay()" style="background: transparent; border: 1px solid rgba(165, 200, 255, 0.3); color: #A5C8FF; padding: 6px 14px; border-radius: 6px; font-size: 0.78rem; cursor: pointer; font-family: inherit;">Dismiss Overlay</button>
            </div>
        </div>
    </div>
    '''

def build_app_header(active_page: str = 'song', user: Optional[Dict[str, Any]] = None) -> str:
    song_active = ' active' if active_page == 'song' else ''
    artist_active = ' active' if active_page == 'artist' else ''
    spotify_active = ' active' if active_page == 'spotify' else ''
    playlists_active = ' active' if active_page in ('playlist', 'playlists') else ''
    history_active = ' active' if active_page == 'history' else ''
    prompts_active = ' active' if active_page == 'prompts' else ''
    admin_active = ' active' if active_page == 'admin' else ''

    admin_nav_link = ''
    admin_bottom_nav_link = ''
    user_menu_html = ''
    if user:
        if user.get('is_admin'):
            admin_nav_link = f'''
                <a href="/admin" class="app-nav-link{admin_active}" id="nav-link-admin">
                    <span class="nav-icon">🛡️</span>
                    <span class="nav-text">Admin</span>
                </a>
            '''
            admin_bottom_nav_link = f'''
                <a href="/admin" class="app-bottom-nav-link{admin_active}" id="mobile-nav-link-admin">
                    <span class="nav-icon">🛡️</span>
                    <span class="nav-text">Admin</span>
                </a>
            '''

        display_name = html_escape(user.get('name') or user.get('email', '').split('@')[0])
        email = html_escape(user.get('email', ''))
        picture = user.get('picture')
        if picture and str(picture).strip():
            avatar_html = f'<img src="{html_escape(picture)}" class="user-avatar" alt="Avatar" referrerpolicy="no-referrer">'
        else:
            initial = (display_name[0] if display_name else 'U').upper()
            avatar_html = f'<div class="user-avatar-initial">{html_escape(initial)}</div>'

        admin_pill = '<span class="badge-role-admin">Admin</span>' if user.get('is_admin') else ''

        user_menu_html = f'''
            <div class="app-user-bar">
                <div class="user-profile-badge" title="{email}">
                    {avatar_html}
                    <span class="user-display-name">{display_name}</span>
                    {admin_pill}
                </div>
                <a href="/logout" class="btn-logout" title="Sign out">Sign Out</a>
            </div>
        '''

    return f'''
    <header class="app-header">
        <div class="app-header-inner">
            <div class="app-header-top">
                <a href="/" class="app-brand" title="Calling Hours">
                    <svg class="app-brand-logo" viewBox="0 0 24 24" aria-hidden="true">
                        <path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/>
                    </svg>
                    <span class="app-brand-text">Calling Hours</span>
                </a>
                {user_menu_html}
            </div>
            <nav class="app-nav" aria-label="Main Navigation">
                <a href="/" class="app-nav-link{song_active}" id="nav-link-song">
                    <span class="nav-icon">🎵</span>
                    <span class="nav-text">Song</span>
                </a>
                <a href="/artist" class="app-nav-link{artist_active}" id="nav-link-artist">
                    <span class="nav-icon">👤</span>
                    <span class="nav-text">Artists</span>
                </a>
                <a href="/spotify" class="app-nav-link{spotify_active}" id="nav-link-spotify">
                    <span class="nav-icon">🎧</span>
                    <span class="nav-text">Spotify</span>
                </a>
                <a href="/playlists" class="app-nav-link{playlists_active}" id="nav-link-playlists">
                    <span class="nav-icon">🎶</span>
                    <span class="nav-text">Playlists</span>
                </a>
                <a href="/history" class="app-nav-link{history_active}" id="nav-link-history">
                    <span class="nav-icon">📜</span>
                    <span class="nav-text"><span class="desktop-only-text">Search </span>History</span>
                </a>
                <a href="/prompts" class="app-nav-link{prompts_active}" id="nav-link-prompts">
                    <span class="nav-icon">⚙️</span>
                    <span class="nav-text">Prompts</span>
                </a>
                {admin_nav_link}
            </nav>
        </div>
    </header>
    <nav class="app-bottom-nav" aria-label="Mobile Navigation">
        <a href="/" class="app-bottom-nav-link{song_active}" id="mobile-nav-link-song">
            <span class="nav-icon">🎵</span>
            <span class="nav-text">Song</span>
        </a>
        <a href="/artist" class="app-bottom-nav-link{artist_active}" id="mobile-nav-link-artist">
            <span class="nav-icon">👤</span>
            <span class="nav-text">Artists</span>
        </a>
        <a href="/spotify" class="app-bottom-nav-link{spotify_active}" id="mobile-nav-link-spotify">
            <span class="nav-icon">🎧</span>
            <span class="nav-text">Spotify</span>
        </a>
        <a href="/playlists" class="app-bottom-nav-link{playlists_active}" id="mobile-nav-link-playlists">
            <span class="nav-icon">🎶</span>
            <span class="nav-text">Playlists</span>
        </a>
        <a href="/history" class="app-bottom-nav-link{history_active}" id="mobile-nav-link-history">
            <span class="nav-icon">📜</span>
            <span class="nav-text">History</span>
        </a>
        <a href="/prompts" class="app-bottom-nav-link{prompts_active}" id="mobile-nav-link-prompts">
            <span class="nav-icon">⚙️</span>
            <span class="nav-text">Prompts</span>
        </a>
        {admin_bottom_nav_link}
    </nav>
    {build_loading_overlay_html()}
    '''

PAGE_HTML = r'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
    <title>Calling Hours</title>
    <link rel="manifest" href="/manifest.json">
    <link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
    <link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">
    <link rel="icon" type="image/png" sizes="192x192" href="/icon-192.png">
    <link rel="icon" type="image/svg+xml" href="/favicon.svg">
    <meta name="mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="Calling Hours">
    <meta name="theme-color" content="#050A14">
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <script>
        // Track app header height for sticky workspace tabs and scrolling offset
        function updateAppHeaderHeight() {
            const header = document.querySelector('.app-header');
            if (header) {
                document.documentElement.style.setProperty('--app-header-height', header.offsetHeight + 'px');
            }
        }
        window.addEventListener('DOMContentLoaded', updateAppHeaderHeight);
        window.addEventListener('resize', updateAppHeaderHeight);
        window.addEventListener('orientationchange', updateAppHeaderHeight);

        // iOS PWA Standalone Mode link handler: ensures internal navigation stays inside standalone app
        if (('standalone' in window.navigator) && window.navigator.standalone) {
            document.addEventListener('click', function(e) {
                if (e.defaultPrevented) return;
                let node = e.target;
                while (node && node.nodeName !== 'A' && node.nodeName !== 'HTML') {
                    node = node.parentNode;
                }
                if (node && node.nodeName === 'A' && node.href && !node.getAttribute('target') && node.origin === window.location.origin) {
                    if (node.pathname === window.location.pathname && node.search === window.location.search && node.hash) {
                        return;
                    }
                    e.preventDefault();
                    window.location.href = node.href;
                }
            }, false);
        }

        // Global Action & Analysis Loading Overlay Management
        let globalActionStatusTimer = null;
        let globalActionDismissTimer = null;
        let isActionLoadingActive = false;

        const GLOBAL_DEFAULT_STATUS_STEPS = [
            'Connecting to music intelligence services...',
            'Retrieving and validating data...',
            'Finalizing response...'
        ];

        const GLOBAL_ANALYSIS_STATUS_STEPS = [
            'Connecting to Gemini AI...',
            'Reading lyrics structure and verse flow...',
            'Identifying poetic devices, metaphors & motifs...',
            'Analyzing emotional themes, tone & subtext...',
            'Synthesizing in-depth literary analysis...',
            'Finalizing formatted interpretation & insights...'
        ];

        function showActionLoadingOverlay(options = {}) {
            const overlay = document.getElementById('analysis-loading-overlay');
            if (!overlay) return;

            const titleEl = document.getElementById('analysis-loading-title');
            const songEl = document.getElementById('analysis-loading-song');
            const statusEl = document.getElementById('analysis-loading-status');
            const iconEl = document.getElementById('analysis-loading-icon');
            const noticeTextEl = document.getElementById('analysis-loading-notice-text');
            const dismissWrap = document.getElementById('analysis-loading-dismiss-wrap');

            const title = options.title || 'Processing Request...';
            const subtitle = options.subtitle || options.song || '';
            const icon = options.icon || '✦';
            const notice = options.notice || 'Please keep this page open. Leaving or navigating away will cancel the operation.';
            const steps = (options.statusSteps && options.statusSteps.length > 0)
                ? options.statusSteps
                : (options.status ? [options.status] : GLOBAL_DEFAULT_STATUS_STEPS);

            if (titleEl) titleEl.textContent = title;
            if (songEl) {
                songEl.textContent = subtitle;
                songEl.style.display = subtitle ? 'block' : 'none';
            }
            if (iconEl) iconEl.textContent = icon;
            if (noticeTextEl) noticeTextEl.textContent = notice;
            if (dismissWrap) dismissWrap.style.display = 'none';

            overlay.style.display = 'flex';
            overlay.setAttribute('aria-hidden', 'false');
            document.body.style.overflow = 'hidden';
            isActionLoadingActive = true;

            // Clear any active timers
            if (globalActionStatusTimer) {
                clearInterval(globalActionStatusTimer);
                globalActionStatusTimer = null;
            }
            if (globalActionDismissTimer) {
                clearTimeout(globalActionDismissTimer);
                globalActionDismissTimer = null;
            }

            if (statusEl && steps.length > 0) {
                statusEl.textContent = steps[0];
                statusEl.style.opacity = '1';
                let stepIdx = 0;
                globalActionStatusTimer = setInterval(() => {
                    stepIdx++;
                    if (stepIdx < steps.length) {
                        statusEl.style.opacity = '0';
                        setTimeout(() => {
                            statusEl.textContent = steps[stepIdx];
                            statusEl.style.opacity = '1';
                        }, 200);
                    }
                }, 2600);
            }

            // Safety escape hatch: show dismiss button if action takes over 24 seconds
            globalActionDismissTimer = setTimeout(() => {
                if (isActionLoadingActive && dismissWrap) {
                    dismissWrap.style.display = 'block';
                }
            }, 24000);

            try {
                history.pushState({ isActionLoading: true }, '');
            } catch (e) {}

            window.addEventListener('popstate', handleActionLoadingPopState);
        }

        function hideActionLoadingOverlay() {
            const overlay = document.getElementById('analysis-loading-overlay');
            if (overlay) {
                overlay.style.display = 'none';
                overlay.setAttribute('aria-hidden', 'true');
            }
            document.body.style.overflow = '';
            isActionLoadingActive = false;

            if (globalActionStatusTimer) {
                clearInterval(globalActionStatusTimer);
                globalActionStatusTimer = null;
            }
            if (globalActionDismissTimer) {
                clearTimeout(globalActionDismissTimer);
                globalActionDismissTimer = null;
            }

            const dismissWrap = document.getElementById('analysis-loading-dismiss-wrap');
            if (dismissWrap) dismissWrap.style.display = 'none';

            window.removeEventListener('popstate', handleActionLoadingPopState);
        }

        function handleActionLoadingPopState(e) {
            if (isActionLoadingActive) {
                const overlay = document.getElementById('analysis-loading-overlay');
                if (overlay && overlay.style.display !== 'none') {
                    history.pushState({ isActionLoading: true }, '');
                    alert('Action is currently in progress. Please stay on this page until it completes.');
                }
            }
        }

        function showAnalysisLoadingOverlay() {
            const artistInput = document.getElementById('artist');
            const songInput = document.getElementById('song');
            const artist = artistInput ? artistInput.value.trim() : '';
            const song = songInput ? songInput.value.trim() : '';
            let subtitle = 'Song Lyric Analysis';
            if (artist && song) {
                subtitle = `${artist} — ${song}`;
            } else if (song) {
                subtitle = song;
            }

            const btnSubmit = document.getElementById('btn-perform-analysis');
            if (btnSubmit) {
                btnSubmit.disabled = true;
                btnSubmit.style.opacity = '0.7';
                btnSubmit.style.cursor = 'not-allowed';
                btnSubmit.textContent = '⏳ Analyzing Lyrics...';
            }

            showActionLoadingOverlay({
                title: 'Analyzing with Gemini...',
                subtitle: subtitle,
                icon: '✦',
                statusSteps: (typeof ANALYSIS_STATUS_STEPS !== 'undefined') ? ANALYSIS_STATUS_STEPS : GLOBAL_ANALYSIS_STATUS_STEPS,
                notice: 'Please keep this page open. Leaving or navigating away will cancel the analysis.'
            });
        }

        function handleAnalysisPopState(e) {
            handleActionLoadingPopState(e);
        }

        // Expose globally on window
        window.showActionLoadingOverlay = showActionLoadingOverlay;
        window.hideActionLoadingOverlay = hideActionLoadingOverlay;
        window.showAnalysisLoadingOverlay = showAnalysisLoadingOverlay;

        // Prevent keyboard interruption (e.g. Esc or Enter repeats) while overlay is active
        document.addEventListener('keydown', function(e) {
            if (isActionLoadingActive) {
                if (e.key === 'Escape' || e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    e.stopPropagation();
                }
            }
        }, true);

        // Global form submission listeners
        document.addEventListener('submit', function(e) {
            if (e.defaultPrevented) return;
            const form = e.target;
            if (form && form.checkValidity && !form.checkValidity()) return;

            // 1. Explicit data-loading attribute on form
            const dataTitle = form.getAttribute('data-loading-title');
            if (dataTitle) {
                showActionLoadingOverlay({
                    title: dataTitle,
                    subtitle: form.getAttribute('data-loading-subtitle') || '',
                    icon: form.getAttribute('data-loading-icon') || '✦',
                    statusSteps: form.getAttribute('data-loading-steps') ? JSON.parse(form.getAttribute('data-loading-steps')) : null,
                    status: form.getAttribute('data-loading-status') || '',
                    notice: form.getAttribute('data-loading-notice') || ''
                });
                return;
            }

            // 2. Main song form (#search-form)
            if (form.id === 'search-form') {
                const artistInput = form.querySelector('#artist');
                const songInput = form.querySelector('#song');
                const artist = artistInput ? artistInput.value.trim() : '';
                const song = songInput ? songInput.value.trim() : '';
                showActionLoadingOverlay({
                    title: 'Finding Song Lyrics...',
                    subtitle: artist && song ? `${artist} — ${song}` : (artist || song || 'Music Databases'),
                    icon: '🎵',
                    statusSteps: [
                        'Searching Genius for song lyrics & annotations...',
                        'Checking LRCLIB for synchronized tracks...',
                        'Retrieving Last.fm tags & audio metadata...',
                        'Preparing lyrics reader & workspace...'
                    ],
                    notice: 'Please keep this page open. Searching online music databases.'
                });
                return;
            }

            // 3. Artist search form (.artist-search-form)
            if (form.classList && form.classList.contains('artist-search-form')) {
                const artistInput = form.querySelector('input[name="artist"]');
                const artistName = artistInput ? artistInput.value.trim() : '';
                showActionLoadingOverlay({
                    title: 'Exploring Artist Intelligence...',
                    subtitle: artistName,
                    icon: '👤',
                    statusSteps: [
                        'Querying Setlist.fm concert databases...',
                        'Scanning North Carolina tour history...',
                        'Retrieving Last.fm tags & top tracks...',
                        'Fetching TheAudioDB biography & discography...',
                        'Assembling unified artist profile...'
                    ],
                    notice: 'Please keep this page open. Gathering live tours and catalog intelligence.'
                });
                return;
            }

            // 4. Playlist generator form (#generator-form)
            if (form.id === 'generator-form') {
                const nameInput = form.querySelector('#input-playlist-name');
                const modeInput = form.querySelector('input[name="mode"]');
                const mode = modeInput ? modeInput.value : '';
                const plTitle = nameInput ? nameInput.value.trim() : '';
                showActionLoadingOverlay({
                    title: 'Generating Playlist...',
                    subtitle: plTitle || (mode ? `Mode: ${mode}` : 'Curating Tracklist'),
                    icon: '🎶',
                    statusSteps: [
                        'Analyzing generator criteria & filters...',
                        'Scanning concert setlists & popularity metrics...',
                        'Ranking and ordering tracks...',
                        'Compiling playlist overview...'
                    ],
                    notice: 'Please keep this page open while your playlist is generated.'
                });
                return;
            }

            // 5. Prompts save form (action contains /prompts/save)
            const action = form.getAttribute('action') || '';
            if (action.includes('/prompts/save')) {
                showActionLoadingOverlay({
                    title: 'Saving Gemini Prompt...',
                    subtitle: 'Updating instructions',
                    icon: '⚙️',
                    statusSteps: [
                        'Validating prompt structure...',
                        'Saving prompt to Calling Hours database...',
                        'Finalizing configuration...'
                    ],
                    notice: 'Please wait while prompt configuration is saved.'
                });
                return;
            }

            // 6. Admin forms (action contains /admin/user)
            if (action.includes('/admin/user')) {
                let op = 'Updating User Permissions...';
                if (action.includes('add')) op = 'Granting User Access...';
                else if (action.includes('toggle-role')) op = 'Updating User Role...';
                else if (action.includes('toggle-status')) op = 'Updating User Status...';
                else if (action.includes('delete')) op = 'Deleting User Account...';
                showActionLoadingOverlay({
                    title: op,
                    subtitle: 'Admin Operation',
                    icon: '🛡️',
                    statusSteps: [
                        'Submitting user change to database...',
                        'Refreshing authorizations...',
                        'Reloading user management...'
                    ],
                    notice: 'Please wait while user authorizations are updated.'
                });
                return;
            }
        }, false);

        // Global link & button click listeners
        document.addEventListener('click', function(e) {
            if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;

            const link = e.target.closest('a');
            const btn = e.target.closest('button');

            // 1. Explicit data-loading attribute on link or button
            const targetEl = link || btn;
            if (targetEl && targetEl.getAttribute('data-loading-title')) {
                showActionLoadingOverlay({
                    title: targetEl.getAttribute('data-loading-title'),
                    subtitle: targetEl.getAttribute('data-loading-subtitle') || targetEl.textContent.trim(),
                    icon: targetEl.getAttribute('data-loading-icon') || '✦',
                    statusSteps: targetEl.getAttribute('data-loading-steps') ? JSON.parse(targetEl.getAttribute('data-loading-steps')) : null,
                    status: targetEl.getAttribute('data-loading-status') || '',
                    notice: targetEl.getAttribute('data-loading-notice') || ''
                });
                return;
            }

            if (link) {
                if (link.target === '_blank') return;
                const href = link.getAttribute('href');
                if (!href || href === '#' || href.startsWith('javascript:')) return;

                try {
                    const url = new URL(link.href, window.location.origin);
                    if (url.origin !== window.location.origin) return;

                    // Skip file export downloads
                    if (url.searchParams.has('export') || url.pathname.includes('/export')) {
                        return;
                    }

                    // a) Auto-analyze links (?auto_analyze=1 or ?analyze=1)
                    if (url.searchParams.get('auto_analyze') === '1' || url.searchParams.get('analyze') === '1') {
                        const artist = url.searchParams.get('artist') || '';
                        const song = url.searchParams.get('song') || '';
                        showActionLoadingOverlay({
                            title: 'Launching Gemini Analysis...',
                            subtitle: artist && song ? `${artist} — ${song}` : (song || artist || 'Song Lyrics'),
                            icon: '✦',
                            statusSteps: GLOBAL_ANALYSIS_STATUS_STEPS,
                            notice: 'Please keep this page open. Lyrics are being retrieved and analyzed with Gemini.'
                        });
                        return;
                    }

                    // b) Explore artist profile (/artist?artist=...)
                    if (url.pathname === '/artist' && url.searchParams.has('artist')) {
                        const artist = url.searchParams.get('artist');
                        if (artist) {
                            showActionLoadingOverlay({
                                title: 'Exploring Artist Intelligence...',
                                subtitle: artist,
                                icon: '👤',
                                statusSteps: [
                                    'Querying Setlist.fm concert databases...',
                                    'Scanning North Carolina tour history...',
                                    'Retrieving Last.fm tags & top tracks...',
                                    'Fetching TheAudioDB biography & discography...',
                                    'Assembling unified artist profile...'
                                ],
                                notice: 'Please keep this page open. Assembling live tours and catalog intelligence.'
                            });
                            return;
                        }
                    }

                    // c) Load song by ID (/?id=...)
                    if (url.pathname === '/' && url.searchParams.has('id')) {
                        let label = link.textContent.trim();
                        if (label === 'Load Song' || label.startsWith('✦ View Analysis') || label.startsWith('Lyrics')) {
                            label = '';
                        }
                        showActionLoadingOverlay({
                            title: 'Loading Saved Song...',
                            subtitle: label,
                            icon: '📜',
                            statusSteps: [
                                'Retrieving cached lyrics and annotations...',
                                'Loading Gemini analysis results...',
                                'Opening workspace...'
                            ],
                            notice: 'Please wait while saved song analysis is loaded.'
                        });
                        return;
                    }

                    // d) Load song by artist & song (/?artist=...&song=...)
                    if (url.pathname === '/' && url.searchParams.has('artist') && url.searchParams.has('song')) {
                        const artist = url.searchParams.get('artist') || '';
                        const song = url.searchParams.get('song') || '';
                        showActionLoadingOverlay({
                            title: 'Fetching Song Lyrics...',
                            subtitle: `${artist} — ${song}`,
                            icon: '🎵',
                            statusSteps: [
                                'Searching Genius for song lyrics...',
                                'Retrieving Last.fm & AudioDB metadata...',
                                'Preparing lyrics reader...'
                            ],
                            notice: 'Please keep this page open while song lyrics are loaded.'
                        });
                        return;
                    }
                } catch (err) {}
            }
        }, false);
    </script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@700;800;900&family=Roboto+Condensed:wght@300;400;700&display=swap');

        :root {
            --app-header-height: 110px;
        }

        *, *::before, *::after {
            box-sizing: border-box;
        }

        html, body {
            max-width: 100vw;
            overflow-x: hidden;
        }

        body {
            margin: 0;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: flex-start;
            font-family: 'Roboto Condensed', sans-serif;
            background: linear-gradient(to bottom, #050A14 0%, #0B1E3F 100%);
            color: #E1E8F0;
            position: relative;
            padding: 0 0 40px 0;
        }

        /* Top App Header & Navigation */
        .app-header {
            position: sticky;
            top: 0;
            left: 0;
            width: 100%;
            z-index: 100;
            background: rgba(5, 10, 20, 0.88);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border-bottom: 1px solid rgba(165, 200, 255, 0.16);
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.5);
            margin-bottom: 28px;
            padding-top: max(0px, env(safe-area-inset-top));
        }

        .app-header-inner {
            width: min(1560px, 96vw);
            margin: 0 auto;
            padding: 12px 16px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            box-sizing: border-box;
        }

        .app-header-top {
            display: contents; /* On desktop, brand and user-bar act as direct flex items of app-header-inner */
        }

        .app-brand {
            display: flex;
            align-items: center;
            gap: 10px;
            text-decoration: none;
            color: #E1E8F0;
            font-family: 'Montserrat', sans-serif;
            font-weight: 800;
            font-size: 1.25rem;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            transition: opacity 0.2s ease;
            order: 1;
        }

        .app-brand:hover {
            opacity: 0.9;
        }

        .app-brand-logo {
            width: 24px;
            height: 24px;
            fill: #A5C8FF;
            filter: drop-shadow(0 0 8px rgba(165, 200, 255, 0.5));
            flex-shrink: 0;
        }

        .app-brand-text {
            white-space: nowrap;
        }

        .app-nav {
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
            order: 2;
            margin-left: auto;
        }

        .app-nav-link {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 8px 16px;
            border-radius: 20px;
            color: #A5C8FF;
            text-decoration: none;
            font-family: 'Montserrat', sans-serif;
            font-size: 0.82rem;
            font-weight: 700;
            letter-spacing: 0.04em;
            text-transform: uppercase;
            border: 1px solid rgba(165, 200, 255, 0.15);
            background: rgba(165, 200, 255, 0.05);
            transition: all 0.2s ease;
            white-space: nowrap;
        }

        .app-nav-link:hover {
            background: rgba(165, 200, 255, 0.18);
            color: #FFFFFF;
            border-color: rgba(165, 200, 255, 0.35);
        }

        .app-nav-link.active {
            background: #A5C8FF;
            color: #050A14;
            border-color: #A5C8FF;
            box-shadow: 0 0 14px rgba(165, 200, 255, 0.35);
        }

        /* User Profile & Navigation */
        .app-user-bar {
            display: flex;
            align-items: center;
            gap: 10px;
            order: 3;
            flex-shrink: 0;
        }
        .user-profile-badge {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 4px 10px 4px 6px;
            background: rgba(165, 200, 255, 0.08);
            border: 1px solid rgba(165, 200, 255, 0.2);
            border-radius: 20px;
            color: #E1E8F0;
            font-size: 0.85rem;
        }
        .user-avatar {
            width: 26px;
            height: 26px;
            border-radius: 50%;
            object-fit: cover;
            border: 1px solid rgba(165, 200, 255, 0.4);
        }
        .user-avatar-initial {
            width: 26px;
            height: 26px;
            border-radius: 50%;
            background: #194685;
            color: #A5C8FF;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 0.78rem;
            text-transform: uppercase;
        }
        .user-display-name {
            font-weight: 500;
            max-width: 140px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .badge-role-admin {
            background: rgba(255, 215, 0, 0.2);
            color: #FFD700;
            border: 1px solid rgba(255, 215, 0, 0.4);
            border-radius: 10px;
            padding: 1px 6px;
            font-size: 0.7rem;
            font-weight: 700;
            text-transform: uppercase;
        }
        .btn-logout {
            display: inline-flex;
            align-items: center;
            padding: 6px 12px;
            border-radius: 16px;
            background: transparent;
            color: rgba(225, 232, 240, 0.7);
            border: 1px solid rgba(225, 232, 240, 0.2);
            text-decoration: none;
            font-size: 0.8rem;
            font-weight: 600;
            transition: all 0.2s ease;
        }
        .btn-logout:hover {
            color: #f08c5a;
            border-color: rgba(240, 140, 90, 0.4);
            background: rgba(240, 140, 90, 0.08);
        }

        /* Mobile Bottom Navigation Bar (Hidden on desktop > 1080px) */
        .app-bottom-nav {
            display: none;
        }

        /* Analysis Loading & Non-Dismissible Overlay */
        .analysis-loading-overlay {
            position: fixed;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            z-index: 99999;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
            box-sizing: border-box;
            pointer-events: all;
            user-select: none;
            -webkit-user-select: none;
        }

        .analysis-loading-backdrop {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(5, 10, 20, 0.88);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
        }

        .analysis-loading-modal {
            position: relative;
            z-index: 2;
            width: min(460px, 92vw);
            background: rgba(11, 30, 63, 0.92);
            border: 1px solid rgba(165, 200, 255, 0.35);
            border-radius: 18px;
            box-shadow: 0 24px 60px rgba(0, 0, 0, 0.8), 0 0 35px rgba(165, 200, 255, 0.2);
            padding: 36px 26px;
            text-align: center;
            box-sizing: border-box;
            animation: modal-pop-in 0.3s cubic-bezier(0.16, 1, 0.3, 1);
        }

        @keyframes modal-pop-in {
            from {
                opacity: 0;
                transform: scale(0.92) translateY(12px);
            }
            to {
                opacity: 1;
                transform: scale(1) translateY(0);
            }
        }

        .analysis-cosmic-spinner {
            position: relative;
            width: 76px;
            height: 76px;
            margin: 0 auto 20px auto;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .spinner-ring {
            position: absolute;
            border-radius: 50%;
            border: 2px solid transparent;
        }

        .spinner-ring.ring-1 {
            width: 72px;
            height: 72px;
            border-top-color: #A5C8FF;
            border-right-color: rgba(165, 200, 255, 0.3);
            animation: ring-rotate 1.6s linear infinite;
            box-shadow: 0 0 16px rgba(165, 200, 255, 0.4);
        }

        .spinner-ring.ring-2 {
            width: 52px;
            height: 52px;
            border-bottom-color: #5af0a5;
            border-left-color: rgba(90, 240, 165, 0.25);
            animation: ring-rotate-rev 2.2s linear infinite;
            box-shadow: 0 0 12px rgba(90, 240, 165, 0.3);
        }

        .spinner-core {
            font-size: 1.6rem;
            color: #FFFFFF;
            text-shadow: 0 0 14px #A5C8FF, 0 0 24px #5af0a5;
            animation: core-pulse 1.8s ease-in-out infinite;
        }

        @keyframes ring-rotate {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
        }

        @keyframes ring-rotate-rev {
            from { transform: rotate(360deg); }
            to { transform: rotate(0deg); }
        }

        @keyframes core-pulse {
            0%, 100% { transform: scale(0.88); opacity: 0.8; }
            50% { transform: scale(1.15); opacity: 1; }
        }

        .analysis-loading-title {
            font-family: 'Montserrat', sans-serif;
            font-size: 1.35rem;
            font-weight: 800;
            letter-spacing: 0.04em;
            color: #FFFFFF;
            margin: 0 0 6px 0;
            text-shadow: 0 2px 10px rgba(0,0,0,0.6);
        }

        .analysis-loading-song {
            font-size: 0.95rem;
            color: #A5C8FF;
            margin-bottom: 22px;
            font-weight: 400;
            min-height: 1.2em;
        }

        .analysis-loading-bar-wrapper {
            width: 100%;
            height: 6px;
            background: rgba(165, 200, 255, 0.12);
            border-radius: 4px;
            overflow: hidden;
            margin-bottom: 18px;
            position: relative;
        }

        .analysis-loading-bar-inner {
            width: 45%;
            height: 100%;
            background: linear-gradient(90deg, #194685, #A5C8FF, #5af0a5);
            border-radius: 4px;
            position: absolute;
            animation: loading-bar-scan 2s ease-in-out infinite;
            box-shadow: 0 0 12px rgba(165, 200, 255, 0.6);
        }

        @keyframes loading-bar-scan {
            0% { left: -45%; }
            50% { left: 45%; }
            100% { left: 100%; }
        }

        .analysis-loading-status {
            font-size: 0.92rem;
            color: #E1E8F0;
            margin-bottom: 22px;
            font-weight: 400;
            min-height: 24px;
            transition: opacity 0.25s ease;
        }

        .analysis-loading-notice {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            background: rgba(240, 140, 90, 0.12);
            border: 1px solid rgba(240, 140, 90, 0.35);
            border-radius: 10px;
            padding: 10px 14px;
            font-size: 0.8rem;
            color: #ffb88c;
            line-height: 1.4;
            text-align: left;
        }

        .notice-lock-icon {
            font-size: 1.1rem;
            flex-shrink: 0;
        }

        #analysis-loading-dismiss-btn:hover {
            background: rgba(165, 200, 255, 0.18) !important;
            border-color: #A5C8FF !important;
        }

        /* Login Card & Auth Styles */
        .login-wrapper {
            position: relative;
            width: 100%;
            min-height: 80vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
            box-sizing: border-box;
            z-index: 10;
        }
        .login-card {
            width: min(500px, 94vw);
            background: rgba(11, 30, 63, 0.75);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border: 1px solid rgba(165, 200, 255, 0.25);
            border-radius: 16px;
            box-shadow: 0 16px 40px rgba(0, 0, 0, 0.6);
            padding: 36px 32px;
            text-align: center;
            box-sizing: border-box;
        }
        .login-logo {
            width: 48px;
            height: 48px;
            fill: #A5C8FF;
            filter: drop-shadow(0 0 12px rgba(165, 200, 255, 0.6));
            margin-bottom: 8px;
        }
        .login-title {
            font-family: 'Montserrat', sans-serif;
            font-weight: 900;
            font-size: 2rem;
            margin: 0;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: #E1E8F0;
        }
        .login-subtitle {
            margin: 6px 0 24px 0;
            color: #A5C8FF;
            font-size: 0.92rem;
        }
        .login-body {
            background: rgba(5, 10, 20, 0.5);
            border: 1px solid rgba(165, 200, 255, 0.15);
            border-radius: 12px;
            padding: 24px 20px;
            margin-bottom: 20px;
        }
        .login-heading {
            font-size: 1.3rem;
            margin: 0 0 8px 0;
            color: #E1E8F0;
        }
        .login-instruction {
            font-size: 0.9rem;
            color: rgba(225, 232, 240, 0.75);
            margin: 0 0 20px 0;
        }
        .btn-google-signin {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 12px;
            background: #FFFFFF;
            color: #1F1F1F;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            font-weight: 600;
            font-size: 0.95rem;
            padding: 12px 24px;
            border-radius: 24px;
            text-decoration: none;
            border: 1px solid #747775;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
            transition: all 0.2s ease;
            cursor: pointer;
            width: 100%;
            max-width: 320px;
            box-sizing: border-box;
            margin: 0 auto;
        }
        .btn-google-signin:hover {
            background: #F8FAFD;
            box-shadow: 0 4px 14px rgba(66, 133, 244, 0.35);
            transform: translateY(-1px);
        }
        .google-icon {
            width: 20px;
            height: 20px;
            flex-shrink: 0;
        }
        .google-config-notice {
            background: rgba(240, 140, 90, 0.1);
            border: 1px solid rgba(240, 140, 90, 0.3);
            border-radius: 10px;
            padding: 16px;
            color: #E1E8F0;
        }
        .google-config-notice h3 {
            margin-top: 0;
            color: #f08c5a;
            font-size: 1.05rem;
        }
        .google-config-notice p {
            font-size: 0.88rem;
            color: rgba(225, 232, 240, 0.8);
            margin: 6px 0;
        }
        .btn-dev-signin {
            display: inline-block;
            background: #194685;
            color: #FFFFFF;
            padding: 10px 20px;
            border-radius: 8px;
            text-decoration: none;
            font-weight: 700;
            font-size: 0.9rem;
            transition: all 0.2s ease;
        }
        .btn-dev-signin:hover {
            background: #2563EB;
        }
        .login-footer {
            font-size: 0.78rem;
            color: rgba(225, 232, 240, 0.4);
        }

        /* Admin Dashboard Styles */
        .admin-card {
            background: rgba(11, 30, 63, 0.65);
            border: 1px solid rgba(165, 200, 255, 0.2);
            border-radius: 12px;
            padding: 24px;
            box-sizing: border-box;
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
        }
        .admin-stat-card {
            background: rgba(11, 30, 63, 0.65);
            border: 1px solid rgba(165, 200, 255, 0.18);
            border-radius: 12px;
            padding: 18px 20px;
            text-align: center;
        }
        .stat-number {
            font-family: 'Montserrat', sans-serif;
            font-size: 2rem;
            font-weight: 800;
            color: #A5C8FF;
        }
        .stat-label {
            font-size: 0.82rem;
            color: rgba(225, 232, 240, 0.65);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-top: 4px;
        }
        .admin-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.9rem;
            text-align: left;
        }
        .admin-table th {
            padding: 12px 14px;
            border-bottom: 1px solid rgba(165, 200, 255, 0.25);
            color: #A5C8FF;
            font-family: 'Montserrat', sans-serif;
            font-weight: 700;
            font-size: 0.78rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        .admin-table td {
            padding: 14px;
            border-bottom: 1px solid rgba(165, 200, 255, 0.1);
            vertical-align: middle;
        }
        .admin-table tr:hover {
            background: rgba(165, 200, 255, 0.04);
        }
        .badge-active {
            display: inline-block;
            background: rgba(90, 240, 165, 0.15);
            color: #5af0a5;
            border: 1px solid rgba(90, 240, 165, 0.3);
            border-radius: 12px;
            padding: 2px 8px;
            font-size: 0.75rem;
            font-weight: 600;
        }
        .badge-suspended {
            display: inline-block;
            background: rgba(240, 140, 90, 0.15);
            color: #f08c5a;
            border: 1px solid rgba(240, 140, 90, 0.3);
            border-radius: 12px;
            padding: 2px 8px;
            font-size: 0.75rem;
            font-weight: 600;
        }
        .badge-role-user {
            display: inline-block;
            background: rgba(165, 200, 255, 0.1);
            color: #A5C8FF;
            border: 1px solid rgba(165, 200, 255, 0.25);
            border-radius: 12px;
            padding: 2px 8px;
            font-size: 0.75rem;
            font-weight: 600;
        }
        .btn-action-sm {
            padding: 5px 10px;
            font-size: 0.78rem;
            border-radius: 6px;
            border: 1px solid rgba(165, 200, 255, 0.25);
            background: rgba(165, 200, 255, 0.08);
            color: #E1E8F0;
            cursor: pointer;
            transition: all 0.2s ease;
        }
        .btn-action-sm:hover {
            background: rgba(165, 200, 255, 0.2);
            color: #FFFFFF;
        }
        .btn-action-danger {
            border-color: rgba(240, 140, 90, 0.3);
            color: #f08c5a;
        }
        .btn-action-danger:hover {
            background: rgba(240, 140, 90, 0.15);
            color: #ff9d6e;
        }

        .admin-add-user-form {
            display: grid;
            grid-template-columns: 2fr 1.5fr 1fr auto;
            gap: 12px;
            align-items: end;
        }
        @media (max-width: 768px) {
            .admin-add-user-form {
                grid-template-columns: 1fr;
                gap: 10px;
            }
        }


        /* Workspace Tabs (Mobile Segmented Control) */
        .workspace-tabs {
            display: none; /* Hidden on desktop (>1080px) */
            width: 100%;
            background: rgba(11, 30, 63, 0.8);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border: 1px solid rgba(165, 200, 255, 0.22);
            border-radius: 12px;
            padding: 5px;
            box-sizing: border-box;
            gap: 6px;
            margin-bottom: 20px;
            position: sticky;
            top: calc(var(--app-header-height, 110px) + 8px);
            z-index: 30;
            scroll-margin-top: calc(var(--app-header-height, 110px) + 12px);
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
        }

        .tab-btn {
            flex: 1;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            padding: 10px 10px;
            border: 1px solid transparent;
            border-radius: 8px;
            background: transparent;
            color: #A5C8FF;
            font-family: 'Montserrat', sans-serif;
            font-size: 0.82rem;
            font-weight: 700;
            letter-spacing: 0.04em;
            text-transform: uppercase;
            cursor: pointer;
            transition: all 0.2s ease;
            box-shadow: none;
            margin: 0;
            width: auto;
            min-height: 44px;
            touch-action: manipulation;
            -webkit-tap-highlight-color: transparent;
        }

        .tab-btn:hover {
            background: rgba(165, 200, 255, 0.12);
            color: #FFFFFF;
            transform: none;
            box-shadow: none;
        }

        .tab-btn.active {
            background: #A5C8FF;
            color: #050A14;
            font-weight: 800;
            box-shadow: 0 0 14px rgba(165, 200, 255, 0.35);
        }

        .tab-btn .tab-badge {
            font-size: 0.68rem;
            padding: 1px 6px;
            border-radius: 10px;
            background: rgba(5, 10, 20, 0.35);
            color: inherit;
        }

        .tab-btn.active .tab-badge {
            background: rgba(5, 10, 20, 0.2);
            color: #050A14;
        }

        /* Mobile flow navigation helpers */
        .mobile-flow-actions {
            display: none;
            margin-top: 20px;
            padding-top: 14px;
            border-top: 1px solid rgba(165, 200, 255, 0.12);
        }

        .flow-btn {
            width: 100%;
            padding: 14px;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            font-size: 0.92rem;
        }

        .flow-btn-back {
            display: none;
        }

        /* Starfield background */
        .stars {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background-image: 
                radial-gradient(1px 1px at 20px 30px, #ffffff, rgba(0,0,0,0)),
                radial-gradient(1px 1px at 40px 70px, #ffffff, rgba(0,0,0,0)),
                radial-gradient(1px 1px at 50px 160px, #ffffff, rgba(0,0,0,0)),
                radial-gradient(1.5px 1.5px at 90px 40px, #ffffff, rgba(0,0,0,0)),
                radial-gradient(2px 2px at 130px 80px, #A5C8FF, rgba(0,0,0,0)),
                radial-gradient(1px 1px at 160px 120px, #ffffff, rgba(0,0,0,0)),
                radial-gradient(1.5px 1.5px at 250px 50px, #A5C8FF, rgba(0,0,0,0)),
                radial-gradient(1px 1px at 300px 90px, #ffffff, rgba(0,0,0,0));
            background-repeat: repeat;
            background-size: 350px 250px;
            opacity: 0.65;
            z-index: 0;
            pointer-events: none;
        }

        /* Suburban horizon silhouette */
        .horizon {
            position: fixed;
            bottom: 0;
            left: 0;
            width: 100%;
            height: 20vh;
            background: url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 100" preserveAspectRatio="none"><path d="M0,100 L0,70 L20,70 L30,40 L60,40 L70,70 L100,70 L110,50 L140,50 L150,70 L180,70 L180,60 L200,60 L200,70 L250,70 L280,30 L320,30 L350,70 L400,70 L400,50 L450,50 L450,70 L500,70 L520,40 L560,40 L580,70 L650,70 L650,20 L680,20 L680,70 L750,70 L770,50 L800,50 L820,70 L880,70 L900,30 L940,30 L960,70 L1000,70 L1000,100 Z" fill="%2302040A"/></svg>') repeat-x bottom;
            background-size: auto 100%;
            z-index: 1;
            pointer-events: none;
        }

        .container {
            position: relative;
            z-index: 2;
            width: min(1560px, 96vw);
            display: flex;
            flex-direction: row;
            gap: 32px;
            padding: 36px;
            border-radius: 16px;
            background: rgba(5, 10, 20, 0.5);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid rgba(165, 200, 255, 0.12);
            box-shadow: 0 30px 60px rgba(0, 0, 0, 0.7), inset 0 1px 0 rgba(255, 255, 255, 0.05);
        }

        .form-section {
            flex: 0.9;
            min-width: 250px;
        }

        .lyrics-section {
            flex: 1.4;
            display: flex;
            flex-direction: column;
            min-width: 320px;
        }
        
        .analysis-section {
            flex: 1.7;
            display: flex;
            flex-direction: column;
            min-width: 340px;
        }

        h1 {
            margin-top: 0;
            font-family: 'Montserrat', sans-serif;
            font-size: 2.3rem;
            text-align: center;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            font-weight: 800;
            text-shadow: 0 4px 15px rgba(0,0,0,0.8);
            color: #E1E8F0;
        }

        p {
            line-height: 1.6;
            color: #A5C8FF;
            text-align: center;
            font-size: 1.05rem;
            font-weight: 300;
            margin-bottom: 24px;
        }

        label {
            display: block;
            margin: 20px 0 8px;
            font-weight: 400;
            color: #A5C8FF;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            font-size: 0.85rem;
        }

        input[type="text"], textarea, select {
            width: 100%;
            padding: 14px 16px;
            border: 1px solid rgba(165, 200, 255, 0.2);
            border-radius: 8px;
            background: rgba(11, 30, 63, 0.5);
            color: #E1E8F0;
            font-size: 1.05rem;
            font-family: inherit;
            box-sizing: border-box;
            transition: all 0.3s ease;
        }

        input[type="text"]:focus, textarea:focus, select:focus {
            outline: none;
            border-color: #A5C8FF;
            background: rgba(11, 30, 63, 0.8);
            box-shadow: 0 0 15px rgba(165, 200, 255, 0.25);
        }

        input[type="text"]::placeholder, textarea::placeholder {
            color: rgba(225, 232, 240, 0.3);
        }
        
        select {
            appearance: none;
            cursor: pointer;
        }
        
        select option {
            background: #0B1E3F;
            color: #E1E8F0;
        }

        button {
            width: 100%;
            margin-top: 32px;
            padding: 16px;
            border: none;
            border-radius: 8px;
            background: #A5C8FF;
            color: #050A14;
            font-family: 'Montserrat', sans-serif;
            font-size: 1.05rem;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            cursor: pointer;
            transition: all 0.3s ease;
            box-shadow: 0 0 25px rgba(165, 200, 255, 0.35);
        }

        button:hover {
            background: #C4DFFF;
            transform: translateY(-2px);
            box-shadow: 0 0 35px rgba(165, 200, 255, 0.5);
        }

        .message {
            margin-top: 24px;
            padding: 16px;
            border-radius: 8px;
            background: rgba(165, 200, 255, 0.08);
            border: 1px solid rgba(165, 200, 255, 0.25);
            color: #E1E8F0;
            white-space: pre-wrap;
            font-weight: 300;
            text-align: center;
        }

        .message a {
            color: #A5C8FF;
            font-weight: 400;
            text-decoration: none;
            border-bottom: 1px dotted #A5C8FF;
            transition: color 0.3s;
        }

        .message a:hover {
            color: #ffffff;
            border-bottom-color: #ffffff;
        }

        /* Section Header Bars & Controls */
        .section-header-bar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin: 20px 0 10px;
            min-height: 36px;
            gap: 8px;
            flex-wrap: wrap;
        }

        .section-title {
            font-weight: 700;
            color: #A5C8FF;
            letter-spacing: 0.06em;
            text-transform: uppercase;
            font-size: 0.85rem;
            margin: 0;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .view-controls {
            display: flex;
            align-items: center;
            gap: 6px;
            flex-wrap: wrap;
        }

        .pill-btn {
            background: rgba(165, 200, 255, 0.08);
            color: #A5C8FF;
            border: 1px solid rgba(165, 200, 255, 0.22);
            padding: 6px 14px;
            border-radius: 20px;
            font-size: 0.78rem;
            font-family: inherit;
            cursor: pointer;
            transition: all 0.2s ease;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            font-weight: 700;
            margin: 0;
            box-shadow: none;
            width: auto;
            line-height: 1.2;
        }

        .pill-btn:hover {
            background: rgba(165, 200, 255, 0.22);
            color: #FFFFFF;
            transform: none;
            box-shadow: 0 0 10px rgba(165, 200, 255, 0.2);
        }

        .pill-btn.active {
            background: #A5C8FF;
            color: #050A14;
            border-color: #A5C8FF;
            box-shadow: 0 0 12px rgba(165, 200, 255, 0.35);
        }

        .pill-btn.secondary {
            font-weight: 400;
            opacity: 0.85;
        }

        .pill-btn.secondary:hover {
            opacity: 1;
        }

        /* Lyrics Styling */
        .lyrics-box {
            flex-grow: 1;
            width: 100%;
            min-height: 480px;
            max-height: 72vh;
            overflow-y: auto;
            padding: 22px;
            border-radius: 8px;
            border: 1px solid rgba(165, 200, 255, 0.2);
            background: rgba(5, 10, 20, 0.65);
            color: #E1E8F0;
            font-family: inherit;
            font-size: 1.02rem;
            font-weight: 300;
            line-height: 1.7;
            white-space: pre-wrap;
            box-sizing: border-box;
            resize: vertical;
        }

        .lyrics-box:focus {
            outline: none;
            border-color: #A5C8FF;
            box-shadow: 0 0 12px rgba(165, 200, 255, 0.25);
        }

        .lyrics-reader-box {
            flex-grow: 1;
            width: 100%;
            min-height: 480px;
            max-height: 72vh;
            overflow-y: auto;
            padding: 22px;
            border-radius: 8px;
            border: 1px solid rgba(165, 200, 255, 0.2);
            background: rgba(5, 10, 20, 0.65);
            box-sizing: border-box;
            display: flex;
            flex-direction: column;
            gap: 14px;
        }

        .lyric-stanza {
            background: rgba(11, 30, 63, 0.35);
            border: 1px solid rgba(165, 200, 255, 0.09);
            border-radius: 8px;
            padding: 14px 18px;
            transition: all 0.2s ease;
        }

        .lyric-stanza:hover {
            background: rgba(11, 30, 63, 0.6);
            border-color: rgba(165, 200, 255, 0.25);
        }

        .lyric-line {
            font-size: 1.05rem;
            line-height: 1.75;
            color: #E1E8F0;
            font-weight: 300;
            letter-spacing: 0.01em;
        }

        .lyric-section-badge {
            display: inline-flex;
            align-items: center;
            align-self: flex-start;
            padding: 4px 12px;
            margin: 10px 0 2px 0;
            border-radius: 14px;
            background: rgba(165, 200, 255, 0.14);
            border: 1px solid rgba(165, 200, 255, 0.35);
            color: #C4DFFF;
            font-family: 'Montserrat', sans-serif;
            font-size: 0.74rem;
            font-weight: 800;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            box-shadow: 0 0 10px rgba(165, 200, 255, 0.12);
        }

        .backing-vocal {
            color: #A5C8FF;
            font-style: italic;
            opacity: 0.85;
        }

        /* Analysis Box & Cards */
        #analysis-result-wrapper {
            flex-grow: 1;
            min-height: 480px;
            max-height: 72vh;
            overflow-y: auto;
            padding-right: 4px;
        }

        .analysis-container {
            display: flex;
            flex-direction: column;
            gap: 14px;
        }

        .analysis-card {
            background: rgba(11, 30, 63, 0.42);
            border: 1px solid rgba(165, 200, 255, 0.18);
            border-radius: 10px;
            padding: 18px 22px;
            box-sizing: border-box;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.35);
            transition: all 0.25s ease;
        }

        .analysis-card:hover {
            border-color: rgba(165, 200, 255, 0.35);
            box-shadow: 0 6px 24px rgba(0, 0, 0, 0.5), 0 0 16px rgba(165, 200, 255, 0.08);
        }

        .analysis-card h1, .analysis-card h2, .analysis-card h3, .analysis-card h4 {
            font-family: 'Montserrat', sans-serif;
            margin-top: 0;
            margin-bottom: 12px;
            color: #FFFFFF;
            font-weight: 800;
            letter-spacing: 0.03em;
            text-align: left;
            text-transform: none;
            text-shadow: none;
        }

        .analysis-card h1 {
            font-size: 1.35rem;
            color: #A5C8FF;
            border-bottom: 1px solid rgba(165, 200, 255, 0.2);
            padding-bottom: 8px;
        }

        .analysis-card h2 {
            font-size: 1.18rem;
            color: #C4DFFF;
            border-bottom: 1px solid rgba(165, 200, 255, 0.16);
            padding-bottom: 6px;
        }

        .analysis-card h3 {
            font-size: 1.05rem;
            color: #A5C8FF;
        }

        .analysis-card p {
            font-size: 1.02rem;
            line-height: 1.7;
            color: #E1E8F0;
            font-weight: 300;
            text-align: left;
            margin: 0 0 10px 0;
        }

        .analysis-card p:last-child {
            margin-bottom: 0;
        }

        .analysis-card strong, .analysis-card b {
            color: #FFFFFF;
            font-weight: 700;
        }

        .analysis-card ul, .analysis-card ol {
            margin: 8px 0 12px 20px;
            padding: 0;
            color: #E1E8F0;
            font-size: 1rem;
            line-height: 1.65;
            font-weight: 300;
        }

        .analysis-card li {
            margin-bottom: 6px;
        }

        .analysis-card li::marker {
            color: #A5C8FF;
        }

        .analysis-card blockquote {
            margin: 10px 0;
            padding: 10px 16px;
            border-left: 3px solid #A5C8FF;
            background: rgba(165, 200, 255, 0.06);
            border-radius: 0 8px 8px 0;
            font-style: italic;
            color: #C4DFFF;
        }

        .analysis-card code {
            background: rgba(165, 200, 255, 0.12);
            padding: 2px 6px;
            border-radius: 4px;
            font-family: monospace;
            font-size: 0.9em;
            color: #A5C8FF;
        }

        .analysis-raw-box {
            white-space: pre-wrap;
            font-family: inherit;
            font-size: 0.95rem;
            line-height: 1.65;
            background: rgba(5, 10, 20, 0.65);
            border: 1px solid rgba(165, 200, 255, 0.2);
            border-radius: 8px;
            padding: 20px;
            color: #E1E8F0;
            max-height: 72vh;
            overflow-y: auto;
            box-sizing: border-box;
            margin: 0;
        }

        .analysis-form {
            margin-top: 16px;
        }

        /* Scrollbars */
        .lyrics-box::-webkit-scrollbar, .lyrics-reader-box::-webkit-scrollbar, #analysis-result-wrapper::-webkit-scrollbar, .analysis-raw-box::-webkit-scrollbar {
            width: 8px;
        }
        
        .lyrics-box::-webkit-scrollbar-track, .lyrics-reader-box::-webkit-scrollbar-track, #analysis-result-wrapper::-webkit-scrollbar-track, .analysis-raw-box::-webkit-scrollbar-track {
            background: rgba(11, 30, 63, 0.5);
            border-radius: 8px;
        }
        
        .lyrics-box::-webkit-scrollbar-thumb, .lyrics-reader-box::-webkit-scrollbar-thumb, #analysis-result-wrapper::-webkit-scrollbar-thumb, .analysis-raw-box::-webkit-scrollbar-thumb {
            background: rgba(165, 200, 255, 0.3);
            border-radius: 8px;
        }
        
        .lyrics-box::-webkit-scrollbar-thumb:hover, .lyrics-reader-box::-webkit-scrollbar-thumb:hover, #analysis-result-wrapper::-webkit-scrollbar-thumb:hover, .analysis-raw-box::-webkit-scrollbar-thumb:hover {
            background: rgba(165, 200, 255, 0.5);
        }

        /* Responsive & Mobile Enhancements */
        @media (max-width: 1080px) {
            body {
                padding: 0 0 calc(76px + env(safe-area-inset-bottom, 0px)) 0 !important;
            }

            .desktop-only-text {
                display: none;
            }

            .workspace-tabs {
                display: flex;
            }

            .mobile-flow-actions {
                display: block;
            }

            .flow-btn-back {
                display: inline-flex;
            }

            .container {
                flex-direction: column;
                gap: 16px;
                width: min(1560px, 94vw);
                padding: 24px;
            }

            /* Container data-active-tab controls which panel is displayed on mobile */
            .container[data-active-tab="search"] .tab-panel:not(#panel-search) {
                display: none !important;
            }
            .container[data-active-tab="search"] #panel-search {
                display: block !important;
            }

            .container[data-active-tab="lyrics"] .tab-panel:not(#panel-lyrics) {
                display: none !important;
            }
            .container[data-active-tab="lyrics"] #panel-lyrics {
                display: flex !important;
            }

            .container[data-active-tab="analysis"] .tab-panel:not(#panel-analysis) {
                display: none !important;
            }
            .container[data-active-tab="analysis"] #panel-analysis {
                display: flex !important;
            }

            .form-section, .lyrics-section, .analysis-section {
                min-width: 0;
                width: 100%;
            }

            /* Compact top header on mobile */
            .app-header {
                margin-bottom: 14px;
            }

            .app-header-inner {
                padding: 8px 14px;
                flex-direction: row;
                align-items: center;
                justify-content: space-between;
                gap: 8px;
                width: 100%;
                box-sizing: border-box;
                min-height: 48px;
            }

            .app-header-top {
                display: flex;
                align-items: center;
                justify-content: space-between;
                width: 100%;
                gap: 8px;
            }

            .app-brand {
                flex-shrink: 1;
                min-width: 0;
            }

            .app-brand-text {
                font-size: 1.05rem;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }

            .app-user-bar {
                margin-left: auto;
                flex-shrink: 0;
                gap: 6px;
                justify-content: flex-end;
            }

            .user-profile-badge {
                padding: 3px 8px 3px 5px;
                gap: 5px;
                font-size: 0.78rem;
            }

            .user-display-name {
                max-width: 80px;
            }

            .btn-logout {
                padding: 4px 8px;
                font-size: 0.75rem;
                white-space: nowrap;
                flex-shrink: 0;
            }

            /* Hide desktop navigation in top header on mobile */
            .app-header .app-nav {
                display: none !important;
            }

            /* Fixed Bottom Navigation Bar - Rendered directly in body, free from backdrop-filter containing block trap */
            .app-bottom-nav {
                position: fixed;
                bottom: 0;
                left: 0;
                right: 0;
                width: 100%;
                margin: 0;
                z-index: 9990;
                background: rgba(5, 10, 20, 0.96);
                backdrop-filter: blur(20px);
                -webkit-backdrop-filter: blur(20px);
                border-top: 1px solid rgba(165, 200, 255, 0.22);
                box-shadow: 0 -4px 24px rgba(0, 0, 0, 0.6);
                display: flex !important;
                align-items: center;
                justify-content: space-around;
                gap: 2px;
                padding: 6px 8px max(8px, env(safe-area-inset-bottom, 0px)) 8px;
                box-sizing: border-box;
                overflow: hidden;
            }

            .app-bottom-nav::-webkit-scrollbar {
                display: none;
            }

            .app-bottom-nav-link {
                flex: 1 1 0;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                gap: 2px;
                padding: 5px 2px;
                border-radius: 10px;
                color: #A5C8FF;
                background: transparent;
                border: 1px solid transparent;
                text-decoration: none;
                font-size: 0.66rem;
                letter-spacing: 0.02em;
                min-height: 48px;
                touch-action: manipulation;
                -webkit-tap-highlight-color: transparent;
                transition: all 0.15s ease;
            }

            .app-bottom-nav-link .nav-icon {
                font-size: 1.25rem;
                line-height: 1;
                display: block;
                transition: transform 0.15s ease;
            }

            .app-bottom-nav-link .nav-text {
                font-size: 0.65rem;
                font-weight: 700;
                line-height: 1.1;
                text-align: center;
                white-space: nowrap;
            }

            .app-bottom-nav-link:active {
                background: rgba(165, 200, 255, 0.15);
                transform: scale(0.95);
            }

            .app-bottom-nav-link.active {
                background: rgba(165, 200, 255, 0.16);
                border-color: rgba(165, 200, 255, 0.4);
                color: #FFFFFF;
                box-shadow: 0 0 12px rgba(165, 200, 255, 0.25);
            }

            .app-bottom-nav-link.active .nav-icon {
                transform: scale(1.12);
            }
        }

        @media (max-width: 768px) {

            .workspace-tabs {
                top: calc(var(--app-header-height, 50px) + 4px);
                scroll-margin-top: calc(var(--app-header-height, 50px) + 8px);
                margin-bottom: 14px;
                padding: 4px;
                border-radius: 12px;
                background: rgba(11, 30, 63, 0.88);
                backdrop-filter: blur(12px);
                -webkit-backdrop-filter: blur(12px);
            }

            .tab-btn {
                padding: 10px 6px;
                font-size: 0.78rem;
                min-height: 44px;
                gap: 4px;
                -webkit-tap-highlight-color: transparent;
            }

            .container {
                width: 100% !important;
                max-width: 100% !important;
                padding: 18px 12px !important;
                gap: 14px;
                border-radius: 12px;
                box-sizing: border-box;
            }

            .horizon {
                height: 10vh;
            }

            h1 {
                font-size: 1.6rem;
                letter-spacing: 0.08em;
                margin-bottom: 4px;
            }

            p {
                font-size: 0.95rem;
                margin-bottom: 18px;
            }

            label {
                margin: 14px 0 6px;
                font-size: 0.8rem;
            }

            input[type="text"], textarea, select {
                font-size: 16px; /* Prevents automatic iOS Safari zoom */
                padding: 12px 14px;
                border-radius: 6px;
            }

            button {
                margin-top: 20px;
                padding: 14px;
                font-size: 0.95rem;
                touch-action: manipulation;
            }

            .section-header-bar {
                margin: 10px 0 8px;
                gap: 8px;
            }

            .section-title {
                font-size: 0.82rem;
            }

            .view-controls {
                gap: 6px;
                width: 100%;
                justify-content: flex-start;
            }

            .pill-btn {
                padding: 8px 12px;
                font-size: 0.75rem;
                min-height: 38px;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                white-space: nowrap;
                touch-action: manipulation;
                -webkit-tap-highlight-color: transparent;
            }

            .lyrics-box, .lyrics-reader-box {
                min-height: 300px;
                max-height: 65vh;
                padding: 16px;
                -webkit-overflow-scrolling: touch;
            }

            .lyric-stanza {
                padding: 12px 14px;
                margin-bottom: 10px;
            }

            .lyric-line {
                font-size: 0.98rem;
                line-height: 1.65;
            }

            .lyric-section-badge {
                font-size: 0.7rem;
                padding: 3px 10px;
                margin: 8px 0 2px 0;
            }

            #analysis-result-wrapper {
                min-height: 300px;
                max-height: 65vh;
                -webkit-overflow-scrolling: touch;
            }

            .analysis-card {
                padding: 14px 16px;
                border-radius: 8px;
                margin-bottom: 10px;
            }

            .analysis-card h1 {
                font-size: 1.18rem;
                padding-bottom: 6px;
            }

            .analysis-card h2 {
                font-size: 1.08rem;
                padding-bottom: 4px;
            }

            .analysis-card h3 {
                font-size: 0.98rem;
            }

            .analysis-card p {
                font-size: 0.95rem;
                line-height: 1.6;
            }

            .analysis-card ul, .analysis-card ol {
                margin: 6px 0 10px 16px;
                font-size: 0.94rem;
            }

            .analysis-card li {
                margin-bottom: 5px;
            }

            .analysis-raw-box {
                padding: 14px;
                font-size: 0.88rem;
                max-height: 65vh;
                -webkit-overflow-scrolling: touch;
            }

            .message {
                margin-top: 16px;
                padding: 12px;
                font-size: 0.92rem;
                word-break: break-word;
            }
        }

        @media (max-width: 480px) {
            body {
                padding: 0 0 28px 0;
            }

            .app-header-inner {
                padding: 8px 10px;
                gap: 8px;
            }

            .app-brand-text {
                font-size: 0.95rem;
            }

            .app-brand-logo {
                width: 20px;
                height: 20px;
            }

            .user-display-name {
                max-width: 65px;
            }

            .btn-logout {
                padding: 4px 8px;
                font-size: 0.74rem;
            }

            .app-bottom-nav {
                gap: 2px;
            }

            .app-bottom-nav-link {
                padding: 5px 2px;
                font-size: 0.62rem;
                gap: 2px;
            }

            .container {
                padding: 14px 10px !important;
                border-radius: 10px;
            }

            h1 {
                font-size: 1.4rem;
                letter-spacing: 0.05em;
            }

            .workspace-tabs {
                top: calc(var(--app-header-height, 50px) + 4px);
                scroll-margin-top: calc(var(--app-header-height, 50px) + 8px);
                gap: 4px;
            }

            .tab-btn {
                padding: 10px 4px;
                font-size: 0.74rem;
                min-height: 44px;
                -webkit-tap-highlight-color: transparent;
            }

            .pill-btn {
                padding: 8px 12px;
                font-size: 0.74rem;
                min-height: 38px;
                -webkit-tap-highlight-color: transparent;
            }

            .lastfm-track-meta {
                display: none;
            }

            .lastfm-track-row {
                padding: 8px 10px;
                gap: 10px;
            }

            .lastfm-quick-load-btn {
                padding: 7px 12px;
                font-size: 0.78rem;
                min-height: 38px;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                touch-action: manipulation;
                -webkit-tap-highlight-color: transparent;
            }
        }

        /* Last.fm Music Intelligence Card & Badges */
        .lastfm-card {
            background: rgba(11, 30, 63, 0.75);
            border: 1px solid rgba(165, 200, 255, 0.22);
            border-radius: 12px;
            padding: 18px 20px;
            box-shadow: 0 6px 20px rgba(0, 0, 0, 0.35);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
        }

        .lastfm-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
            padding-bottom: 10px;
            border-bottom: 1px solid rgba(165, 200, 255, 0.12);
        }

        .lastfm-tag-cloud {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            align-items: center;
        }

        .lastfm-tag-chip {
            display: inline-flex;
            align-items: center;
            gap: 5px;
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 0.78rem;
            font-weight: 500;
            text-decoration: none;
            transition: all 0.2s ease;
        }

        .lastfm-tag-chip.track-tag {
            background: rgba(75, 140, 255, 0.18);
            border: 1px solid rgba(165, 200, 255, 0.35);
            color: #C4DFFF;
        }

        .lastfm-tag-chip.artist-tag {
            background: rgba(140, 100, 255, 0.18);
            border: 1px solid rgba(195, 175, 255, 0.35);
            color: #D8CEFF;
        }

        .lastfm-tag-chip:hover {
            transform: translateY(-1px);
            border-color: #A5C8FF;
            color: #FFFFFF;
            box-shadow: 0 2px 8px rgba(165, 200, 255, 0.3);
        }

        .lastfm-chip-count {
            font-size: 0.68rem;
            background: rgba(0, 0, 0, 0.3);
            color: rgba(225, 232, 240, 0.75);
            padding: 1px 5px;
            border-radius: 8px;
        }

        .lastfm-tracks-list {
            display: flex;
            flex-direction: column;
            gap: 6px;
        }

        .lastfm-track-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            padding: 7px 12px;
            background: rgba(5, 10, 20, 0.45);
            border-radius: 8px;
            border: 1px solid rgba(165, 200, 255, 0.1);
            font-size: 0.84rem;
            transition: background 0.15s ease, border-color 0.15s ease;
        }

        .lastfm-track-row:hover {
            background: rgba(25, 70, 133, 0.3);
            border-color: rgba(165, 200, 255, 0.3);
        }

        .lastfm-track-rank {
            font-weight: 700;
            color: #A5C8FF;
            min-width: 18px;
            flex-shrink: 0;
        }

        .lastfm-track-name {
            flex: 1 1 auto;
            min-width: 0;
            font-weight: 500;
            color: #E1E8F0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }

        .lastfm-track-meta {
            font-size: 0.74rem;
            color: rgba(225, 232, 240, 0.5);
            white-space: nowrap;
            flex-shrink: 0;
        }

        .lastfm-quick-load-btn {
            width: auto;
            margin: 0;
            flex-shrink: 0;
            box-shadow: none;
            background: linear-gradient(135deg, #194685, #2563EB);
            color: #FFFFFF;
            border: none;
            padding: 4px 10px;
            border-radius: 6px;
            font-family: inherit;
            font-size: 0.75rem;
            font-weight: 600;
            letter-spacing: normal;
            text-transform: none;
            line-height: 1.3;
            cursor: pointer;
            transition: all 0.2s ease;
            white-space: nowrap;
        }

        .lastfm-quick-load-btn:hover {
            background: linear-gradient(135deg, #2563EB, #4285F4);
            transform: scale(1.04);
            box-shadow: 0 2px 8px rgba(66, 133, 244, 0.4);
        }

        /* TheAudioDB Track Insights Card */
        .audiodb-card {
            background: rgba(11, 30, 63, 0.75);
            border: 1px solid rgba(165, 200, 255, 0.22);
            border-radius: 12px;
            padding: 18px 20px;
            box-shadow: 0 6px 20px rgba(0, 0, 0, 0.35);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            margin-bottom: 20px;
        }

        .audiodb-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
            padding-bottom: 10px;
            border-bottom: 1px solid rgba(165, 200, 255, 0.12);
            flex-wrap: wrap;
        }

        .audiodb-pill {
            display: inline-flex;
            align-items: center;
            gap: 5px;
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 0.78rem;
            font-weight: 500;
            background: rgba(37, 99, 235, 0.18);
            border: 1px solid rgba(165, 200, 255, 0.35);
            color: #C4DFFF;
        }

        .audiodb-pill.mood {
            background: rgba(236, 72, 153, 0.18);
            border-color: rgba(244, 114, 182, 0.35);
            color: #FBCFE8;
        }

        .audiodb-pill.tempo {
            background: rgba(16, 185, 129, 0.18);
            border-color: rgba(52, 211, 153, 0.35);
            color: #A7F3D0;
        }

        .audiodb-pill.key {
            background: rgba(245, 158, 11, 0.18);
            border-color: rgba(251, 191, 36, 0.35);
            color: #FDE68A;
        }

        .audiodb-meter-container {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
            gap: 8px;
            margin-top: 12px;
        }

        .audiodb-meter-item {
            background: rgba(5, 10, 20, 0.45);
            border-radius: 8px;
            padding: 8px 10px;
            border: 1px solid rgba(165, 200, 255, 0.1);
        }

        .audiodb-meter-label {
            font-size: 0.72rem;
            color: rgba(225, 232, 240, 0.7);
            display: flex;
            justify-content: space-between;
            margin-bottom: 4px;
        }

        .audiodb-meter-bar-bg {
            background: rgba(255, 255, 255, 0.1);
            height: 6px;
            border-radius: 3px;
            overflow: hidden;
        }

        .audiodb-meter-bar-fill {
            height: 100%;
            background: linear-gradient(90deg, #3B82F6, #10B981);
            border-radius: 3px;
        }

        .audiodb-story-box {
            margin-top: 6px;
            padding: 10px 14px;
            background: rgba(5, 10, 20, 0.4);
            border-left: 3px solid #60A5FA;
            border-radius: 0 8px 8px 0;
            font-size: 0.84rem;
            color: rgba(225, 232, 240, 0.9);
            line-height: 1.55;
            max-height: 180px;
            overflow-y: auto;
        }

        /* Artist Info Modal */
        .artist-modal-overlay {
            position: fixed;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            z-index: 9999;
            display: flex;
            align-items: center;
            justify-content: center;
            background: rgba(3, 7, 18, 0.75);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            cursor: pointer;
        }

        .artist-modal {
            position: relative;
            width: min(560px, 94vw);
            max-height: 85vh;
            overflow-y: auto;
            background: rgba(11, 30, 63, 0.95);
            border: 1px solid rgba(165, 200, 255, 0.35);
            border-radius: 16px;
            padding: 24px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.7);
            animation: modal-pop-in 0.25s cubic-bezier(0.16, 1, 0.3, 1);
            cursor: default;
        }

        .artist-modal-close {
            background: transparent;
            border: none;
            color: #A5C8FF;
            font-size: 1.6rem;
            cursor: pointer;
            line-height: 1;
            padding: 6px;
            width: auto;
            min-width: 44px;
            min-height: 44px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            margin: 0;
            box-shadow: none;
            transition: color 0.15s ease, transform 0.15s ease;
            touch-action: manipulation;
            -webkit-tap-highlight-color: transparent;
        }

        .artist-modal-close:hover {
            color: #FFFFFF;
            transform: scale(1.1);
            background: transparent;
            box-shadow: none;
        }
    </style>
</head>
<body>
    <div class="stars"></div>
    <div class="horizon"></div>
    <!-- Artist Info Modal -->
    <div id="artist-modal-overlay" class="artist-modal-overlay" style="display: none;" onclick="closeArtistModalOnBackdrop(event)">
        <div class="artist-modal" role="dialog" aria-modal="true" aria-labelledby="artist-modal-title">
            <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 16px; border-bottom: 1px solid rgba(165, 200, 255, 0.15); padding-bottom: 12px;">
                <div>
                    <h2 id="artist-modal-title" style="margin: 0; font-family: 'Montserrat', sans-serif; font-size: 1.35rem; color: #E1E8F0;">Artist Intelligence</h2>
                    <div id="artist-modal-subtitle" style="font-size: 0.85rem; color: #A5C8FF; margin-top: 4px;">Last.fm Top Tags &amp; Catalog</div>
                </div>
                <button type="button" class="artist-modal-close" onclick="closeArtistModal()" aria-label="Close modal">&times;</button>
            </div>
            <div id="artist-modal-content">
                <div style="text-align: center; color: #A5C8FF; padding: 30px;">Loading artist data...</div>
            </div>
        </div>
    </div>
    {app_header}
    <div class="container" data-active-tab="search">
        <!-- Mobile Workspace Segmented Tabs Bar -->
        <nav class="workspace-tabs" id="workspace-tabs" aria-label="Song Workspace Sections">
            <button type="button" class="tab-btn active" id="tab-btn-search" onclick="switchWorkspaceTab('search')" aria-selected="true">
                <span class="tab-icon">🔍</span>
                <span class="tab-text">Search</span>
            </button>
            <button type="button" class="tab-btn" id="tab-btn-lyrics" onclick="switchWorkspaceTab('lyrics')" aria-selected="false">
                <span class="tab-icon">📝</span>
                <span class="tab-text">Lyrics</span>
                <span class="tab-badge" id="lyrics-tab-badge" style="{lyrics_badge_display}">●</span>
            </button>
            <button type="button" class="tab-btn" id="tab-btn-analysis" onclick="switchWorkspaceTab('analysis')" aria-selected="false">
                <span class="tab-icon">🧠</span>
                <span class="tab-text">Analysis</span>
                <span class="tab-badge" id="analysis-tab-badge" style="{analysis_badge_display}">✦</span>
            </button>
        </nav>

        <div class="form-section tab-panel" id="panel-search">
            <h1>Calling Hours</h1>
            <p>Enter an artist and a song title, then submit to post the details.</p>

            <form method="post" action="/submit" id="search-form" onsubmit="onSearchSubmit()">
                <input type="hidden" name="refresh" id="force_refresh" value="0">

                <div id="band-history-group" style="{band_select_display} margin-bottom: 14px;">
                    <label for="band_select" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span>Previously Searched Artists &amp; Bands</span>
                        <span style="font-size: 0.72rem; color: #A5C8FF; background: rgba(165, 200, 255, 0.15); padding: 2px 8px; border-radius: 10px; font-weight: 400;">{band_count_text}</span>
                    </label>
                    <select id="band_select" onchange="onBandSelected(this.value)">
                        <option value="">-- Select a previously searched artist --</option>
                        {band_options}
                    </select>
                </div>

                <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 14px; margin-bottom: 6px;">
                    <label for="artist" style="margin: 0;">Artist Name</label>
                    <button type="button" id="btn-artist-profile" class="pill-btn secondary" style="font-size: 0.72rem; padding: 2px 8px; {artist_info_btn_display}" onclick="openArtistModal(document.getElementById('artist').value)">👤 Artist Profile</button>
                </div>
                <input type="text" id="artist" name="artist" placeholder="e.g. Adele" value="{artist_value}" list="bands-datalist" autocomplete="off" required oninput="onArtistInput(this.value)" onchange="onArtistChange(this.value)">
                <datalist id="bands-datalist">
                    {band_datalist_options}
                </datalist>

                <div id="band-songs-group" style="display: none; margin-top: 14px; margin-bottom: 8px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <label for="band_song_select" style="margin: 0;">Previous Songs</label>
                        <button type="button" id="btn-quick-load" class="pill-btn secondary" style="font-size: 0.7rem; padding: 2px 8px; display: none;" onclick="loadSelectedSavedSong()">⚡ Load Result</button>
                    </div>
                    <select id="band_song_select" onchange="onPreviousSongSelected(this)">
                        <option value="">-- Or pick a previous song --</option>
                    </select>
                </div>

                <label for="song">Song Title</label>
                <input type="text" id="song" name="song" placeholder="e.g. Hello" value="{song_value}" required>

                <button type="submit" id="btn-post-song">Post Song</button>
            </form>

            {message_block}
            
            <div style="text-align: center; margin-top: 30px; display: flex; justify-content: center; gap: 20px;">
                <a href="/history" style="color: #A5C8FF; text-decoration: none; border-bottom: 1px dotted #A5C8FF;">Search History</a>
                <a href="/prompts" style="color: #A5C8FF; text-decoration: none; border-bottom: 1px dotted #A5C8FF;">Manage Prompts</a>
            </div>
        </div>

        <div class="lyrics-section tab-panel" id="panel-lyrics" style="{lyrics_display}">
            <div class="section-header-bar">
                <span class="section-title">Lyrics</span>
                <div class="view-controls">
                    <button type="button" id="btn-lyrics-reader" class="pill-btn active" onclick="setLyricsMode('reader')">Reader View</button>
                    <button type="button" id="btn-lyrics-edit" class="pill-btn" onclick="setLyricsMode('edit')">Edit Lyrics</button>
                    <button type="button" id="btn-lyrics-format" class="pill-btn secondary" title="Add spacing between stanzas" onclick="autoFormatStanzas()">Auto-Space</button>
                </div>
            </div>
            <div id="lyrics-reader" class="lyrics-reader-box"></div>
            <textarea id="lyrics" class="lyrics-box" placeholder="Paste or edit lyrics here..." style="display: none;" oninput="updateLyricsReader()">{lyrics_text}</textarea>

            <div class="mobile-flow-actions" id="lyrics-mobile-actions">
                <button type="button" class="pill-btn flow-btn" onclick="switchWorkspaceTab('analysis')">
                    <span>⚡ Continue to Gemini Analysis</span> &rarr;
                </button>
            </div>
        </div>
        
        <div class="analysis-section tab-panel" id="panel-analysis" style="{analysis_display}">
            <div class="section-header-bar">
                <span class="section-title">Analysis</span>
                <div class="view-controls" id="analysis-controls" style="{analysis_controls_display}">
                    <button type="button" id="btn-copy-analysis" class="pill-btn secondary" onclick="copyAnalysis()">📋 Copy</button>
                    <button type="button" id="btn-raw-analysis" class="pill-btn secondary" onclick="toggleRawAnalysis()">Raw View</button>
                    <button type="button" class="pill-btn secondary" onclick="showAnalysisForm()">Re-Analyze</button>
                    <button type="button" class="pill-btn secondary flow-btn-back" onclick="switchWorkspaceTab('lyrics')">&larr; Lyrics</button>
                </div>
            </div>
            
            {theaudiodb_widget}
            {lastfm_widget}

            <div class="analysis-form" style="{analysis_form_display}">
                <form id="analyze-form" method="post" action="/analyze" onsubmit="return startAnalysisSubmit(event);">
                    <input type="hidden" name="artist" value="{artist_value}">
                    <input type="hidden" name="song" value="{song_value}">
                    <input type="hidden" id="form_lyrics" name="lyrics" value="{lyrics_text_attr}">
                    
                    <label for="model_name">Select Gemini Model</label>
                    <select name="model_name" id="model_name">
                        {model_options}
                    </select>

                    <label for="prompt_idx">Select Prompt</label>
                    <select name="prompt_idx" id="prompt_idx">
                        {prompt_options}
                    </select>
                    
                    <button type="submit" id="btn-perform-analysis" style="margin-top: 16px;">Perform Analysis</button>

                    <div style="margin-top: 14px; text-align: center;">
                        <button type="button" class="pill-btn secondary flow-btn-back" onclick="switchWorkspaceTab('lyrics')" style="width: auto; padding: 8px 16px;">&larr; Back to Lyrics</button>
                    </div>
                </form>
            </div>
            
            <div id="analysis-result-wrapper" style="{analysis_result_display}">
                <div id="analysis-formatted" class="analysis-container"></div>
                <pre id="analysis-raw" class="analysis-raw-box" style="display: none;">{analysis_result}</pre>
                
                <div style="margin-top: 20px; text-align: center; display: flex; justify-content: center; gap: 10px; flex-wrap: wrap;">
                    <button type="button" onclick="showAnalysisForm()" style="background: transparent; border: 1px solid rgba(165, 200, 255, 0.4); color: #A5C8FF; padding: 10px 18px; font-size: 0.9rem; cursor: pointer; border-radius: 6px; font-family: inherit; width: auto;">Perform Another Analysis</button>
                    <button type="button" class="flow-btn-back" onclick="switchWorkspaceTab('lyrics')" style="background: transparent; border: 1px solid rgba(165, 200, 255, 0.4); color: #A5C8FF; padding: 10px 18px; font-size: 0.9rem; cursor: pointer; border-radius: 6px; font-family: inherit; width: auto;">&larr; View Lyrics</button>
                </div>
            </div>
        </div>
    </div>

    <script>
        function escapeHtml(str) {
            return String(str)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#039;');
        }

        // Lyrics Reader Logic
        function renderLyricsHtml(text) {
            if (!text || !text.trim()) {
                return '<div style="color: rgba(225,232,240,0.5); font-style: italic; text-align: center; padding: 40px;">No lyrics available. Paste or edit lyrics in Edit mode.</div>';
            }
            const rawLines = text.replace(/\r\n/g, '\n').split('\n');
            const hasBlankLines = rawLines.some(l => l.trim() === '');
            const hasSectionTags = rawLines.some(l => /^\[.*?\]$/.test(l.trim()));
            
            let html = '';
            let currentStanzaLines = [];
            let linesInStanzaCount = 0;
            
            function flushStanza() {
                if (currentStanzaLines.length > 0) {
                    html += '<div class="lyric-stanza">';
                    for (const line of currentStanzaLines) {
                        html += `<div class="lyric-line">${line}</div>`;
                    }
                    html += '</div>';
                    currentStanzaLines = [];
                    linesInStanzaCount = 0;
                }
            }
            
            for (let i = 0; i < rawLines.length; i++) {
                const trimmed = rawLines[i].trim();
                
                // Section tags like [Verse 1], [Chorus]
                const sectionMatch = trimmed.match(/^\[(.*?)\]$/);
                if (sectionMatch) {
                    flushStanza();
                    const tag = escapeHtml(sectionMatch[1]);
                    html += `<div class="lyric-section-badge"><span>${tag}</span></div>`;
                    continue;
                }
                
                if (trimmed === '') {
                    flushStanza();
                    continue;
                }
                
                // If lyrics lack both blank lines and section tags, auto-group into 4 lines for readability
                if (!hasBlankLines && !hasSectionTags && linesInStanzaCount >= 4) {
                    flushStanza();
                }
                
                let formattedLine = escapeHtml(trimmed);
                // Backing vocals / parenthetical styling: (Yeah, yeah)
                formattedLine = formattedLine.replace(/\(([^)]+)\)/g, '<span class="backing-vocal">($1)</span>');
                currentStanzaLines.push(formattedLine);
                linesInStanzaCount++;
            }
            flushStanza();
            return html;
        }

        function updateLyricsReader() {
            const textarea = document.getElementById('lyrics');
            const reader = document.getElementById('lyrics-reader');
            if (textarea && reader) {
                reader.innerHTML = renderLyricsHtml(textarea.value);
            }
            updateWorkspaceBadges();
        }

        function setLyricsMode(mode) {
            const reader = document.getElementById('lyrics-reader');
            const textarea = document.getElementById('lyrics');
            const btnReader = document.getElementById('btn-lyrics-reader');
            const btnEdit = document.getElementById('btn-lyrics-edit');
            
            if (mode === 'reader') {
                updateLyricsReader();
                if (reader) reader.style.display = 'flex';
                if (textarea) textarea.style.display = 'none';
                if (btnReader) btnReader.classList.add('active');
                if (btnEdit) btnEdit.classList.remove('active');
            } else {
                if (reader) reader.style.display = 'none';
                if (textarea) {
                    textarea.style.display = 'block';
                    textarea.focus();
                }
                if (btnReader) btnReader.classList.remove('active');
                if (btnEdit) btnEdit.classList.add('active');
            }
        }

        function autoFormatStanzas() {
            const textarea = document.getElementById('lyrics');
            if (!textarea) return;
            const text = textarea.value.replace(/\r\n/g, '\n');
            if (!text.trim()) return;
            
            const lines = text.split('\n');
            const newLines = [];
            let count = 0;
            
            for (let i = 0; i < lines.length; i++) {
                const trimmed = lines[i].trim();
                if (/^\[.*?\]$/.test(trimmed)) {
                    if (newLines.length > 0 && newLines[newLines.length - 1] !== '') {
                        newLines.push('');
                    }
                    newLines.push(trimmed);
                    count = 0;
                } else if (trimmed === '') {
                    if (newLines.length > 0 && newLines[newLines.length - 1] !== '') {
                        newLines.push('');
                    }
                    count = 0;
                } else {
                    newLines.push(trimmed);
                    count++;
                    if (count >= 4 && i < lines.length - 1 && lines[i+1].trim() !== '') {
                        newLines.push('');
                        count = 0;
                    }
                }
            }
            textarea.value = newLines.join('\n');
            updateLyricsReader();
            setLyricsMode('reader');
        }

        // Analysis Formatting Logic
        function fallbackMarkdown(md) {
            if (!md) return '';
            let text = md.replace(/\r\n/g, '\n');
            text = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            
            text = text.replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>');
            text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
            
            text = text.replace(/^#### (.*$)/gim, '<h4>$1</h4>');
            text = text.replace(/^### (.*$)/gim, '<h3>$1</h3>');
            text = text.replace(/^## (.*$)/gim, '<h2>$1</h2>');
            text = text.replace(/^# (.*$)/gim, '<h1>$1</h1>');
            
            text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
            text = text.replace(/\*([^*]+)\*/g, '<em>$1</em>');
            text = text.replace(/^> (.*$)/gim, '<blockquote>$1</blockquote>');
            
            text = text.replace(/^\s*[-*]\s+(.*$)/gim, '<li>$1</li>');
            text = text.replace(/(<li>[\s\S]*?<\/li>)/g, '<ul>$1</ul>');
            text = text.replace(/<\/ul>\s*<ul>/g, '');
            
            const paras = text.split(/\n{2,}/);
            return paras.map(p => {
                p = p.trim();
                if (/^<(h[1-6]|ul|ol|blockquote|pre)/.test(p)) return p;
                return `<p>${p.replace(/\n/g, '<br>')}</p>`;
            }).join('\n');
        }

        function parseMarkdown(md) {
            if (window.marked && typeof window.marked.parse === 'function') {
                try {
                    return window.marked.parse(md);
                } catch (e) {
                    console.warn('Marked parse error, using fallback:', e);
                }
            }
            return fallbackMarkdown(md);
        }

        function renderAnalysisCards() {
            const rawEl = document.getElementById('analysis-raw');
            const formattedContainer = document.getElementById('analysis-formatted');
            if (!rawEl || !formattedContainer) return;
            const rawText = rawEl.textContent || '';
            if (!rawText.trim()) return;
            
            const parsedHtml = parseMarkdown(rawText);
            const temp = document.createElement('div');
            temp.innerHTML = parsedHtml;
            
            const headings = temp.querySelectorAll('h1, h2, h3, h4');
            if (headings.length === 0) {
                formattedContainer.innerHTML = `<div class="analysis-card">${parsedHtml}</div>`;
                return;
            }
            
            formattedContainer.innerHTML = '';
            let currentCard = null;
            
            Array.from(temp.childNodes).forEach(node => {
                if (node.nodeType === Node.TEXT_NODE && !node.textContent.trim()) {
                    return;
                }
                const isHeading = node.nodeType === Node.ELEMENT_NODE && ['H1', 'H2', 'H3', 'H4'].includes(node.tagName);
                if (isHeading || !currentCard) {
                    currentCard = document.createElement('div');
                    currentCard.className = 'analysis-card';
                    formattedContainer.appendChild(currentCard);
                }
                currentCard.appendChild(node.cloneNode(true));
            });
        }

        function copyAnalysis() {
            const rawEl = document.getElementById('analysis-raw');
            const btn = document.getElementById('btn-copy-analysis');
            if (!rawEl) return;
            const text = rawEl.textContent || '';

            function showSuccess() {
                if (btn) {
                    const original = btn.innerHTML;
                    btn.innerHTML = '✅ Copied!';
                    setTimeout(() => { btn.innerHTML = original; }, 2000);
                }
            }

            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(showSuccess).catch(() => {
                    fallbackCopyText(text);
                    showSuccess();
                });
            } else {
                fallbackCopyText(text);
                showSuccess();
            }
        }

        function fallbackCopyText(text) {
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.focus();
            ta.select();
            try {
                document.execCommand('copy');
            } catch (e) {
                console.warn('Fallback copy failed:', e);
            }
            document.body.removeChild(ta);
        }

        let isRawView = false;
        function toggleRawAnalysis() {
            const formatted = document.getElementById('analysis-formatted');
            const raw = document.getElementById('analysis-raw');
            const btn = document.getElementById('btn-raw-analysis');
            isRawView = !isRawView;
            if (isRawView) {
                if (formatted) formatted.style.display = 'none';
                if (raw) raw.style.display = 'block';
                if (btn) {
                    btn.classList.add('active');
                    btn.textContent = 'Formatted';
                }
            } else {
                if (formatted) formatted.style.display = 'flex';
                if (raw) raw.style.display = 'none';
                if (btn) {
                    btn.classList.remove('active');
                    btn.textContent = 'Raw View';
                }
            }
        }

        function showAnalysisForm() {
            const form = document.querySelector('.analysis-form');
            const resultWrapper = document.getElementById('analysis-result-wrapper');
            const controls = document.getElementById('analysis-controls');
            if (form) form.style.display = 'block';
            if (resultWrapper) resultWrapper.style.display = 'none';
            if (controls) controls.style.display = 'none';
        }

        function quickLoadTrack(artist, track) {
            closeArtistModal();
            if (typeof showActionLoadingOverlay === 'function') {
                showActionLoadingOverlay({
                    title: 'Finding Song Lyrics...',
                    subtitle: `${artist} — ${track}`,
                    icon: '🎵',
                    statusSteps: [
                        'Searching Genius for song lyrics...',
                        'Querying Last.fm & AudioDB catalog...',
                        'Preparing lyrics reader & analyzer...'
                    ],
                    notice: 'Please keep this page open while track data is loaded.'
                });
            }
            const artistInput = document.getElementById('artist');
            const songInput = document.getElementById('song');
            if (artistInput && songInput) {
                artistInput.value = artist;
                songInput.value = track;
                switchWorkspaceTab('search');
                const form = document.getElementById('search-form');
                if (form) {
                    if (typeof onSearchSubmit === 'function') onSearchSubmit();
                    form.submit();
                }
            } else {
                window.location.href = '/?artist=' + encodeURIComponent(artist) + '&song=' + encodeURIComponent(track);
            }
        }

        function openArtistModal(artistName) {
            if (!artistName || !artistName.trim()) return;
            const overlay = document.getElementById('artist-modal-overlay');
            const title = document.getElementById('artist-modal-title');
            const content = document.getElementById('artist-modal-content');
            if (!overlay || !content) return;
            if (title) title.textContent = artistName;
            overlay.style.display = 'flex';
            content.innerHTML = '<div style="text-align: center; color: #A5C8FF; padding: 30px;">Fetching Last.fm intelligence...</div>';

            fetch('/api/lastfm/artist?artist=' + encodeURIComponent(artistName))
                .then(r => r.json())
                .then(data => {
                    let html = `
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; background: rgba(165, 200, 255, 0.08); padding: 8px 12px; border-radius: 8px; border: 1px solid rgba(165, 200, 255, 0.2); flex-wrap: wrap; gap: 8px;">
                            <span style="font-size: 0.84rem; color: #A5C8FF;">Live tours, NC shows &amp; full catalog</span>
                            <a href="/artist?artist=${encodeURIComponent(artistName)}" class="pill-btn primary" style="font-size: 0.78rem; padding: 4px 12px; text-decoration: none; display: inline-flex; align-items: center; gap: 4px;">🌟 View Full Artist Page &rarr;</a>
                        </div>
                    `;
                    if (data.tags && data.tags.length > 0) {
                        html += '<div style="margin-bottom: 16px;">';
                        html += '<div style="font-size: 0.76rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: #C5B8FF; margin-bottom: 8px;">Top Genres &amp; Tags</div>';
                        html += '<div class="lastfm-tag-cloud">';
                        data.tags.forEach(t => {
                            const tagUrl = t.url || ('https://www.last.fm/tag/' + encodeURIComponent(t.name));
                            html += `<a href="${escapeHtml(tagUrl)}" target="_blank" class="lastfm-tag-chip artist-tag">#${escapeHtml(t.name)}</a> `;
                        });
                        html += '</div></div>';
                    }

                    if (data.top_tracks && data.top_tracks.length > 0) {
                        html += '<div>';
                        html += '<div style="font-size: 0.76rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: #A5C8FF; margin-bottom: 8px;">Top Tracks</div>';
                        html += '<div class="lastfm-tracks-list">';
                        data.top_tracks.forEach(t => {
                            const rank = t.rank || '';
                            const playText = t.listeners ? (Number(t.listeners).toLocaleString() + ' listeners') : (t.playcount ? Number(t.playcount).toLocaleString() + ' plays' : '');
                            html += `
                                <div class="lastfm-track-row">
                                    <span class="lastfm-track-rank">${rank}</span>
                                    <span class="lastfm-track-name" title="${escapeHtml(t.name)}">${escapeHtml(t.name)}</span>
                                    <span class="lastfm-track-meta">${playText}</span>
                                    <button type="button" class="lastfm-quick-load-btn" data-artist="${escapeHtml(artistName)}" data-track="${escapeHtml(t.name)}" onclick="quickLoadTrack(this.dataset.artist, this.dataset.track)">⚡ Analyze</button>
                                </div>
                            `;
                        });
                        html += '</div></div>';
                    } else if (!data.has_key) {
                        html += '<div style="color: #A5C8FF; font-size: 0.88rem; padding: 10px 0; line-height: 1.5;">Set <code>LASTFM_API_KEY</code> in <code>calling_hours_secrets.py</code> to browse Last.fm artist info.<br><a href="https://www.last.fm/api/account/create" target="_blank" style="color: #C4DFFF; text-decoration: underline; margin-top: 6px; display: inline-block;">Get a free API key &rarr;</a></div>';
                    } else {
                        html += '<div style="color: rgba(225, 232, 240, 0.6); font-style: italic; padding: 20px; text-align: center;">No Last.fm metadata found for this artist.</div>';
                    }
                    content.innerHTML = html;
                })
                .catch(err => {
                    content.innerHTML = '<div style="color: #ff6e6e; padding: 20px;">Failed to load artist details: ' + escapeHtml(err.message) + '</div>';
                });
        }

        function closeArtistModal() {
            const overlay = document.getElementById('artist-modal-overlay');
            if (overlay) overlay.style.display = 'none';
        }

        function closeArtistModalOnBackdrop(e) {
            if (e.target && e.target.id === 'artist-modal-overlay') {
                closeArtistModal();
            }
        }

        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                closeArtistModal();
            }
        });

        // Previous Bands & Songs handling
        function onBandSelected(band) {
            const artistInput = document.getElementById('artist');
            const songInput = document.getElementById('song');
            const artistBtn = document.getElementById('btn-artist-profile');
            if (artistInput) {
                const prev = artistInput.value.trim().toLowerCase();
                artistInput.value = band || '';
                if (songInput && band && band.trim().toLowerCase() !== prev) {
                    songInput.value = '';
                }
            }
            if (artistBtn) {
                artistBtn.style.display = (band && band.trim().length > 0) ? 'inline-block' : 'none';
            }
            fetchBandSongs(band);
        }

        let artistInputTimeout = null;
        function onArtistInput(val) {
            const bandSelect = document.getElementById('band_select');
            const artistBtn = document.getElementById('btn-artist-profile');
            const trimmed = (val || '').trim().toLowerCase();
            if (artistBtn) {
                artistBtn.style.display = (trimmed.length > 0) ? 'inline-block' : 'none';
            }
            if (bandSelect) {
                let matched = false;
                for (let i = 0; i < bandSelect.options.length; i++) {
                    if (bandSelect.options[i].value && bandSelect.options[i].value.toLowerCase() === trimmed) {
                        bandSelect.selectedIndex = i;
                        matched = true;
                        break;
                    }
                }
                if (!matched) {
                    bandSelect.selectedIndex = 0;
                }
            }

            clearTimeout(artistInputTimeout);
            artistInputTimeout = setTimeout(() => {
                fetchBandSongs(val);
            }, 250);
        }

        function onArtistChange(val) {
            clearTimeout(artistInputTimeout);
            fetchBandSongs(val);
        }

        function fetchBandSongs(band) {
            const songsGroup = document.getElementById('band-songs-group');
            const songSelect = document.getElementById('band_song_select');
            const quickLoadBtn = document.getElementById('btn-quick-load');
            const currentSongInput = document.getElementById('song');
            if (!songsGroup || !songSelect) return;

            if (!band || !band.trim()) {
                songsGroup.style.display = 'none';
                if (quickLoadBtn) quickLoadBtn.style.display = 'none';
                return;
            }

            fetch('/api/songs?artist=' + encodeURIComponent(band.trim()))
                .then(r => r.json())
                .then(data => {
                    if (data.songs && data.songs.length > 0) {
                        songSelect.innerHTML = '<option value="">-- Or pick a previous song (' + data.songs.length + ') --</option>';
                        const currentSongVal = (currentSongInput ? currentSongInput.value.trim().toLowerCase() : '');
                        let foundMatch = false;
                        data.songs.forEach(s => {
                            const opt = document.createElement('option');
                            opt.value = s.id;
                            let label = s.song;
                            if (s.has_analysis) {
                                label += ' ✦ (analyzed)';
                            } else if (s.has_lyrics) {
                                label += ' (cached)';
                            }
                            opt.textContent = label;
                            opt.dataset.song = s.song;
                            if (currentSongVal && s.song.trim().toLowerCase() === currentSongVal) {
                                opt.selected = true;
                                foundMatch = true;
                            }
                            songSelect.appendChild(opt);
                        });
                        songsGroup.style.display = 'block';
                        if (quickLoadBtn) {
                            quickLoadBtn.style.display = foundMatch ? 'inline-block' : 'none';
                        }
                    } else {
                        songsGroup.style.display = 'none';
                        if (quickLoadBtn) quickLoadBtn.style.display = 'none';
                    }
                })
                .catch(err => console.error('Error fetching band songs:', err));
        }

        function onPreviousSongSelected(selectEl) {
            const quickLoadBtn = document.getElementById('btn-quick-load');
            const songInput = document.getElementById('song');
            const selectedOpt = selectEl.options[selectEl.selectedIndex];
            if (!selectedOpt || !selectedOpt.value) {
                if (quickLoadBtn) quickLoadBtn.style.display = 'none';
                return;
            }
            if (songInput && selectedOpt.dataset.song) {
                songInput.value = selectedOpt.dataset.song;
            }
            if (quickLoadBtn) {
                quickLoadBtn.style.display = 'inline-block';
            }
        }

        function loadSelectedSavedSong() {
            const songSelect = document.getElementById('band_song_select');
            if (!songSelect || !songSelect.value) return;
            window.location.href = '/?id=' + encodeURIComponent(songSelect.value);
        }

        function forceRefreshSearch() {
            const refreshInput = document.getElementById('force_refresh');
            const form = document.getElementById('search-form');
            if (refreshInput && form) {
                refreshInput.value = '1';
                form.submit();
            }
        }

        function switchWorkspaceTab(tabName, shouldScroll = true) {
            const container = document.querySelector('.container');
            if (!container) return;
            
            container.setAttribute('data-active-tab', tabName);
            
            const tabBtns = document.querySelectorAll('.workspace-tabs .tab-btn');
            tabBtns.forEach(btn => {
                if (btn.id === 'tab-btn-' + tabName) {
                    btn.classList.add('active');
                    btn.setAttribute('aria-selected', 'true');
                } else {
                    btn.classList.remove('active');
                    btn.setAttribute('aria-selected', 'false');
                }
            });

            // If a panel was hidden with style="display: none;", unhide it so the active tab displays
            const panel = document.getElementById('panel-' + tabName);
            if (panel && panel.style.display === 'none') {
                panel.style.display = (tabName === 'search') ? 'block' : 'flex';
            }

            if (tabName === 'lyrics') {
                const textarea = document.getElementById('lyrics');
                if (textarea && !textarea.value.trim()) {
                    setLyricsMode('edit');
                }
            }

            if (shouldScroll && window.innerWidth <= 1080) {
                const tabsBar = document.getElementById('workspace-tabs');
                if (tabsBar) {
                    const header = document.querySelector('.app-header');
                    const headerHeight = header ? header.offsetHeight : 0;
                    const y = tabsBar.getBoundingClientRect().top + window.pageYOffset - headerHeight - 10;
                    window.scrollTo({ top: Math.max(0, y), behavior: 'smooth' });
                }
            }
        }

        function updateWorkspaceBadges() {
            const textarea = document.getElementById('lyrics');
            const lyricsBadge = document.getElementById('lyrics-tab-badge');
            if (lyricsBadge && textarea) {
                lyricsBadge.style.display = textarea.value.trim() ? 'inline-block' : 'none';
            }
            const rawEl = document.getElementById('analysis-raw');
            const analysisBadge = document.getElementById('analysis-tab-badge');
            if (analysisBadge && rawEl) {
                analysisBadge.style.display = rawEl.textContent.trim() ? 'inline-block' : 'none';
            }
        }

        let analysisStatusTimer = null;
        const ANALYSIS_STATUS_STEPS = [
            'Connecting to Gemini AI...',
            'Reading lyrics structure and verse flow...',
            'Identifying poetic devices, metaphors & motifs...',
            'Analyzing emotional themes, tone & subtext...',
            'Synthesizing in-depth literary analysis...',
            'Finalizing formatted interpretation & insights...'
        ];

        function onSearchSubmit() {
            const btn = document.getElementById('btn-post-song') || document.querySelector('#search-form button[type="submit"]');
            if (btn) {
                btn.textContent = '⏳ Finding Lyrics...';
                setTimeout(() => {
                    btn.style.opacity = '0.7';
                }, 50);
            }
            return true;
        }

        function startAnalysisSubmit(event) {
            const textarea = document.getElementById('lyrics');
            const formLyrics = document.getElementById('form_lyrics');
            if (textarea && formLyrics) {
                formLyrics.value = textarea.value;
            }

            const lyricsVal = textarea ? textarea.value.trim() : '';
            if (!lyricsVal) {
                alert('Please enter or paste lyrics in the lyrics box before running analysis.');
                if (event) {
                    event.preventDefault();
                }
                return false;
            }

            showAnalysisLoadingOverlay();
            return true;
        }

        function showAnalysisLoadingOverlay() {
            const overlay = document.getElementById('analysis-loading-overlay');
            const songEl = document.getElementById('analysis-loading-song');
            const statusEl = document.getElementById('analysis-loading-status');
            const btnSubmit = document.getElementById('btn-perform-analysis');
            const artistInput = document.getElementById('artist');
            const songInput = document.getElementById('song');

            const artist = artistInput ? artistInput.value.trim() : '';
            const song = songInput ? songInput.value.trim() : '';
            if (songEl) {
                if (artist && song) {
                    songEl.textContent = `${artist} — ${song}`;
                } else if (song) {
                    songEl.textContent = song;
                } else {
                    songEl.textContent = 'Song Lyric Analysis';
                }
            }

            if (btnSubmit) {
                btnSubmit.disabled = true;
                btnSubmit.style.opacity = '0.7';
                btnSubmit.style.cursor = 'not-allowed';
                btnSubmit.textContent = '⏳ Analyzing Lyrics...';
            }

            if (overlay) {
                overlay.style.display = 'flex';
                overlay.setAttribute('aria-hidden', 'false');
            }

            // Lock document scrolling so user cannot interact with page below
            document.body.style.overflow = 'hidden';

            let stepIndex = 0;
            if (statusEl) {
                statusEl.textContent = ANALYSIS_STATUS_STEPS[0];
                if (analysisStatusTimer) clearInterval(analysisStatusTimer);
                analysisStatusTimer = setInterval(() => {
                    stepIndex++;
                    if (stepIndex < ANALYSIS_STATUS_STEPS.length) {
                        statusEl.style.opacity = '0';
                        setTimeout(() => {
                            statusEl.textContent = ANALYSIS_STATUS_STEPS[stepIndex];
                            statusEl.style.opacity = '1';
                        }, 200);
                    }
                }, 2800);
            }

            try {
                history.pushState({ isAnalyzing: true }, '');
            } catch (e) {}

            window.addEventListener('popstate', handleAnalysisPopState);
        }

        function handleAnalysisPopState(e) {
            const overlay = document.getElementById('analysis-loading-overlay');
            if (overlay && overlay.style.display !== 'none') {
                history.pushState({ isAnalyzing: true }, '');
                alert('Gemini analysis is currently running. Please stay on this page until it completes.');
            }
        }

        // Initialize on page load
        document.addEventListener('DOMContentLoaded', () => {
            const artistInput = document.getElementById('artist');
            if (artistInput && artistInput.value.trim()) {
                const bandSelect = document.getElementById('band_select');
                if (bandSelect) {
                    const trimmed = artistInput.value.trim().toLowerCase();
                    for (let i = 0; i < bandSelect.options.length; i++) {
                        if (bandSelect.options[i].value && bandSelect.options[i].value.toLowerCase() === trimmed) {
                            bandSelect.selectedIndex = i;
                            break;
                        }
                    }
                }
                fetchBandSongs(artistInput.value.trim());
            }

            const textarea = document.getElementById('lyrics');
            if (textarea && textarea.value.trim()) {
                updateLyricsReader();
                setLyricsMode('reader');
            } else {
                setLyricsMode('edit');
            }
            renderAnalysisCards();
            updateWorkspaceBadges();

            // Auto-select workspace tab based on content
            const analysisWrapper = document.getElementById('analysis-result-wrapper');
            const hasAnalysis = analysisWrapper && analysisWrapper.style.display !== 'none' && analysisWrapper.textContent.trim() !== '';
            const hasLyrics = textarea && textarea.value.trim() !== '';

            let initialTab = 'search';
            if (hasAnalysis) {
                initialTab = 'analysis';
            } else if (hasLyrics) {
                initialTab = 'lyrics';
            }
            switchWorkspaceTab(initialTab, false);
            initMobileTabSwipes();
        });

        function initMobileTabSwipes() {
            let startX = 0;
            let startY = 0;
            let startTime = 0;
            const container = document.querySelector('.container');
            if (!container) return;

            const tabs = ['search', 'lyrics', 'analysis'];

            container.addEventListener('touchstart', (e) => {
                if (e.touches.length === 1) {
                    startX = e.touches[0].clientX;
                    startY = e.touches[0].clientY;
                    startTime = Date.now();
                }
            }, { passive: true });

            container.addEventListener('touchend', (e) => {
                if (window.innerWidth > 1080) return;
                if (!e.changedTouches || e.changedTouches.length === 0) return;

                const dx = e.changedTouches[0].clientX - startX;
                const dy = e.changedTouches[0].clientY - startY;
                const elapsed = Date.now() - startTime;

                if (elapsed > 550) return;
                if (Math.abs(dx) < 55 || Math.abs(dx) < Math.abs(dy) * 1.5) return;

                const target = e.target;
                if (target && (target.tagName === 'TEXTAREA' || target.tagName === 'INPUT' || target.isContentEditable)) {
                    return;
                }

                const currentTab = container.getAttribute('data-active-tab') || 'search';
                const idx = tabs.indexOf(currentTab);
                if (idx === -1) return;

                if (dx < 0 && idx < tabs.length - 1) {
                    switchWorkspaceTab(tabs[idx + 1]);
                } else if (dx > 0 && idx > 0) {
                    switchWorkspaceTab(tabs[idx - 1]);
                }
            }, { passive: true });
        }
    </script>
</body>
</html>'''

PROMPTS_PAGE_HTML = PAGE_HTML.split('<body>')[0] + '''<body>
    <div class="stars"></div>
    <div class="horizon"></div>
    {app_header}
    <div class="container" style="flex-direction: column; width: min(800px, 94vw);">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; flex-wrap: wrap; gap: 12px;">
            <h1 style="font-size: 2rem; margin: 0;">Manage Prompts</h1>
            <a href="/" style="color:#A5C8FF; text-decoration:none; font-size: 1rem; border-bottom: 1px dotted #A5C8FF;">&larr; Back to Song</a>
        </div>
        
        <div style="{message_display}">
            {message_block}
        </div>

        <form method="post" action="/prompts/save">
            <label for="prompt_name">Prompt Name</label>
            <input type="text" id="prompt_name" name="prompt_name" placeholder="e.g. Poetic Devices" required>
            
            <label for="prompt_text">Prompt Text (Use {song}, {artist}, {lyrics_text} as placeholders)</label>
            <textarea id="prompt_text" name="prompt_text" rows="5" required style="resize: vertical;"></textarea>
            
            <button type="submit" style="margin-top: 16px;">Save Prompt</button>
        </form>

        <h2 style="margin-top: 40px; color:#E1E8F0; text-align: center; font-weight: 300;">Saved Prompts</h2>
        <div style="display:flex; flex-direction:column; gap:16px;">
            {prompts_list}
        </div>
    </div>
</body>
</html>'''

HISTORY_PAGE_HTML = PAGE_HTML.split('<body>')[0] + '''<body>
    <div class="stars"></div>
    <div class="horizon"></div>
    <!-- Artist Info Modal -->
    <div id="artist-modal-overlay" class="artist-modal-overlay" style="display: none;" onclick="closeArtistModalOnBackdrop(event)">
        <div class="artist-modal" role="dialog" aria-modal="true" aria-labelledby="artist-modal-title">
            <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 16px; border-bottom: 1px solid rgba(165, 200, 255, 0.15); padding-bottom: 12px;">
                <div>
                    <h2 id="artist-modal-title" style="margin: 0; font-family: 'Montserrat', sans-serif; font-size: 1.35rem; color: #E1E8F0;">Artist Intelligence</h2>
                    <div id="artist-modal-subtitle" style="font-size: 0.85rem; color: #A5C8FF; margin-top: 4px;">Last.fm Top Tags &amp; Catalog</div>
                </div>
                <button type="button" class="artist-modal-close" onclick="closeArtistModal()" aria-label="Close modal">&times;</button>
            </div>
            <div id="artist-modal-content">
                <div style="text-align: center; color: #A5C8FF; padding: 30px;">Loading artist data...</div>
            </div>
        </div>
    </div>
    {app_header}
    <style>
        @media (max-width: 768px) {
            .history-card {
                flex-direction: column !important;
                align-items: flex-start !important;
                gap: 12px !important;
                padding: 14px !important;
            }
            .history-card > div:last-child {
                width: 100%;
                display: flex;
                justify-content: space-between;
                align-items: center;
                border-top: 1px solid rgba(165, 200, 255, 0.1);
                padding-top: 10px;
                margin-top: 4px;
            }
            .history-card > div:last-child a:first-child {
                flex: 1;
                text-align: center;
                padding: 9px 16px !important;
            }
            .artist-modal {
                padding: 16px !important;
                width: 95vw !important;
                max-height: 90vh !important;
            }
        }
    </style>
    <div class="container" style="flex-direction: column; width: min(880px, 95vw);">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; flex-wrap: wrap; gap: 12px;">
            <h1 style="font-size: 2rem; margin: 0; text-align: left;">Search History</h1>
            <a href="/" style="color:#A5C8FF; text-decoration:none; font-size: 1rem; border-bottom: 1px dotted #A5C8FF;">&larr; Back to Song</a>
        </div>

        <div style="margin-bottom: 16px; display: flex; gap: 12px; flex-wrap: wrap; align-items: center;">
            <div style="flex: 1; min-width: 260px;">
                <input type="text" id="history-filter" placeholder="Filter history by artist or song title..." oninput="filterHistory(this.value)">
            </div>
            <div style="min-width: 220px;">
                <select id="history-artist-filter" onchange="filterByArtist(this.value)" style="width: 100%; padding: 12px 14px; background: rgba(11, 30, 63, 0.7); border: 1px solid rgba(165, 200, 255, 0.3); border-radius: 8px; color: #E1E8F0; font-size: 0.95rem;">
                    <option value="">All Artists ({total_artists_count})</option>
                    {artist_filter_options}
                </select>
            </div>
        </div>

        <div id="history-artist-chips" style="{artist_chips_display} margin-bottom: 22px;">
            <div style="font-size: 0.76rem; text-transform: uppercase; letter-spacing: 0.07em; color: #A5C8FF; margin-bottom: 8px; font-weight: 600;">
                Previously Searched Artists
            </div>
            <div style="display: flex; gap: 8px; flex-wrap: wrap; align-items: center;">
                <button type="button" class="artist-chip" id="chip-all" onclick="filterByArtist('')" style="{all_chip_active_style} border: 1px solid; padding: 5px 12px; border-radius: 20px; font-size: 0.82rem; cursor: pointer; transition: all 0.2s ease;">
                    All ({total_songs_count})
                </button>
                {artist_chips_html}
            </div>
        </div>

        <div id="history-list" style="display: flex; flex-direction: column; gap: 14px;">
            {history_list}
        </div>
    </div>

    <script>
        let activeArtistFilter = '{initial_artist_filter}';

        function escapeHtml(str) {
            if (!str) return '';
            return String(str)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#039;');
        }

        function quickLoadTrack(artist, track) {
            window.location.href = '/?artist=' + encodeURIComponent(artist) + '&song=' + encodeURIComponent(track);
        }

        function openArtistModal(artistName) {
            if (!artistName || !artistName.trim()) return;
            const overlay = document.getElementById('artist-modal-overlay');
            const title = document.getElementById('artist-modal-title');
            const content = document.getElementById('artist-modal-content');
            if (!overlay || !content) return;
            if (title) title.textContent = artistName;
            overlay.style.display = 'flex';
            content.innerHTML = '<div style="text-align: center; color: #A5C8FF; padding: 30px;">Fetching Last.fm intelligence...</div>';

            fetch('/api/lastfm/artist?artist=' + encodeURIComponent(artistName))
                .then(r => r.json())
                .then(data => {
                    let html = `
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; background: rgba(165, 200, 255, 0.08); padding: 8px 12px; border-radius: 8px; border: 1px solid rgba(165, 200, 255, 0.2); flex-wrap: wrap; gap: 8px;">
                            <span style="font-size: 0.84rem; color: #A5C8FF;">Live tours, NC shows &amp; full catalog</span>
                            <a href="/artist?artist=${encodeURIComponent(artistName)}" class="pill-btn primary" style="font-size: 0.78rem; padding: 4px 12px; text-decoration: none; display: inline-flex; align-items: center; gap: 4px;">🌟 View Full Artist Page &rarr;</a>
                        </div>
                    `;
                    if (data.tags && data.tags.length > 0) {
                        html += '<div style="margin-bottom: 16px;">';
                        html += '<div style="font-size: 0.76rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: #C5B8FF; margin-bottom: 8px;">Top Genres &amp; Tags</div>';
                        html += '<div class="lastfm-tag-cloud">';
                        data.tags.forEach(t => {
                            const tagUrl = t.url || ('https://www.last.fm/tag/' + encodeURIComponent(t.name));
                            html += `<a href="${escapeHtml(tagUrl)}" target="_blank" rel="noopener noreferrer" class="lastfm-tag-chip artist-tag">#${escapeHtml(t.name)}</a> `;
                        });
                        html += '</div></div>';
                    }

                    if (data.top_tracks && data.top_tracks.length > 0) {
                        html += '<div>';
                        html += '<div style="font-size: 0.76rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: #A5C8FF; margin-bottom: 8px;">Top Tracks</div>';
                        html += '<div class="lastfm-tracks-list">';
                        data.top_tracks.forEach(t => {
                            const rank = t.rank || '';
                            const playText = t.listeners ? (Number(t.listeners).toLocaleString() + ' listeners') : (t.playcount ? Number(t.playcount).toLocaleString() + ' plays' : '');
                            html += `
                                <div class="lastfm-track-row">
                                    <span class="lastfm-track-rank">${rank}</span>
                                    <span class="lastfm-track-name" title="${escapeHtml(t.name)}">${escapeHtml(t.name)}</span>
                                    <span class="lastfm-track-meta">${playText}</span>
                                    <button type="button" class="lastfm-quick-load-btn" data-artist="${escapeHtml(artistName)}" data-track="${escapeHtml(t.name)}" onclick="quickLoadTrack(this.dataset.artist, this.dataset.track)">⚡ Load</button>
                                </div>
                            `;
                        });
                        html += '</div></div>';
                    } else if (!data.has_key) {
                        html += '<div style="color: #A5C8FF; font-size: 0.88rem; padding: 10px 0; line-height: 1.5;">Set <code>LASTFM_API_KEY</code> in <code>calling_hours_secrets.py</code> to browse Last.fm artist info.<br><a href="https://www.last.fm/api/account/create" target="_blank" rel="noopener noreferrer" style="color: #C4DFFF; text-decoration: underline; margin-top: 6px; display: inline-block;">Get a free API key &rarr;</a></div>';
                    } else {
                        html += '<div style="color: rgba(225, 232, 240, 0.6); font-style: italic; padding: 20px; text-align: center;">No Last.fm metadata found for this artist.</div>';
                    }
                    content.innerHTML = html;
                })
                .catch(err => {
                    content.innerHTML = '<div style="color: #ff6e6e; padding: 20px;">Failed to load artist details: ' + escapeHtml(err.message) + '</div>';
                });
        }

        function closeArtistModal() {
            const overlay = document.getElementById('artist-modal-overlay');
            if (overlay) overlay.style.display = 'none';
        }

        function closeArtistModalOnBackdrop(e) {
            if (e.target && e.target.id === 'artist-modal-overlay') {
                closeArtistModal();
            }
        }

        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                closeArtistModal();
            }
        });

        function filterByArtist(artist) {
            activeArtistFilter = (artist || '').trim().toLowerCase();
            const selectEl = document.getElementById('history-artist-filter');
            if (selectEl) {
                let found = false;
                for (let i = 0; i < selectEl.options.length; i++) {
                    if (selectEl.options[i].value && selectEl.options[i].value.toLowerCase() === activeArtistFilter) {
                        selectEl.selectedIndex = i;
                        found = true;
                        break;
                    }
                }
                if (!found) selectEl.selectedIndex = 0;
            }

            const chips = document.querySelectorAll('.artist-chip');
            chips.forEach(chip => {
                const chipArtist = (chip.dataset.artist || '').toLowerCase();
                if ((!activeArtistFilter && chip.id === 'chip-all') || (activeArtistFilter && chipArtist === activeArtistFilter)) {
                    chip.style.background = 'rgba(165, 200, 255, 0.35)';
                    chip.style.borderColor = '#A5C8FF';
                    chip.style.color = '#FFFFFF';
                } else {
                    chip.style.background = 'rgba(11, 30, 63, 0.6)';
                    chip.style.borderColor = 'rgba(165, 200, 255, 0.2)';
                    chip.style.color = '#A5C8FF';
                }
            });

            applyCombinedFilter();
        }

        function filterHistory(query) {
            applyCombinedFilter();
        }

        function applyCombinedFilter() {
            const query = (document.getElementById('history-filter')?.value || '').toLowerCase().trim();
            const cards = document.querySelectorAll('.history-card');
            let visible = 0;
            cards.forEach(card => {
                const cardArtist = (card.dataset.artist || '').toLowerCase();
                const cardText = card.textContent.toLowerCase();
                const matchesArtist = !activeArtistFilter || cardArtist === activeArtistFilter;
                const matchesQuery = !query || cardText.includes(query);
                const show = matchesArtist && matchesQuery;
                card.style.display = show ? 'flex' : 'none';
                if (show) visible++;
            });
            const noMatchEl = document.getElementById('history-no-matches');
            if (noMatchEl) {
                noMatchEl.style.display = (visible === 0 && cards.length > 0) ? 'block' : 'none';
            }
        }

        document.addEventListener('DOMContentLoaded', () => {
            if (activeArtistFilter) {
                filterByArtist(activeArtistFilter);
            }
        });
    </script>
</body>
</html>'''

ARTIST_PAGE_HTML = PAGE_HTML.split('<body>')[0] + '''<body>
    <div class="stars"></div>
    <div class="horizon"></div>
    {app_header}
    <style>
        .artist-page-container {
            position: relative;
            z-index: 2;
            width: min(1160px, 94vw);
            margin: 0 auto 60px auto;
            display: flex;
            flex-direction: column;
            gap: 24px;
        }
        .artist-top-bar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 16px;
            flex-wrap: wrap;
        }
        .artist-search-form {
            display: flex;
            gap: 10px;
            flex: 1;
            max-width: 480px;
        }
        .artist-search-input {
            flex: 1;
            padding: 10px 16px;
            background: rgba(11, 30, 63, 0.75);
            border: 1px solid rgba(165, 200, 255, 0.3);
            border-radius: 8px;
            color: #E1E8F0;
            font-size: 0.95rem;
            outline: none;
            transition: border-color 0.2s;
        }
        .artist-search-input:focus {
            border-color: #A5C8FF;
            box-shadow: 0 0 10px rgba(165, 200, 255, 0.3);
        }
        .artist-hero-card {
            position: relative;
            background: linear-gradient(135deg, rgba(14, 34, 72, 0.88) 0%, rgba(6, 14, 30, 0.95) 100%);
            border: 1px solid rgba(165, 200, 255, 0.25);
            border-radius: 16px;
            padding: 32px;
            box-shadow: 0 12px 36px rgba(0, 0, 0, 0.55);
            overflow: hidden;
        }
        .artist-hero-backdrop {
            position: absolute;
            top: 0; left: 0; right: 0; bottom: 0;
            background-size: cover;
            background-position: center;
            opacity: 0.16;
            filter: blur(5px);
            z-index: 0;
            pointer-events: none;
        }
        .artist-hero-inner {
            position: relative;
            z-index: 1;
            display: flex;
            align-items: center;
            gap: 32px;
            flex-wrap: wrap;
        }
        .artist-avatar-img {
            width: 140px;
            height: 140px;
            border-radius: 50%;
            object-fit: cover;
            border: 3px solid #A5C8FF;
            box-shadow: 0 0 24px rgba(165, 200, 255, 0.45);
            background: #0B1E3F;
            flex-shrink: 0;
        }
        .artist-avatar-placeholder {
            width: 140px;
            height: 140px;
            border-radius: 50%;
            border: 3px solid #A5C8FF;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 3.5rem;
            background: rgba(11, 30, 63, 0.8);
            color: #A5C8FF;
            flex-shrink: 0;
            box-shadow: 0 0 24px rgba(165, 200, 255, 0.35);
        }
        .artist-info-col {
            flex: 1;
            min-width: 280px;
        }
        .artist-heading {
            margin: 0 0 8px 0;
            font-family: 'Montserrat', sans-serif;
            font-size: 2.6rem;
            font-weight: 900;
            letter-spacing: -0.02em;
            color: #FFFFFF;
            text-shadow: 0 2px 10px rgba(0, 0, 0, 0.7);
            line-height: 1.15;
        }
        .artist-badges-row {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            margin: 10px 0 14px 0;
        }
        .artist-meta-badge {
            background: rgba(165, 200, 255, 0.12);
            border: 1px solid rgba(165, 200, 255, 0.25);
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.84rem;
            color: #D2E4FF;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }
        .artist-meta-badge strong {
            color: #FFFFFF;
        }
        .artist-links-row {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            margin-top: 14px;
        }
        .nc-spotlight-card {
            background: linear-gradient(135deg, rgba(16, 48, 102, 0.8) 0%, rgba(8, 24, 56, 0.9) 100%);
            border: 1px solid rgba(165, 200, 255, 0.35);
            border-left: 6px solid #60A5FA;
            border-radius: 14px;
            padding: 24px 28px;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
        }
        .tour-history-card {
            background: rgba(11, 30, 63, 0.65);
            border: 1px solid rgba(165, 200, 255, 0.2);
            border-radius: 14px;
            padding: 24px 28px;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
        }
        .section-header-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
            margin-bottom: 18px;
            border-bottom: 1px solid rgba(165, 200, 255, 0.15);
            padding-bottom: 12px;
        }
        .section-title {
            font-family: 'Montserrat', sans-serif;
            font-size: 1.35rem;
            font-weight: 800;
            color: #E1E8F0;
            margin: 0;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .tour-cards-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
            gap: 18px;
        }
        .tour-item-card {
            background: rgba(14, 38, 80, 0.55);
            border: 1px solid rgba(165, 200, 255, 0.22);
            border-radius: 12px;
            padding: 20px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            gap: 16px;
            transition: transform 0.2s ease, border-color 0.2s ease;
        }
        .tour-item-card:hover {
            transform: translateY(-2px);
            border-color: rgba(165, 200, 255, 0.4);
        }
        .tour-title {
            font-family: 'Montserrat', sans-serif;
            font-size: 1.15rem;
            font-weight: 800;
            color: #FFFFFF;
            margin: 0 0 6px 0;
        }
        .coperformer-chip {
            background: rgba(99, 102, 241, 0.22);
            border: 1px solid rgba(165, 200, 255, 0.35);
            color: #E0E7FF;
            padding: 5px 12px;
            border-radius: 16px;
            font-size: 0.82rem;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            min-height: 32px;
            touch-action: manipulation;
            -webkit-tap-highlight-color: transparent;
            transition: all 0.2s ease;
        }
        .coperformer-chip:hover {
            background: rgba(99, 102, 241, 0.45);
            border-color: #A5C8FF;
            color: #FFFFFF;
            transform: scale(1.04);
        }
        .songs-table-card {
            background: rgba(11, 30, 63, 0.65);
            border: 1px solid rgba(165, 200, 255, 0.2);
            border-radius: 14px;
            padding: 24px 28px;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
        }
        .artist-song-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 16px;
            padding: 12px 14px;
            border-bottom: 1px solid rgba(165, 200, 255, 0.08);
            border-radius: 8px;
            transition: background 0.15s ease;
            flex-wrap: wrap;
        }
        .artist-song-row:hover {
            background: rgba(165, 200, 255, 0.07);
        }
        .btn-analyze-song {
            background: linear-gradient(135deg, #2563EB 0%, #1D4ED8 100%);
            color: #FFFFFF;
            border: 1px solid rgba(255, 255, 255, 0.25);
            border-radius: 8px;
            padding: 8px 16px;
            font-size: 0.86rem;
            font-weight: 700;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            min-height: 40px;
            box-shadow: 0 2px 8px rgba(37, 99, 235, 0.4);
            transition: all 0.2s ease;
            white-space: nowrap;
            touch-action: manipulation;
            -webkit-tap-highlight-color: transparent;
        }
        .btn-analyze-song:hover {
            background: linear-gradient(135deg, #3B82F6 0%, #2563EB 100%);
            box-shadow: 0 4px 14px rgba(37, 99, 235, 0.6);
            transform: translateY(-1px);
        }
        .similar-chip {
            background: rgba(165, 200, 255, 0.1);
            border: 1px solid rgba(165, 200, 255, 0.25);
            color: #C5B8FF;
            padding: 5px 12px;
            border-radius: 20px;
            font-size: 0.85rem;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            min-height: 32px;
            touch-action: manipulation;
            -webkit-tap-highlight-color: transparent;
            transition: all 0.2s ease;
        }
        .similar-chip:hover {
            background: rgba(165, 200, 255, 0.25);
            color: #FFFFFF;
            border-color: #A5C8FF;
            transform: scale(1.03);
        }
        /* Spotify Analytics Card Styles */
        .spotify-analytics-card {
            background: linear-gradient(135deg, rgba(8, 36, 32, 0.88) 0%, rgba(6, 20, 28, 0.95) 100%);
            border: 1px solid rgba(16, 185, 129, 0.35);
            border-left: 6px solid #10B981;
            border-radius: 14px;
            padding: 26px 28px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.45);
        }
        .spotify-kpi-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 14px;
            margin: 18px 0;
        }
        .spotify-kpi-item {
            background: rgba(16, 185, 129, 0.08);
            border: 1px solid rgba(16, 185, 129, 0.22);
            border-radius: 10px;
            padding: 14px 16px;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }
        .spotify-kpi-label {
            font-size: 0.74rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: #6EE7B7;
        }
        .spotify-kpi-value {
            font-family: 'Montserrat', sans-serif;
            font-size: 1.45rem;
            font-weight: 900;
            color: #FFFFFF;
        }
        .spotify-kpi-sub {
            font-size: 0.78rem;
            color: rgba(225, 232, 240, 0.65);
        }
        .popularity-gauge-wrap {
            margin-top: 4px;
            width: 100%;
            height: 7px;
            background: rgba(255, 255, 255, 0.12);
            border-radius: 4px;
            overflow: hidden;
        }
        .popularity-gauge-fill {
            height: 100%;
            background: linear-gradient(90deg, #10B981 0%, #34D399 100%);
            border-radius: 4px;
            box-shadow: 0 0 8px rgba(16, 185, 129, 0.5);
        }
        .spotify-track-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 10px 14px;
            border-bottom: 1px solid rgba(16, 185, 129, 0.12);
            gap: 12px;
            flex-wrap: wrap;
            border-radius: 8px;
            transition: background 0.15s ease;
        }
        .spotify-track-row:hover {
            background: rgba(16, 185, 129, 0.08);
        }
        .spotify-track-thumb {
            width: 44px;
            height: 44px;
            border-radius: 6px;
            object-fit: cover;
            border: 1px solid rgba(255, 255, 255, 0.2);
            flex-shrink: 0;
            background: #0B1E3F;
        }
        .spotify-track-thumb-placeholder {
            width: 44px;
            height: 44px;
            border-radius: 6px;
            display: flex;
            align-items: center;
            justify-content: center;
            background: rgba(16, 185, 129, 0.15);
            color: #10B981;
            font-size: 1.1rem;
            flex-shrink: 0;
        }
        .spotify-preview-player {
            height: 28px;
            max-width: 130px;
            outline: none;
            filter: invert(0.9) hue-rotate(85deg);
            border-radius: 14px;
        }
        .spotify-genre-badge {
            background: rgba(16, 185, 129, 0.15);
            border: 1px solid rgba(16, 185, 129, 0.3);
            color: #A7F3D0;
            padding: 3px 10px;
            border-radius: 14px;
            font-size: 0.78rem;
            font-weight: 600;
        }
        .btn-analyze-song-sm {
            background: linear-gradient(135deg, #2563EB 0%, #1D4ED8 100%);
            color: #FFFFFF;
            border: 1px solid rgba(255, 255, 255, 0.25);
            border-radius: 6px;
            padding: 4px 10px;
            font-size: 0.76rem;
            font-weight: 700;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 4px;
            box-shadow: 0 2px 6px rgba(37, 99, 235, 0.35);
            transition: all 0.2s ease;
            white-space: nowrap;
        }
        .btn-analyze-song-sm:hover {
            background: linear-gradient(135deg, #3B82F6 0%, #2563EB 100%);
            box-shadow: 0 4px 12px rgba(37, 99, 235, 0.55);
            transform: translateY(-1px);
        }

        /* Top 10 Bands Played With Styles */
        .top-bands-card {
            background: rgba(11, 30, 63, 0.65);
            border: 1px solid rgba(165, 200, 255, 0.25);
            border-left: 6px solid #8B5CF6;
            border-radius: 14px;
            padding: 26px 28px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.35);
        }
        .top-bands-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
            gap: 16px;
            margin-top: 18px;
        }
        .top-band-item {
            background: rgba(14, 38, 80, 0.6);
            border: 1px solid rgba(165, 200, 255, 0.2);
            border-radius: 12px;
            padding: 18px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            gap: 14px;
            position: relative;
            transition: transform 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
        }
        .top-band-item:hover {
            transform: translateY(-2px);
            border-color: rgba(165, 200, 255, 0.45);
            box-shadow: 0 6px 20px rgba(0, 0, 0, 0.4);
        }
        .top-band-item.rank-1 {
            border-color: rgba(245, 158, 11, 0.5);
            background: linear-gradient(135deg, rgba(35, 30, 15, 0.6) 0%, rgba(14, 38, 80, 0.6) 100%);
        }
        .top-band-item.rank-2 {
            border-color: rgba(203, 213, 225, 0.45);
        }
        .top-band-item.rank-3 {
            border-color: rgba(217, 119, 6, 0.45);
        }
        .top-band-rank-badge {
            font-family: 'Montserrat', sans-serif;
            font-size: 0.8rem;
            font-weight: 800;
            padding: 2px 10px;
            border-radius: 12px;
            display: inline-flex;
            align-items: center;
            gap: 4px;
        }
        .rank-badge-1 {
            background: rgba(245, 158, 11, 0.25);
            border: 1px solid rgba(245, 158, 11, 0.6);
            color: #FCD34D;
        }
        .rank-badge-2 {
            background: rgba(203, 213, 225, 0.2);
            border: 1px solid rgba(203, 213, 225, 0.5);
            color: #E2E8F0;
        }
        .rank-badge-3 {
            background: rgba(217, 119, 6, 0.25);
            border: 1px solid rgba(217, 119, 6, 0.55);
            color: #FDBA74;
        }
        .rank-badge-other {
            background: rgba(165, 200, 255, 0.12);
            border: 1px solid rgba(165, 200, 255, 0.25);
            color: #93C5FD;
        }
        .top-band-name-link {
            font-family: 'Montserrat', sans-serif;
            font-size: 1.22rem;
            font-weight: 800;
            color: #FFFFFF;
            text-decoration: none;
            transition: color 0.15s ease;
        }
        .top-band-name-link:hover {
            color: #A5C8FF;
            text-decoration: underline;
        }
        .top-band-role-badge {
            font-size: 0.74rem;
            font-weight: 700;
            background: rgba(139, 92, 246, 0.2);
            border: 1px solid rgba(139, 92, 246, 0.4);
            color: #DDD6FE;
            padding: 2px 8px;
            border-radius: 8px;
            display: inline-flex;
            align-items: center;
        }

        @media (max-width: 768px) {
            .artist-page-container {
                width: 95vw;
                margin-bottom: 40px;
                gap: 16px;
            }
            .artist-top-bar {
                flex-direction: column;
                align-items: stretch;
                gap: 12px;
            }
            .artist-search-form {
                max-width: 100%;
                width: 100%;
            }
            .artist-hero-card {
                padding: 20px 16px;
            }
            .artist-hero-inner {
                flex-direction: column;
                align-items: center;
                text-align: center;
                gap: 18px;
            }
            .artist-avatar-img,
            .artist-avatar-placeholder {
                width: 100px;
                height: 100px;
                font-size: 2.5rem;
            }
            .artist-heading {
                font-size: 1.8rem !important;
                text-align: center;
            }
            .artist-badges-row,
            .artist-links-row {
                justify-content: center;
            }
            .nc-spotlight-card,
            .tour-history-card,
            .songs-table-card,
            .spotify-analytics-card,
            .top-bands-card {
                padding: 18px 16px;
            }
            .tour-cards-grid,
            .top-bands-grid {
                grid-template-columns: 1fr;
            }
            .spotify-kpi-grid {
                grid-template-columns: repeat(2, 1fr);
            }
            .spotify-track-row {
                flex-direction: column;
                align-items: stretch;
                gap: 10px;
            }
            .artist-song-row {
                flex-direction: column;
                align-items: stretch;
                gap: 10px;
                padding: 10px 8px;
            }
            .artist-song-row .btn-analyze-song {
                width: 100%;
                justify-content: center;
                text-align: center;
            }
        }
        @media (max-width: 480px) {
            .artist-heading {
                font-size: 1.5rem !important;
            }
            .artist-meta-badge {
                font-size: 0.76rem;
                padding: 3px 8px;
            }
            .artist-links-row .pill-btn {
                flex: 1 1 100%;
                text-align: center;
                justify-content: center;
            }
            .spotify-kpi-grid {
                grid-template-columns: 1fr;
            }
        }
    </style>

    <div class="artist-page-container">
        {artist_page_content}
    </div>
</body>
</html>'''

SPOTIFY_PAGE_HTML = PAGE_HTML.split('<body>')[0] + '''<body>
    <div class="stars"></div>
    <div class="horizon"></div>
    {app_header}
    <style>
        .spotify-page-container {
            position: relative;
            z-index: 2;
            width: min(1180px, 94vw);
            margin: 0 auto 60px auto;
            display: flex;
            flex-direction: column;
            gap: 24px;
        }
        .spotify-top-bar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 16px;
            flex-wrap: wrap;
        }
        .spotify-card {
            background: linear-gradient(135deg, rgba(14, 34, 72, 0.85) 0%, rgba(6, 14, 30, 0.95) 100%);
            border: 1px solid rgba(165, 200, 255, 0.22);
            border-radius: 16px;
            padding: 24px 28px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.45);
        }
        .spotify-now-playing-card {
            background: linear-gradient(135deg, rgba(10, 36, 25, 0.92) 0%, rgba(6, 20, 15, 0.98) 100%);
            border: 1px solid rgba(29, 185, 84, 0.45);
            border-radius: 16px;
            padding: 22px 26px;
            box-shadow: 0 0 25px rgba(29, 185, 84, 0.16), 0 12px 32px rgba(0, 0, 0, 0.5);
            position: relative;
            overflow: hidden;
        }
        .spotify-equalizer {
            display: inline-flex;
            align-items: flex-end;
            gap: 3px;
            height: 15px;
        }
        .eq-bar {
            width: 3px;
            background: #1DB954;
            border-radius: 2px;
            animation: eq-bounce 1.1s ease-in-out infinite alternate;
        }
        .eq-bar:nth-child(1) { height: 60%; animation-delay: 0.1s; }
        .eq-bar:nth-child(2) { height: 100%; animation-delay: 0.3s; }
        .eq-bar:nth-child(3) { height: 40%; animation-delay: 0.2s; }
        .eq-bar:nth-child(4) { height: 85%; animation-delay: 0.4s; }
        @keyframes eq-bounce {
            0% { transform: scaleY(0.25); }
            100% { transform: scaleY(1); }
        }
        .spotify-kpi-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 16px;
        }
        .spotify-kpi-card {
            background: rgba(14, 38, 80, 0.6);
            border: 1px solid rgba(165, 200, 255, 0.2);
            border-radius: 12px;
            padding: 18px 20px;
            display: flex;
            flex-direction: column;
            gap: 6px;
            transition: transform 0.2s ease, border-color 0.2s ease;
        }
        .spotify-kpi-card:hover {
            transform: translateY(-2px);
            border-color: rgba(29, 185, 84, 0.5);
        }
        .kpi-label {
            font-size: 0.78rem;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: #A5C8FF;
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .kpi-value {
            font-size: 1.65rem;
            font-weight: 900;
            color: #FFFFFF;
            font-family: 'Montserrat', sans-serif;
            line-height: 1.2;
        }
        .kpi-subtext {
            font-size: 0.78rem;
            color: rgba(225, 232, 240, 0.7);
        }
        .spotify-analytics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
            gap: 20px;
        }
        .analytics-block {
            background: rgba(11, 30, 63, 0.6);
            border: 1px solid rgba(165, 200, 255, 0.18);
            border-radius: 14px;
            padding: 22px;
        }
        .analytics-block-title {
            font-family: 'Montserrat', sans-serif;
            font-size: 1.05rem;
            font-weight: 800;
            color: #E1E8F0;
            margin-bottom: 14px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .time-bar-row {
            display: flex;
            flex-direction: column;
            gap: 6px;
            margin-bottom: 12px;
        }
        .time-bar-header {
            display: flex;
            justify-content: space-between;
            font-size: 0.82rem;
            color: #E1E8F0;
        }
        .time-bar-bg {
            width: 100%;
            height: 8px;
            background: rgba(255, 255, 255, 0.08);
            border-radius: 4px;
            overflow: hidden;
        }
        .time-bar-fill {
            height: 100%;
            border-radius: 4px;
            transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1);
        }
        .dow-bars-container {
            display: flex;
            align-items: flex-end;
            justify-content: space-between;
            gap: 8px;
            height: 120px;
            padding-top: 15px;
        }
        .dow-col {
            flex: 1;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 6px;
            height: 100%;
            justify-content: flex-end;
        }
        .dow-bar-fill {
            width: 100%;
            max-width: 24px;
            border-radius: 4px 4px 0 0;
            background: linear-gradient(180deg, #1DB954 0%, #0d7031 100%);
            min-height: 4px;
            transition: height 0.5s ease;
        }
        .dow-label {
            font-size: 0.72rem;
            color: #A5C8FF;
            font-weight: 700;
        }
        .dow-count {
            font-size: 0.68rem;
            color: rgba(225, 232, 240, 0.7);
        }
        .artist-rank-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 8px 12px;
            background: rgba(14, 38, 80, 0.45);
            border-radius: 8px;
            margin-bottom: 8px;
            transition: background 0.2s ease;
        }
        .artist-rank-item:hover {
            background: rgba(14, 38, 80, 0.8);
        }
        .genre-chip {
            display: inline-block;
            background: rgba(29, 185, 84, 0.15);
            border: 1px solid rgba(29, 185, 84, 0.35);
            color: #A7F3D0;
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 0.78rem;
            font-weight: 600;
            margin: 3px;
        }
        .history-track-card {
            background: rgba(11, 30, 63, 0.65);
            border: 1px solid rgba(165, 200, 255, 0.16);
            border-radius: 12px;
            padding: 14px 18px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            transition: transform 0.15s ease, border-color 0.15s ease, background 0.15s ease;
        }
        .history-track-card:hover {
            border-color: rgba(29, 185, 84, 0.4);
            background: rgba(14, 38, 80, 0.7);
        }
        .spotify-green-btn {
            background: #1DB954;
            color: #FFFFFF;
            font-weight: 700;
            border: none;
            border-radius: 24px;
            padding: 10px 20px;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            text-decoration: none;
            font-size: 0.88rem;
            cursor: pointer;
            transition: background 0.2s ease, transform 0.15s ease, box-shadow 0.2s ease;
        }
        .spotify-green-btn:hover {
            background: #1ed760;
            transform: translateY(-1px);
            box-shadow: 0 4px 14px rgba(29, 185, 84, 0.4);
        }
        .preview-audio-btn {
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.2);
            color: #FFFFFF;
            width: 32px;
            height: 32px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            font-size: 0.78rem;
            flex-shrink: 0;
            transition: background 0.2s ease;
        }
        .preview-audio-btn:hover {
            background: #1DB954;
            border-color: #1DB954;
        }
        .spotify-search-input {
            width: 100%;
            padding: 10px 16px;
            background: rgba(11, 30, 63, 0.75);
            border: 1px solid rgba(165, 200, 255, 0.3);
            border-radius: 8px;
            color: #E1E8F0;
            font-size: 0.95rem;
            outline: none;
            transition: border-color 0.2s;
        }
        .spotify-search-input:focus {
            border-color: #1DB954;
            box-shadow: 0 0 10px rgba(29, 185, 84, 0.35);
        }
        @media (max-width: 768px) {
            .history-track-card {
                flex-direction: column;
                align-items: flex-start;
                gap: 12px;
            }
            .history-track-card > div:last-child {
                width: 100%;
                display: flex;
                justify-content: space-between;
                align-items: center;
                border-top: 1px solid rgba(165, 200, 255, 0.1);
                padding-top: 10px;
            }
        }
    </style>

    <div class="spotify-page-container">
        {spotify_page_content}
    </div>

    <script>
        let currentAudio = null;
        let currentAudioBtn = null;

        function toggleAudioPreview(btn, url) {
            if (!url) return;
            if (currentAudio && !currentAudio.paused) {
                currentAudio.pause();
                if (currentAudioBtn) {
                    currentAudioBtn.innerHTML = '▶';
                    currentAudioBtn.title = 'Play 30s preview';
                }
                if (currentAudioBtn === btn) {
                    currentAudio = null;
                    currentAudioBtn = null;
                    return;
                }
            }
            currentAudio = new Audio(url);
            currentAudioBtn = btn;
            btn.innerHTML = '⏸';
            btn.title = 'Pause preview';
            currentAudio.play().catch(e => {
                console.log('Audio preview playback error:', e);
                btn.innerHTML = '▶';
            });
            currentAudio.onended = () => {
                btn.innerHTML = '▶';
                currentAudio = null;
                currentAudioBtn = null;
            };
        }

        function filterSpotifyHistory() {
            const input = document.getElementById('spotify-filter-input');
            if (!input) return;
            const query = input.value.trim().toLowerCase();
            const cards = document.querySelectorAll('.history-track-card');
            let visibleCount = 0;
            cards.forEach(card => {
                const searchData = (card.getAttribute('data-search') || '').toLowerCase();
                if (!query || searchData.includes(query)) {
                    card.style.display = 'flex';
                    visibleCount++;
                } else {
                    card.style.display = 'none';
                }
            });
            const noMatch = document.getElementById('history-no-match');
            if (noMatch) {
                noMatch.style.display = (visibleCount === 0 && cards.length > 0) ? 'block' : 'none';
            }
        }
    </script>
</body>
</html>'''

PLAYLISTS_PAGE_HTML = PAGE_HTML.split('<body>')[0] + '''<body>
    <div class="stars"></div>
    <div class="horizon"></div>
    {app_header}
    <style>
        .playlist-page-container {
            position: relative;
            z-index: 2;
            width: min(1180px, 94vw);
            margin: 0 auto 60px auto;
            display: flex;
            flex-direction: column;
            gap: 24px;
        }
        .playlist-top-bar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 16px;
            flex-wrap: wrap;
        }
        .playlist-card {
            background: linear-gradient(135deg, rgba(14, 34, 72, 0.85) 0%, rgba(6, 14, 30, 0.95) 100%);
            border: 1px solid rgba(165, 200, 255, 0.22);
            border-radius: 16px;
            padding: 24px 28px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.45);
        }
        .playlist-kpi-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 16px;
        }
        .playlist-kpi-card {
            background: rgba(14, 38, 80, 0.6);
            border: 1px solid rgba(165, 200, 255, 0.2);
            border-radius: 12px;
            padding: 16px 18px;
            display: flex;
            flex-direction: column;
            gap: 4px;
            transition: transform 0.2s ease, border-color 0.2s ease;
        }
        .playlist-kpi-card:hover {
            transform: translateY(-2px);
            border-color: rgba(165, 200, 255, 0.4);
        }
        .playlist-kpi-value {
            font-size: 1.8rem;
            font-weight: 800;
            color: #E1E8F0;
            font-family: 'Montserrat', sans-serif;
            line-height: 1.1;
        }
        .playlist-kpi-label {
            font-size: 0.78rem;
            color: #A5C8FF;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            font-weight: 600;
        }
        .playlist-mode-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
            gap: 14px;
        }
        .playlist-mode-card {
            background: rgba(11, 30, 63, 0.55);
            border: 1px solid rgba(165, 200, 255, 0.2);
            border-radius: 14px;
            padding: 18px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            gap: 12px;
            cursor: pointer;
            text-decoration: none;
            transition: all 0.2s ease;
            position: relative;
            overflow: hidden;
        }
        .playlist-mode-card:hover {
            background: rgba(165, 200, 255, 0.12);
            border-color: rgba(165, 200, 255, 0.5);
            transform: translateY(-2px);
        }
        .playlist-mode-card.active {
            background: linear-gradient(135deg, rgba(25, 70, 133, 0.5) 0%, rgba(14, 34, 72, 0.8) 100%);
            border: 2px solid #A5C8FF;
            box-shadow: 0 0 20px rgba(165, 200, 255, 0.25);
        }
        .playlist-mode-card.flagship {
            border-color: rgba(165, 200, 255, 0.4);
        }
        .playlist-mode-badge {
            align-self: flex-start;
            font-size: 0.68rem;
            font-weight: 800;
            letter-spacing: 0.04em;
            text-transform: uppercase;
            padding: 3px 8px;
            border-radius: 6px;
            background: rgba(165, 200, 255, 0.15);
            color: #A5C8FF;
        }
        .playlist-mode-badge.primary {
            background: #A5C8FF;
            color: #050A14;
            box-shadow: 0 0 10px rgba(165, 200, 255, 0.4);
        }
        .playlist-mode-icon {
            font-size: 1.8rem;
            line-height: 1;
        }
        .playlist-mode-title {
            font-size: 1.05rem;
            font-weight: 800;
            color: #E1E8F0;
            font-family: 'Montserrat', sans-serif;
            margin-top: 4px;
        }
        .playlist-mode-desc {
            font-size: 0.8rem;
            color: rgba(225, 232, 240, 0.68);
            line-height: 1.4;
        }
        .playlist-mode-count {
            font-size: 0.76rem;
            color: #A5C8FF;
            font-weight: 600;
            margin-top: auto;
        }
        .playlist-form-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 16px;
            align-items: flex-end;
        }
        .playlist-input-group {
            display: flex;
            flex-direction: column;
            gap: 6px;
        }
        .playlist-input-group label {
            font-size: 0.78rem;
            font-weight: 700;
            color: #A5C8FF;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }
        .playlist-input-group select,
        .playlist-input-group input[type="text"] {
            background: rgba(5, 10, 20, 0.85);
            border: 1px solid rgba(165, 200, 255, 0.3);
            border-radius: 8px;
            padding: 10px 14px;
            color: #FFFFFF;
            font-family: inherit;
            font-size: 0.9rem;
            outline: none;
            transition: border-color 0.2s ease;
        }
        .playlist-input-group select:focus,
        .playlist-input-group input[type="text"]:focus {
            border-color: #A5C8FF;
            box-shadow: 0 0 10px rgba(165, 200, 255, 0.25);
        }
        .playlist-actions-toolbar {
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }
        .btn-playlist-action {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 8px 16px;
            border-radius: 8px;
            font-size: 0.84rem;
            font-weight: 700;
            cursor: pointer;
            text-decoration: none;
            transition: all 0.2s ease;
            border: 1px solid transparent;
        }
        .btn-playlist-spotify {
            background: #1DB954;
            color: #050A14;
            font-weight: 800;
            box-shadow: 0 4px 14px rgba(29, 185, 84, 0.35);
        }
        .btn-playlist-spotify:hover {
            background: #1ed760;
            transform: translateY(-1px);
            box-shadow: 0 6px 18px rgba(29, 185, 84, 0.45);
        }
        .btn-playlist-outline {
            background: rgba(165, 200, 255, 0.1);
            border-color: rgba(165, 200, 255, 0.25);
            color: #A5C8FF;
        }
        .btn-playlist-outline:hover {
            background: rgba(165, 200, 255, 0.2);
            color: #FFFFFF;
            border-color: #A5C8FF;
        }
        .playlist-tracklist-table {
            width: 100%;
            border-collapse: separate;
            border-spacing: 0 8px;
        }
        .playlist-tracklist-row {
            background: rgba(11, 30, 63, 0.55);
            border: 1px solid rgba(165, 200, 255, 0.15);
            border-radius: 10px;
            transition: all 0.15s ease;
        }
        .playlist-tracklist-row:hover {
            background: rgba(165, 200, 255, 0.12);
            border-color: rgba(165, 200, 255, 0.35);
        }
        .playlist-tracklist-row td {
            padding: 12px 16px;
            vertical-align: middle;
        }
        .playlist-tracklist-row td:first-child {
            border-top-left-radius: 10px;
            border-bottom-left-radius: 10px;
        }
        .playlist-tracklist-row td:last-child {
            border-top-right-radius: 10px;
            border-bottom-right-radius: 10px;
        }
        .playlist-track-num {
            font-size: 0.86rem;
            color: rgba(225, 232, 240, 0.5);
            font-weight: 700;
            width: 40px;
            text-align: center;
        }
        .playlist-track-main {
            display: flex;
            flex-direction: column;
            gap: 3px;
        }
        .playlist-track-title {
            font-weight: 700;
            color: #E1E8F0;
            font-size: 0.98rem;
        }
        .playlist-track-artist {
            color: #A5C8FF;
            font-size: 0.84rem;
        }
        .playlist-badge-model {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            font-size: 0.72rem;
            padding: 2px 7px;
            border-radius: 6px;
            background: rgba(120, 90, 255, 0.25);
            color: #D1C4E9;
            font-weight: 600;
        }
        .playlist-tab-btn {
            background: transparent;
            border: none;
            color: #A5C8FF;
            font-size: 0.95rem;
            font-weight: 700;
            font-family: 'Montserrat', sans-serif;
            padding: 8px 18px;
            border-radius: 20px;
            cursor: pointer;
            text-decoration: none;
            transition: all 0.2s ease;
        }
        .playlist-tab-btn.active {
            background: #A5C8FF;
            color: #050A14;
        }
        .playlist-tag-chip {
            display: inline-block;
            background: rgba(165, 200, 255, 0.12);
            color: #A5C8FF;
            border: 1px solid rgba(165, 200, 255, 0.25);
            padding: 3px 10px;
            border-radius: 16px;
            font-size: 0.76rem;
            cursor: pointer;
            text-decoration: none;
            transition: all 0.15s ease;
        }
        .playlist-tag-chip:hover {
            background: #A5C8FF;
            color: #050A14;
        }
        .playlist-modal-backdrop {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(5, 10, 20, 0.85);
            backdrop-filter: blur(8px);
            z-index: 1000;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .playlist-modal {
            background: #0E2248;
            border: 1px solid #A5C8FF;
            border-radius: 16px;
            padding: 28px;
            max-width: 500px;
            width: 100%;
            box-shadow: 0 12px 40px rgba(0, 0, 0, 0.7);
            color: #E1E8F0;
            position: relative;
        }
        #toast-notification {
            position: fixed;
            bottom: 30px;
            right: 30px;
            background: #194685;
            color: #FFFFFF;
            border: 1px solid #A5C8FF;
            padding: 12px 20px;
            border-radius: 10px;
            font-size: 0.88rem;
            font-weight: 600;
            box-shadow: 0 6px 20px rgba(0,0,0,0.5);
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.3s ease, transform 0.3s ease;
            transform: translateY(10px);
            z-index: 2000;
        }
        #toast-notification.show {
            opacity: 1;
            transform: translateY(0);
        }
    </style>

    <div class="playlist-page-container">
        {message_banner_html}

        <!-- Top Title & Navigation -->
        <div class="playlist-top-bar">
            <div>
                <h1 style="font-size: 2.1rem; font-weight: 800; font-family: 'Montserrat', sans-serif; color: #FFFFFF; display: flex; align-items: center; gap: 12px; margin: 0;">
                    <span>🎶</span> Playlist Generator
                </h1>
                <p style="color: rgba(225, 232, 240, 0.7); font-size: 0.92rem; margin: 6px 0 0 0;">
                    Curate, export, and listen to intelligent playlists created from your Calling Hours lyric analyses.
                </p>
            </div>
            <div style="display: flex; gap: 10px; align-items: center; background: rgba(11, 30, 63, 0.7); padding: 4px; border-radius: 24px; border: 1px solid rgba(165, 200, 255, 0.2);">
                <a href="/playlists" class="playlist-tab-btn{generate_tab_active}">✨ Generator Studio</a>
                <a href="/playlists?tab=saved" class="playlist-tab-btn{saved_playlists_tab_active}">💾 Saved Playlists ({saved_playlists_count})</a>
            </div>
        </div>

        <!-- Global Stats KPI Grid -->
        <div class="playlist-kpi-grid">
            <div class="playlist-kpi-card">
                <span class="playlist-kpi-label">Analyzed Songs</span>
                <span class="playlist-kpi-value">{total_analyzed_count}</span>
            </div>
            <div class="playlist-kpi-card">
                <span class="playlist-kpi-label">Artists Analyzed</span>
                <span class="playlist-kpi-value">{total_artists_count}</span>
            </div>
            <div class="playlist-kpi-card">
                <span class="playlist-kpi-label">Genre & Vibe Tags</span>
                <span class="playlist-kpi-value">{total_tags_count}</span>
            </div>
            <div class="playlist-kpi-card">
                <span class="playlist-kpi-label">Spotify Destination</span>
                <div style="margin-top: 4px;">{spotify_status_badge}</div>
            </div>
        </div>

        <!-- Generator Studio View -->
        <div id="generator-view" style="{generator_view_display}; display: flex; flex-direction: column; gap: 24px;">
            <!-- Different Generator Options (First one is All Analyzed Songs) -->
            <div class="playlist-card">
                <div style="margin-bottom: 16px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
                        <h2 style="font-size: 1.15rem; font-weight: 800; font-family: 'Montserrat', sans-serif; color: #FFFFFF; margin: 0;">
                            1. Select Playlist Generator Option
                        </h2>
                        <span style="font-size: 0.8rem; color: #A5C8FF; font-weight: 600;">Choose a generation strategy below</span>
                    </div>
                </div>

                <div class="playlist-mode-grid">
                    <!-- Option 1: All Analyzed Songs (Flagship / Primary) -->
                    <a href="/playlists?mode=all_analyzed" class="playlist-mode-card flagship{mode_all_analyzed_active}" id="option-card-all">
                        <div class="playlist-mode-badge primary">Option 1 • Flagship</div>
                        <div>
                            <div class="playlist-mode-icon">✦</div>
                            <div class="playlist-mode-title">All Analyzed Songs</div>
                        </div>
                        <div class="playlist-mode-desc">
                            Create a master playlist containing every track in your collection that has been analyzed by Gemini.
                        </div>
                        <div class="playlist-mode-count">{total_analyzed_count} tracks ready</div>
                    </a>

                    <!-- Option 2: By Artist / Band -->
                    <a href="/playlists?mode=artist" class="playlist-mode-card{mode_artist_active}" id="option-card-artist">
                        <div class="playlist-mode-badge">Option 2 • Artist</div>
                        <div>
                            <div class="playlist-mode-icon">👤</div>
                            <div class="playlist-mode-title">By Artist / Band</div>
                        </div>
                        <div class="playlist-mode-desc">
                            Generate a curated playlist focusing on all analyzed songs from a specific band or musician.
                        </div>
                        <div class="playlist-mode-count">{total_artists_count} artists available</div>
                    </a>

                    <!-- Option 3: By Genre & Mood Tag -->
                    <a href="/playlists?mode=tag" class="playlist-mode-card{mode_tag_active}" id="option-card-tag">
                        <div class="playlist-mode-badge">Option 3 • Last.fm</div>
                        <div>
                            <div class="playlist-mode-icon">🏷️</div>
                            <div class="playlist-mode-title">By Genre & Mood Tag</div>
                        </div>
                        <div class="playlist-mode-desc">
                            Filter analyzed songs by Last.fm community genre tags like Post-Punk, Shoegaze, or Midwest Emo.
                        </div>
                        <div class="playlist-mode-count">{total_tags_count} tags available</div>
                    </a>

                    <!-- Option 4: By Audio Attributes -->
                    <a href="/playlists?mode=mood" class="playlist-mode-card{mode_mood_active}" id="option-card-mood">
                        <div class="playlist-mode-badge">Option 4 • Audio</div>
                        <div>
                            <div class="playlist-mode-icon">⚡</div>
                            <div class="playlist-mode-title">By Audio Attributes</div>
                        </div>
                        <div class="playlist-mode-desc">
                            Curate songs by musical energy, danceability, deep melancholy (valence), or acoustic atmosphere.
                        </div>
                        <div class="playlist-mode-count">Sonic Audio Radar</div>
                    </a>

                    <!-- Option 5: Spotify Heavy Rotation -->
                    <a href="/playlists?mode=spotify" class="playlist-mode-card{mode_spotify_active}" id="option-card-spotify">
                        <div class="playlist-mode-badge">Option 5 • Streaming</div>
                        <div>
                            <div class="playlist-mode-icon">🎧</div>
                            <div class="playlist-mode-title">Spotify Rotation</div>
                        </div>
                        <div class="playlist-mode-desc">
                            Build a playlist from your recent Spotify listening history matched with Calling Hours analyses.
                        </div>
                        <div class="playlist-mode-count">Listening Sync</div>
                    </a>
                </div>
            </div>

            <!-- Configuration & Filter Bar -->
            <div class="playlist-card" style="padding: 20px 26px;">
                <form method="GET" action="/playlists" id="generator-form">
                    <input type="hidden" name="mode" value="{current_mode}">
                    <div style="font-weight: 700; color: #E1E8F0; font-size: 0.95rem; margin-bottom: 14px; display: flex; align-items: center; gap: 8px;">
                        <span>⚙️</span> 2. Configure Generator Settings
                    </div>

                    <div class="playlist-form-grid">
                        <!-- Mode-specific selector -->
                        {mode_specific_inputs}

                        <div class="playlist-input-group">
                            <label for="select-order">Sort Order</label>
                            <select name="order" id="select-order">
                                {sort_options_html}
                            </select>
                        </div>

                        <div class="playlist-input-group">
                            <label for="select-limit">Track Limit</label>
                            <select name="limit" id="select-limit">
                                {limit_options_html}
                            </select>
                        </div>

                        <div class="playlist-input-group">
                            <label for="input-playlist-name">Playlist Title</label>
                            <input type="text" name="name" id="input-playlist-name" value="{active_playlist_title}" placeholder="Playlist Title">
                        </div>

                        <div>
                            <button type="submit" class="btn-playlist-action" style="background: #194685; color: #FFFFFF; border-color: #A5C8FF; width: 100%; height: 42px; justify-content: center;">
                                <span>⚡</span> Generate Playlist
                            </button>
                        </div>
                    </div>
                    {tag_chips_container_html}
                </form>
            </div>

            <!-- Generated Playlist Output Card -->
            <div class="playlist-card" id="generated-playlist-section">
                <!-- Header with Title, Count, and Export Actions -->
                <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 20px; flex-wrap: wrap; padding-bottom: 20px; border-bottom: 1px solid rgba(165, 200, 255, 0.15);">
                    <div style="display: flex; gap: 16px; align-items: center;">
                        <div style="width: 60px; height: 60px; border-radius: 12px; background: linear-gradient(135deg, #194685 0%, #0B1E3F 100%); border: 1px solid #A5C8FF; display: flex; align-items: center; justify-content: center; font-size: 1.8rem; box-shadow: 0 4px 16px rgba(0,0,0,0.4);">
                            🎶
                        </div>
                        <div>
                            <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap;">
                                <h3 id="display-playlist-title" style="margin: 0; font-size: 1.35rem; font-weight: 800; font-family: 'Montserrat', sans-serif; color: #FFFFFF;">
                                    {active_playlist_title}
                                </h3>
                                <span class="playlist-mode-badge primary">{track_count} Tracks</span>
                            </div>
                            <div style="font-size: 0.84rem; color: rgba(225, 232, 240, 0.7); margin-top: 4px;">
                                {active_playlist_desc}
                            </div>
                        </div>
                    </div>

                    <!-- Action Buttons -->
                    <div class="playlist-actions-toolbar">
                        <button type="button" class="btn-playlist-action btn-playlist-spotify" onclick="triggerSpotifyExport()" id="btn-export-spotify">
                            <span>🎧</span> Export to Spotify
                        </button>
                        <a href="{export_m3u_url}" class="btn-playlist-action btn-playlist-outline" title="Download .m3u8 playlist file">
                            <span>📥</span> .M3U8
                        </a>
                        <a href="{export_csv_url}" class="btn-playlist-action btn-playlist-outline" title="Download .csv spreadsheet">
                            <span>📄</span> .CSV
                        </a>
                        <button type="button" class="btn-playlist-action btn-playlist-outline" onclick="copyPlaylistTracklist()" title="Copy tracklist to clipboard">
                            <span>📋</span> Copy
                        </button>
                        <button type="button" class="btn-playlist-action btn-playlist-outline" onclick="savePlaylistToCallingHours()" id="btn-save-db" title="Save playlist in Calling Hours">
                            <span>💾</span> Save
                        </button>
                    </div>
                </div>

                <!-- Tracklist Search & Table -->
                <div style="margin-top: 20px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; gap: 12px; flex-wrap: wrap;">
                        <span style="font-size: 0.85rem; font-weight: 700; color: #A5C8FF; text-transform: uppercase; letter-spacing: 0.04em;">Tracklist Overview</span>
                        <input type="text" id="tracklist-filter-input" placeholder="Filter songs in this playlist..." onkeyup="filterPlaylistTracks()" style="background: rgba(5, 10, 20, 0.7); border: 1px solid rgba(165, 200, 255, 0.25); border-radius: 20px; padding: 6px 14px; color: #FFFFFF; font-size: 0.82rem; outline: none; width: min(280px, 100%);">
                    </div>

                    <div style="overflow-x: auto;">
                        <table class="playlist-tracklist-table">
                            <tbody id="playlist-tracklist-body">
                                {tracklist_html}
                            </tbody>
                        </table>
                    </div>
                    <div id="playlist-no-tracks" style="display: none; text-align: center; color: #A5C8FF; padding: 30px; font-style: italic;">
                        No matching tracks found in this playlist.
                    </div>
                </div>
            </div>
        </div>

        <!-- Saved Playlists Library View -->
        <div id="saved-view" style="{saved_view_display}; display: flex; flex-direction: column; gap: 20px;">
            <div class="playlist-card">
                <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; margin-bottom: 20px;">
                    <div>
                        <h2 style="font-size: 1.25rem; font-weight: 800; font-family: 'Montserrat', sans-serif; color: #FFFFFF; margin: 0;">
                            Saved Playlists
                        </h2>
                        <p style="font-size: 0.82rem; color: rgba(225, 232, 240, 0.65); margin: 4px 0 0 0;">
                            Browse previously generated playlists saved to your database.
                        </p>
                    </div>
                    <a href="/playlists" class="btn-playlist-action btn-playlist-outline" style="font-size: 0.8rem; padding: 6px 14px;">
                        <span>✨</span> Create New Playlist
                    </a>
                </div>
                {saved_playlists_html}
            </div>
        </div>
    </div>

    <!-- Spotify Export Modal -->
    <div class="playlist-modal-backdrop" id="spotify-modal-backdrop" onclick="closeSpotifyModal(event)">
        <div class="playlist-modal" onclick="event.stopPropagation()">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <span style="font-size: 1.5rem;">🎧</span>
                    <h3 style="margin: 0; font-size: 1.15rem; font-family: 'Montserrat', sans-serif; font-weight: 800; color: #1DB954;">
                        Spotify Playlist Export
                    </h3>
                </div>
                <button type="button" onclick="closeSpotifyModal()" style="background: none; border: none; color: #A5C8FF; font-size: 1.4rem; cursor: pointer;">&times;</button>
            </div>
            <div id="spotify-modal-content">
                <!-- Content injected dynamically -->
            </div>
        </div>
    </div>

    <!-- Floating Toast Notification -->
    <div id="toast-notification"></div>

    <script>
        const CURRENT_PLAYLIST = {current_playlist_json};
        const IS_SPOTIFY_CONNECTED = {is_spotify_connected_json};

        function showToast(msg) {
            const toast = document.getElementById('toast-notification');
            if (!toast) return;
            toast.textContent = msg;
            toast.classList.add('show');
            setTimeout(() => {
                toast.classList.remove('show');
            }, 3000);
        }

        function filterPlaylistTracks() {
            const query = (document.getElementById('tracklist-filter-input').value || '').toLowerCase().trim();
            const rows = document.querySelectorAll('.playlist-tracklist-row');
            let visible = 0;
            rows.forEach(r => {
                const text = (r.getAttribute('data-search') || '').toLowerCase();
                if (!query || text.includes(query)) {
                    r.style.display = '';
                    visible++;
                } else {
                    r.style.display = 'none';
                }
            });
            const noMatch = document.getElementById('playlist-no-tracks');
            if (noMatch) {
                noMatch.style.display = (visible === 0 && rows.length > 0) ? 'block' : 'none';
            }
        }

        function copyPlaylistTracklist() {
            if (!CURRENT_PLAYLIST || !CURRENT_PLAYLIST.tracks || CURRENT_PLAYLIST.tracks.length === 0) {
                showToast("No tracks to copy!");
                return;
            }
            const lines = CURRENT_PLAYLIST.tracks.map((t, idx) => `${idx + 1}. ${t.artist} - ${t.song}`);
            const text = `${CURRENT_PLAYLIST.name}\\n${lines.join('\\n')}`;
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(() => {
                    showToast("Copied " + CURRENT_PLAYLIST.tracks.length + " tracks to clipboard!");
                }).catch(() => {
                    fallbackCopy(text);
                });
            } else {
                fallbackCopy(text);
            }
        }

        function fallbackCopy(text) {
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.select();
            try {
                document.execCommand('copy');
                showToast("Copied tracklist to clipboard!");
            } catch (e) {
                showToast("Could not copy automatically.");
            }
            document.body.removeChild(ta);
        }

        async function savePlaylistToCallingHours() {
            const btn = document.getElementById('btn-save-db');
            if (btn) {
                btn.disabled = true;
                btn.textContent = 'Saving...';
            }
            try {
                const payload = {
                    name: document.getElementById('input-playlist-name').value || CURRENT_PLAYLIST.name,
                    description: CURRENT_PLAYLIST.description,
                    generator_type: CURRENT_PLAYLIST.mode,
                    criteria: CURRENT_PLAYLIST.criteria || {},
                    items: CURRENT_PLAYLIST.tracks || []
                };
                const resp = await fetch('/api/playlists/save', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                const res = await resp.json();
                if (res.success) {
                    showToast("Playlist saved to Calling Hours library!");
                    if (btn) btn.textContent = '✓ Saved';
                } else {
                    showToast("Error saving playlist: " + (res.error || 'Unknown error'));
                    if (btn) { btn.disabled = false; btn.textContent = '💾 Save'; }
                }
            } catch (err) {
                showToast("Save request failed.");
                if (btn) { btn.disabled = false; btn.textContent = '💾 Save'; }
            }
        }

        function triggerSpotifyExport() {
            const backdrop = document.getElementById('spotify-modal-backdrop');
            const content = document.getElementById('spotify-modal-content');
            backdrop.style.display = 'flex';

            if (!IS_SPOTIFY_CONNECTED) {
                content.innerHTML = `
                    <p style="color: rgba(225, 232, 240, 0.85); font-size: 0.92rem; line-height: 1.5; margin-bottom: 20px;">
                        Connect your personal Spotify account to export Calling Hours playlists directly into your Spotify library with 1 click!
                    </p>
                    <div style="display: flex; gap: 10px; justify-content: flex-end;">
                        <button type="button" class="btn-playlist-action btn-playlist-outline" onclick="closeSpotifyModal()">Cancel</button>
                        <a href="/auth/spotify" class="btn-playlist-action btn-playlist-spotify">Connect Spotify Account</a>
                    </div>
                `;
                return;
            }

            content.innerHTML = `
                <div style="text-align: center; padding: 20px 10px;">
                    <div style="font-size: 2rem; animation: spin 1s linear infinite; display: inline-block;">⏳</div>
                    <div style="font-weight: 700; color: #E1E8F0; margin-top: 14px; font-size: 1.05rem;">Exporting to Spotify...</div>
                    <div style="color: rgba(225, 232, 240, 0.6); font-size: 0.84rem; margin-top: 6px;">Matching track catalog and creating your Spotify playlist.</div>
                </div>
                <style>@keyframes spin { 100% { transform: rotate(360deg); } }</style>
            `;

            const payload = {
                name: document.getElementById('input-playlist-name').value || CURRENT_PLAYLIST.name,
                description: CURRENT_PLAYLIST.description,
                tracks: CURRENT_PLAYLIST.tracks || []
            };

            fetch('/api/playlists/export-spotify', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            })
            .then(async r => {
                const text = await r.text();
                try {
                    return JSON.parse(text);
                } catch (pe) {
                    throw new Error(text || `Server returned empty or invalid response (HTTP ${r.status})`);
                }
            })
            .then(data => {
                if (data.success) {
                    content.innerHTML = `
                        <div style="padding: 10px 0;">
                            <div style="color: #1DB954; font-size: 2.2rem; text-align: center; margin-bottom: 10px;">🎉</div>
                            <h4 style="margin: 0 0 10px 0; font-size: 1.15rem; color: #FFFFFF; text-align: center; font-family: 'Montserrat', sans-serif;">
                                Playlist Created on Spotify!
                            </h4>
                            <p style="color: rgba(225, 232, 240, 0.8); font-size: 0.88rem; line-height: 1.5; text-align: center;">
                                Successfully created <strong>${data.playlist_name}</strong> with <strong>${data.tracks_added} tracks</strong> added to your Spotify account.
                            </p>
                            <div style="margin-top: 24px; display: flex; gap: 10px; justify-content: center;">
                                <a href="${data.playlist_url}" target="_blank" rel="noopener noreferrer" class="btn-playlist-action btn-playlist-spotify" style="text-decoration: none;">
                                    Open in Spotify ↗
                                </a>
                                <button type="button" class="btn-playlist-action btn-playlist-outline" onclick="closeSpotifyModal()">Done</button>
                            </div>
                        </div>
                    `;
                } else if (data.needs_reauth || (data.error && (data.error.includes('403') || data.error.includes('Forbidden') || data.error.toLowerCase().includes('permission')))) {
                    content.innerHTML = `
                        <div style="padding: 10px 0; text-align: center;">
                            <div style="font-size: 2.2rem; margin-bottom: 10px;">🔐</div>
                            <h4 style="margin: 0 0 10px 0; color: #FFFFFF; font-size: 1.15rem; font-family: 'Montserrat', sans-serif;">
                                Spotify Permissions Update Required
                            </h4>
                            <p style="color: rgba(225, 232, 240, 0.85); font-size: 0.88rem; line-height: 1.5; margin-bottom: 20px;">
                                Your Spotify account was connected before playlist creation permissions were added. Please re-authorize your Spotify account with 1 click to grant permission to create playlists in your library.
                            </p>
                            <div style="display: flex; gap: 10px; justify-content: center;">
                                <a href="${data.auth_url || '/auth/spotify'}" class="btn-playlist-action btn-playlist-spotify" style="text-decoration: none;">
                                    <span>🟢</span> Re-Authorize Spotify (1-Click)
                                </a>
                                <button type="button" class="btn-playlist-action btn-playlist-outline" onclick="closeSpotifyModal()">Cancel</button>
                            </div>
                        </div>
                    `;
                } else {
                    content.innerHTML = `
                        <div style="padding: 10px 0;">
                            <div style="color: #f08c5a; font-size: 2rem; text-align: center; margin-bottom: 10px;">⚠️</div>
                            <h4 style="margin: 0 0 10px 0; color: #FFFFFF; text-align: center;">Export Failed</h4>
                            <p style="color: rgba(225, 232, 240, 0.8); font-size: 0.86rem; text-align: center;">
                                ${data.error || 'An error occurred during Spotify export.'}
                            </p>
                            <div style="margin-top: 20px; display: flex; justify-content: center;">
                                <button type="button" class="btn-playlist-action btn-playlist-outline" onclick="closeSpotifyModal()">Close</button>
                            </div>
                        </div>
                    `;
                }
            })
            .catch(err => {
                content.innerHTML = `
                    <div style="padding: 10px 0;">
                        <div style="color: #f08c5a; font-size: 2rem; text-align: center; margin-bottom: 10px;">⚠️</div>
                        <h4 style="margin: 0 0 10px 0; color: #FFFFFF; text-align: center;">Export Error</h4>
                        <p style="color: rgba(225, 232, 240, 0.85); font-size: 0.86rem; text-align: center; line-height: 1.4;">
                            ${(err && err.message) ? err.message : 'Network or server error while exporting.'}
                        </p>
                        <div style="margin-top: 20px; display: flex; justify-content: center;">
                            <button type="button" class="btn-playlist-action btn-playlist-outline" onclick="closeSpotifyModal()">Close</button>
                        </div>
                    </div>
                `;
            });
        }

        function closeSpotifyModal(e) {
            if (e && e.target !== e.currentTarget) return;
            const backdrop = document.getElementById('spotify-modal-backdrop');
            if (backdrop) backdrop.style.display = 'none';
        }
    </script>
</body>
</html>'''

LOGIN_PAGE_HTML = PAGE_HTML.split('<body>')[0] + '''<body>
    <div class="stars"></div>
    <div class="horizon"></div>
    <div class="login-wrapper">
        <div class="login-card">
            <div class="login-brand">
                <svg class="login-logo" viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/>
                </svg>
                <h1 class="login-title">Calling Hours</h1>
                <p class="login-subtitle">Song Lyric Search &amp; Gemini Poetic Analysis</p>
            </div>

            {error_banner}

            <div class="login-body">
                <h2 class="login-heading">Sign In</h2>
                <p class="login-instruction">Sign in with your authorized Google account to access Calling Hours.</p>
                
                {login_button_or_notice}
            </div>

            <div class="login-footer">
                <span>Calling Hours &bull; Powered by Google Gemini</span>
            </div>
        </div>
    </div>
</body>
</html>'''

UNAUTHORIZED_PAGE_HTML = PAGE_HTML.split('<body>')[0] + '''<body>
    <div class="stars"></div>
    <div class="horizon"></div>
    <div class="login-wrapper">
        <div class="login-card" style="border-color: rgba(240, 140, 90, 0.4);">
            <div class="login-brand">
                <div style="font-size: 3rem; margin-bottom: 8px;">🔒</div>
                <h1 class="login-title" style="color: #f08c5a;">Access Restricted</h1>
                <p class="login-subtitle">Calling Hours is currently private</p>
            </div>

            <div class="login-body">
                <p style="font-size: 1.05rem; line-height: 1.5; color: #E1E8F0; margin: 0 0 12px 0;">
                    The Google account <strong>{email}</strong> is not authorized to access this application.
                </p>
                <p style="font-size: 0.9rem; color: #A5C8FF; line-height: 1.5; margin: 0 0 20px 0;">
                    Access is currently granted on an invitation-only basis. Please ask the administrator (<code>jpmclaug@gmail.com</code>) to grant you access.
                </p>
                
                <div style="display: flex; flex-direction: column; gap: 12px; margin-top: 16px;">
                    <a href="/auth/google" class="btn-google-signin" style="justify-content: center; width: 100%;">
                        <span>Sign In with a Different Account</span>
                    </a>
                    <a href="/login" style="color: #A5C8FF; text-decoration: none; font-size: 0.88rem; margin-top: 6px;">&larr; Return to Sign In</a>
                </div>
            </div>
        </div>
    </div>
</body>
</html>'''

ADMIN_PAGE_HTML = PAGE_HTML.split('<body>')[0] + '''<body>
    <div class="stars"></div>
    <div class="horizon"></div>
    {app_header}
    <div class="container" style="flex-direction: column; width: min(1080px, 95vw);">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; flex-wrap: wrap; gap: 12px;">
            <div>
                <h1 style="font-size: 2rem; margin: 0; display: flex; align-items: center; gap: 10px;">
                    <span>🛡️ User Access Management</span>
                </h1>
                <p style="margin: 4px 0 0 0; color: #A5C8FF; font-size: 0.95rem;">Manage authorized Google accounts and administrative permissions</p>
            </div>
            <a href="/" style="color:#A5C8FF; text-decoration:none; font-size: 1rem; border-bottom: 1px dotted #A5C8FF;">&larr; Back to Song</a>
        </div>

        {admin_message_block}

        <!-- Stats Overview -->
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 28px;">
            <div class="admin-stat-card">
                <div class="stat-number">{total_users_count}</div>
                <div class="stat-label">Total Authorized Users</div>
            </div>
            <div class="admin-stat-card">
                <div class="stat-number" style="color: #5af0a5;">{active_users_count}</div>
                <div class="stat-label">Active Users</div>
            </div>
            <div class="admin-stat-card">
                <div class="stat-number" style="color: #FFD700;">{admin_users_count}</div>
                <div class="stat-label">Administrators</div>
            </div>
        </div>

        <!-- Add User Form -->
        <div class="admin-card" style="margin-bottom: 28px;">
            <h2 style="margin-top: 0; font-size: 1.25rem; color: #A5C8FF; display: flex; align-items: center; gap: 8px;">
                <span>➕ Grant Access to New User</span>
            </h2>
            <p style="color: rgba(225, 232, 240, 0.7); font-size: 0.9rem; margin-top: 4px; margin-bottom: 16px;">
                Add a Google email address to allow that user to sign in immediately.
            </p>
            <form method="post" action="/admin/user/add" class="admin-add-user-form">
                <div>
                    <label for="new_email" style="display: block; font-size: 0.85rem; color: #A5C8FF; margin-bottom: 4px;">Google Email *</label>
                    <input type="email" id="new_email" name="email" placeholder="e.g. friend@gmail.com" required style="width: 100%; box-sizing: border-box;">
                </div>
                <div>
                    <label for="new_name" style="display: block; font-size: 0.85rem; color: #A5C8FF; margin-bottom: 4px;">Display Name (Optional)</label>
                    <input type="text" id="new_name" name="name" placeholder="e.g. Alex Smith" style="width: 100%; box-sizing: border-box;">
                </div>
                <div>
                    <label for="new_role" style="display: block; font-size: 0.85rem; color: #A5C8FF; margin-bottom: 4px;">Role</label>
                    <select id="new_role" name="role" style="width: 100%; box-sizing: border-box;">
                        <option value="user">User</option>
                        <option value="admin">Administrator</option>
                    </select>
                </div>
                <div>
                    <button type="submit" style="padding: 10px 20px; white-space: nowrap;">Grant Access</button>
                </div>
            </form>
        </div>

        <!-- User Directory Table -->
        <div class="admin-card">
            <h2 style="margin-top: 0; font-size: 1.25rem; color: #A5C8FF; margin-bottom: 16px;">
                Authorized Users Directory
            </h2>
            <div style="overflow-x: auto;">
                <table class="admin-table">
                    <thead>
                        <tr>
                            <th>User</th>
                            <th>Role</th>
                            <th>Status</th>
                            <th>Created</th>
                            <th>Last Active</th>
                            <th style="text-align: right;">Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        {users_table_rows}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</body>
</html>'''

class CallingHoursRequestHandler(http.server.BaseHTTPRequestHandler):
    def is_request_secure(self) -> bool:
        proto = self.headers.get('X-Forwarded-Proto', '').lower()
        return proto == 'https' or IS_CLOUD_RUN

    def get_current_user(self) -> Optional[Dict[str, Any]]:
        cookies = parse_cookies(self.headers)
        session_id = cookies.get('session_id')
        if not session_id:
            return None
        return database.get_session_user(session_id)

    def handle_logout(self):
        cookies = parse_cookies(self.headers)
        session_id = cookies.get('session_id')
        if session_id:
            database.delete_session(session_id)
        expired_cookie = build_cookie_header('session_id', '', max_age=0, secure=self.is_request_secure())
        self.send_response(302)
        self.send_header('Set-Cookie', expired_cookie)
        self.send_header('Location', '/login')
        self.end_headers()

    def handle_google_auth(self):
        if not GOOGLE_CLIENT_ID:
            self.send_response(302)
            self.send_header('Location', '/login?error=oauth_unconfigured')
            self.end_headers()
            return

        oauth_state = secrets.token_urlsafe(16)
        redirect_uri = get_google_redirect_uri(self.headers.get('Host'))
        params = {
            'client_id': GOOGLE_CLIENT_ID,
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'scope': 'openid email profile',
            'state': oauth_state,
            'access_type': 'online',
            'prompt': 'select_account',
        }
        auth_url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"
        state_cookie = build_cookie_header('oauth_state', oauth_state, max_age=300, secure=self.is_request_secure())
        self.send_response(302)
        self.send_header('Set-Cookie', state_cookie)
        self.send_header('Location', auth_url)
        self.end_headers()

    def handle_google_callback(self, query: str):
        params = urllib.parse.parse_qs(query)
        if params.get('error'):
            err = params.get('error', ['Unknown error'])[0]
            self.send_response(302)
            self.send_header('Location', f'/login?error={urllib.parse.quote(err)}')
            self.end_headers()
            return

        code = params.get('code', [''])[0].strip()
        if not code:
            self.send_response(302)
            self.send_header('Location', '/login?error=missing_code')
            self.end_headers()
            return

        redirect_uri = get_google_redirect_uri(self.headers.get('Host'))
        try:
            token_resp = requests.post(
                GOOGLE_TOKEN_URL,
                data={
                    'client_id': GOOGLE_CLIENT_ID,
                    'client_secret': GOOGLE_CLIENT_SECRET,
                    'code': code,
                    'grant_type': 'authorization_code',
                    'redirect_uri': redirect_uri,
                },
                headers={'Accept': 'application/json'},
                timeout=10
            )
            token_resp.raise_for_status()
            token_data = token_resp.json()
            access_token = token_data.get('access_token')
            if not access_token:
                raise ValueError("No access token returned from Google")

            userinfo_resp = requests.get(
                GOOGLE_USERINFO_URL,
                headers={'Authorization': f'Bearer {access_token}'},
                timeout=10
            )
            userinfo_resp.raise_for_status()
            user_info = userinfo_resp.json()

            user_email = user_info.get('email', '').lower().strip()
            user_name = user_info.get('name', '')
            user_picture = user_info.get('picture', '')

            if not user_email:
                raise ValueError("No email found in Google profile")

            user_record = database.get_user(user_email)
            if user_record and user_record.get('is_active'):
                database.update_user_last_login(user_email, name=user_name, picture=user_picture)
                session_id = database.create_session(user_email)
                cookie_header = build_cookie_header(
                    'session_id', session_id,
                    secure=self.is_request_secure()
                )
                self.send_response(302)
                self.send_header('Set-Cookie', cookie_header)
                self.send_header('Location', '/')
                self.end_headers()
            else:
                self.send_response(302)
                self.send_header('Location', f'/unauthorized?email={urllib.parse.quote(user_email)}')
                self.end_headers()
        except Exception as e:
            print(f"Google OAuth callback error: {e}")
            self.send_response(302)
            self.send_header('Location', f'/login?error={urllib.parse.quote(str(e))}')
            self.end_headers()

    def handle_dev_login(self, email: str = 'jpmclaug@gmail.com'):
        clean_email = email.lower().strip()
        user = database.get_user(clean_email)
        if not user or not user.get('is_active'):
            if clean_email == database.PRIMARY_ADMIN_EMAIL.lower().strip():
                database.upsert_user(clean_email, name='JP McLaughlin', is_admin=True, is_active=True)
                user = database.get_user(clean_email)
            else:
                self.send_response(302)
                self.send_header('Location', f'/unauthorized?email={urllib.parse.quote(clean_email)}')
                self.end_headers()
                return

        session_id = database.create_session(clean_email)
        cookie_header = build_cookie_header('session_id', session_id, secure=self.is_request_secure())
        self.send_response(302)
        self.send_header('Set-Cookie', cookie_header)
        self.send_header('Location', '/')
        self.end_headers()

    def render_login_page(self, error: str = ''):
        if self.get_current_user():
            self.send_response(302)
            self.send_header('Location', '/')
            self.end_headers()
            return

        error_banner = ''
        if error:
            error_msg_map = {
                'missing_code': 'Google authorization was not completed. Missing code parameter.',
                'oauth_unconfigured': 'Google OAuth credentials are not configured in calling_hours_secrets.py.',
                'access_denied': 'Sign-in was cancelled or denied by Google.',
            }
            display_err = error_msg_map.get(error, error)
            error_banner = f'<div class="message" style="border-color: rgba(240,140,90,0.5); color: #f08c5a; margin-bottom: 20px;">{html_escape(display_err)}</div>'

        redirect_uri = get_google_redirect_uri(self.headers.get('Host'))

        if GOOGLE_CLIENT_ID:
            button_or_notice = '''
                <a href="/auth/google" class="btn-google-signin">
                    <svg class="google-icon" viewBox="0 0 48 48">
                        <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>
                        <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>
                        <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>
                        <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
                    </svg>
                    <span>Sign in with Google</span>
                </a>
            '''
        else:
            button_or_notice = f'''
                <div class="google-config-notice">
                    <h3>Google OAuth Setup Required</h3>
                    <p>Configure <code>GOOGLE_CLIENT_ID</code> and <code>GOOGLE_CLIENT_SECRET</code> in <code>calling_hours_secrets.py</code> or environment variables to enable live Google authentication.</p>
                    <div style="margin-top: 14px; text-align: left; font-size: 0.85rem; color: #E1E8F0; background: rgba(0,0,0,0.3); padding: 12px; border-radius: 8px;">
                        <div><strong>1.</strong> Create credentials at <a href="https://console.cloud.google.com/apis/credentials" target="_blank" style="color: #A8D2FF; text-decoration: underline;">Google Cloud Console</a></div>
                        <div><strong>2.</strong> Authorized redirect URI: <code style="color: #A8D2FF;">{html_escape(redirect_uri)}</code></div>
                        <div><strong>3.</strong> Add credentials to <code>calling_hours_secrets.py</code></div>
                    </div>
                    <div style="margin-top: 18px;">
                        <a href="/auth/dev-login" class="btn-dev-signin">
                            ⚡ Quick Sign-In as jpmclaug@gmail.com
                        </a>
                    </div>
                </div>
            '''

        content = LOGIN_PAGE_HTML.replace('{error_banner}', error_banner)\
                                 .replace('{login_button_or_notice}', button_or_notice)

        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(content.encode('utf-8'))))
        self.end_headers()
        self.wfile.write(content.encode('utf-8'))

    def render_unauthorized_page(self, email: str = ''):
        content = UNAUTHORIZED_PAGE_HTML.replace('{email}', html_escape(email or 'Your account'))
        self.send_response(403)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(content.encode('utf-8'))))
        self.end_headers()
        self.wfile.write(content.encode('utf-8'))

    def render_admin_page(self, message: str = '', user: Optional[Dict[str, Any]] = None):
        users = database.get_all_users()
        total_count = len(users)
        active_count = sum(1 for u in users if u.get('is_active'))
        admin_count = sum(1 for u in users if u.get('is_admin'))

        message_html = ''
        if message:
            message_html = f'<div class="message" style="margin-bottom: 24px;">{html_escape(message)}</div>'

        rows = []
        for u in users:
            u_email = u.get('email', '')
            u_name = u.get('name') or u_email.split('@')[0]
            u_picture = u.get('picture')
            is_admin = u.get('is_admin')
            is_active = u.get('is_active')
            is_primary = (u_email.lower().strip() == database.PRIMARY_ADMIN_EMAIL.lower().strip())
            created_at = u.get('created_at') or 'Unknown'
            last_login = u.get('last_login_at') or 'Never'

            if u_picture and str(u_picture).strip():
                avatar_html = f'<img src="{html_escape(u_picture)}" class="user-avatar" alt="Avatar" referrerpolicy="no-referrer">'
            else:
                initial = (u_name[0] if u_name else 'U').upper()
                avatar_html = f'<div class="user-avatar-initial">{html_escape(initial)}</div>'

            role_badge = '<span class="badge-role-admin">Administrator</span>' if is_admin else '<span class="badge-role-user">User</span>'
            status_badge = '<span class="badge-active">Active</span>' if is_active else '<span class="badge-suspended">Suspended</span>'

            if is_primary:
                actions_html = '<span style="font-size: 0.78rem; color: #FFD700; font-weight: 700;">★ Primary Superadmin</span>'
            else:
                status_btn_text = "Deactivate" if is_active else "Activate"
                status_btn_class = "btn-action-sm btn-action-danger" if is_active else "btn-action-sm"
                role_btn_text = "Demote to User" if is_admin else "Make Admin"
                actions_html = f'''
                <div style="display: flex; gap: 8px; justify-content: flex-end; align-items: center; flex-wrap: wrap;">
                    <form method="post" action="/admin/user/toggle-role" style="display: inline; margin: 0;">
                        <input type="hidden" name="email" value="{html_escape(u_email)}">
                        <button type="submit" class="btn-action-sm">{role_btn_text}</button>
                    </form>
                    <form method="post" action="/admin/user/toggle-status" style="display: inline; margin: 0;">
                        <input type="hidden" name="email" value="{html_escape(u_email)}">
                        <button type="submit" class="{status_btn_class}">{status_btn_text}</button>
                    </form>
                    <form method="post" action="/admin/user/delete" style="display: inline; margin: 0;" onsubmit="return confirm('Permanently remove {html_escape(u_email)}?');">
                        <input type="hidden" name="email" value="{html_escape(u_email)}">
                        <button type="submit" class="btn-action-sm btn-action-danger" title="Delete User">&times;</button>
                    </form>
                </div>
                '''

            rows.append(f'''
            <tr>
                <td>
                    <div style="display: flex; align-items: center; gap: 10px;">
                        {avatar_html}
                        <div>
                            <div style="font-weight: 700; color: #E1E8F0;">{html_escape(u_name)}</div>
                            <div style="font-size: 0.82rem; color: #A5C8FF;">{html_escape(u_email)}</div>
                        </div>
                    </div>
                </td>
                <td>{role_badge}</td>
                <td>{status_badge}</td>
                <td style="font-size: 0.8rem; color: rgba(225, 232, 240, 0.6);">{html_escape(str(created_at))}</td>
                <td style="font-size: 0.8rem; color: rgba(225, 232, 240, 0.6);">{html_escape(str(last_login))}</td>
                <td style="text-align: right;">{actions_html}</td>
            </tr>
            ''')

        table_rows_html = '\n'.join(rows) if rows else '<tr><td colspan="6" style="text-align:center; color:#A5C8FF;">No users found.</td></tr>'

        content = ADMIN_PAGE_HTML.replace('{app_header}', build_app_header('admin', user=user))\
                                 .replace('{admin_message_block}', message_html)\
                                 .replace('{total_users_count}', str(total_count))\
                                 .replace('{active_users_count}', str(active_count))\
                                 .replace('{admin_users_count}', str(admin_count))\
                                 .replace('{users_table_rows}', table_rows_html)

        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(content.encode('utf-8'))))
        self.end_headers()
        self.wfile.write(content.encode('utf-8'))

    def _handle_internal_error(self, err: Exception, is_post: bool = False):
        traceback.print_exc()
        try:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path.startswith('/api/'):
                self.send_response(500)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Internal Server Error', 'detail': str(err)}).encode('utf-8'))
                return
            err_msg = f'<div class="message error"><strong>Notice:</strong> An unexpected error occurred: {html_escape(str(err))}. Please try again.</div>'
            self.send_response(200)
            self.render_page(message=err_msg, lyrics_text='')
        except Exception as fallback_e:
            print(f"Fallback error rendering failed: {fallback_e}")
            try:
                self.send_error(500, "Internal Server Error")
            except Exception:
                pass

    def do_GET(self):
        try:
            self._do_GET()
        except Exception as e:
            self._handle_internal_error(e)

    def _do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        # 1. Health check (unauthenticated)
        if parsed.path == '/healthz':
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(b'OK')
            return

        # 2. Static files (unauthenticated)
        static_files = {
            '/manifest.json': (os.path.join(SCRIPT_DIR, 'manifest.json'), 'application/manifest+json'),
            '/apple-touch-icon.png': (os.path.join(SCRIPT_DIR, 'static', 'apple-touch-icon.png'), 'image/png'),
            '/apple-touch-icon-precomposed.png': (os.path.join(SCRIPT_DIR, 'static', 'apple-touch-icon.png'), 'image/png'),
            '/icon-192.png': (os.path.join(SCRIPT_DIR, 'static', 'icon-192.png'), 'image/png'),
            '/icon-512.png': (os.path.join(SCRIPT_DIR, 'static', 'icon-512.png'), 'image/png'),
            '/favicon-32x32.png': (os.path.join(SCRIPT_DIR, 'static', 'favicon-32x32.png'), 'image/png'),
            '/favicon.ico': (os.path.join(SCRIPT_DIR, 'static', 'favicon.ico'), 'image/x-icon'),
            '/favicon.svg': (os.path.join(SCRIPT_DIR, 'static', 'logo.svg'), 'image/svg+xml'),
            '/logo.svg': (os.path.join(SCRIPT_DIR, 'static', 'logo.svg'), 'image/svg+xml'),
        }
        if parsed.path in static_files:
            file_path, content_type = static_files[parsed.path]
            if os.path.exists(file_path):
                with open(file_path, 'rb') as f:
                    content = f.read()
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(content)))
                self.send_header('Cache-Control', 'public, max-age=86400')
                self.end_headers()
                self.wfile.write(content)
                return

        if parsed.path.startswith('/static/'):
            filename = os.path.basename(parsed.path)
            file_path = os.path.join(SCRIPT_DIR, 'static', filename)
            if os.path.exists(file_path) and os.path.isfile(file_path):
                ext = os.path.splitext(file_path)[1].lower()
                mime_map = {
                    '.png': 'image/png',
                    '.jpg': 'image/jpeg',
                    '.jpeg': 'image/jpeg',
                    '.ico': 'image/x-icon',
                    '.svg': 'image/svg+xml',
                    '.json': 'application/json',
                }
                content_type = mime_map.get(ext, 'application/octet-stream')
                with open(file_path, 'rb') as f:
                    content = f.read()
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(content)))
                self.send_header('Cache-Control', 'public, max-age=86400')
                self.end_headers()
                self.wfile.write(content)
                return

        # 3. Public Auth Routes
        if parsed.path == '/login':
            params = urllib.parse.parse_qs(parsed.query)
            err = params.get('error', [''])[0]
            self.render_login_page(error=err)
            return

        if parsed.path == '/logout':
            self.handle_logout()
            return

        if parsed.path == '/auth/google':
            self.handle_google_auth()
            return

        if parsed.path == '/auth/google/callback':
            self.handle_google_callback(parsed.query)
            return

        if parsed.path == '/auth/dev-login':
            params = urllib.parse.parse_qs(parsed.query)
            email = params.get('email', ['jpmclaug@gmail.com'])[0]
            self.handle_dev_login(email)
            return

        if parsed.path == '/unauthorized':
            params = urllib.parse.parse_qs(parsed.query)
            email = params.get('email', [''])[0]
            self.render_unauthorized_page(email=email)
            return

        # 4. Require authentication for all remaining routes
        current_user = self.get_current_user()
        if not current_user:
            if parsed.path.startswith('/api/'):
                self.send_response(401)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Unauthorized', 'login_url': '/login'}).encode('utf-8'))
                return
            self.send_response(302)
            self.send_header('Location', '/login')
            self.end_headers()
            return

        # 5. Admin routes
        if parsed.path == '/admin':
            if not current_user.get('is_admin'):
                self.send_error(403, 'Forbidden: Administrator access required.')
                return
            params = urllib.parse.parse_qs(parsed.query)
            msg = params.get('msg', [''])[0]
            self.render_admin_page(message=msg, user=current_user)
            return

        # 6. Protected application routes
        if parsed.path == '/authorize':
            self.handle_authorize()
            return

        if parsed.path == '/callback':
            self.handle_callback(parsed.query)
            return

        if parsed.path in ('/submit', '/analyze'):
            self.send_response(302)
            self.send_header('Location', '/')
            self.end_headers()
            return

        if parsed.path == '/prompts/save':
            self.send_response(302)
            self.send_header('Location', '/prompts')
            self.end_headers()
            return

        if parsed.path == '/prompts':
            self.render_prompts_page()
            return

        if parsed.path == '/spotify':
            params = urllib.parse.parse_qs(parsed.query)
            demo_mode = params.get('demo', ['0'])[0] == '1'
            msg = params.get('msg', [''])[0]
            err = params.get('error', [''])[0]
            if params.get('connected', ['0'])[0] == '1':
                msg = 'Successfully connected your Spotify account!'
            elif params.get('disconnected', ['0'])[0] == '1':
                msg = 'Disconnected Spotify account.'
            elif err:
                msg = f'Spotify error: {err}'
            refresh = params.get('refresh', ['0'])[0] == '1'
            self.render_spotify_page(demo=demo_mode, message=msg, refresh=refresh)
            return

        if parsed.path == '/auth/spotify':
            self.handle_spotify_auth()
            return

        if parsed.path == '/auth/spotify/callback':
            self.handle_spotify_callback(parsed.query)
            return

        if parsed.path == '/auth/spotify/disconnect':
            self.handle_spotify_disconnect()
            return

        if parsed.path == '/api/spotify/history':
            self.handle_api_spotify_history(parsed.query)
            return

        if parsed.path == '/api/spotify/now-playing':
            self.handle_api_spotify_now_playing()
            return

        if parsed.path == '/api/spotify/status':
            self.handle_api_spotify_status()
            return

        if parsed.path == '/api/spotify/sync':
            self.handle_api_spotify_sync()
            return

        if parsed.path in ('/playlists', '/playlist-generator'):
            params = urllib.parse.parse_qs(parsed.query)
            mode = params.get('mode', ['all_analyzed'])[0].strip()
            artist = params.get('artist', [''])[0].strip()
            tag = params.get('tag', [''])[0].strip()
            order = params.get('order', ['updated_at DESC'])[0].strip()
            mood = params.get('mood', [''])[0].strip()
            limit_str = params.get('limit', [''])[0].strip()
            limit = int(limit_str) if limit_str.isdigit() else None
            playlist_name = params.get('name', [''])[0].strip()
            saved_id_str = params.get('id', [''])[0].strip()
            saved_id = int(saved_id_str) if saved_id_str.isdigit() else None
            tab = params.get('tab', ['generate'])[0].strip()
            msg = params.get('msg', [''])[0].strip()
            self.render_playlists_page(
                mode=mode,
                selected_artist=artist,
                selected_tag=tag,
                selected_mood=mood,
                order_by=order,
                limit=limit,
                custom_name=playlist_name,
                saved_id=saved_id,
                active_tab=tab,
                message=msg
            )
            return

        if parsed.path == '/playlists/export/m3u':
            self.handle_export_m3u(parsed.query)
            return

        if parsed.path == '/playlists/export/csv':
            self.handle_export_csv(parsed.query)
            return

        if parsed.path == '/playlists/delete':
            params = urllib.parse.parse_qs(parsed.query)
            pid = params.get('id', [''])[0].strip()
            if pid.isdigit():
                database.delete_saved_playlist(int(pid))
            self.send_response(302)
            self.send_header('Location', '/playlists?tab=saved&msg=' + urllib.parse.quote('Playlist deleted successfully.'))
            self.end_headers()
            return

        if parsed.path == '/api/playlists/analyzed-songs':
            params = urllib.parse.parse_qs(parsed.query)
            artist = params.get('artist', [''])[0].strip() or None
            tag = params.get('tag', [''])[0].strip() or None
            order = params.get('order', ['updated_at DESC'])[0].strip()
            limit_str = params.get('limit', [''])[0].strip()
            limit = int(limit_str) if limit_str.isdigit() else None
            songs = database.get_analyzed_songs(artist=artist, tag=tag, order_by=order, limit=limit)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({'songs': songs, 'count': len(songs)}).encode('utf-8'))
            return

        if parsed.path == '/api/playlists/saved':
            playlists = database.get_saved_playlists()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({'playlists': playlists}).encode('utf-8'))
            return

        if parsed.path == '/history':
            params = urllib.parse.parse_qs(parsed.query)
            selected_artist = params.get('artist', [''])[0].strip()
            self.render_history_page(selected_artist=selected_artist)
            return

        if parsed.path == '/history/delete':
            params = urllib.parse.parse_qs(parsed.query)
            search_id = params.get('id', [''])[0].strip()
            if search_id.isdigit():
                database.delete_search(int(search_id))
            self.send_response(302)
            self.send_header('Location', '/history')
            self.end_headers()
            return

        if parsed.path == '/api/bands':
            bands = database.get_distinct_bands()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({'bands': bands}).encode('utf-8'))
            return

        if parsed.path == '/api/songs':
            params = urllib.parse.parse_qs(parsed.query)
            artist = params.get('artist', [''])[0].strip()
            songs = database.get_songs_by_band(artist) if artist else []
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({'songs': songs}).encode('utf-8'))
            return

        if parsed.path == '/api/search':
            params = urllib.parse.parse_qs(parsed.query)
            search_id = params.get('id', [''])[0].strip()
            record = database.get_search_by_id(int(search_id)) if search_id.isdigit() else None
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({'search': record}).encode('utf-8'))
            return

        if parsed.path == '/api/lastfm/track-tags':
            params = urllib.parse.parse_qs(parsed.query)
            artist = params.get('artist', [''])[0].strip()
            song = params.get('song', [''])[0].strip()
            refresh = params.get('refresh', ['0'])[0] == '1'
            tags = lastfm.get_or_fetch_track_tags(artist, song, api_key=LASTFM_API_KEY, force_refresh=refresh) if (artist and song) else []
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({'tags': tags, 'has_key': bool(LASTFM_API_KEY)}).encode('utf-8'))
            return

        if parsed.path == '/api/lastfm/artist':
            params = urllib.parse.parse_qs(parsed.query)
            artist = params.get('artist', [''])[0].strip()
            refresh = params.get('refresh', ['0'])[0] == '1'
            metadata = lastfm.get_or_fetch_artist_metadata(artist, api_key=LASTFM_API_KEY, force_refresh=refresh) if artist else {'artist': '', 'tags': [], 'top_tracks': []}
            metadata['has_key'] = bool(LASTFM_API_KEY)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(metadata).encode('utf-8'))
            return

        if parsed.path == '/api/theaudiodb/track':
            params = urllib.parse.parse_qs(parsed.query)
            artist = params.get('artist', [''])[0].strip()
            song = params.get('song', [''])[0].strip()
            refresh = params.get('refresh', ['0'])[0] == '1'
            data = theaudiodb.get_or_fetch_track_metadata(artist, song, api_key=THEAUDIODB_API_KEY, force_refresh=refresh) if (artist and song) else None
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({'theaudiodb_data': data}).encode('utf-8'))
            return

        if parsed.path == '/artist':
            params = urllib.parse.parse_qs(parsed.query)
            selected_artist = params.get('artist', [''])[0].strip()
            refresh = params.get('refresh', ['0'])[0] == '1'
            self.render_artist_page(selected_artist=selected_artist, refresh=refresh)
            return

        if parsed.path == '/api/setlistfm/artist':
            params = urllib.parse.parse_qs(parsed.query)
            artist = params.get('artist', [''])[0].strip()
            refresh = params.get('refresh', ['0'])[0] == '1'
            data = setlistfm.get_or_fetch_artist_setlist_data(artist, api_key=SETLIST_FM_API_KEY, force_refresh=refresh) if artist else {}
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(data).encode('utf-8'))
            return

        if parsed.path == '/api/spotify/artist':
            params = urllib.parse.parse_qs(parsed.query)
            artist = params.get('artist', [''])[0].strip()
            refresh = params.get('refresh', ['0'])[0] == '1'
            current_user = self.get_current_user()
            user_email = current_user.get('email') if current_user else None
            data = spotify.get_or_fetch_artist_spotify_data(artist, user_email=user_email, force_refresh=refresh, db_path=DATABASE_PATH) if artist else {}
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(data).encode('utf-8'))
            return

        if parsed.path == '/api/artist':
            params = urllib.parse.parse_qs(parsed.query)
            artist = params.get('artist', [''])[0].strip()
            refresh = params.get('refresh', ['0'])[0] == '1'
            current_user = self.get_current_user()
            user_email = current_user.get('email') if current_user else None
            s_data = setlistfm.get_or_fetch_artist_setlist_data(artist, api_key=SETLIST_FM_API_KEY, force_refresh=refresh, db_path=DATABASE_PATH) if artist else {}
            l_data = lastfm.get_or_fetch_artist_metadata(artist, api_key=LASTFM_API_KEY, force_refresh=refresh, db_path=DATABASE_PATH) if artist else {}
            a_data = theaudiodb.get_or_fetch_artist_details(artist, api_key=THEAUDIODB_API_KEY, force_refresh=refresh, db_path=DATABASE_PATH) if artist else {}
            sp_data = spotify.get_or_fetch_artist_spotify_data(artist, user_email=user_email, force_refresh=refresh, db_path=DATABASE_PATH) if artist else {}
            db_s = database.get_songs_by_band(artist, db_path=DATABASE_PATH) if artist else []
            resp_payload = {
                "artist": artist,
                "setlistfm": s_data,
                "lastfm": l_data,
                "theaudiodb": a_data,
                "spotify": sp_data,
                "songs": db_s,
            }
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(resp_payload).encode('utf-8'))
            return

        if parsed.path not in ('/', '/load'):
            self.send_error(404, 'Not Found')
            return

        params = urllib.parse.parse_qs(parsed.query)
        load_id = params.get('id', [''])[0].strip()
        artist_param = params.get('artist', [''])[0].strip()
        song_param = params.get('song', [''])[0].strip()
        auto_analyze = params.get('auto_analyze', ['0'])[0] == '1' or params.get('analyze', ['0'])[0] == '1'
        if load_id.isdigit():
            rec = database.get_search_by_id(int(load_id))
            if rec:
                artist = rec['artist']
                song = rec['song']
                try:
                    database.touch_search(artist, song)
                except Exception as e:
                    print(f"Touch search error: {e}")
                lyrics = rec['lyrics'] or ''
                analysis = rec['analysis'] or ''
                source = rec['source'] or 'Database'
                model_name = rec['model_name'] or DEFAULT_GEMINI_MODEL
                song_url = rec.get('song_url')
                track_tags = rec.get('track_tags') or []
                if not track_tags and LASTFM_API_KEY:
                    try:
                        track_tags = lastfm.get_or_fetch_track_tags(artist, song, api_key=LASTFM_API_KEY)
                    except Exception as lfe:
                        print(f"Last.fm track tags load error: {lfe}")
                theaudiodb_data = rec.get('theaudiodb_data')
                if not theaudiodb_data:
                    try:
                        theaudiodb_data = theaudiodb.get_or_fetch_track_metadata(artist, song, api_key=THEAUDIODB_API_KEY)
                    except Exception as adbe:
                        print(f"TheAudioDB load error: {adbe}")
                artist_metadata = None
                try:
                    artist_metadata = lastfm.get_or_fetch_artist_metadata(artist, api_key=LASTFM_API_KEY)
                except Exception as lfe:
                    print(f"Last.fm artist metadata load error: {lfe}")

                genius_link = f' <a href="{html_escape(song_url)}" target="_blank" style="color:#A8D2FF; text-decoration:underline;">View on Genius</a>' if song_url else ''
                has_analysis_msg = ' with saved analysis' if analysis else ''
                message = (
                    f'<div class="message">'
                    f'Loaded saved search for <strong>{html_escape(artist)}</strong> - <strong>{html_escape(song)}</strong> from database{has_analysis_msg}.'
                    f'{genius_link}'
                    f'</div>'
                )
                self.render_page(
                    message=message,
                    lyrics_text=lyrics,
                    artist_value=html_escape(artist),
                    song_value=html_escape(song),
                    analysis_result=analysis,
                    selected_model=model_name,
                    show_editor=bool(lyrics or analysis),
                    track_tags=track_tags,
                    artist_metadata=artist_metadata,
                    theaudiodb_data=theaudiodb_data
                )
                return
        elif artist_param and song_param:
            rec = database.get_search(artist_param, song_param)
            if rec:
                artist = rec['artist']
                song = rec['song']
                try:
                    database.touch_search(artist, song)
                except Exception as e:
                    print(f"Touch search error: {e}")
                lyrics = rec['lyrics'] or ''
                analysis = rec['analysis'] or ''
                source = rec['source'] or 'Database'
                model_name = rec['model_name'] or DEFAULT_GEMINI_MODEL
                song_url = rec.get('song_url')
                track_tags = rec.get('track_tags') or []
                if not track_tags and LASTFM_API_KEY:
                    try:
                        track_tags = lastfm.get_or_fetch_track_tags(artist, song, api_key=LASTFM_API_KEY)
                    except Exception as lfe:
                        print(f"Last.fm track tags load error: {lfe}")
                theaudiodb_data = rec.get('theaudiodb_data')
                if not theaudiodb_data:
                    try:
                        theaudiodb_data = theaudiodb.get_or_fetch_track_metadata(artist, song, api_key=THEAUDIODB_API_KEY)
                    except Exception as adbe:
                        print(f"TheAudioDB load error: {adbe}")
                artist_metadata = None
                try:
                    artist_metadata = lastfm.get_or_fetch_artist_metadata(artist, api_key=LASTFM_API_KEY)
                except Exception as lfe:
                    print(f"Last.fm artist metadata load error: {lfe}")

                # Auto-analyze if requested and lyrics exist but no analysis yet
                if auto_analyze and not analysis and lyrics and GEMINI_API_KEY:
                    try:
                        prompts = load_prompts()
                        p_template = prompts[0]['text'] if prompts else "Analyze lyrics:\n{lyrics_text}"
                        p_text = p_template.replace('{song}', song).replace('{artist}', artist).replace('{lyrics_text}', html.unescape(lyrics))
                        client = genai.Client(api_key=GEMINI_API_KEY)
                        inter = client.interactions.create(model=model_name, input=p_text)
                        analysis = inter.output_text or ''
                        database.save_analysis(
                            artist=artist,
                            song=song,
                            analysis=analysis,
                            model_name=model_name,
                            prompt_name="Default Analysis",
                            lyrics=lyrics,
                            track_tags=track_tags,
                            theaudiodb_data=theaudiodb_data
                        )
                    except Exception as aae:
                        print(f"Auto-analyze error: {aae}")

                genius_link = f' <a href="{html_escape(song_url)}" target="_blank" style="color:#A8D2FF; text-decoration:underline;">View on Genius</a>' if song_url else ''
                has_analysis_msg = ' with saved analysis' if analysis else ''
                message = (
                    f'<div class="message">'
                    f'Loaded saved search for <strong>{html_escape(artist)}</strong> - <strong>{html_escape(song)}</strong> from database{has_analysis_msg}.'
                    f'{genius_link}'
                    f'</div>'
                )
                self.render_page(
                    message=message,
                    lyrics_text=lyrics,
                    artist_value=html_escape(artist),
                    song_value=html_escape(song),
                    analysis_result=analysis,
                    selected_model=model_name,
                    show_editor=bool(lyrics or analysis),
                    track_tags=track_tags,
                    artist_metadata=artist_metadata,
                    theaudiodb_data=theaudiodb_data
                )
                return
            else:
                # Song not previously cached in searches table: fetch lyrics online
                lyrics = ''
                song_url = None
                source = None
                canonical_artist = None
                canonical_song = None

                if ACCESS_TOKEN or (GENIUS_CLIENT_ID and GENIUS_CLIENT_SECRET):
                    try:
                        genius_info = search_genius_song_details(artist_param, song_param)
                        if genius_info:
                            song_url = genius_info.get('url')
                            canonical_artist = genius_info.get('artist')
                            canonical_song = genius_info.get('song')
                    except Exception as gse:
                        print(f"Genius search error in do_GET: {gse}")

                if song_url:
                    try:
                        fetched = fetch_genius_lyrics(song_url)
                        if fetched:
                            lyrics = fetched
                            source = 'Genius'
                    except Exception as gfe:
                        print(f"Genius lyrics fetch error in do_GET: {gfe}")

                if not lyrics:
                    try:
                        lrclib_lyrics = fetch_lrclib_lyrics(
                            artist=artist_param,
                            song=song_param,
                            canonical_artist=canonical_artist,
                            canonical_song=canonical_song
                        )
                        if lrclib_lyrics:
                            lyrics = lrclib_lyrics
                            source = 'LRCLIB'
                    except Exception as lre:
                        print(f"LRCLIB fetch error in do_GET: {lre}")

                effective_artist = canonical_artist or artist_param
                effective_song = canonical_song or song_param

                track_tags = []
                if LASTFM_API_KEY:
                    try:
                        track_tags = lastfm.get_or_fetch_track_tags(effective_artist, effective_song, api_key=LASTFM_API_KEY)
                    except Exception:
                        pass

                theaudiodb_data = None
                try:
                    theaudiodb_data = theaudiodb.get_or_fetch_track_metadata(effective_artist, effective_song, api_key=THEAUDIODB_API_KEY)
                except Exception:
                    pass

                artist_metadata = None
                try:
                    artist_metadata = lastfm.get_or_fetch_artist_metadata(effective_artist, api_key=LASTFM_API_KEY)
                except Exception:
                    pass

                analysis = ''
                if lyrics:
                    try:
                        database.save_search(
                            artist=effective_artist,
                            song=effective_song,
                            lyrics=lyrics,
                            source=source or 'Online',
                            song_url=song_url,
                            track_tags=track_tags,
                            theaudiodb_data=theaudiodb_data
                        )
                    except Exception as sse:
                        print(f"Save search on auto load error: {sse}")

                    if auto_analyze and GEMINI_API_KEY:
                        try:
                            prompts = load_prompts()
                            p_template = prompts[0]['text'] if prompts else "Analyze lyrics:\n{lyrics_text}"
                            p_text = p_template.replace('{song}', effective_song).replace('{artist}', effective_artist).replace('{lyrics_text}', html.unescape(lyrics))
                            client = genai.Client(api_key=GEMINI_API_KEY)
                            inter = client.interactions.create(model=DEFAULT_GEMINI_MODEL, input=p_text)
                            analysis = inter.output_text or ''
                            database.save_analysis(
                                artist=effective_artist,
                                song=effective_song,
                                analysis=analysis,
                                model_name=DEFAULT_GEMINI_MODEL,
                                prompt_name="Default Analysis",
                                lyrics=lyrics,
                                track_tags=track_tags,
                                theaudiodb_data=theaudiodb_data
                            )
                        except Exception as aae:
                            print(f"Auto-analyze new song error: {aae}")

                genius_link = f' <a href="{html_escape(song_url)}" target="_blank" rel="noopener noreferrer" style="color:#A8D2FF; text-decoration:underline;">View on Genius</a>' if song_url else ''
                msg = f'<div class="message">Loaded <strong>{html_escape(effective_artist)}</strong> - <strong>{html_escape(effective_song)}</strong>.{genius_link}</div>' if lyrics else ''
                self.render_page(
                    message=msg,
                    lyrics_text=lyrics,
                    artist_value=html_escape(effective_artist),
                    song_value=html_escape(effective_song),
                    analysis_result=analysis,
                    selected_model=DEFAULT_GEMINI_MODEL,
                    show_editor=bool(lyrics or analysis),
                    track_tags=track_tags,
                    artist_metadata=artist_metadata,
                    theaudiodb_data=theaudiodb_data
                )
                return

        if not ACCESS_TOKEN:
            if GENIUS_CLIENT_ID and GENIUS_CLIENT_SECRET:
                message = (
                    '<div class="message">Genius is not authorized yet. '
                    '<a href="/authorize" style="color:#A8D2FF; text-decoration:underline;">Authorize Genius</a>'
                    '</div>'
                )
            else:
                message = get_genius_missing_message()
        else:
            message = ''

        self.render_page(message=message, lyrics_text='')

    def do_POST(self):
        try:
            self._do_POST()
        except Exception as e:
            self._handle_internal_error(e, is_post=True)

    def _do_POST(self):
        current_user = self.get_current_user()
        if not current_user:
            self.send_response(302)
            self.send_header('Location', '/login')
            self.end_headers()
            return

        # Admin user management POST endpoints
        if self.path.startswith('/admin/user/'):
            if not current_user.get('is_admin'):
                self.send_error(403, 'Forbidden: Administrator access required.')
                return

            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            data = urllib.parse.parse_qs(body)

            if self.path == '/admin/user/add':
                email = data.get('email', [''])[0].strip().lower()
                name = data.get('name', [''])[0].strip()
                role = data.get('role', ['user'])[0].strip().lower()
                is_admin = (role == 'admin')
                if email and '@' in email:
                    database.upsert_user(email, name=name or None, is_admin=is_admin, is_active=True)
                    msg = urllib.parse.quote(f"Access granted for {email}.")
                else:
                    msg = urllib.parse.quote("Please provide a valid email address.")
                self.send_response(302)
                self.send_header('Location', f'/admin?msg={msg}')
                self.end_headers()
                return

            if self.path == '/admin/user/toggle-status':
                email = data.get('email', [''])[0].strip().lower()
                target_user = database.get_user(email)
                if target_user:
                    new_status = not target_user['is_active']
                    success = database.set_user_active_status(email, new_status)
                    if success:
                        status_str = "activated" if new_status else "deactivated"
                        msg = urllib.parse.quote(f"User {email} {status_str}.")
                    else:
                        msg = urllib.parse.quote("Primary administrator status cannot be modified.")
                else:
                    msg = urllib.parse.quote("User not found.")
                self.send_response(302)
                self.send_header('Location', f'/admin?msg={msg}')
                self.end_headers()
                return

            if self.path == '/admin/user/toggle-role':
                email = data.get('email', [''])[0].strip().lower()
                target_user = database.get_user(email)
                if target_user:
                    new_role = not target_user['is_admin']
                    success = database.set_user_admin_role(email, new_role)
                    if success:
                        role_str = "promoted to Administrator" if new_role else "demoted to User"
                        msg = urllib.parse.quote(f"User {email} {role_str}.")
                    else:
                        msg = urllib.parse.quote("Primary administrator role cannot be modified.")
                else:
                    msg = urllib.parse.quote("User not found.")
                self.send_response(302)
                self.send_header('Location', f'/admin?msg={msg}')
                self.end_headers()
                return

            if self.path == '/admin/user/delete':
                email = data.get('email', [''])[0].strip().lower()
                success = database.delete_user(email)
                if success:
                    msg = urllib.parse.quote(f"User {email} removed.")
                else:
                    msg = urllib.parse.quote("Primary administrator cannot be removed.")
                self.send_response(302)
                self.send_header('Location', f'/admin?msg={msg}')
                self.end_headers()
                return

            self.send_error(404, 'Not Found')
            return
        if self.path == '/auth/spotify/disconnect':
            self.handle_spotify_disconnect()
            return

        if self.path == '/api/playlists/save':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            try:
                data = json.loads(body)
            except Exception:
                data = urllib.parse.parse_qs(body)
                data = {k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in data.items()}
            self.handle_api_playlists_save(data)
            return

        if self.path == '/api/playlists/export-spotify':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            try:
                data = json.loads(body)
            except Exception:
                data = urllib.parse.parse_qs(body)
                data = {k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in data.items()}
            self.handle_api_playlists_export_spotify(data)
            return

        if self.path not in ('/submit', '/analyze', '/prompts/save'):
            self.send_error(404, 'Not Found')
            return

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')
        data = urllib.parse.parse_qs(body)
        
        if self.path == '/prompts/save':
            name = data.get('prompt_name', [''])[0].strip()
            text = data.get('prompt_text', [''])[0].strip()
            if name and text:
                save_prompt(name, text)
                message = f'<div class="message">Prompt "{html_escape(name)}" saved successfully.</div>'
            else:
                message = '<div class="message">Name and text are required.</div>'
            self.render_prompts_page(message=message)
            return

        if self.path == '/submit':
            artist = data.get('artist', [''])[0].strip()
            song = data.get('song', [''])[0].strip()
            force_refresh = data.get('refresh', ['0'])[0] == '1'
            lyrics_text = ''
            message = ''
            show_editor = False
            cached_analysis = ''
            cached_model = DEFAULT_GEMINI_MODEL

            if not artist or not song:
                message = '<div class="message">Please enter both artist name and song title.</div>'
            else:
                lyrics = None
                source = ""
                song_url = None
                genius_error = None
                canonical_artist = None
                canonical_song = None

                # Check database cache first if not forcing a refresh
                if not force_refresh:
                    cached = database.get_search(artist, song)
                    if cached and cached.get('lyrics') and cached['lyrics'].strip():
                        lyrics = cached['lyrics']
                        source = cached.get('source') or 'Database'
                        song_url = cached.get('song_url')
                        cached_analysis = cached.get('analysis') or ''
                        cached_model = cached.get('model_name') or DEFAULT_GEMINI_MODEL
                        try:
                            database.touch_search(artist, song)
                        except Exception as e:
                            print(f"Touch search error: {e}")

                if not lyrics:
                    genius_info = None

                    # 1. Attempt Genius song search if credentials or access token are present
                    if ACCESS_TOKEN or (GENIUS_CLIENT_ID and GENIUS_CLIENT_SECRET):
                        try:
                            genius_info = search_genius_song_details(artist, song)
                            if genius_info:
                                song_url = genius_info.get('url')
                                canonical_artist = genius_info.get('artist')
                                canonical_song = genius_info.get('song')
                        except Exception as e:
                            print(f"Genius search error: {e}")
                            genius_error = str(e)

                    # 2. Try scraping Genius if song URL was resolved
                    if song_url:
                        try:
                            lyrics = fetch_genius_lyrics(song_url)
                            if lyrics:
                                source = "Genius"
                        except Exception as e:
                            print(f"Genius scraping failed ({e}), attempting LRCLIB fallback...")
                            genius_error = str(e)

                    # 3. Fallback to LRCLIB open database if Genius didn't provide lyrics
                    if not lyrics:
                        try:
                            lyrics = fetch_lrclib_lyrics(
                                artist=artist,
                                song=song,
                                canonical_artist=canonical_artist,
                                canonical_song=canonical_song
                            )
                            if lyrics:
                                source = "LRCLIB"
                        except Exception as e:
                            print(f"LRCLIB fallback error: {e}")

                    effective_artist = canonical_artist or artist
                    effective_song = canonical_song or song

                    track_tags = []
                    artist_metadata = None
                    theaudiodb_data = None
                    try:
                        track_tags = lastfm.get_or_fetch_track_tags(effective_artist, effective_song, api_key=LASTFM_API_KEY, force_refresh=force_refresh)
                        if not track_tags and effective_artist != artist:
                            track_tags = lastfm.get_or_fetch_track_tags(artist, song, api_key=LASTFM_API_KEY, force_refresh=force_refresh)
                    except Exception as lfe:
                        print(f"Last.fm track tags error on submit: {lfe}")
                    try:
                        theaudiodb_data = theaudiodb.get_or_fetch_track_metadata(effective_artist, effective_song, api_key=THEAUDIODB_API_KEY, force_refresh=force_refresh)
                        if not theaudiodb_data and effective_artist != artist:
                            theaudiodb_data = theaudiodb.get_or_fetch_track_metadata(artist, song, api_key=THEAUDIODB_API_KEY, force_refresh=force_refresh)
                    except Exception as adbe:
                        print(f"TheAudioDB track fetch error on submit: {adbe}")
                    try:
                        artist_metadata = lastfm.get_or_fetch_artist_metadata(effective_artist, api_key=LASTFM_API_KEY, force_refresh=force_refresh)
                        if not artist_metadata and effective_artist != artist:
                            artist_metadata = lastfm.get_or_fetch_artist_metadata(artist, api_key=LASTFM_API_KEY, force_refresh=force_refresh)
                    except Exception as lfe:
                        print(f"Last.fm artist metadata error on submit: {lfe}")

                    # 4. Save search result into database only if lyrics were found
                    if lyrics:
                        try:
                            database.save_search(
                                artist=artist,
                                song=song,
                                lyrics=lyrics,
                                source=source,
                                song_url=song_url,
                                track_tags=track_tags,
                                theaudiodb_data=theaudiodb_data
                            )
                            if canonical_artist and (canonical_artist.lower() != artist.lower() or (canonical_song and canonical_song.lower() != song.lower())):
                                database.save_search(
                                    artist=canonical_artist,
                                    song=canonical_song or song,
                                    lyrics=lyrics,
                                    source=source,
                                    song_url=song_url,
                                    track_tags=track_tags,
                                    theaudiodb_data=theaudiodb_data
                                )
                        except Exception as e:
                            print(f"Database save error: {e}")
                else:
                    track_tags = []
                    artist_metadata = None
                    theaudiodb_data = None
                    if artist:
                        try:
                            artist_metadata = lastfm.get_or_fetch_artist_metadata(artist, api_key=LASTFM_API_KEY, force_refresh=force_refresh)
                        except Exception as lfe:
                            print(f"Last.fm artist metadata error on submit: {lfe}")

                refresh_link = ' <button type="button" onclick="forceRefreshSearch()" style="background: none; border: none; padding: 0; color:#A8D2FF; text-decoration:underline; font-size:0.85em; margin-left:8px; cursor: pointer; font-family: inherit;">[Re-fetch fresh]</button>'
                if lyrics:
                    lyrics_text = lyrics
                    show_editor = True
                    genius_link = f' <a href="{html_escape(song_url)}" target="_blank" style="color:#A8D2FF; text-decoration:underline;">View on Genius</a>' if song_url else ''
                    note = ' (via LRCLIB fallback - Genius web access blocked)' if (source == 'LRCLIB' and song_url) else (f' (via {source})' if source in ('LRCLIB', 'Database') else '')
                    matched_note = f' (matched for <em>{html_escape(artist)}</em>)' if (canonical_artist and canonical_artist.lower() != artist.lower()) else ''
                    display_artist = canonical_artist or artist
                    display_song = canonical_song or song
                    message = (
                        '<div class="message">'
                        f'Successfully found lyrics for <strong>{html_escape(display_artist)}</strong> - <strong>{html_escape(display_song)}</strong>{matched_note}{note}.'
                        f'{genius_link}{refresh_link}'
                        '</div>'
                    )
                else:
                    show_editor = True
                    reason_msg = f" (Genius returned: {html_escape(genius_error)})" if genius_error else ""
                    genius_link = f' <a href="{html_escape(song_url)}" target="_blank" style="color:#A8D2FF; text-decoration:underline;">View on Genius</a>' if song_url else ''
                    message = (
                        '<div class="message">'
                        f'Could not automatically retrieve lyrics for <strong>{html_escape(artist)}</strong> - <strong>{html_escape(song)}</strong>{reason_msg}.'
                        f'{genius_link}<br>'
                        'You can paste or edit the lyrics in the box below to run Gemini analysis.'
                        '</div>'
                    )

            self.render_page(
                message=message,
                lyrics_text=lyrics_text,
                artist_value=html_escape(canonical_artist or artist) if lyrics else html_escape(artist),
                song_value=html_escape(canonical_song or song) if lyrics else html_escape(song),
                analysis_result=cached_analysis,
                selected_model=cached_model,
                show_editor=show_editor,
                track_tags=track_tags,
                artist_metadata=artist_metadata,
                theaudiodb_data=theaudiodb_data
            )
            
        elif self.path == '/analyze':
            artist = data.get('artist', [''])[0].strip()
            song = data.get('song', [''])[0].strip()
            lyrics_text = data.get('lyrics', [''])[0].strip()
            prompt_idx_str = data.get('prompt_idx', ['0'])[0].strip()
            model_name = data.get('model_name', [DEFAULT_GEMINI_MODEL])[0].strip()
            if model_name not in [m['id'] for m in AVAILABLE_GEMINI_MODELS]:
                model_name = DEFAULT_GEMINI_MODEL
            analysis_result = ''
            
            track_tags = []
            artist_metadata = None
            theaudiodb_data = None
            if artist and song:
                try:
                    track_tags = lastfm.get_or_fetch_track_tags(artist, song, api_key=LASTFM_API_KEY)
                except Exception as lfe:
                    print(f"Last.fm track tags error on analyze: {lfe}")
                try:
                    theaudiodb_data = theaudiodb.get_or_fetch_track_metadata(artist, song, api_key=THEAUDIODB_API_KEY)
                except Exception as adbe:
                    print(f"TheAudioDB track error on analyze: {adbe}")
                try:
                    artist_metadata = lastfm.get_or_fetch_artist_metadata(artist, api_key=LASTFM_API_KEY)
                except Exception as lfe:
                    print(f"Last.fm artist metadata error on analyze: {lfe}")

            prompt_idx = 0
            try:
                prompt_idx = int(prompt_idx_str)
                prompts = load_prompts()
                if 0 <= prompt_idx < len(prompts):
                    prompt_template = prompts[prompt_idx]['text']
                else:
                    prompt_template = prompts[0]['text']
                    prompt_idx = 0
            except (ValueError, IndexError):
                prompts = load_prompts()
                prompt_template = prompts[0]['text']
                prompt_idx = 0
            
            if not GEMINI_API_KEY:
                message = get_gemini_missing_message()
            elif not lyrics_text:
                message = '<div class="message">Please paste or enter lyrics in the lyrics box before running analysis.</div>'
            else:
                try:
                    client = genai.Client(api_key=GEMINI_API_KEY)
                    prompt = prompt_template.replace('{song}', song).replace('{artist}', artist).replace('{lyrics_text}', html.unescape(lyrics_text))
                    interaction = client.interactions.create(
                        model=model_name,
                        input=prompt,
                    )
                    analysis_result = interaction.output_text or ''
                    model_display_name = next((m['name'] for m in AVAILABLE_GEMINI_MODELS if m['id'] == model_name), model_name)
                    message = f'<div class="message">Analysis complete using {html_escape(model_display_name)}.</div>'

                    # Save analysis to database
                    prompt_name = prompts[prompt_idx]['name'] if (prompts and 0 <= prompt_idx < len(prompts)) else "Default Analysis"
                    try:
                        database.save_analysis(
                            artist=artist,
                            song=song,
                            analysis=analysis_result,
                            model_name=model_name,
                            prompt_name=prompt_name,
                            lyrics=lyrics_text,
                            track_tags=track_tags,
                            theaudiodb_data=theaudiodb_data
                        )
                    except Exception as e:
                        print(f"Error saving analysis to database: {e}")

                except Exception as e:
                    message = f'<div class="message">Analysis failed: {html_escape(str(e))}</div>'
                    
            self.render_page(
                message=message,
                lyrics_text=lyrics_text,
                artist_value=html_escape(artist),
                song_value=html_escape(song),
                analysis_result=analysis_result,
                selected_model=model_name,
                selected_prompt=prompt_idx,
                show_editor=True,
                track_tags=track_tags,
                artist_metadata=artist_metadata,
                theaudiodb_data=theaudiodb_data
            )

    def handle_authorize(self):
        if not GENIUS_CLIENT_ID or not GENIUS_CLIENT_SECRET:
            self.render_page(
                message=get_genius_missing_message(action='authorizing'),
                lyrics_text=''
            )
            return

        redirect_uri = get_redirect_uri(self.headers.get('Host'))
        auth_url = (
            f'{GENIUS_AUTH_URL}?client_id={urllib.parse.quote(GENIUS_CLIENT_ID)}'
            f'&redirect_uri={urllib.parse.quote(redirect_uri)}&scope=me&response_type=code'
        )

        self.send_response(302)
        self.send_header('Location', auth_url)
        self.end_headers()

    def handle_callback(self, query):
        params = urllib.parse.parse_qs(query)
        code = params.get('code', [''])[0]
        error = params.get('error', [''])[0]

        if error or not code:
            self.render_page(
                message='<div class="message">Authorization failed or was cancelled.</div>',
                lyrics_text=''
            )
            return

        try:
            redirect_uri = get_redirect_uri(self.headers.get('Host'))
            token = exchange_genius_code(code, redirect_uri)
            if token:
                global ACCESS_TOKEN
                ACCESS_TOKEN = token
                save_access_token(token)
                message = '<div class="message">Genius authorization completed. You can now search for lyrics.</div>'
            else:
                message = '<div class="message">Authorization succeeded but no access token was returned.</div>'
        except Exception as e:
            message = '<div class="message">Genius authorization failed: {}</div>'.format(html_escape(str(e)))

        self.render_page(message=message, lyrics_text='')

    def render_page(self, message: str, lyrics_text: str, artist_value: str = '', song_value: str = '', analysis_result: str = '', selected_model: str = DEFAULT_GEMINI_MODEL, selected_prompt: int = 0, show_editor: bool = False, track_tags: Optional[List[Dict[str, Any]]] = None, artist_metadata: Optional[Dict[str, Any]] = None, theaudiodb_data: Optional[Dict[str, Any]] = None):
        show_sections = bool(lyrics_text or show_editor)
        lyrics_display = '' if show_sections else 'display: none;'
        analysis_display = '' if show_sections else 'display: none;'
        analysis_form_display = 'display: none;' if analysis_result else ''
        analysis_result_display = 'display: none;' if not analysis_result else ''
        analysis_controls_display = 'display: flex;' if analysis_result else 'display: none;'

        if artist_value and song_value and track_tags is None:
            try:
                track_tags = lastfm.get_or_fetch_track_tags(artist_value, song_value, api_key=LASTFM_API_KEY)
            except Exception:
                track_tags = []
        if artist_value and artist_metadata is None:
            try:
                artist_metadata = lastfm.get_or_fetch_artist_metadata(artist_value, api_key=LASTFM_API_KEY)
            except Exception:
                artist_metadata = None
        if artist_value and song_value and theaudiodb_data is None:
            try:
                theaudiodb_data = theaudiodb.get_or_fetch_track_metadata(artist_value, song_value, api_key=THEAUDIODB_API_KEY)
            except Exception:
                theaudiodb_data = None

        theaudiodb_widget = build_theaudiodb_widget(
            artist=artist_value,
            song=song_value,
            theaudiodb_data=theaudiodb_data
        )

        lastfm_widget = build_lastfm_widget(
            artist=artist_value,
            song=song_value,
            track_tags=track_tags,
            artist_metadata=artist_metadata,
            has_api_key=bool(LASTFM_API_KEY)
        )
        artist_info_btn_display = '' if artist_value else 'display: none;'
        
        model_options = ''
        for m in AVAILABLE_GEMINI_MODELS:
            selected_attr = ' selected' if m['id'] == selected_model else ''
            model_options += f'<option value="{html_escape(m["id"])}"{selected_attr}>{html_escape(m["name"])}</option>\n'

        prompts = load_prompts()
        prompt_options = ''
        for idx, p in enumerate(prompts):
            selected_attr = ' selected' if idx == selected_prompt else ''
            prompt_options += f'<option value="{idx}"{selected_attr}>{html_escape(p["name"])}</option>\n'

        bands = database.get_distinct_bands()
        band_options = ''
        band_datalist_options = ''
        if bands:
            band_select_display = ''
            band_count_text = f'{len(bands)} saved'
            for b in bands:
                sel = ' selected' if b['artist'].lower() == artist_value.lower() else ''
                songs_label = f"{b['song_count']} song" if b['song_count'] == 1 else f"{b['song_count']} songs"
                band_options += f'<option value="{html_escape(b["artist"])}"{sel}>{html_escape(b["artist"])} ({songs_label})</option>\n'
                band_datalist_options += f'<option value="{html_escape(b["artist"])}">\n'
        else:
            band_select_display = 'display: none;'
            band_count_text = '0 saved'

        lyrics_badge_display = 'display: inline-block;' if lyrics_text.strip() else 'display: none;'
        analysis_badge_display = 'display: inline-block;' if analysis_result.strip() else 'display: none;'

        lyrics_text_attr = html.escape(lyrics_text, quote=True)
        content = PAGE_HTML.replace('{message_block}', message)\
                           .replace('{lyrics_text}', html_escape(lyrics_text))\
                           .replace('{lyrics_text_attr}', lyrics_text_attr)\
                           .replace('{artist_value}', artist_value)\
                           .replace('{song_value}', song_value)\
                           .replace('{lyrics_display}', lyrics_display)\
                           .replace('{analysis_display}', analysis_display)\
                           .replace('{analysis_form_display}', analysis_form_display)\
                           .replace('{analysis_result_display}', analysis_result_display)\
                           .replace('{analysis_controls_display}', analysis_controls_display)\
                           .replace('{analysis_result}', html_escape(analysis_result))\
                           .replace('{theaudiodb_widget}', theaudiodb_widget)\
                           .replace('{lastfm_widget}', lastfm_widget)\
                           .replace('{artist_info_btn_display}', artist_info_btn_display)\
                           .replace('{model_options}', model_options)\
                           .replace('{prompt_options}', prompt_options)\
                           .replace('{band_options}', band_options)\
                           .replace('{band_datalist_options}', band_datalist_options)\
                           .replace('{band_select_display}', band_select_display)\
                           .replace('{band_count_text}', band_count_text)\
                           .replace('{lyrics_badge_display}', lyrics_badge_display)\
                           .replace('{analysis_badge_display}', analysis_badge_display)\
                           .replace('{app_header}', build_app_header('song', user=self.get_current_user()))
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(content.encode('utf-8'))))
        self.end_headers()
        self.wfile.write(content.encode('utf-8'))

    def render_prompts_page(self, message=''):
        prompts = load_prompts()
        prompts_list_html = ''
        if not prompts:
            prompts_list_html = '<p style="text-align: center; color: #A5C8FF;">No prompts found.</p>'
        else:
            for p in prompts:
                prompts_list_html += f'''
                <div style="background: rgba(165, 200, 255, 0.08); padding: 16px; border-radius: 8px; border: 1px solid rgba(165, 200, 255, 0.2);">
                    <h3 style="margin-top: 0; color: #A5C8FF;">{html_escape(p['name'])}</h3>
                    <pre style="white-space: pre-wrap; margin-bottom: 0; font-family: inherit; font-size: 0.9rem; color: #E1E8F0;">{html_escape(p['text'])}</pre>
                </div>
                '''
                
        message_display = 'display: none;' if not message else 'display: block;'

        content = PROMPTS_PAGE_HTML.replace('{message_block}', message)\
                                   .replace('{message_display}', message_display)\
                                   .replace('{prompts_list}', prompts_list_html)\
                                   .replace('{app_header}', build_app_header('prompts', user=self.get_current_user()))
                                   
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(content.encode('utf-8'))))
        self.end_headers()
        self.wfile.write(content.encode('utf-8'))

    def render_history_page(self, selected_artist: str = ''):
        searches = database.get_recent_searches(limit=200)
        bands = database.get_distinct_bands()
        total_songs_count = len(searches)
        total_artists_count = len(bands)
        artist_norm_target = selected_artist.strip().lower()

        artist_filter_options = ''
        artist_chips_html = ''
        for b in bands:
            b_artist = b['artist']
            b_count = b['song_count']
            songs_label = f"{b_count} song" if b_count == 1 else f"{b_count} songs"
            sel = ' selected' if b_artist.lower() == artist_norm_target else ''
            artist_filter_options += f'<option value="{html_escape(b_artist)}"{sel}>{html_escape(b_artist)} ({songs_label})</option>\n'
            active_style = 'background: rgba(165, 200, 255, 0.35); border-color: #A5C8FF; color: #FFFFFF;' if b_artist.lower() == artist_norm_target else 'background: rgba(11, 30, 63, 0.6); border-color: rgba(165, 200, 255, 0.2); color: #A5C8FF;'
            artist_chips_html += f'''<button type="button" class="artist-chip" data-artist="{html_escape(b_artist)}" onclick="filterByArtist(this.dataset.artist)" style="{active_style} border: 1px solid; padding: 5px 12px; border-radius: 20px; font-size: 0.82rem; cursor: pointer; transition: all 0.2s ease;">{html_escape(b_artist)} ({b_count})</button>\n'''

        artist_chips_display = '' if bands else 'display: none;'
        all_chip_active_style = 'background: rgba(165, 200, 255, 0.35); border-color: #A5C8FF; color: #FFFFFF;' if not artist_norm_target else 'background: rgba(11, 30, 63, 0.6); border-color: rgba(165, 200, 255, 0.2); color: #A5C8FF;'

        if not searches:
            history_list_html = '<p style="text-align: center; color: #A5C8FF; padding: 40px; font-style: italic;">No search history yet. Search for songs to build your collection!</p>'
        else:
            cards = []
            for s in searches:
                artist_esc = html_escape(s['artist'])
                song_esc = html_escape(s['song'])
                source_val = html_escape(s['source'] or 'Manual')
                source_badge = f'<span style="background: rgba(165, 200, 255, 0.15); color: #A5C8FF; padding: 2px 8px; border-radius: 6px; font-size: 0.78rem;">{source_val}</span>'
                lyrics_badge = '<span style="color: #5af0a5; font-size: 0.78rem;">● Lyrics</span>' if s['has_lyrics'] else '<span style="color: #f08c5a; font-size: 0.78rem;">○ No lyrics</span>'
                analysis_badge = '<span style="background: rgba(120, 90, 255, 0.3); color: #C5B8FF; padding: 2px 8px; border-radius: 6px; font-size: 0.78rem;">✦ Analyzed</span>' if s['has_analysis'] else ''
                date_str = html_escape(s['updated_at'] or '')

                tags_list = s.get('track_tags') or []
                tags_chips_html = ''
                if tags_list:
                    chips = []
                    for tag in tags_list[:3]:
                        t_name = html_escape(tag.get('name', '')) if isinstance(tag, dict) else html_escape(str(tag))
                        if t_name:
                            chips.append(f'<span class="lastfm-tag-chip track-tag" style="font-size: 0.70rem; padding: 1px 7px;">#{t_name}</span>')
                    if chips:
                        tags_chips_html = '<div style="display: flex; gap: 4px; flex-wrap: wrap; margin-top: 6px;">' + ''.join(chips) + '</div>'

                cards.append(f'''
                <div class="history-card" data-artist="{artist_esc}" style="background: rgba(11, 30, 63, 0.6); padding: 14px 18px; border-radius: 10px; border: 1px solid rgba(165, 200, 255, 0.15); display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap;">
                    <div style="flex: 1; min-width: 220px;">
                        <div style="font-size: 1.1rem; font-weight: 700; color: #E1E8F0; display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                            <button type="button" data-artist="{artist_esc}" onclick="filterByArtist(this.dataset.artist)" style="background: none; border: none; padding: 0; font: inherit; color: inherit; cursor: pointer; border-bottom: 1px dotted rgba(165, 200, 255, 0.4); text-align: left;" title="Filter history by {artist_esc}">{artist_esc}</button>
                            <button type="button" class="pill-btn secondary" style="font-size: 0.68rem; padding: 1px 6px; line-height: 1.3;" data-artist="{artist_esc}" onclick="openArtistModal(this.dataset.artist)" title="View Last.fm info for {artist_esc}">👤 Info</button>
                            <span style="font-weight: 300; color: #A5C8FF;">&mdash;</span> {song_esc}
                        </div>
                        <div style="display: flex; gap: 8px; align-items: center; margin-top: 6px; flex-wrap: wrap;">
                            {source_badge}
                            {lyrics_badge}
                            {analysis_badge}
                            <span style="font-size: 0.75rem; color: rgba(225, 232, 240, 0.45);">{date_str}</span>
                        </div>
                        {tags_chips_html}
                    </div>
                    <div style="display: flex; gap: 8px; align-items: center;">
                        <a href="/?id={s['id']}" style="background: #194685; color: #fff; padding: 7px 14px; border-radius: 6px; text-decoration: none; font-size: 0.85rem; font-weight: 500;">Load Song</a>
                        <a href="/history/delete?id={s['id']}" onclick="return confirm('Delete this saved song?');" style="background: transparent; color: #f08c5a; border: 1px solid rgba(240, 140, 90, 0.3); padding: 6px 10px; border-radius: 6px; text-decoration: none; font-size: 0.82rem;" title="Delete">&times;</a>
                    </div>
                </div>
                ''')
            cards.append('<div id="history-no-matches" style="display:none; text-align:center; color:#A5C8FF; padding:20px;">No matching songs found.</div>')
            history_list_html = '\n'.join(cards)

        content = HISTORY_PAGE_HTML.replace('{history_list}', history_list_html)\
                                   .replace('{artist_filter_options}', artist_filter_options)\
                                   .replace('{artist_chips_html}', artist_chips_html)\
                                   .replace('{artist_chips_display}', artist_chips_display)\
                                   .replace('{all_chip_active_style}', all_chip_active_style)\
                                   .replace('{total_songs_count}', str(total_songs_count))\
                                   .replace('{total_artists_count}', str(total_artists_count))\
                                   .replace('{initial_artist_filter}', html_escape(selected_artist))\
                                   .replace('{app_header}', build_app_header('history', user=self.get_current_user()))
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(content.encode('utf-8'))))
        self.end_headers()
        self.wfile.write(content.encode('utf-8'))

    def render_artist_page(self, selected_artist: str = '', refresh: bool = False):
        current_user = self.get_current_user()
        clean_artist = selected_artist.strip()

        if not clean_artist:
            # Render Artist Directory / Index
            bands = database.get_distinct_bands()
            bands_count = len(bands)

            band_cards = []
            for b in bands:
                b_name = html_escape(b['artist'])
                b_count = b['song_count']
                b_last = html_escape(b.get('last_searched', '') or '')
                songs_text = f"{b_count} song" if b_count == 1 else f"{b_count} songs"
                band_cards.append(f'''
                <div style="background: rgba(14, 38, 80, 0.55); border: 1px solid rgba(165, 200, 255, 0.2); border-radius: 12px; padding: 20px; display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap;">
                    <div>
                        <div style="font-size: 1.2rem; font-weight: 800; color: #FFFFFF; font-family: 'Montserrat', sans-serif;">{b_name}</div>
                        <div style="font-size: 0.82rem; color: #A5C8FF; margin-top: 4px;">{songs_text} in library &bull; Last active: {b_last}</div>
                    </div>
                    <a href="/artist?artist={urllib.parse.quote(b['artist'])}" class="pill-btn primary" style="font-size: 0.84rem; padding: 6px 14px; text-decoration: none;">
                        Explore Profile &rarr;
                    </a>
                </div>
                ''')

            cards_html = "".join(band_cards) if band_cards else '<div style="text-align: center; color: rgba(225, 232, 240, 0.6); padding: 40px; font-style: italic;">No artists in library yet. Search for a song or look up an artist below!</div>'

            directory_html = f'''
            <div class="artist-top-bar">
                <div>
                    <h1 style="font-family: 'Montserrat', sans-serif; font-size: 2rem; font-weight: 900; margin: 0; color: #FFFFFF;">Artist Intelligence Directory</h1>
                    <div style="font-size: 0.88rem; color: #A5C8FF; margin-top: 4px;">Live touring, North Carolina concerts, catalog intelligence, and thematic lyrics analysis</div>
                </div>
                <form action="/artist" method="get" class="artist-search-form">
                    <input type="text" name="artist" class="artist-search-input" placeholder="Search any artist (e.g. Jimmy Eat World, Slowdive)..." required>
                    <button type="submit" class="pill-btn primary" style="padding: 10px 18px; font-size: 0.9rem;">Explore</button>
                </form>
            </div>

            <div style="background: rgba(11, 30, 63, 0.65); border: 1px solid rgba(165, 200, 255, 0.2); border-radius: 14px; padding: 24px 28px; box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; border-bottom: 1px solid rgba(165, 200, 255, 0.15); padding-bottom: 12px;">
                    <span style="font-family: 'Montserrat', sans-serif; font-size: 1.15rem; font-weight: 800; color: #E1E8F0;">Saved Artists in Library ({bands_count})</span>
                    <a href="/" style="color: #A5C8FF; text-decoration: none; font-size: 0.86rem;">&larr; Back to Song Search</a>
                </div>
                <div style="display: flex; flex-direction: column; gap: 12px;">
                    {cards_html}
                </div>
            </div>
            '''

            content = ARTIST_PAGE_HTML.replace('{app_header}', build_app_header('artist', user=current_user))\
                                      .replace('{artist_page_content}', directory_html)
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(content.encode('utf-8'))))
            self.end_headers()
            self.wfile.write(content.encode('utf-8'))
            return

        # Fetch intelligence across all APIs
        artist_esc = html_escape(clean_artist)
        artist_url_param = urllib.parse.quote(clean_artist)

        # 1. Setlist.fm
        setlist_data = {}
        try:
            setlist_data = setlistfm.get_or_fetch_artist_setlist_data(
                clean_artist,
                api_key=SETLIST_FM_API_KEY,
                force_refresh=refresh
            )
        except Exception as se:
            print(f"Setlist.fm load error for {clean_artist}: {se}")

        # 2. TheAudioDB
        audiodb_data = {}
        try:
            audiodb_data = theaudiodb.get_or_fetch_artist_details(
                clean_artist,
                api_key=THEAUDIODB_API_KEY,
                force_refresh=refresh
            ) or {}
        except Exception as ae:
            print(f"TheAudioDB artist details error for {clean_artist}: {ae}")

        # 3. Last.fm
        lastfm_data = {}
        try:
            lastfm_data = lastfm.get_or_fetch_artist_metadata(
                clean_artist,
                api_key=LASTFM_API_KEY,
                force_refresh=refresh
            ) or {}
        except Exception as le:
            print(f"Last.fm artist metadata error for {clean_artist}: {le}")

        # 4. Database songs
        db_songs = database.get_songs_by_band(clean_artist, db_path=DATABASE_PATH) or []

        # 5. Spotify Analytics
        spotify_data = {}
        user_email = current_user.get('email') if current_user else None
        try:
            spotify_data = spotify.get_or_fetch_artist_spotify_data(
                clean_artist,
                user_email=user_email,
                force_refresh=refresh,
                db_path=DATABASE_PATH
            ) or {}
        except Exception as spe:
            print(f"Spotify artist data error for {clean_artist}: {spe}")

        # Build Hero Section
        banner_url = audiodb_data.get('banner_url') or audiodb_data.get('fanart_url')
        thumb_url = audiodb_data.get('thumbnail_url') or spotify_data.get('image_url')
        logo_url = audiodb_data.get('logo_url')
        formed_year = audiodb_data.get('formed_year')
        country = audiodb_data.get('country')
        genre = audiodb_data.get('genre') or (lastfm_data.get('tags', [{}])[0].get('name') if lastfm_data.get('tags') else '')
        style = audiodb_data.get('style')

        listeners = lastfm_data.get('listeners') or 0
        playcount = lastfm_data.get('playcount') or 0
        total_concerts = setlist_data.get('total_concerts') or 0
        spotify_pop = spotify_data.get('popularity')
        spotify_followers_fmt = spotify_data.get('followers_formatted')

        # Avatar
        if thumb_url:
            avatar_html = f'<img src="{html_escape(thumb_url)}" alt="{artist_esc}" class="artist-avatar-img">'
        else:
            first_char = clean_artist[0].upper() if clean_artist else '?'
            avatar_html = f'<div class="artist-avatar-placeholder">{first_char}</div>'

        # Backdrop
        backdrop_html = f'<div class="artist-hero-backdrop" style="background-image: url(\'{html_escape(banner_url)}\');"></div>' if banner_url else ''

        # Logo or Title
        if logo_url:
            title_html = f'''
            <div style="margin-bottom: 8px;">
                <img src="{html_escape(logo_url)}" alt="{artist_esc}" style="max-height: 70px; max-width: 320px; object-fit: contain; filter: drop-shadow(0 2px 8px rgba(0,0,0,0.8));">
            </div>
            <h1 class="artist-heading" style="font-size: 2.2rem;">{artist_esc}</h1>
            '''
        else:
            title_html = f'<h1 class="artist-heading">{artist_esc}</h1>'

        # Meta badges
        badges = []
        if formed_year:
            badges.append(f'<span class="artist-meta-badge">🎸 Formed: <strong>{formed_year}</strong></span>')
        if country:
            badges.append(f'<span class="artist-meta-badge">📍 Origin: <strong>{html_escape(country)}</strong></span>')
        if listeners > 0:
            badges.append(f'<span class="artist-meta-badge">🎧 Listeners: <strong>{listeners:,}</strong></span>')
        if playcount > 0:
            badges.append(f'<span class="artist-meta-badge">📻 Scrobbles: <strong>{playcount:,}</strong></span>')
        if total_concerts > 0:
            badges.append(f'<span class="artist-meta-badge">🎤 Setlist.fm Shows: <strong>{total_concerts:,}</strong></span>')
        if spotify_pop is not None and spotify_pop > 0:
            badges.append(f'<span class="artist-meta-badge">🟢 Spotify Popularity: <strong>{spotify_pop}/100</strong></span>')
        if spotify_followers_fmt:
            badges.append(f'<span class="artist-meta-badge">👥 Spotify Followers: <strong>{spotify_followers_fmt}</strong></span>')
        badges_html = "".join(badges)

        # Tags
        tags_chips = []
        for t in (lastfm_data.get('tags') or [])[:8]:
            t_name = html_escape(t.get('name', ''))
            t_url = html_escape(t.get('url') or f"https://www.last.fm/tag/{urllib.parse.quote_plus(t.get('name', ''))}")
            tags_chips.append(f'<a href="{t_url}" target="_blank" rel="noopener noreferrer" class="lastfm-tag-chip artist-tag">#{t_name}</a>')
        tags_html = " ".join(tags_chips)

        # External Links
        ext_links = []
        if setlist_data.get('url'):
            ext_links.append(f'<a href="{html_escape(setlist_data["url"])}" target="_blank" rel="noopener noreferrer" class="pill-btn secondary" style="font-size: 0.78rem; padding: 4px 10px;">🎤 Setlist.fm Profile &rarr;</a>')
        if spotify_data.get('spotify_url'):
            ext_links.append(f'<a href="{html_escape(spotify_data["spotify_url"])}" target="_blank" rel="noopener noreferrer" class="pill-btn secondary" style="font-size: 0.78rem; padding: 4px 10px;">🟢 Spotify Profile &rarr;</a>')
        ext_links.append(f'<a href="https://www.last.fm/music/{artist_url_param}" target="_blank" rel="noopener noreferrer" class="pill-btn secondary" style="font-size: 0.78rem; padding: 4px 10px;">📻 Last.fm Profile &rarr;</a>')
        ext_links.append(f'<a href="https://genius.com/search?q={artist_url_param}" target="_blank" rel="noopener noreferrer" class="pill-btn secondary" style="font-size: 0.78rem; padding: 4px 10px;">📝 Genius Catalog &rarr;</a>')
        ext_links.append(f'<a href="/artist?artist={artist_url_param}&refresh=1" class="pill-btn secondary" style="font-size: 0.78rem; padding: 4px 10px;" title="Bypass cache and reload data from all APIs">↻ Refresh Data</a>')
        links_html = " ".join(ext_links)

        # Hero Card HTML
        hero_html = f'''
        <div class="artist-hero-card">
            {backdrop_html}
            <div class="artist-hero-inner">
                {avatar_html}
                <div class="artist-info-col">
                    {title_html}
                    <div class="artist-badges-row">{badges_html}</div>
                    {f'<div style="margin-top: 8px;">{tags_html}</div>' if tags_html else ''}
                    <div class="artist-links-row">{links_html}</div>
                </div>
            </div>
        </div>
        '''

        # Spotify Streaming & Catalog Analytics Card
        spot_pop_val = int(spotify_data.get('popularity') or 0)
        spot_tier_val = spotify_data.get('popularity_tier') or 'Catalog Artist'
        spot_followers_val = spotify_data.get('followers_formatted') or '0'
        spot_avg_pop_val = spotify_data.get('avg_track_popularity') or 0
        spot_disco = spotify_data.get('discography') or {}
        spot_albums = spot_disco.get('albums_count', 0)
        spot_singles = spot_disco.get('singles_count', 0)
        spot_span = spot_disco.get('years_active_span') or 'Recorded Catalog'
        spot_latest = spot_disco.get('latest_release')
        spot_tracks = spotify_data.get('top_tracks') or []
        spot_genres = spotify_data.get('genres') or []
        spot_url = spotify_data.get('spotify_url') or ''
        spot_is_demo = spotify_data.get('is_demo', False)

        spot_genre_badges = []
        for g in spot_genres[:8]:
            spot_genre_badges.append(f'<span class="spotify-genre-badge">{html_escape(g)}</span>')
        genres_row_html = f'<div style="display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px;">{" ".join(spot_genre_badges)}</div>' if spot_genre_badges else ''

        spot_track_rows = []
        for idx, t in enumerate(spot_tracks[:10]):
            t_name = html_escape(t.get('name') or 'Unknown Track')
            t_album = html_escape(t.get('album_name') or '')
            t_year = html_escape(t.get('release_year') or '')
            t_dur = html_escape(t.get('duration_formatted') or '0:00')
            t_pop = int(t.get('popularity') or 0)
            t_preview = t.get('preview_url') or ''
            t_spot_url = t.get('spotify_url') or ''
            t_img = t.get('album_image') or ''

            thumb_el = f'<img src="{html_escape(t_img)}" alt="{t_name}" class="spotify-track-thumb">' if t_img else '<div class="spotify-track-thumb-placeholder">🎵</div>'
            preview_el = f'<audio controls preload="none" src="{html_escape(t_preview)}" class="spotify-preview-player" title="Play 30s Audio Preview"></audio>' if t_preview else ''
            spot_link_el = f'<a href="{html_escape(t_spot_url)}" target="_blank" rel="noopener noreferrer" class="pill-btn secondary" style="font-size: 0.74rem; padding: 3px 8px;" title="Open in Spotify">▶ Listen</a>' if t_spot_url else ''

            spot_track_rows.append(f'''
            <div class="spotify-track-row">
                <div style="display: flex; align-items: center; gap: 12px; min-width: 220px; flex: 2;">
                    <span style="font-family: \'Montserrat\', sans-serif; font-weight: 800; color: #10B981; font-size: 0.92rem; width: 22px; text-align: right;">{idx + 1}</span>
                    {thumb_el}
                    <div>
                        <div style="font-size: 0.98rem; font-weight: 700; color: #FFFFFF; line-height: 1.25;">{t_name}</div>
                        <div style="font-size: 0.8rem; color: rgba(225, 232, 240, 0.65); margin-top: 2px;">
                            {t_album}{f" &bull; {t_year}" if t_year else ""}
                        </div>
                    </div>
                </div>

                <div style="display: flex; align-items: center; gap: 14px; flex: 1.5; justify-content: flex-end; flex-wrap: wrap;">
                    <div style="min-width: 110px;">
                        <div style="display: flex; justify-content: space-between; font-size: 0.72rem; color: #6EE7B7; font-weight: 700;">
                            <span>POPULARITY</span>
                            <span>{t_pop}%</span>
                        </div>
                        <div class="popularity-gauge-wrap" style="height: 5px;">
                            <div class="popularity-gauge-fill" style="width: {t_pop}%;"></div>
                        </div>
                    </div>
                    <span style="font-size: 0.8rem; color: rgba(225, 232, 240, 0.6); min-width: 36px; text-align: right;">{t_dur}</span>
                    {preview_el}
                    {spot_link_el}
                    <a href="/?artist={artist_url_param}&song={urllib.parse.quote(t.get('name', ''))}&auto_analyze=1" class="btn-analyze-song-sm" title="Launch AI thematic lyrics analysis">
                        ⚡ Analyze
                    </a>
                </div>
            </div>
            ''')

        demo_badge_spot = '<span style="font-size: 0.76rem; background: rgba(245, 158, 11, 0.2); border: 1px solid rgba(245, 158, 11, 0.4); color: #FCD34D; padding: 2px 8px; border-radius: 6px; margin-left: 8px;">Preview Mode (Spotify Credentials Unconfigured)</span>' if spot_is_demo else ''

        latest_release_html = ''
        if spot_latest and spot_latest.get('name'):
            lat_name = html_escape(spot_latest.get('name', ''))
            lat_date = html_escape(spot_latest.get('release_date', ''))
            lat_type = html_escape(spot_latest.get('type', 'Release'))
            lat_img = spot_latest.get('image_url') or ''
            lat_url = spot_latest.get('spotify_url') or ''
            lat_thumb = f'<img src="{html_escape(lat_img)}" alt="{lat_name}" style="width: 50px; height: 50px; border-radius: 8px; object-fit: cover;">' if lat_img else ''
            latest_release_html = f'''
            <div style="background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.25); border-radius: 10px; padding: 12px 16px; display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-top: 18px; flex-wrap: wrap;">
                <div style="display: flex; align-items: center; gap: 12px;">
                    {lat_thumb}
                    <div>
                        <div style="font-size: 0.74rem; font-weight: 700; color: #6EE7B7; text-transform: uppercase;">Latest {lat_type} Release</div>
                        <div style="font-size: 1rem; font-weight: 800; color: #FFFFFF;">{lat_name}</div>
                        <div style="font-size: 0.8rem; color: rgba(225, 232, 240, 0.65);">{lat_date}</div>
                    </div>
                </div>
                {f'<a href="{html_escape(lat_url)}" target="_blank" rel="noopener noreferrer" class="pill-btn secondary" style="font-size: 0.76rem; padding: 4px 10px;">Listen &rarr;</a>' if lat_url else ''}
            </div>
            '''

        spotify_analytics_html = f'''
        <div class="spotify-analytics-card">
            <div class="section-header-row" style="border-bottom-color: rgba(16, 185, 129, 0.25);">
                <div>
                    <h2 class="section-title" style="color: #6EE7B7;">
                        <span>🟢</span> Spotify Catalog &amp; Streaming Analytics {demo_badge_spot}
                    </h2>
                    <div style="font-size: 0.84rem; color: rgba(225, 232, 240, 0.75); margin-top: 4px;">
                        Streaming popularity, audience reach, top streamed tracks, and discography telemetry
                    </div>
                </div>
                {f'<a href="{html_escape(spot_url)}" target="_blank" rel="noopener noreferrer" class="pill-btn primary" style="padding: 6px 14px; font-size: 0.82rem; background: #10B981; border-color: #34D399; text-decoration: none;">Open Spotify Profile &rarr;</a>' if spot_url else ''}
            </div>

            <!-- KPI Metric Cards -->
            <div class="spotify-kpi-grid">
                <div class="spotify-kpi-item">
                    <span class="spotify-kpi-label">Artist Popularity</span>
                    <span class="spotify-kpi-value">{spot_pop_val}<span style="font-size: 0.85rem; font-weight: normal; color: rgba(225, 232, 240, 0.5);">/100</span></span>
                    <div class="popularity-gauge-wrap">
                        <div class="popularity-gauge-fill" style="width: {spot_pop_val}%;"></div>
                    </div>
                    <span class="spotify-kpi-sub" style="color: #A7F3D0; font-weight: 600;">{spot_tier_val}</span>
                </div>

                <div class="spotify-kpi-item">
                    <span class="spotify-kpi-label">Spotify Followers</span>
                    <span class="spotify-kpi-value">{spot_followers_val}</span>
                    <span class="spotify-kpi-sub">Global streaming followers</span>
                </div>

                <div class="spotify-kpi-item">
                    <span class="spotify-kpi-label">Discography Breadth</span>
                    <span class="spotify-kpi-value">{spot_albums} <span style="font-size: 0.9rem; font-weight: normal; color: rgba(225,232,240,0.6);">Albums</span></span>
                    <span class="spotify-kpi-sub">{spot_singles} Singles &amp; EPs</span>
                </div>

                <div class="spotify-kpi-item">
                    <span class="spotify-kpi-label">Catalog Active Span</span>
                    <span class="spotify-kpi-value" style="font-size: 1.15rem;">{spot_span}</span>
                    <span class="spotify-kpi-sub">Releases on Spotify</span>
                </div>

                <div class="spotify-kpi-item">
                    <span class="spotify-kpi-label">Avg Top Track Score</span>
                    <span class="spotify-kpi-value">{spot_avg_pop_val}<span style="font-size: 0.85rem; font-weight: normal; color: rgba(225, 232, 240, 0.5);">/100</span></span>
                    <span class="spotify-kpi-sub">Across top 10 songs</span>
                </div>
            </div>

            {genres_row_html}
            {latest_release_html}

            <!-- Top Tracks Table -->
            <div style="margin-top: 22px;">
                <div style="font-size: 0.82rem; font-weight: 800; text-transform: uppercase; letter-spacing: 0.05em; color: #6EE7B7; margin-bottom: 10px;">
                    🔥 Top 10 Most Streamed Tracks on Spotify (With Audio Previews &amp; AI Analysis)
                </div>
                <div style="display: flex; flex-direction: column;">
                    {"".join(spot_track_rows) if spot_track_rows else '<div style="color: rgba(225,232,240,0.6); padding: 12px; font-style: italic;">No top tracks recorded.</div>'}
                </div>
            </div>
        </div>
        '''

        # Top 10 Bands Played With (Setlist.fm Tour History)
        top_bands = setlist_data.get("most_played_with") or []
        is_demo_setlist = setlist_data.get('is_demo', False)
        demo_badge_setlist = '<span style="font-size: 0.76rem; background: rgba(245, 158, 11, 0.2); border: 1px solid rgba(245, 158, 11, 0.4); color: #FCD34D; padding: 2px 8px; border-radius: 6px; margin-left: 8px;">Preview Tour Roster</span>' if is_demo_setlist else ''

        if top_bands:
            band_cards = []
            for b in top_bands[:10]:
                rank = b.get("rank", 1)
                b_name = b.get("band", "Unknown Band")
                b_esc = html_escape(b_name)
                b_url = urllib.parse.quote(b_name)
                shows_cnt = b.get("shows_shared", 1)
                shows_text = f"{shows_cnt} show" if shows_cnt == 1 else f"{shows_cnt} shows"
                tours_cnt = b.get("tours_shared", 0)
                tours_text = f"{tours_cnt} tour" if tours_cnt == 1 else f"{tours_cnt} tours"
                role = html_escape(b.get("primary_role") or "Tour Mate")
                years_act = html_escape(b.get("years_active") or "")
                t_names = b.get("tours") or []
                lat_show = b.get("latest_show") or {}

                if rank == 1:
                    rank_html = '<span class="top-band-rank-badge rank-badge-1">🥇 #1 Most Played With</span>'
                    card_rank_class = "rank-1"
                elif rank == 2:
                    rank_html = '<span class="top-band-rank-badge rank-badge-2">🥈 #2 Most Played With</span>'
                    card_rank_class = "rank-2"
                elif rank == 3:
                    rank_html = '<span class="top-band-rank-badge rank-badge-3">🥉 #3 Most Played With</span>'
                    card_rank_class = "rank-3"
                else:
                    rank_html = f'<span class="top-band-rank-badge rank-badge-other">#{rank} Most Played With</span>'
                    card_rank_class = ""

                tour_chips = []
                for tn in t_names[:3]:
                    tour_chips.append(f'<span style="background: rgba(165, 200, 255, 0.1); border: 1px solid rgba(165, 200, 255, 0.2); color: #93C5FD; padding: 1px 7px; border-radius: 8px; font-size: 0.74rem;">{html_escape(tn)}</span>')
                if len(t_names) > 3:
                    tour_chips.append(f'<span style="color: rgba(225, 232, 240, 0.5); font-size: 0.74rem;">+{len(t_names) - 3} more</span>')
                tours_html_block = f'<div style="display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px;">{" ".join(tour_chips)}</div>' if tour_chips else ''

                latest_show_html = ''
                if lat_show and (lat_show.get('venue_name') or lat_show.get('date_formatted')):
                    l_date = html_escape(lat_show.get('date_formatted') or lat_show.get('date') or '')
                    l_venue = html_escape(lat_show.get('venue_name') or '')
                    l_loc = html_escape(lat_show.get('location') or '')
                    l_tour = html_escape(lat_show.get('tour_name') or '')
                    l_url = lat_show.get('url') or ''
                    loc_part = f" ({l_loc})" if l_loc else ""
                    tour_part = f" &bull; {l_tour}" if l_tour and l_tour != "Concert" else ""
                    link_part = f' &bull; <a href="{html_escape(l_url)}" target="_blank" rel="noopener noreferrer" style="color: #A5C8FF; text-decoration: underline;">Setlist &rarr;</a>' if l_url else ''
                    latest_show_html = f'''
                    <div style="font-size: 0.78rem; color: rgba(225, 232, 240, 0.75); margin-top: 6px;">
                        📍 Latest: <strong>{l_date}</strong> &bull; {l_venue}{loc_part}{tour_part}{link_part}
                    </div>
                    '''

                band_cards.append(f'''
                <div class="top-band-item {card_rank_class}">
                    <div>
                        <div style="display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 8px;">
                            {rank_html}
                            <span class="top-band-role-badge">{role}</span>
                        </div>
                        <a href="/artist?artist={b_url}" class="top-band-name-link" title="Explore {b_esc} profile">
                            🎸 {b_esc}
                        </a>
                        <div style="display: flex; gap: 8px; align-items: center; margin-top: 6px; flex-wrap: wrap;">
                            <span style="font-size: 0.82rem; font-weight: 700; color: #DDD6FE; background: rgba(139, 92, 246, 0.2); padding: 2px 8px; border-radius: 6px;">
                                🏟️ {shows_text}
                            </span>
                            <span style="font-size: 0.82rem; color: #A5C8FF;">
                                🚌 {tours_text}
                            </span>
                            {f'<span style="font-size: 0.78rem; color: rgba(225, 232, 240, 0.55);">&bull; {years_act}</span>' if years_act else ''}
                        </div>
                        {tours_html_block}
                        {latest_show_html}
                    </div>
                    <div style="display: flex; justify-content: flex-end; margin-top: 8px;">
                        <a href="/artist?artist={b_url}" class="pill-btn primary" style="font-size: 0.76rem; padding: 4px 12px; text-decoration: none;">
                            Explore Band Profile &rarr;
                        </a>
                    </div>
                </div>
                ''')

            top_bands_played_with_html = f'''
            <div class="top-bands-card">
                <div class="section-header-row" style="border-bottom-color: rgba(139, 92, 246, 0.25);">
                    <div>
                        <h2 class="section-title" style="color: #DDD6FE;">
                            <span>🎸</span> Top 10 Bands Played With (Setlist.fm Tour History) {demo_badge_setlist}
                        </h2>
                        <div style="font-size: 0.84rem; color: #C5B8FF; margin-top: 4px;">
                            The 10 bands and artists that have shared the bill and toured the most with {artist_esc}, ranked by shared concert appearances
                        </div>
                    </div>
                    {f'<a href="{html_escape(setlist_data.get("url", ""))}" target="_blank" rel="noopener noreferrer" class="pill-btn secondary" style="font-size: 0.78rem; padding: 4px 10px;">Setlist.fm Concert Archive &rarr;</a>' if setlist_data.get('url') else ''}
                </div>
                <div class="top-bands-grid">
                    {"".join(band_cards)}
                </div>
            </div>
            '''
        else:
            top_bands_played_with_html = ''

        # North Carolina Spotlight Card (Setlist.fm)
        has_setlist_key = setlist_data.get('has_key', bool(SETLIST_FM_API_KEY))
        last_nc = setlist_data.get('last_nc_show')
        if last_nc:
            nc_date = html_escape(last_nc.get('date_formatted') or last_nc.get('event_date') or 'Unknown Date')
            nc_venue = html_escape(last_nc.get('venue_name') or 'Unknown Venue')
            nc_city = html_escape(last_nc.get('city') or '')
            nc_state = html_escape(last_nc.get('state') or 'NC')
            nc_tour = html_escape(last_nc.get('tour_name') or 'No tour assigned')
            nc_url = html_escape(last_nc.get('url') or '')
            nc_info = html_escape(last_nc.get('info') or '')
            nc_songs_count = last_nc.get('song_count', 0)
            nc_total = last_nc.get('total_nc_shows', 1)

            tour_badge = f'<span style="background: rgba(96, 165, 250, 0.2); border: 1px solid rgba(96, 165, 250, 0.4); color: #93C5FD; padding: 2px 10px; border-radius: 12px; font-size: 0.82rem;">Tour: {nc_tour}</span>'
            songs_badge = f'<span style="background: rgba(165, 200, 255, 0.15); color: #E1E8F0; padding: 2px 10px; border-radius: 12px; font-size: 0.82rem;">🎵 {nc_songs_count} songs played</span>' if nc_songs_count > 0 else ''
            notes_html = f'<div style="margin-top: 10px; font-size: 0.84rem; color: rgba(225, 232, 240, 0.75); font-style: italic;">Note: {nc_info}</div>' if nc_info else ''

            nc_spotlight_html = f'''
            <div class="nc-spotlight-card">
                <div class="nc-card-header">
                    <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
                        <span style="font-size: 1.6rem;">📍</span>
                        <div>
                            <div style="display: flex; align-items: center; gap: 8px;">
                                <span class="nc-badge">North Carolina Show Spotlight</span>
                                <span style="font-size: 0.8rem; color: #93C5FD;">{nc_total} total recorded NC shows on Setlist.fm</span>
                            </div>
                            <h2 style="font-family: 'Montserrat', sans-serif; font-size: 1.4rem; font-weight: 800; color: #FFFFFF; margin: 6px 0 0 0;">
                                Last Played in NC: {nc_date}
                            </h2>
                        </div>
                    </div>
                    {f'<a href="{nc_url}" target="_blank" rel="noopener noreferrer" class="pill-btn primary" style="padding: 8px 16px; font-size: 0.86rem; text-decoration: none; white-space: nowrap;">View NC Setlist on Setlist.fm &rarr;</a>' if nc_url else ''}
                </div>
                <div style="margin-top: 14px; font-size: 1.05rem; color: #E1E8F0; line-height: 1.5;">
                    🏟️ <strong>{nc_venue}</strong> &bull; {nc_city}, {nc_state}
                </div>
                <div style="display: flex; gap: 10px; flex-wrap: wrap; margin-top: 10px; align-items: center;">
                    {tour_badge}
                    {songs_badge}
                </div>
                {notes_html}
            </div>
            '''
        elif not has_setlist_key:
            nc_spotlight_html = '''
            <div class="nc-spotlight-card" style="border-left-color: #F59E0B;">
                <div style="display: flex; align-items: center; gap: 12px;">
                    <span style="font-size: 1.5rem;">📍</span>
                    <div>
                        <div style="font-weight: 700; color: #FCD34D;">Setlist.fm API Connection Required</div>
                        <div style="font-size: 0.85rem; color: rgba(225, 232, 240, 0.8); margin-top: 4px;">
                            Configure <code>SETLIST_FM_API_KEY</code> in <code>calling_hours_secrets.py</code> to see North Carolina concert history and tour co-performers.
                        </div>
                    </div>
                </div>
            </div>
            '''
        else:
            nc_spotlight_html = f'''
            <div class="nc-spotlight-card" style="border-left-color: rgba(165, 200, 255, 0.4);">
                <div style="display: flex; align-items: center; gap: 12px;">
                    <span style="font-size: 1.5rem;">📍</span>
                    <div>
                        <div style="font-family: 'Montserrat', sans-serif; font-weight: 700; color: #E1E8F0;">North Carolina Concert History</div>
                        <div style="font-size: 0.88rem; color: rgba(225, 232, 240, 0.7); margin-top: 4px;">
                            No recorded concerts in North Carolina found for <strong>{artist_esc}</strong> on Setlist.fm.
                        </div>
                    </div>
                </div>
            </div>
            '''

        # Last 3 Tours & Who They Played With (Setlist.fm)
        tours = setlist_data.get('last_3_tours') or []
        if tours:
            tour_cards = []
            for t in tours:
                t_name = html_escape(t.get('tour_name') or 'Unnamed Tour')
                sample_date = html_escape(t.get('sample_date_formatted') or t.get('sample_date') or '')
                venue_name = html_escape(t.get('venue_name') or '')
                location = html_escape(t.get('location') or '')
                tour_url = html_escape(t.get('setlist_url') or '')
                notes = html_escape(t.get('notes') or '')
                played_with = t.get('played_with') or []

                coperformer_chips = []
                for band in played_with:
                    band_esc = html_escape(band)
                    band_clean = band.replace(" (Guest)", "")
                    coperformer_chips.append(f'<a href="/artist?artist={urllib.parse.quote(band_clean)}" class="coperformer-chip" title="Explore {band_esc} profile">🎸 {band_esc}</a>')

                coperformers_html = f'<div style="display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px;">{" ".join(coperformer_chips)}</div>' if coperformer_chips else '<span style="font-size: 0.82rem; color: rgba(225, 232, 240, 0.5); font-style: italic;">Solo headline bill or no co-performers recorded on sample show</span>'

                tour_cards.append(f'''
                <div class="tour-item-card">
                    <div>
                        <div style="font-size: 0.74rem; font-weight: 800; text-transform: uppercase; letter-spacing: 0.05em; color: #93C5FD; margin-bottom: 4px;">Tour</div>
                        <div class="tour-title">{t_name}</div>
                        <div style="font-size: 0.82rem; color: rgba(225, 232, 240, 0.7); margin-top: 4px;">
                            📅 {sample_date} &bull; {venue_name} ({location})
                        </div>
                        <div style="margin-top: 14px;">
                            <div style="font-size: 0.76rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: #C5B8FF; margin-bottom: 4px;">Played With / Co-Performers</div>
                            {coperformers_html}
                        </div>
                        {f'<div style="margin-top: 10px; font-size: 0.8rem; color: rgba(225, 232, 240, 0.65); font-style: italic;">Note: {notes}</div>' if notes else ''}
                    </div>
                    {f'<div style="margin-top: 14px; text-align: right;"><a href="{tour_url}" target="_blank" rel="noopener noreferrer" class="pill-btn secondary" style="font-size: 0.76rem; padding: 4px 10px;">View Tour Setlist &rarr;</a></div>' if tour_url else ''}
                </div>
                ''')

            tours_html = f'''
            <div class="tour-history-card">
                <div class="section-header-row">
                    <div>
                        <h2 class="section-title"><span>🚌</span> Last 3 Tours &amp; Who They Played With</h2>
                        <div style="font-size: 0.84rem; color: #A5C8FF; margin-top: 4px;">Tours and artists {artist_esc} shared the bill with (co-headliners, openers, and festival lineups)</div>
                    </div>
                </div>
                <div class="tour-cards-grid">
                    {"".join(tour_cards)}
                </div>
            </div>
            '''
        elif has_setlist_key:
            tours_html = f'''
            <div class="tour-history-card">
                <div class="section-header-row">
                    <h2 class="section-title"><span>🚌</span> Tours &amp; Tour Mates</h2>
                </div>
                <div style="font-size: 0.88rem; color: rgba(225, 232, 240, 0.65); font-style: italic; padding: 12px 0;">
                    No distinct tours with co-performer data recorded on Setlist.fm for {artist_esc}.
                </div>
            </div>
            '''
        else:
            tours_html = ''

        # Recent Setlists (Setlist.fm)
        recent_setlists = setlist_data.get('recent_setlists') or []
        if recent_setlists:
            setlist_rows = []
            for s in recent_setlists:
                s_date = html_escape(s.get('date_formatted') or s.get('event_date') or '')
                s_venue = html_escape(s.get('venue_name') or 'Venue')
                s_city = html_escape(s.get('city') or '')
                s_state = html_escape(s.get('state_or_country') or '')
                s_tour = html_escape(s.get('tour_name') or '')
                s_url = html_escape(s.get('url') or '')
                s_count = s.get('song_count', 0)
                sample_songs = s.get('sample_songs') or []
                songs_preview = f'<span style="font-size: 0.78rem; color: rgba(225, 232, 240, 0.6); margin-left: 8px;">({", ".join(html_escape(x) for x in sample_songs[:3])}...)</span>' if sample_songs else ''

                tour_tag = f'<span style="font-size: 0.78rem; background: rgba(165, 200, 255, 0.1); padding: 1px 8px; border-radius: 10px; color: #93C5FD; margin-left: 8px;">{s_tour}</span>' if s_tour else ''

                setlist_rows.append(f'''
                <div style="display: flex; justify-content: space-between; align-items: center; padding: 10px 14px; border-bottom: 1px solid rgba(165, 200, 255, 0.08); gap: 12px; flex-wrap: wrap;">
                    <div>
                        <div style="font-size: 0.95rem; font-weight: 700; color: #FFFFFF;">
                            📅 {s_date} &bull; {s_venue} ({s_city}, {s_state})
                            {tour_tag}
                        </div>
                        <div style="font-size: 0.8rem; color: #A5C8FF; margin-top: 2px;">
                            {s_count} songs played {songs_preview}
                        </div>
                    </div>
                    {f'<a href="{s_url}" target="_blank" rel="noopener noreferrer" class="pill-btn secondary" style="font-size: 0.74rem; padding: 3px 8px;">Setlist &rarr;</a>' if s_url else ''}
                </div>
                ''')

            recent_setlists_html = f'''
            <div class="tour-history-card">
                <div class="section-header-row">
                    <h2 class="section-title"><span>🎤</span> Recent Concert Setlists</h2>
                    {f'<a href="{html_escape(setlist_data.get("url"))}" target="_blank" rel="noopener noreferrer" style="color: #A5C8FF; font-size: 0.82rem; text-decoration: none;">View all {total_concerts} concerts on Setlist.fm &rarr;</a>' if setlist_data.get('url') else ''}
                </div>
                <div style="display: flex; flex-direction: column;">
                    {"".join(setlist_rows)}
                </div>
            </div>
            '''
        else:
            recent_setlists_html = ''

        # Catalog & Songs with One-Click Thematic Analysis
        # Merge top tracks from Last.fm and database songs
        seen_songs = set()
        catalog_items = []

        # 1. DB songs first (with analysis or lyrics status)
        for s in db_songs:
            s_title = s['song']
            s_norm = s_title.strip().lower()
            if s_norm in seen_songs:
                continue
            seen_songs.add(s_norm)
            catalog_items.append({
                "title": s_title,
                "has_analysis": s.get('has_analysis', False),
                "has_lyrics": s.get('has_lyrics', False),
                "source": "database",
                "plays": None,
                "listeners": None,
            })

        # 2. Last.fm top tracks
        for t in (lastfm_data.get('top_tracks') or []):
            t_title = t.get('name', '')
            t_norm = t_title.strip().lower()
            if t_norm in seen_songs:
                # enrich existing item with plays/listeners
                for item in catalog_items:
                    if item['title'].strip().lower() == t_norm:
                        item['plays'] = t.get('playcount')
                        item['listeners'] = t.get('listeners')
                        break
            else:
                seen_songs.add(t_norm)
                catalog_items.append({
                    "title": t_title,
                    "has_analysis": False,
                    "has_lyrics": False,
                    "source": "lastfm",
                    "plays": t.get('playcount'),
                    "listeners": t.get('listeners'),
                })

        song_rows = []
        for idx, item in enumerate(catalog_items[:18]):
            s_title_esc = html_escape(item['title'])
            s_title_url = urllib.parse.quote(item['title'])

            badges = []
            if item['has_analysis']:
                badges.append('<span style="background: rgba(120, 90, 255, 0.35); border: 1px solid rgba(165, 140, 255, 0.5); color: #DDD6FE; padding: 2px 8px; border-radius: 6px; font-size: 0.74rem; font-weight: 700;">✦ Analysis Saved</span>')
            elif item['has_lyrics']:
                badges.append('<span style="background: rgba(16, 185, 129, 0.25); border: 1px solid rgba(16, 185, 129, 0.4); color: #6EE7B7; padding: 2px 8px; border-radius: 6px; font-size: 0.74rem;">● Lyrics in DB</span>')
            else:
                badges.append('<span style="background: rgba(165, 200, 255, 0.12); color: #A5C8FF; padding: 2px 8px; border-radius: 6px; font-size: 0.74rem;">Last.fm Top Track</span>')

            plays_text = ''
            if item['listeners']:
                plays_text = f"{item['listeners']:,} listeners"
            elif item['plays']:
                plays_text = f"{item['plays']:,} plays"

            song_rows.append(f'''
            <div class="artist-song-row">
                <div style="display: flex; align-items: center; gap: 14px;">
                    <span style="font-family: 'Montserrat', sans-serif; font-weight: 800; color: rgba(225, 232, 240, 0.4); font-size: 0.9rem; width: 24px; text-align: right;">{idx + 1}</span>
                    <div>
                        <div style="font-size: 1.05rem; font-weight: 700; color: #FFFFFF;">{s_title_esc}</div>
                        <div style="display: flex; align-items: center; gap: 8px; margin-top: 3px;">
                            {"".join(badges)}
                            {f'<span style="font-size: 0.78rem; color: rgba(225, 232, 240, 0.5);">{plays_text}</span>' if plays_text else ''}
                        </div>
                    </div>
                </div>
                <div style="display: flex; gap: 8px; align-items: center;">
                    <a href="/?artist={artist_url_param}&song={s_title_url}&auto_analyze=1" class="btn-analyze-song" title="Load lyrics and perform Gemini thematic analysis">
                        ⚡ Analyze Song
                    </a>
                </div>
            </div>
            ''')

        songs_html = f'''
        <div class="songs-table-card">
            <div class="section-header-row">
                <div>
                    <h2 class="section-title"><span>🎵</span> Songs &amp; Thematic Analysis Launcher</h2>
                    <div style="font-size: 0.84rem; color: #A5C8FF; margin-top: 4px;">Click any song to immediately fetch lyrics and execute AI thematic analysis</div>
                </div>
            </div>
            <div style="display: flex; flex-direction: column;">
                {"".join(song_rows) if song_rows else '<div style="text-align: center; color: rgba(225,232,240,0.6); padding: 20px;">No songs cataloged yet.</div>'}
            </div>
        </div>
        '''

        # Biography & Similar Artists (TheAudioDB & Last.fm)
        bio_text = audiodb_data.get('biography') or lastfm_data.get('bio') or ''
        similar_artists = lastfm_data.get('similar_artists') or []

        similar_chips = []
        for s in similar_artists[:10]:
            s_name = s.get('name') if isinstance(s, dict) else str(s)
            if s_name:
                similar_chips.append(f'<a href="/artist?artist={urllib.parse.quote(s_name)}" class="similar-chip">👥 {html_escape(s_name)}</a>')

        bio_card_html = ''
        if bio_text or similar_chips:
            bio_card_html = f'''
            <div class="tour-history-card">
                <div class="section-header-row">
                    <h2 class="section-title"><span>📖</span> Artist Biography &amp; Similar Bands</h2>
                </div>
                {f'<div style="font-size: 0.92rem; line-height: 1.65; color: #E1E8F0; max-height: 280px; overflow-y: auto; padding-right: 8px; margin-bottom: 20px; white-space: pre-wrap;">{html_escape(bio_text)}</div>' if bio_text else ''}
                {f'<div><div style="font-size: 0.78rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: #93C5FD; margin-bottom: 8px;">Similar Artists (Click to explore)</div><div style="display: flex; flex-wrap: wrap; gap: 8px;">{" ".join(similar_chips)}</div></div>' if similar_chips else ''}
            </div>
            '''

        page_content = f'''
        <div class="artist-top-bar">
            <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
                <a href="/artist" style="color: #A5C8FF; text-decoration: none; font-size: 0.88rem;">&larr; All Artists</a>
                <span style="color: rgba(165, 200, 255, 0.4);">&bull;</span>
                <a href="/" style="color: #A5C8FF; text-decoration: none; font-size: 0.88rem;">Song Search</a>
            </div>
            <form action="/artist" method="get" class="artist-search-form">
                <input type="text" name="artist" class="artist-search-input" placeholder="Search another artist..." value="{artist_esc}" required>
                <button type="submit" class="pill-btn primary" style="padding: 10px 18px; font-size: 0.9rem;">Search</button>
            </form>
        </div>

        {hero_html}
        {spotify_analytics_html}
        {top_bands_played_with_html}
        {nc_spotlight_html}
        {tours_html}
        {recent_setlists_html}
        {songs_html}
        {bio_card_html}
        '''

        content = ARTIST_PAGE_HTML.replace('{app_header}', build_app_header('artist', user=current_user))\
                                  .replace('{artist_page_content}', page_content)
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(content.encode('utf-8'))))
        self.end_headers()
        self.wfile.write(content.encode('utf-8'))

    def handle_spotify_auth(self):
        if not spotify.is_spotify_configured():
            self.send_response(302)
            self.send_header('Location', '/spotify?error=unconfigured')
            self.end_headers()
            return

        state = secrets.token_urlsafe(16)
        redirect_uri = spotify.resolve_spotify_redirect_uri(self.headers.get('Host'), is_secure=self.is_request_secure())
        auth_url = spotify.get_auth_url(redirect_uri, state=state)
        state_cookie = build_cookie_header('spotify_oauth_state', state, max_age=300, secure=self.is_request_secure())
        self.send_response(302)
        self.send_header('Set-Cookie', state_cookie)
        self.send_header('Location', auth_url)
        self.end_headers()

    def handle_spotify_callback(self, query_string: str):
        params = urllib.parse.parse_qs(query_string)
        code = params.get('code', [''])[0]
        state = params.get('state', [''])[0]
        error = params.get('error', [''])[0]

        expired_state_cookie = build_cookie_header('spotify_oauth_state', '', max_age=0, secure=self.is_request_secure())

        if error:
            self.send_response(302)
            self.send_header('Set-Cookie', expired_state_cookie)
            self.send_header('Location', f'/spotify?error={urllib.parse.quote(error)}')
            self.end_headers()
            return

        cookies = parse_cookies(self.headers)
        expected_state = cookies.get('spotify_oauth_state')

        if not expected_state or state != expected_state:
            self.send_response(302)
            self.send_header('Set-Cookie', expired_state_cookie)
            self.send_header('Location', '/spotify?error=state_mismatch')
            self.end_headers()
            return

        if not code:
            self.send_response(302)
            self.send_header('Set-Cookie', expired_state_cookie)
            self.send_header('Location', '/spotify?error=missing_code')
            self.end_headers()
            return

        current_user = self.get_current_user()
        if not current_user:
            self.send_response(302)
            self.send_header('Set-Cookie', expired_state_cookie)
            self.send_header('Location', '/login')
            self.end_headers()
            return

        try:
            redirect_uri = spotify.resolve_spotify_redirect_uri(self.headers.get('Host'), is_secure=self.is_request_secure())
            tokens = spotify.exchange_code_for_token(code, redirect_uri)
            access_token = tokens['access_token']
            refresh_token = tokens.get('refresh_token')
            expires_in = tokens.get('expires_in', 3600)
            exp_iso = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + expires_in, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

            profile = spotify.fetch_user_profile(access_token)

            database.save_spotify_token(
                user_email=current_user['email'],
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=exp_iso,
                spotify_user_id=profile.get('id'),
                spotify_display_name=profile.get('display_name'),
                spotify_profile_url=profile.get('spotify_url'),
                spotify_image_url=profile.get('image_url')
            )

            # Pre-sync initial recent tracks into history table
            try:
                initial_tracks = spotify.fetch_recently_played(access_token, limit=50)
                if initial_tracks:
                    database.save_spotify_history_items(current_user['email'], initial_tracks)
            except Exception as fe:
                print(f"Initial Spotify recent tracks sync error: {fe}")

            self.send_response(302)
            self.send_header('Set-Cookie', expired_state_cookie)
            self.send_header('Location', '/spotify?connected=1')
            self.end_headers()
        except Exception as e:
            print(f"Spotify callback error: {e}")
            self.send_response(302)
            self.send_header('Set-Cookie', expired_state_cookie)
            self.send_header('Location', f'/spotify?error={urllib.parse.quote(str(e))}')
            self.end_headers()

    def handle_spotify_disconnect(self):
        current_user = self.get_current_user()
        if current_user:
            database.delete_spotify_token(current_user['email'])
        self.send_response(302)
        self.send_header('Location', '/spotify?disconnected=1')
        self.end_headers()

    def handle_api_spotify_status(self):
        current_user = self.get_current_user()
        configured = spotify.is_spotify_configured()
        token_rec = database.get_spotify_token(current_user['email']) if current_user else None
        connected = bool(token_rec and token_rec.get('access_token'))
        payload = {
            "configured": configured,
            "connected": connected,
            "spotify_user": {
                "display_name": token_rec.get('spotify_display_name') if token_rec else None,
                "user_id": token_rec.get('spotify_user_id') if token_rec else None,
                "image_url": token_rec.get('spotify_image_url') if token_rec else None,
                "profile_url": token_rec.get('spotify_profile_url') if token_rec else None,
            } if token_rec else None
        }
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode('utf-8'))

    def handle_api_spotify_now_playing(self):
        current_user = self.get_current_user()
        if not current_user:
            self.send_response(401)
            self.end_headers()
            return

        access_token = spotify.get_valid_access_token(current_user['email'])
        if not access_token:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({'playing': False, 'connected': False}).encode('utf-8'))
            return

        now_playing = spotify.fetch_currently_playing(access_token)
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.end_headers()
        self.wfile.write(json.dumps({'playing': bool(now_playing), 'track': now_playing, 'connected': True}).encode('utf-8'))

    def handle_api_spotify_history(self, query_string: str):
        current_user = self.get_current_user()
        if not current_user:
            self.send_response(401)
            self.end_headers()
            return

        params = urllib.parse.parse_qs(query_string)
        demo_mode = params.get('demo', ['0'])[0] == '1'

        if demo_mode:
            _, sample_tracks, analytics = spotify.get_demo_sample_data()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({'tracks': sample_tracks, 'analytics': analytics, 'demo': True}).encode('utf-8'))
            return

        access_token = spotify.get_valid_access_token(current_user['email'])
        if not access_token:
            saved_history = database.get_spotify_history(current_user['email'], limit=50)
            analytics = spotify.compute_analytics(saved_history)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({'tracks': saved_history, 'analytics': analytics, 'connected': False}).encode('utf-8'))
            return

        try:
            tracks = spotify.fetch_recently_played(access_token, limit=50)
            if tracks:
                database.save_spotify_history_items(current_user['email'], tracks)
            top_artists = spotify.fetch_top_artists(access_token, limit=20)
            analytics = spotify.compute_analytics(tracks, top_artists=top_artists)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({'tracks': tracks, 'analytics': analytics, 'connected': True}).encode('utf-8'))
        except Exception as e:
            saved_history = database.get_spotify_history(current_user['email'], limit=50)
            analytics = spotify.compute_analytics(saved_history)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({'tracks': saved_history, 'analytics': analytics, 'error': str(e)}).encode('utf-8'))

    def handle_api_spotify_sync(self):
        current_user = self.get_current_user()
        if not current_user:
            self.send_response(401)
            self.end_headers()
            return

        access_token = spotify.get_valid_access_token(current_user['email'])
        if not access_token:
            self.send_response(400)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'Spotify not connected'}).encode('utf-8'))
            return

        try:
            tracks = spotify.fetch_recently_played(access_token, limit=50)
            inserted = database.save_spotify_history_items(current_user['email'], tracks)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({'success': True, 'count': len(tracks), 'new_items': inserted}).encode('utf-8'))
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode('utf-8'))

    def render_spotify_page(self, demo: bool = False, message: str = '', refresh: bool = False):
        current_user = self.get_current_user()
        if not current_user:
            self.send_response(302)
            self.send_header('Location', '/login')
            self.end_headers()
            return

        profile = None
        tracks: List[Dict[str, Any]] = []
        top_artists: List[Dict[str, Any]] = []
        now_playing = None
        analytics = None
        is_connected = False
        is_demo = demo
        is_configured = spotify.is_spotify_configured()

        if is_demo:
            profile, tracks, analytics = spotify.get_demo_sample_data()
            now_playing = tracks[0] if tracks else None
            is_connected = True
        else:
            token_rec = database.get_spotify_token(current_user['email'])
            if token_rec:
                access_token = spotify.get_valid_access_token(current_user['email'])
                if access_token:
                    is_connected = True
                    profile = {
                        'display_name': token_rec.get('spotify_display_name') or 'Spotify User',
                        'id': token_rec.get('spotify_user_id') or '',
                        'image_url': token_rec.get('spotify_image_url') or '',
                        'spotify_url': token_rec.get('spotify_profile_url') or '',
                    }
                    try:
                        tracks = spotify.fetch_recently_played(access_token, limit=50)
                        if tracks:
                            database.save_spotify_history_items(current_user['email'], tracks)
                        top_artists = spotify.fetch_top_artists(access_token, limit=20)
                        now_playing = spotify.fetch_currently_playing(access_token)
                    except Exception as e:
                        print(f"Spotify fetch error: {e}")
                    
                    if not tracks:
                        tracks = database.get_spotify_history(current_user['email'], limit=50)
                    
                    analytics = spotify.compute_analytics(tracks, top_artists=top_artists)
                else:
                    is_connected = False
                    if not message:
                        message = "Your Spotify session has expired. Please reconnect your account."

        if not analytics:
            analytics = spotify.compute_analytics([])

        # Build UI Sections
        message_banner_html = f'<div class="message" style="margin-bottom: 20px;">{html_escape(message)}</div>' if message else ''

        # 1. Top Bar Actions
        top_actions = []
        if is_demo:
            top_actions.append('<a href="/spotify" class="pill-btn secondary" style="font-size: 0.84rem; padding: 6px 14px; text-decoration: none;">Exit Demo</a>')
            if is_configured:
                top_actions.append('<a href="/auth/spotify" class="spotify-green-btn" style="font-size: 0.84rem; padding: 6px 16px;">Connect Real Spotify</a>')
        elif is_connected:
            top_actions.append('<a href="/spotify?refresh=1" class="pill-btn secondary" style="font-size: 0.84rem; padding: 6px 14px; text-decoration: none;" title="Refresh listening history">🔄 Sync Fresh</a>')
            top_actions.append('<a href="/auth/spotify/disconnect" class="pill-btn secondary" style="font-size: 0.84rem; padding: 6px 14px; text-decoration: none; border-color: rgba(239, 68, 68, 0.4); color: #FCA5A5;" title="Disconnect Spotify account">🚪 Disconnect</a>')
        else:
            if is_configured:
                top_actions.append('<a href="/auth/spotify" class="spotify-green-btn" style="font-size: 0.84rem; padding: 8px 18px;">🎧 Connect Spotify</a>')
            top_actions.append('<a href="/spotify?demo=1" class="pill-btn secondary" style="font-size: 0.84rem; padding: 8px 16px; text-decoration: none;">🧪 Try Demo Mode</a>')

        top_actions_html = f'<div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap;">{"".join(top_actions)}</div>'

        # 2. Connection State Card
        if is_demo:
            state_card_html = '''
            <div style="background: rgba(245, 158, 11, 0.12); border: 1px solid rgba(245, 158, 11, 0.35); border-radius: 14px; padding: 16px 20px; display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap;">
                <div style="display: flex; align-items: center; gap: 12px;">
                    <span style="font-size: 1.4rem;">🧪</span>
                    <div>
                        <div style="color: #FCD34D; font-weight: 800; font-family: 'Montserrat', sans-serif;">Interactive Demo Preview Mode</div>
                        <div style="color: rgba(225, 232, 240, 0.7); font-size: 0.82rem; margin-top: 2px;">Viewing simulated Spotify listening history, habit charts, and lyrics integration.</div>
                    </div>
                </div>
                <div style="display: flex; gap: 8px;">
                    <a href="/spotify" class="pill-btn secondary" style="font-size: 0.78rem; padding: 6px 12px; text-decoration: none;">Exit Demo</a>
                </div>
            </div>
            '''
        elif is_connected and profile:
            display_name = html_escape(profile.get('display_name') or 'Spotify User')
            user_id = html_escape(profile.get('id') or '')
            avatar_url = profile.get('image_url')
            if avatar_url:
                avatar_el = f'<img src="{html_escape(avatar_url)}" alt="{display_name}" style="width: 44px; height: 44px; border-radius: 50%; object-fit: cover; border: 2px solid #1DB954;">'
            else:
                initial = (display_name[0] if display_name else 'S').upper()
                avatar_el = f'<div style="width: 44px; height: 44px; border-radius: 50%; background: #1DB954; color: #FFFFFF; display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 1.1rem;">{html_escape(initial)}</div>'

            history_count = database.get_spotify_history_count(current_user['email'])
            archive_note = f' &bull; {history_count} tracks saved in personal archive' if history_count > 0 else ''

            state_card_html = f'''
            <div style="background: rgba(14, 38, 80, 0.55); border: 1px solid rgba(29, 185, 84, 0.35); border-radius: 14px; padding: 16px 22px; display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap;">
                <div style="display: flex; align-items: center; gap: 14px;">
                    {avatar_el}
                    <div>
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <span style="color: #FFFFFF; font-weight: 800; font-family: 'Montserrat', sans-serif; font-size: 1.1rem;">{display_name}</span>
                            <span style="background: rgba(29, 185, 84, 0.2); border: 1px solid rgba(29, 185, 84, 0.5); color: #6EE7B7; font-size: 0.72rem; font-weight: 700; padding: 2px 8px; border-radius: 12px;">🟢 Connected</span>
                        </div>
                        <div style="color: #A5C8FF; font-size: 0.8rem; margin-top: 3px;">
                            Spotify ID: {user_id}{archive_note}
                        </div>
                    </div>
                </div>
                <div style="display: flex; gap: 10px;">
                    <a href="/spotify?refresh=1" class="pill-btn secondary" style="font-size: 0.8rem; padding: 6px 14px; text-decoration: none;">🔄 Sync History</a>
                </div>
            </div>
            '''
        elif not is_configured:
            redirect_uri_val = spotify.resolve_spotify_redirect_uri(self.headers.get('Host'), is_secure=self.is_request_secure())
            state_card_html = f'''
            <div class="spotify-card" style="border-color: rgba(245, 158, 11, 0.35); background: linear-gradient(135deg, rgba(20, 24, 45, 0.9) 0%, rgba(10, 14, 25, 0.95) 100%);">
                <div style="display: flex; align-items: flex-start; gap: 16px; flex-wrap: wrap;">
                    <div style="font-size: 2.2rem; line-height: 1;">⚙️</div>
                    <div style="flex: 1; min-width: 280px;">
                        <h2 style="margin: 0 0 6px 0; font-family: 'Montserrat', sans-serif; font-size: 1.25rem; color: #FFFFFF;">Spotify API Setup Required</h2>
                        <p style="margin: 0 0 14px 0; color: rgba(225, 232, 240, 0.8); font-size: 0.88rem; line-height: 1.5;">
                            To fetch your real listening history and habit analytics, configure free Spotify Developer credentials for Calling Hours:
                        </p>
                        <ol style="margin: 0 0 16px 20px; padding: 0; color: #A5C8FF; font-size: 0.85rem; line-height: 1.6;">
                            <li>Open the <a href="https://developer.spotify.com/dashboard" target="_blank" rel="noopener noreferrer" style="color: #1DB954; font-weight: 700; text-decoration: underline;">Spotify Developer Dashboard</a> and create an App (e.g. <em>Calling Hours</em>).</li>
                            <li>In the App Settings, add this exact Redirect URI:<br>
                                <code style="display: inline-block; background: rgba(0,0,0,0.5); padding: 4px 10px; border-radius: 6px; color: #6EE7B7; margin: 4px 0; font-family: monospace;">{html_escape(redirect_uri_val)}</code>
                            </li>
                            <li>Copy your <strong>Client ID</strong> and <strong>Client Secret</strong> into <code style="background: rgba(0,0,0,0.4); padding: 2px 6px; border-radius: 4px; color: #E1E8F0;">calling_hours_secrets.py</code> (or set <code style="background: rgba(0,0,0,0.4); padding: 2px 6px; border-radius: 4px; color: #E1E8F0;">SPOTIFY_CLIENT_ID</code> and <code style="background: rgba(0,0,0,0.4); padding: 2px 6px; border-radius: 4px; color: #E1E8F0;">SPOTIFY_CLIENT_SECRET</code> env vars).</li>
                            <li>Reload this page and click <strong>Connect with Spotify</strong>.</li>
                        </ol>
                        <div style="display: flex; gap: 12px; align-items: center; flex-wrap: wrap;">
                            <a href="/spotify?demo=1" class="pill-btn primary" style="text-decoration: none; font-size: 0.86rem; padding: 8px 16px;">
                                🧪 Try Demo Mode with Sample Data &rarr;
                            </a>
                        </div>
                    </div>
                </div>
            </div>
            '''
        else:
            state_card_html = '''
            <div class="spotify-card" style="border-color: rgba(29, 185, 84, 0.4); text-align: center; padding: 40px 24px;">
                <div style="font-size: 3rem; margin-bottom: 12px;">🎧</div>
                <h2 style="font-family: 'Montserrat', sans-serif; font-size: 1.5rem; color: #FFFFFF; margin: 0 0 8px 0;">Connect Your Spotify Account</h2>
                <p style="max-width: 580px; margin: 0 auto 20px auto; color: rgba(225, 232, 240, 0.75); font-size: 0.92rem; line-height: 1.5;">
                    Link your Spotify account to sync listening history, analyze listening habits, discover release eras, and trigger 1-click Gemini poetic lyrics analysis on the songs you love.
                </p>
                <div style="display: flex; justify-content: center; gap: 14px; flex-wrap: wrap;">
                    <a href="/auth/spotify" class="spotify-green-btn" style="font-size: 0.95rem; padding: 12px 28px;">
                        🎧 Connect with Spotify
                    </a>
                    <a href="/spotify?demo=1" class="pill-btn secondary" style="font-size: 0.9rem; padding: 10px 20px; text-decoration: none;">
                        🧪 Preview Demo
                    </a>
                </div>
            </div>
            '''

        # 3. Now Playing Card (if active)
        now_playing_html = ''
        if now_playing:
            np_track = html_escape(now_playing.get('name') or 'Unknown Track')
            np_artist = html_escape(now_playing.get('artist') or 'Unknown Artist')
            np_album = html_escape(now_playing.get('album') or '')
            np_img = html_escape(now_playing.get('album_image') or '')
            np_url = html_escape(now_playing.get('spotify_url') or '')
            np_prog_fmt = html_escape(now_playing.get('progress_formatted') or '0:00')
            np_dur_fmt = html_escape(now_playing.get('duration_formatted') or '0:00')
            np_pct = now_playing.get('progress_percent') or 0

            img_el = f'<img src="{np_img}" alt="{np_track}" style="width: 64px; height: 64px; border-radius: 10px; object-fit: cover; border: 1px solid rgba(29, 185, 84, 0.4); flex-shrink: 0;">' if np_img else ''
            analyze_link = f'/?artist={urllib.parse.quote(now_playing.get("artist") or "")}&song={urllib.parse.quote(now_playing.get("name") or "")}&auto_analyze=1'
            artist_link = f'/artist?artist={urllib.parse.quote(now_playing.get("artist") or "")}'

            now_playing_html = f'''
            <div class="spotify-now-playing-card">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <div class="spotify-equalizer">
                            <div class="eq-bar"></div>
                            <div class="eq-bar"></div>
                            <div class="eq-bar"></div>
                            <div class="eq-bar"></div>
                        </div>
                        <span style="font-family: 'Montserrat', sans-serif; font-size: 0.8rem; font-weight: 800; text-transform: uppercase; letter-spacing: 0.08em; color: #1DB954;">Currently Playing</span>
                    </div>
                    <div style="font-size: 0.78rem; color: #6EE7B7; font-weight: 600;">{np_prog_fmt} / {np_dur_fmt}</div>
                </div>

                <div style="display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap;">
                    <div style="display: flex; align-items: center; gap: 14px;">
                        {img_el}
                        <div>
                            <div style="font-size: 1.2rem; font-weight: 800; color: #FFFFFF; font-family: 'Montserrat', sans-serif;">
                                <a href="{np_url}" target="_blank" rel="noopener noreferrer" style="color: #FFFFFF; text-decoration: none;" title="Open in Spotify">{np_track}</a>
                            </div>
                            <div style="font-size: 0.88rem; color: #A5C8FF; margin-top: 2px;">
                                <a href="{artist_link}" style="color: #A5C8FF; text-decoration: none;" title="View Artist Profile">{np_artist}</a>
                                {f' &bull; <span style="color: rgba(225, 232, 240, 0.6);">{np_album}</span>' if np_album else ''}
                            </div>
                        </div>
                    </div>

                    <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap;">
                        <a href="{analyze_link}" class="pill-btn primary" style="font-size: 0.82rem; padding: 8px 16px; text-decoration: none;" title="Fetch lyrics and launch Gemini thematic analysis">
                            ✨ Analyze Lyrics
                        </a>
                        <a href="{artist_link}" class="pill-btn secondary" style="font-size: 0.82rem; padding: 8px 14px; text-decoration: none;">
                            👤 Artist Intel
                        </a>
                        {f'<a href="{np_url}" target="_blank" rel="noopener noreferrer" class="pill-btn secondary" style="font-size: 0.82rem; padding: 8px 12px; text-decoration: none; border-color: rgba(29, 185, 84, 0.4); color: #6EE7B7;">🎧 Spotify</a>' if np_url else ''}
                    </div>
                </div>

                <div style="width: 100%; height: 4px; background: rgba(255, 255, 255, 0.1); border-radius: 2px; margin-top: 14px; overflow: hidden;">
                    <div style="width: {np_pct}%; height: 100%; background: #1DB954; border-radius: 2px;"></div>
                </div>
            </div>
            '''

        # 4. KPI Cards
        kpis = [
            ("🎧 Recent Plays", str(analytics.get('total_tracks', 0)), "Tracks in listening sample"),
            ("⏱️ Total Audio", html_escape(analytics.get('total_duration_formatted', '0m')), "Continuous listening time"),
            ("👥 Unique Artists", str(analytics.get('unique_artists_count', 0)), f"Across {analytics.get('unique_albums_count', 0)} distinct albums"),
            ("⭐ Avg Popularity", f"{analytics.get('avg_popularity', 0)}/100", html_escape(analytics.get('popularity_vibe', ''))),
            ("🎭 Archetype", html_escape(analytics.get('persona', 'Explorer')), html_escape(analytics.get('persona_desc', ''))),
        ]
        kpi_cards = []
        for label, val, sub in kpis:
            kpi_cards.append(f'''
            <div class="spotify-kpi-card">
                <div class="kpi-label">{label}</div>
                <div class="kpi-value">{val}</div>
                <div class="kpi-subtext">{sub}</div>
            </div>
            ''')
        kpi_grid_html = f'<div class="spotify-kpi-grid">{"".join(kpi_cards)}</div>'

        # 5. Visual Habit Analytics Grid
        # Time of Day
        tod_data = analytics.get('time_of_day', {})
        tod_rows = []
        tod_styles = {
            'morning': ('🌅 Morning', '#F59E0B'),
            'afternoon': ('☀️ Afternoon', '#3B82F6'),
            'evening': ('🌆 Evening', '#8B5CF6'),
            'night': ('🌙 Late Night', '#10B981'),
        }
        for key_name, (label, color) in tod_styles.items():
            entry = tod_data.get(key_name, {})
            pct = entry.get('percent', 0)
            cnt = entry.get('count', 0)
            tod_rows.append(f'''
            <div class="time-bar-row">
                <div class="time-bar-header">
                    <span>{label}</span>
                    <span style="font-weight: 700; color: #E1E8F0;">{pct}% <span style="font-weight: 400; color: rgba(225, 232, 240, 0.5);">({cnt})</span></span>
                </div>
                <div class="time-bar-bg">
                    <div class="time-bar-fill" style="width: {pct}%; background: {color};"></div>
                </div>
            </div>
            ''')
        tod_html = f'''
        <div class="analytics-block">
            <div class="analytics-block-title">
                <span>⏱️ Listening by Time of Day</span>
                <span style="font-size: 0.75rem; color: #A5C8FF; font-weight: 400;">24-hour cycle</span>
            </div>
            {"".join(tod_rows)}
        </div>
        '''

        # Day of Week
        dow_data = analytics.get('day_of_week', {})
        max_dow = max([v.get('count', 0) for v in dow_data.values()] or [1]) or 1
        dow_cols = []
        for day in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]:
            d_entry = dow_data.get(day, {})
            cnt = d_entry.get('count', 0)
            pct = d_entry.get('percent', 0)
            h_pct = max(int((cnt / max_dow) * 85), 6) if cnt > 0 else 4
            dow_cols.append(f'''
            <div class="dow-col">
                <div class="dow-count">{cnt}</div>
                <div class="dow-bar-fill" style="height: {h_pct}%;" title="{day}: {cnt} plays ({pct}%)"></div>
                <div class="dow-label">{day}</div>
            </div>
            ''')
        dow_html = f'''
        <div class="analytics-block">
            <div class="analytics-block-title">
                <span>📅 Activity by Day of Week</span>
                <span style="font-size: 0.75rem; color: #A5C8FF; font-weight: 400;">Weekly distribution</span>
            </div>
            <div class="dow-bars-container">
                {"".join(dow_cols)}
            </div>
        </div>
        '''

        # Top Artists
        top_art_items = []
        for a in analytics.get('top_artists', [])[:6]:
            a_name = html_escape(a.get('artist') or '')
            cnt = a.get('count', 0)
            pct = a.get('percent', 0)
            art_link = f'/artist?artist={urllib.parse.quote(a.get("artist") or "")}'
            top_art_items.append(f'''
            <div class="artist-rank-item">
                <a href="{art_link}" style="color: #FFFFFF; font-weight: 700; text-decoration: none; font-size: 0.88rem;" title="Explore Artist Intelligence">{a_name}</a>
                <span style="font-size: 0.78rem; color: #1DB954; font-weight: 700; background: rgba(29, 185, 84, 0.15); padding: 3px 8px; border-radius: 8px;">{cnt} plays ({pct}%)</span>
            </div>
            ''')
        top_art_html = f'''
        <div class="analytics-block">
            <div class="analytics-block-title">
                <span>👑 Top Artists in History</span>
                <span style="font-size: 0.75rem; color: #A5C8FF; font-weight: 400;">Frequency</span>
            </div>
            {"".join(top_art_items) if top_art_items else '<div style="color: rgba(225, 232, 240, 0.5); font-style: italic; font-size: 0.85rem; padding: 20px 0; text-align: center;">No artist history available yet.</div>'}
        </div>
        '''

        # Release Eras
        era_data = analytics.get('release_eras', {})
        era_rows = []
        era_colors = {
            '2020s': '#10B981',
            '2010s': '#3B82F6',
            '2000s': '#8B5CF6',
            '1990s': '#EC4899',
            '1980s': '#F59E0B',
            'Classic': '#6B7280',
        }
        for era_name, color in era_colors.items():
            e_entry = era_data.get(era_name, {})
            pct = e_entry.get('percent', 0)
            cnt = e_entry.get('count', 0)
            if cnt > 0 or era_name in ('2020s', '2010s', '2000s'):
                era_rows.append(f'''
                <div class="time-bar-row">
                    <div class="time-bar-header">
                        <span>{era_name}</span>
                        <span style="font-weight: 700; color: #E1E8F0;">{pct}% <span style="font-weight: 400; color: rgba(225, 232, 240, 0.5);">({cnt})</span></span>
                    </div>
                    <div class="time-bar-bg">
                        <div class="time-bar-fill" style="width: {pct}%; background: {color};"></div>
                    </div>
                </div>
                ''')
        eras_html = f'''
        <div class="analytics-block">
            <div class="analytics-block-title">
                <span>📻 Release Era Breakdown</span>
                <span style="font-size: 0.75rem; color: #A5C8FF; font-weight: 400;">Decades</span>
            </div>
            {"".join(era_rows)}
        </div>
        '''

        # Top Genres
        genre_chips = []
        for g in analytics.get('top_genres', []):
            g_name = html_escape(g.get('genre') or '')
            g_cnt = g.get('count', 0)
            genre_chips.append(f'<span class="genre-chip">{g_name} <span style="opacity: 0.7; font-size: 0.7rem;">&bull; {g_cnt}</span></span>')
        genres_html = ''
        if genre_chips:
            genres_html = f'''
            <div class="analytics-block" style="grid-column: 1 / -1;">
                <div class="analytics-block-title">
                    <span>🏷️ Top Genre Landscape</span>
                    <span style="font-size: 0.75rem; color: #A5C8FF; font-weight: 400;">Derived from your top artists</span>
                </div>
                <div style="display: flex; flex-wrap: wrap; gap: 4px;">
                    {"".join(genre_chips)}
                </div>
            </div>
            '''

        analytics_grid_html = f'''
        <div class="spotify-analytics-grid">
            {tod_html}
            {dow_html}
            {top_art_html}
            {eras_html}
            {genres_html}
        </div>
        '''

        # 6. Listening History Feed
        history_cards = []
        for idx, t in enumerate(tracks):
            t_name = html_escape(t.get('name') or 'Unknown Track')
            a_name = html_escape(t.get('artist') or 'Unknown Artist')
            all_a = html_escape(t.get('all_artists') or a_name)
            alb = html_escape(t.get('album') or '')
            img = html_escape(t.get('album_image') or '')
            dur = html_escape(t.get('duration_formatted') or '0:00')
            rel = html_escape(t.get('relative_time') or '')
            pop = t.get('popularity', 0)
            preview = html_escape(t.get('preview_url') or '')
            spot_url = html_escape(t.get('spotify_url') or '')
            year = html_escape(t.get('release_year') or '')

            search_blob = html_escape(f"{t.get('name', '')} {t.get('artist', '')} {t.get('album', '')} {year}")

            img_el = f'<img src="{img}" alt="{t_name}" style="width: 48px; height: 48px; border-radius: 8px; object-fit: cover; flex-shrink: 0; border: 1px solid rgba(165, 200, 255, 0.2);">' if img else '<div style="width: 48px; height: 48px; border-radius: 8px; background: rgba(14, 38, 80, 0.6); display: flex; align-items: center; justify-content: center; font-size: 1.2rem; flex-shrink: 0;">🎵</div>'
            preview_btn = f'<button type="button" class="preview-audio-btn" onclick="toggleAudioPreview(this, \'{preview}\')" title="Play 30s audio preview">▶</button>' if preview else ''

            analyze_link = f'/?artist={urllib.parse.quote(t.get("artist") or "")}&song={urllib.parse.quote(t.get("name") or "")}&auto_analyze=1'
            artist_link = f'/artist?artist={urllib.parse.quote(t.get("artist") or "")}'

            album_year_text = f'{alb} ({year})' if year else alb

            history_cards.append(f'''
            <div class="history-track-card" data-search="{search_blob}">
                <div style="display: flex; align-items: center; gap: 14px; min-width: 0; flex: 1;">
                    {preview_btn}
                    {img_el}
                    <div style="min-width: 0; flex: 1;">
                        <div style="font-weight: 800; font-size: 1rem; color: #FFFFFF; font-family: 'Montserrat', sans-serif; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                            <a href="{spot_url}" target="_blank" rel="noopener noreferrer" style="color: #FFFFFF; text-decoration: none;" title="Open in Spotify">{t_name}</a>
                        </div>
                        <div style="font-size: 0.84rem; color: #A5C8FF; margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                            <a href="{artist_link}" style="color: #A5C8FF; text-decoration: none;" title="View Artist Profile">{all_a}</a>
                            {f' &bull; <span style="color: rgba(225, 232, 240, 0.55);">{album_year_text}</span>' if alb else ''}
                        </div>
                    </div>
                </div>

                <div style="display: flex; align-items: center; gap: 12px; flex-shrink: 0;">
                    <div style="text-align: right;">
                        <div style="font-size: 0.8rem; color: #E1E8F0; font-weight: 600;">{rel}</div>
                        <div style="font-size: 0.74rem; color: rgba(225, 232, 240, 0.5);">{dur} &bull; Pop: {pop}</div>
                    </div>
                    <div style="display: flex; gap: 6px; align-items: center;">
                        <a href="{analyze_link}" class="pill-btn primary" style="font-size: 0.78rem; padding: 6px 12px; text-decoration: none;" title="Analyze lyrics with Gemini">
                            ✨ Analyze Lyrics
                        </a>
                        <a href="{artist_link}" class="pill-btn secondary" style="font-size: 0.78rem; padding: 6px 10px; text-decoration: none;" title="Artist Intelligence">
                            👤
                        </a>
                        {f'<a href="{spot_url}" target="_blank" rel="noopener noreferrer" class="pill-btn secondary" style="font-size: 0.78rem; padding: 6px 10px; text-decoration: none; border-color: rgba(29, 185, 84, 0.35); color: #6EE7B7;" title="Open in Spotify">🎧</a>' if spot_url else ''}
                    </div>
                </div>
            </div>
            ''')

        tracks_count = len(tracks)
        history_feed_html = f'''
        <div style="display: flex; flex-direction: column; gap: 14px; margin-top: 10px;">
            <div style="display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap;">
                <div>
                    <h2 style="font-family: 'Montserrat', sans-serif; font-size: 1.3rem; font-weight: 800; color: #FFFFFF; margin: 0;">Listening History Stream ({tracks_count})</h2>
                    <div style="font-size: 0.82rem; color: #A5C8FF; margin-top: 2px;">Recently played tracks synced from Spotify with 1-click lyric analysis</div>
                </div>
                <div style="flex: 1; max-width: 360px;">
                    <input type="text" id="spotify-filter-input" class="spotify-search-input" placeholder="Filter recent songs or artists..." oninput="filterSpotifyHistory()">
                </div>
            </div>

            <div id="history-no-match" style="display: none; text-align: center; color: rgba(225, 232, 240, 0.6); padding: 30px; font-style: italic;">
                No recently played tracks match your filter.
            </div>

            <div style="display: flex; flex-direction: column; gap: 10px;">
                {"".join(history_cards) if history_cards else '<div style="text-align: center; color: rgba(225, 232, 240, 0.6); padding: 40px; font-style: italic;">No listening history found. Start listening on Spotify or click Sync to fetch tracks!</div>'}
            </div>
        </div>
        '''

        # Full Page Assembly
        full_content = f'''
        <div class="spotify-top-bar">
            <div>
                <h1 style="font-family: 'Montserrat', sans-serif; font-size: 2rem; font-weight: 900; margin: 0; color: #FFFFFF;">
                    🎧 Spotify Listening Intelligence
                </h1>
                <div style="font-size: 0.88rem; color: #A5C8FF; margin-top: 4px;">
                    Live playback, listening habits, recent streams, and one-click Gemini lyric analysis
                </div>
            </div>
            {top_actions_html}
        </div>

        {message_banner_html}
        {state_card_html}
        {now_playing_html}
        {kpi_grid_html if (is_connected or is_demo) else ''}
        {analytics_grid_html if (is_connected or is_demo) else ''}
        {history_feed_html if (is_connected or is_demo) else ''}
        '''

        content = SPOTIFY_PAGE_HTML.replace('{app_header}', build_app_header('spotify', user=current_user))\
                                   .replace('{spotify_page_content}', full_content)

        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(content.encode('utf-8'))))
        self.end_headers()
        self.wfile.write(content.encode('utf-8'))

    def render_playlists_page(
        self,
        mode: str = 'all_analyzed',
        selected_artist: str = '',
        selected_tag: str = '',
        selected_mood: str = '',
        order_by: str = 'updated_at DESC',
        limit: Optional[int] = None,
        custom_name: str = '',
        saved_id: Optional[int] = None,
        active_tab: str = 'generate',
        message: str = ''
    ):
        current_user = self.get_current_user()
        if not current_user:
            self.send_response(302)
            self.send_header('Location', '/login')
            self.end_headers()
            return

        total_analyzed_count = database.get_analyzed_songs_count()
        analyzed_artists = database.get_analyzed_artists()
        analyzed_tags = database.get_analyzed_tags()
        saved_playlists = database.get_saved_playlists()
        total_artists_count = len(analyzed_artists)
        total_tags_count = len(analyzed_tags)

        # Check Spotify connection
        token_rec = database.get_spotify_token(current_user['email'])
        spotify_access_token = spotify.get_valid_access_token(current_user['email']) if token_rec else None
        is_spotify_connected = bool(spotify_access_token)

        if is_spotify_connected:
            spotify_status_badge = '<span style="background: rgba(29, 185, 84, 0.2); color: #1DB954; border: 1px solid rgba(29, 185, 84, 0.4); padding: 2px 8px; border-radius: 12px; font-size: 0.74rem; font-weight: 700;">🟢 Connected</span>'
        elif spotify.is_spotify_configured():
            spotify_status_badge = '<a href="/auth/spotify" style="background: rgba(165, 200, 255, 0.15); color: #A5C8FF; border: 1px solid rgba(165, 200, 255, 0.3); padding: 2px 8px; border-radius: 12px; font-size: 0.74rem; font-weight: 700; text-decoration: none;">Connect Spotify</a>'
        else:
            spotify_status_badge = '<span style="background: rgba(225, 232, 240, 0.1); color: rgba(225, 232, 240, 0.5); padding: 2px 8px; border-radius: 12px; font-size: 0.74rem;">M3U/CSV Available</span>'

        active_mode = mode if mode in ('all_analyzed', 'artist', 'tag', 'mood', 'spotify') else 'all_analyzed'
        songs: List[Dict[str, Any]] = []
        active_playlist_title = custom_name.strip()
        active_playlist_desc = ""

        if saved_id:
            saved_p = database.get_saved_playlist(saved_id)
            if saved_p:
                songs = saved_p.get('items', [])
                if not active_playlist_title:
                    active_playlist_title = saved_p.get('name', 'Saved Playlist')
                active_playlist_desc = saved_p.get('description') or f"Saved playlist ({len(songs)} tracks)"
                active_mode = saved_p.get('generator_type', 'all_analyzed')

        if not songs and not saved_id:
            if active_mode == 'artist':
                if not selected_artist and analyzed_artists:
                    selected_artist = analyzed_artists[0]['artist']
                songs = database.get_analyzed_songs(artist=selected_artist, order_by=order_by, limit=limit)
                if not active_playlist_title:
                    active_playlist_title = f"Calling Hours: {selected_artist} (Analyzed)" if selected_artist else "Calling Hours: Artist Tracks"
                active_playlist_desc = f"Lyrical analysis collection for {selected_artist}." if selected_artist else "Analyzed songs for selected artist."
            elif active_mode == 'tag':
                if not selected_tag and analyzed_tags:
                    selected_tag = analyzed_tags[0]['tag']
                songs = database.get_analyzed_songs(tag=selected_tag, order_by=order_by, limit=limit)
                if not active_playlist_title:
                    active_playlist_title = f"Calling Hours: #{selected_tag} Vibes" if selected_tag else "Calling Hours: Tagged Tracks"
                active_playlist_desc = f"Analyzed songs tagged with #{selected_tag} on Last.fm." if selected_tag else "Analyzed songs matching tag."
            elif active_mode == 'mood':
                selected_mood = selected_mood or 'high_energy'
                all_songs = database.get_analyzed_songs(order_by=order_by)
                filtered = []
                for s in all_songs:
                    audiodb = s.get('theaudiodb_data') or {}
                    if isinstance(audiodb, str):
                        try:
                            audiodb = json.loads(audiodb)
                        except Exception:
                            audiodb = {}
                    energy = audiodb.get('energy') or 0
                    danceability = audiodb.get('danceability') or 0
                    valence = audiodb.get('valence') or 0
                    acousticness = audiodb.get('acousticness') or 0
                    mood_str = (audiodb.get('mood') or '').lower()

                    if selected_mood == 'high_energy':
                        if energy >= 60 or any(w in mood_str for w in ('energy', 'fast', 'hard', 'heavy', 'upbeat')):
                            filtered.append(s)
                    elif selected_mood == 'danceable':
                        if danceability >= 55 or 'dance' in mood_str or 'groove' in mood_str:
                            filtered.append(s)
                    elif selected_mood == 'melancholic':
                        if (valence > 0 and valence <= 45) or any(w in mood_str for w in ('sad', 'dark', 'melanchol', 'gloom', 'depress', 'slow')):
                            filtered.append(s)
                    elif selected_mood == 'acoustic':
                        if acousticness >= 40 or 'acoustic' in mood_str or 'folk' in mood_str:
                            filtered.append(s)
                    else:
                        filtered.append(s)
                if not filtered:
                    filtered = all_songs
                if limit and limit > 0:
                    filtered = filtered[:limit]
                songs = filtered
                mood_labels = {
                    'high_energy': 'High Energy ⚡',
                    'danceable': 'Upbeat & Danceable 💃',
                    'melancholic': 'Deep & Melancholic 🌧️',
                    'acoustic': 'Acoustic & Mellow 🎻',
                }
                m_label = mood_labels.get(selected_mood, 'Curated')
                if not active_playlist_title:
                    active_playlist_title = f"Calling Hours: {m_label}"
                active_playlist_desc = f"Analyzed songs curated for {m_label} sonic atmosphere."
            elif active_mode == 'spotify':
                recent_streams = database.get_spotify_history(current_user['email'], limit=50) if is_spotify_connected else []
                if not recent_streams and is_spotify_connected and spotify_access_token:
                    try:
                        recent_streams = spotify.fetch_recently_played(spotify_access_token, limit=50)
                    except Exception:
                        recent_streams = []
                analyzed_map = {}
                for s in database.get_analyzed_songs():
                    key = (s.get('artist_normalized') or s.get('artist', '').lower(), s.get('song_normalized') or s.get('song', '').lower())
                    analyzed_map[key] = s
                matched_songs = []
                for r in recent_streams:
                    art = (r.get('artist_name') or r.get('artist', '')).lower()
                    sng = (r.get('track_name') or r.get('name', '')).lower()
                    m = analyzed_map.get((art, sng))
                    if m and m not in matched_songs:
                        matched_songs.append(m)
                if not matched_songs:
                    matched_songs = database.get_analyzed_songs(limit=limit)
                elif limit and limit > 0:
                    matched_songs = matched_songs[:limit]
                songs = matched_songs
                if not active_playlist_title:
                    active_playlist_title = "Calling Hours: Spotify Rotation"
                active_playlist_desc = "Analyzed songs matched from your recent Spotify listening history."
            else:
                active_mode = 'all_analyzed'
                songs = database.get_analyzed_songs(order_by=order_by, limit=limit)
                if not active_playlist_title:
                    active_playlist_title = "Calling Hours: All Analyzed Tracks"
                active_playlist_desc = "Master collection of all tracks analyzed for poetic and thematic lyrics in Calling Hours."

        # Build Sort Options
        sort_choices = [
            ('updated_at DESC', 'Recently Analyzed (Newest First)'),
            ('updated_at ASC', 'Earliest Analyzed (Oldest First)'),
            ('artist ASC', 'Artist (A-Z)'),
            ('song ASC', 'Song Title (A-Z)'),
        ]
        sort_options_html = "".join(f'<option value="{k}"{" selected" if k == order_by else ""}>{html_escape(v)}</option>' for k, v in sort_choices)

        # Build Limit Options
        limit_choices = [
            ('', f'All Songs ({total_analyzed_count})'),
            ('25', 'Top 25 Songs'),
            ('50', 'Top 50 Songs'),
            ('100', 'Top 100 Songs'),
        ]
        cur_limit_str = str(limit) if limit else ''
        limit_options_html = "".join(f'<option value="{k}"{" selected" if k == cur_limit_str else ""}>{html_escape(v)}</option>' for k, v in limit_choices)

        # Build Mode-Specific Inputs
        mode_specific_inputs = ""
        tag_chips_container_html = ""
        if active_mode == 'artist':
            artist_opts = "".join(
                f'<option value="{html_escape(a["artist"])}"' +
                (' selected' if a['artist'].lower() == (selected_artist or '').lower() else '') +
                f'>{html_escape(a["artist"])} ({a["song_count"]})</option>'
                for a in analyzed_artists
            )
            mode_specific_inputs = f'''
                <div class="playlist-input-group">
                    <label for="select-artist">Select Artist</label>
                    <select name="artist" id="select-artist" onchange="document.getElementById('generator-form').submit()">
                        {artist_opts or '<option value="">No analyzed artists yet</option>'}
                    </select>
                </div>
            '''
        elif active_mode == 'tag':
            tag_opts = "".join(
                f'<option value="{html_escape(t["tag"])}"' +
                (' selected' if t['tag'].lower() == (selected_tag or '').lower() else '') +
                f'>#{html_escape(t["tag"])} ({t["count"]})</option>'
                for t in analyzed_tags
            )
            mode_specific_inputs = f'''
                <div class="playlist-input-group">
                    <label for="select-tag">Select Genre Tag</label>
                    <select name="tag" id="select-tag" onchange="document.getElementById('generator-form').submit()">
                        {tag_opts or '<option value="">No tags found yet</option>'}
                    </select>
                </div>
            '''
            top_tag_chips = []
            for t in analyzed_tags[:10]:
                t_esc = html_escape(t['tag'])
                top_tag_chips.append(f'<a href="/playlists?mode=tag&tag={urllib.parse.quote(t["tag"])}" class="playlist-tag-chip">#{t_esc} ({t["count"]})</a>')
            if top_tag_chips:
                tag_chips_container_html = '<div style="display:flex; gap:6px; flex-wrap:wrap; margin-top:14px; align-items:center;"><span style="font-size:0.75rem; color:#A5C8FF; font-weight:700;">Popular Tags:</span>' + "".join(top_tag_chips) + '</div>'
        elif active_mode == 'mood':
            mood_choices = [
                ('high_energy', '⚡ High Energy (Rock / Fast / Punchy)'),
                ('danceable', '💃 Upbeat & Danceable (Rhythm & Groove)'),
                ('melancholic', '🌧️ Deep & Melancholic (Reflective / Minor Key)'),
                ('acoustic', '🎻 Acoustic & Mellow (Organic / Intimate)'),
            ]
            mood_opts = "".join(
                f'<option value="{k}"{" selected" if k == selected_mood else ""}>{html_escape(v)}</option>'
                for k, v in mood_choices
            )
            mode_specific_inputs = f'''
                <div class="playlist-input-group">
                    <label for="select-mood">Audio Characteristic</label>
                    <select name="mood" id="select-mood" onchange="document.getElementById('generator-form').submit()">
                        {mood_opts}
                    </select>
                </div>
            '''
        elif active_mode == 'spotify':
            mode_specific_inputs = f'''
                <div class="playlist-input-group">
                    <label>Spotify Source</label>
                    <input type="text" value="Recent Listening Stream" readonly style="opacity: 0.8; cursor: default;">
                </div>
            '''
        else:
            mode_specific_inputs = f'''
                <div class="playlist-input-group">
                    <label>Library Scope</label>
                    <input type="text" value="Complete Analyzed Collection ({total_analyzed_count} songs)" readonly style="opacity: 0.85; cursor: default; color: #A5C8FF; font-weight: 600;">
                </div>
            '''

        # Build Tracklist HTML
        track_rows: List[str] = []
        clean_tracks_for_json: List[Dict[str, Any]] = []

        if not songs:
            tracklist_html = '''
                <tr class="playlist-tracklist-row">
                    <td colspan="5" style="text-align: center; padding: 40px; color: #A5C8FF; font-style: italic;">
                        No analyzed songs match your current criteria. Analyze songs from the Song tab to add them to your collection!
                    </td>
                </tr>
            '''
        else:
            for idx, s in enumerate(songs, 1):
                s_id = s.get('id') or s.get('search_id') or ''
                artist_val = s.get('artist', '')
                song_val = s.get('song', '')
                artist_esc = html_escape(artist_val)
                song_esc = html_escape(song_val)
                model_name = s.get('model_name') or 'Gemini'
                model_esc = html_escape(model_name)

                # Track tags
                tags_list = s.get('track_tags') or []
                tag_chips = []
                if tags_list:
                    for t in tags_list[:2]:
                        t_str = t.get('name', '') if isinstance(t, dict) else str(t)
                        if t_str:
                            tag_chips.append(f'<span class="playlist-tag-chip" style="font-size:0.7rem; padding: 1px 7px;">#{html_escape(t_str)}</span>')
                tags_chips_html = "".join(tag_chips)

                # AudioDB data
                audiodb_data = s.get('theaudiodb_data')
                if isinstance(audiodb_data, str):
                    try:
                        audiodb_data = json.loads(audiodb_data)
                    except Exception:
                        audiodb_data = None
                pills = []
                spotify_id = s.get('spotify_id')
                if audiodb_data and isinstance(audiodb_data, dict):
                    if not spotify_id:
                        spotify_id = audiodb_data.get('spotify_id')
                    if audiodb_data.get('tempo'):
                        pills.append(f'<span style="font-size:0.7rem; color:#A5C8FF; background:rgba(165,200,255,0.08); padding:1px 6px; border-radius:4px;">⏱️ {audiodb_data["tempo"]} BPM</span>')
                    if audiodb_data.get('key'):
                        pills.append(f'<span style="font-size:0.7rem; color:#A5C8FF; background:rgba(165,200,255,0.08); padding:1px 6px; border-radius:4px;">🎹 {html_escape(audiodb_data["key"])}</span>')
                    if audiodb_data.get('energy'):
                        pills.append(f'<span style="font-size:0.7rem; color:#F59E0B; background:rgba(245,158,11,0.1); padding:1px 6px; border-radius:4px;">⚡ {int(audiodb_data["energy"])}%</span>')
                audiodb_pills_html = "".join(pills)

                listen_spotify_link = ""
                if spotify_id:
                    listen_spotify_link = f'<a href="https://open.spotify.com/track/{html_escape(spotify_id)}" target="_blank" rel="noopener noreferrer" style="color: #1DB954; font-size: 1.1rem; text-decoration: none;" title="Listen on Spotify">🎧</a>'

                analysis_link_html = f'<a href="/?id={s_id}" style="color: #C5B8FF; font-size: 0.76rem; text-decoration: none; border-bottom: 1px dotted rgba(197, 184, 255, 0.5);" title="View Gemini lyric analysis">✦ View Analysis</a>' if s_id else ''

                track_rows.append(f'''
                    <tr class="playlist-tracklist-row" data-search="{artist_esc} {song_esc}">
                        <td class="playlist-track-num">#{idx}</td>
                        <td>
                            <div class="playlist-track-main">
                                <span class="playlist-track-title">{song_esc}</span>
                                <span class="playlist-track-artist">{artist_esc}</span>
                            </div>
                        </td>
                        <td>
                            <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
                                <span class="playlist-badge-model">✨ {model_esc}</span>
                                {analysis_link_html}
                            </div>
                        </td>
                        <td>
                            <div style="display: flex; gap: 4px; align-items: center; flex-wrap: wrap;">
                                {tags_chips_html}
                                {audiodb_pills_html}
                            </div>
                        </td>
                        <td style="text-align: right;">
                            <div style="display: flex; gap: 8px; justify-content: flex-end; align-items: center;">
                                {listen_spotify_link}
                                <a href="/?id={s_id}" class="btn-playlist-action btn-playlist-outline" style="font-size: 0.74rem; padding: 3px 8px; text-decoration: none;">Lyrics ↗</a>
                            </div>
                        </td>
                    </tr>
                ''')

                clean_tracks_for_json.append({
                    'id': s_id,
                    'artist': artist_val,
                    'song': song_val,
                    'model_name': model_name,
                    'spotify_id': spotify_id or '',
                })

            tracklist_html = "".join(track_rows)

        # Build Saved Playlists HTML
        if not saved_playlists:
            saved_playlists_html = '<p style="text-align: center; color: #A5C8FF; padding: 40px; font-style: italic;">No saved playlists yet. Generate a playlist above and click "Save" to keep it in your library!</p>'
        else:
            saved_cards = []
            for p in saved_playlists:
                p_id = p['id']
                p_name = html_escape(p.get('name') or 'Untitled Playlist')
                p_desc = html_escape(p.get('description') or '')
                p_count = p.get('track_count', 0)
                p_type = html_escape(p.get('generator_type') or 'all_analyzed')
                p_date = html_escape(p.get('created_at') or '')
                spotify_url = p.get('spotify_playlist_url')
                spotify_link_html = f'<a href="{html_escape(spotify_url)}" target="_blank" rel="noopener noreferrer" class="btn-playlist-action btn-playlist-spotify" style="font-size:0.76rem; padding:4px 10px; text-decoration:none;">Open in Spotify ↗</a>' if spotify_url else ''

                saved_cards.append(f'''
                    <div style="background: rgba(11, 30, 63, 0.6); border: 1px solid rgba(165, 200, 255, 0.2); border-radius: 12px; padding: 16px 20px; display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap;">
                        <div style="flex: 1; min-width: 220px;">
                            <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap;">
                                <span style="font-size: 1.1rem; font-weight: 700; color: #FFFFFF;">{p_name}</span>
                                <span class="playlist-mode-badge primary">{p_count} tracks</span>
                                <span class="playlist-mode-badge" style="text-transform: uppercase;">{p_type}</span>
                            </div>
                            <div style="font-size: 0.82rem; color: rgba(225, 232, 240, 0.6); margin-top: 4px;">
                                {p_desc} • Saved {p_date}
                            </div>
                        </div>
                        <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
                            {spotify_link_html}
                            <a href="/playlists?id={p_id}" class="btn-playlist-action btn-playlist-outline" style="font-size: 0.78rem; padding: 5px 12px; text-decoration: none;">📂 Load</a>
                            <a href="/playlists/export/m3u?id={p_id}" class="btn-playlist-action btn-playlist-outline" style="font-size: 0.78rem; padding: 5px 10px; text-decoration: none;" title="Download M3U">📥 M3U</a>
                            <a href="/playlists/export/csv?id={p_id}" class="btn-playlist-action btn-playlist-outline" style="font-size: 0.78rem; padding: 5px 10px; text-decoration: none;" title="Download CSV">📄 CSV</a>
                            <a href="/playlists/delete?id={p_id}" onclick="return confirm('Delete this saved playlist?');" class="btn-playlist-action btn-playlist-outline" style="font-size: 0.78rem; padding: 5px 10px; border-color: rgba(240,140,90,0.4); color: #f08c5a; text-decoration: none;" title="Delete">&times;</a>
                        </div>
                    </div>
                ''')
            saved_playlists_html = '<div style="display: flex; flex-direction: column; gap: 12px;">' + "".join(saved_cards) + '</div>'

        # Export URLs
        if saved_id:
            export_m3u_url = f"/playlists/export/m3u?id={saved_id}"
            export_csv_url = f"/playlists/export/csv?id={saved_id}"
        else:
            q_params = {
                'mode': active_mode,
                'artist': selected_artist,
                'tag': selected_tag,
                'mood': selected_mood,
                'order': order_by,
                'limit': str(limit) if limit else '',
                'name': active_playlist_title,
            }
            export_m3u_url = f"/playlists/export/m3u?{urllib.parse.urlencode({k: v for k, v in q_params.items() if v})}"
            export_csv_url = f"/playlists/export/csv?{urllib.parse.urlencode({k: v for k, v in q_params.items() if v})}"

        current_playlist_dict = {
            'name': active_playlist_title,
            'description': active_playlist_desc,
            'mode': active_mode,
            'criteria': {
                'artist': selected_artist,
                'tag': selected_tag,
                'mood': selected_mood,
                'order': order_by,
                'limit': limit,
            },
            'tracks': clean_tracks_for_json,
        }

        is_saved_tab = (active_tab == 'saved')
        generate_tab_active = '' if is_saved_tab else ' active'
        saved_playlists_tab_active = ' active' if is_saved_tab else ''
        generator_view_display = 'display: none' if is_saved_tab else 'display: flex'
        saved_view_display = 'display: flex' if is_saved_tab else 'display: none'

        mode_all_analyzed_active = ' active' if active_mode == 'all_analyzed' else ''
        mode_artist_active = ' active' if active_mode == 'artist' else ''
        mode_tag_active = ' active' if active_mode == 'tag' else ''
        mode_mood_active = ' active' if active_mode == 'mood' else ''
        mode_spotify_active = ' active' if active_mode == 'spotify' else ''

        message_banner_html = f'<div class="message" style="margin-bottom: 20px;">{html_escape(message)}</div>' if message else ''

        content = PLAYLISTS_PAGE_HTML.replace('{app_header}', build_app_header('playlists', user=current_user))\
                                     .replace('{message_banner_html}', message_banner_html)\
                                     .replace('{total_analyzed_count}', str(total_analyzed_count))\
                                     .replace('{total_artists_count}', str(total_artists_count))\
                                     .replace('{total_tags_count}', str(total_tags_count))\
                                     .replace('{spotify_status_badge}', spotify_status_badge)\
                                     .replace('{mode_all_analyzed_active}', mode_all_analyzed_active)\
                                     .replace('{mode_artist_active}', mode_artist_active)\
                                     .replace('{mode_tag_active}', mode_tag_active)\
                                     .replace('{mode_mood_active}', mode_mood_active)\
                                     .replace('{mode_spotify_active}', mode_spotify_active)\
                                     .replace('{current_mode}', html_escape(active_mode))\
                                     .replace('{mode_specific_inputs}', mode_specific_inputs)\
                                     .replace('{sort_options_html}', sort_options_html)\
                                     .replace('{limit_options_html}', limit_options_html)\
                                     .replace('{active_playlist_title}', html_escape(active_playlist_title))\
                                     .replace('{active_playlist_desc}', html_escape(active_playlist_desc))\
                                     .replace('{tag_chips_container_html}', tag_chips_container_html)\
                                     .replace('{track_count}', str(len(songs)))\
                                     .replace('{tracklist_html}', tracklist_html)\
                                     .replace('{export_m3u_url}', export_m3u_url)\
                                     .replace('{export_csv_url}', export_csv_url)\
                                     .replace('{saved_playlists_count}', str(len(saved_playlists)))\
                                     .replace('{generate_tab_active}', generate_tab_active)\
                                     .replace('{saved_playlists_tab_active}', saved_playlists_tab_active)\
                                     .replace('{generator_view_display}', generator_view_display)\
                                     .replace('{saved_view_display}', saved_view_display)\
                                     .replace('{saved_playlists_html}', saved_playlists_html)\
                                     .replace('{current_playlist_json}', json.dumps(current_playlist_dict))\
                                     .replace('{is_spotify_connected_json}', 'true' if is_spotify_connected else 'false')

        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(content.encode('utf-8'))))
        self.end_headers()
        self.wfile.write(content.encode('utf-8'))

    def handle_export_m3u(self, query_string: str):
        current_user = self.get_current_user()
        if not current_user:
            self.send_response(302)
            self.send_header('Location', '/login')
            self.end_headers()
            return

        params = urllib.parse.parse_qs(query_string)
        saved_id = params.get('id', [''])[0].strip()
        if saved_id.isdigit():
            saved_p = database.get_saved_playlist(int(saved_id))
            if saved_p:
                tracks = saved_p.get('items', [])
                title = saved_p.get('name', 'Calling Hours Playlist')
            else:
                tracks, title = [], 'Calling Hours Playlist'
        else:
            mode = params.get('mode', ['all_analyzed'])[0].strip()
            artist = params.get('artist', [''])[0].strip() or None
            tag = params.get('tag', [''])[0].strip() or None
            order = params.get('order', ['updated_at DESC'])[0].strip()
            limit_str = params.get('limit', [''])[0].strip()
            limit = int(limit_str) if limit_str.isdigit() else None
            title = params.get('name', [''])[0].strip() or 'Calling Hours All Analyzed Songs'
            tracks = database.get_analyzed_songs(artist=artist, tag=tag, order_by=order, limit=limit)

        lines = ['#EXTM3U', f'#PLAYLIST:{title}']
        for t in tracks:
            a = t.get('artist', 'Unknown Artist').strip()
            s = t.get('song', 'Unknown Track').strip()
            lines.append(f'#EXTINF:-1,{a} - {s}')
            lines.append(f'{a} - {s}')

        content = '\n'.join(lines) + '\n'
        safe_name = "".join(c for c in title if c.isalnum() or c in (' ', '_', '-')).strip().replace(' ', '_') or 'calling_hours_playlist'

        self.send_response(200)
        self.send_header('Content-Type', 'audio/x-mpegurl; charset=utf-8')
        self.send_header('Content-Disposition', f'attachment; filename="{safe_name}.m3u8"')
        self.send_header('Content-Length', str(len(content.encode('utf-8'))))
        self.end_headers()
        self.wfile.write(content.encode('utf-8'))

    def handle_export_csv(self, query_string: str):
        current_user = self.get_current_user()
        if not current_user:
            self.send_response(302)
            self.send_header('Location', '/login')
            self.end_headers()
            return

        import csv
        import io

        params = urllib.parse.parse_qs(query_string)
        saved_id = params.get('id', [''])[0].strip()
        if saved_id.isdigit():
            saved_p = database.get_saved_playlist(int(saved_id))
            if saved_p:
                tracks = saved_p.get('items', [])
                title = saved_p.get('name', 'Calling Hours Playlist')
            else:
                tracks, title = [], 'Calling Hours Playlist'
        else:
            mode = params.get('mode', ['all_analyzed'])[0].strip()
            artist = params.get('artist', [''])[0].strip() or None
            tag = params.get('tag', [''])[0].strip() or None
            order = params.get('order', ['updated_at DESC'])[0].strip()
            limit_str = params.get('limit', [''])[0].strip()
            limit = int(limit_str) if limit_str.isdigit() else None
            title = params.get('name', [''])[0].strip() or 'Calling Hours All Analyzed Songs'
            tracks = database.get_analyzed_songs(artist=artist, tag=tag, order_by=order, limit=limit)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Artist', 'Song', 'Gemini Model', 'Analysis Prompt', 'Analyzed Date', 'Calling Hours ID'])
        for t in tracks:
            writer.writerow([
                t.get('artist', ''),
                t.get('song', ''),
                t.get('model_name', ''),
                t.get('prompt_name', ''),
                t.get('updated_at') or t.get('created_at') or '',
                t.get('id') or t.get('search_id') or '',
            ])

        content = output.getvalue()
        safe_name = "".join(c for c in title if c.isalnum() or c in (' ', '_', '-')).strip().replace(' ', '_') or 'calling_hours_playlist'

        self.send_response(200)
        self.send_header('Content-Type', 'text/csv; charset=utf-8')
        self.send_header('Content-Disposition', f'attachment; filename="{safe_name}.csv"')
        self.send_header('Content-Length', str(len(content.encode('utf-8'))))
        self.end_headers()
        self.wfile.write(content.encode('utf-8'))

    def handle_api_playlists_save(self, data: Dict[str, Any]):
        current_user = self.get_current_user()
        if not current_user:
            self.send_response(401)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'Unauthorized'}).encode('utf-8'))
            return

        name = (data.get('name') or 'Calling Hours Playlist').strip()
        description = (data.get('description') or '').strip()
        generator_type = (data.get('generator_type') or 'all_analyzed').strip()
        criteria = data.get('criteria') or {}
        items = data.get('items') or []

        if not items:
            items = database.get_analyzed_songs()

        playlist_id = database.save_playlist(
            name=name,
            generator_type=generator_type,
            items=items,
            description=description,
            criteria=criteria
        )

        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.end_headers()
        self.wfile.write(json.dumps({'success': True, 'playlist_id': playlist_id}).encode('utf-8'))

    def handle_api_playlists_export_spotify(self, data: Dict[str, Any]):
        current_user = self.get_current_user()
        if not current_user:
            self.send_response(401)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'Unauthorized'}).encode('utf-8'))
            return

        token_rec = database.get_spotify_token(current_user['email'])
        access_token = spotify.get_valid_access_token(current_user['email']) if token_rec else None

        if not access_token:
            self.send_response(401)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({
                'success': False,
                'error': 'Spotify account not connected. Please connect your Spotify account first.',
                'needs_auth': True,
                'auth_url': '/auth/spotify'
            }).encode('utf-8'))
            return

        name = (data.get('name') or 'Calling Hours Playlist').strip()
        description = (data.get('description') or 'Generated by Calling Hours Lyric Intelligence').strip()
        tracks = data.get('tracks') or []

        if not tracks:
            tracks = database.get_analyzed_songs()

        try:
            user_id = token_rec.get('spotify_user_id') if token_rec else None
            res = spotify.export_songs_to_spotify_playlist(
                access_token=access_token,
                user_id=user_id,
                playlist_name=name,
                songs=tracks,
                description=description
            )
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(res).encode('utf-8'))
        except Exception as e:
            err_str = str(e)
            is_forbidden = '403' in err_str or 'Forbidden' in err_str or 'permission' in err_str.lower()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({
                'success': False,
                'error': err_str,
                'needs_reauth': is_forbidden,
                'auth_url': '/auth/spotify',
                'message': 'Your Spotify account was connected before playlist permissions were added. Please re-authorize to grant playlist creation access.' if is_forbidden else err_str
            }).encode('utf-8'))

    def log_message(self, format, *args):
        return

def build_genius_api_headers():
    headers = {
        'User-Agent': 'CallingHours/1.0',
        'Accept': 'application/json',
    }
    if ACCESS_TOKEN:
        headers['Authorization'] = f'Bearer {ACCESS_TOKEN}'
    elif GENIUS_CLIENT_ID:
        headers['X-Genius-Client-Id'] = GENIUS_CLIENT_ID
    return headers

def build_genius_headers():
    return build_genius_api_headers()

def build_browser_headers():
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Sec-Ch-Ua': '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Upgrade-Insecure-Requests': '1',
    }

def fetch_lrclib_lyrics(
    artist: str,
    song: str,
    canonical_artist: Optional[str] = None,
    canonical_song: Optional[str] = None
) -> str | None:
    headers = {
        'User-Agent': 'CallingHours/1.0 (https://github.com/callinghours)'
    }

    # Pairs of (artist, song) candidates to try first
    candidates = []
    if canonical_artist and canonical_song:
        c_art = canonical_artist.strip()
        c_sng = canonical_song.strip()
        if c_art and c_sng:
            candidates.append((c_art, c_sng))
    if artist and song:
        u_art = artist.strip()
        u_sng = song.strip()
        if (u_art, u_sng) not in candidates:
            candidates.append((u_art, u_sng))

    for art, sng in candidates:
        # 1. Exact match endpoint
        try:
            resp = requests.get(
                'https://lrclib.net/api/get',
                params={'artist_name': art, 'track_name': sng},
                headers=headers,
                timeout=8
            )
            if resp.status_code == 200:
                data = resp.json()
                lyrics = data.get('plainLyrics')
                if lyrics and lyrics.strip():
                    return lyrics.strip()
        except Exception as e:
            print(f"LRCLIB direct fetch failed for {art} - {sng}: {e}")

        # 2. Search endpoint fallback
        try:
            resp = requests.get(
                'https://lrclib.net/api/search',
                params={'q': f"{art} {sng}"},
                headers=headers,
                timeout=8
            )
            if resp.status_code == 200:
                results = resp.json()
                if isinstance(results, list):
                    for item in results:
                        lyrics = item.get('plainLyrics')
                        if lyrics and lyrics.strip():
                            return lyrics.strip()
        except Exception as e:
            print(f"LRCLIB search fallback failed for {art} {sng}: {e}")

    # 3. Track-only search with fuzzy artist matching (resilient to artist typos e.g. 'the hoteliers')
    query_song = (canonical_song or song).strip()
    target_artist = (canonical_artist or artist).strip().lower()
    if query_song and target_artist:
        try:
            resp = requests.get(
                'https://lrclib.net/api/search',
                params={'q': query_song},
                headers=headers,
                timeout=8
            )
            if resp.status_code == 200:
                results = resp.json()
                if isinstance(results, list):
                    for item in results:
                        lyrics = item.get('plainLyrics')
                        if not lyrics or not lyrics.strip():
                            continue
                        item_artist = (item.get('artistName') or '').strip().lower()
                        sim = difflib.SequenceMatcher(None, target_artist, item_artist).ratio()
                        if target_artist in item_artist or item_artist in target_artist or sim >= 0.7:
                            return lyrics.strip()
        except Exception as e:
            print(f"LRCLIB track fuzzy search failed for {target_artist} - {query_song}: {e}")

    return None

def get_redirect_uri(host_header: str | None = None) -> str:
    # Explicit override via environment variable
    if os.environ.get('GENIUS_REDIRECT_URI'):
        return os.environ['GENIUS_REDIRECT_URI']

    if host_header:
        scheme = 'https' if (IS_CLOUD_RUN or 'run.app' in host_header) else 'http'
        return f'{scheme}://{host_header}/callback'

    display_host = '127.0.0.1' if HOST == '0.0.0.0' else HOST
    return f'http://{display_host}:{SERVER_PORT or PORT}/callback'

def exchange_genius_code(code: str, redirect_uri: str) -> str | None:
    if not GENIUS_CLIENT_ID or not GENIUS_CLIENT_SECRET:
        raise RuntimeError('Missing Genius client credentials.')

    payload = {
        'client_id': GENIUS_CLIENT_ID,
        'client_secret': GENIUS_CLIENT_SECRET,
        'code': code,
        'grant_type': 'authorization_code',
        'redirect_uri': redirect_uri,
    }
    response = requests.post(GENIUS_TOKEN_URL, data=payload, headers={'Accept': 'application/json'}, timeout=10)
    response.raise_for_status()
    data = response.json()
    return data.get('access_token')

def save_access_token(token: str):
    secrets_path = os.path.join(SCRIPT_DIR, 'calling_hours_secrets.py')
    if not os.path.exists(secrets_path):
        return

    try:
        with open(secrets_path, 'r', encoding='utf-8') as file:
            content = file.read()

        if 'GENIUS_ACCESS_TOKEN' in content:
            content = re.sub(r'GENIUS_ACCESS_TOKEN\s*=\s*.*', f'GENIUS_ACCESS_TOKEN = "{token}"', content, count=1)
        else:
            content += f'\nGENIUS_ACCESS_TOKEN = "{token}"\n'

        with open(secrets_path, 'w', encoding='utf-8') as file:
            file.write(content)
    except (OSError, IOError) as e:
        print(f"Warning: Could not write access token to file ({e}). Token is stored in-memory.")

def search_genius_song_details(artist: str, song: str) -> Dict[str, str] | None:
    query = f"{artist} {song}".strip()

    if ACCESS_TOKEN:
        try:
            response = requests.get(GENIUS_API_SEARCH_URL, params={'q': query}, headers=build_genius_headers(), timeout=10)
            if response.status_code == 200:
                data = response.json()
                for hit in data.get('response', {}).get('hits', []):
                    result = hit.get('result')
                    if result and (hit.get('type') == 'song' or result.get('_type') == 'song' or result.get('type') == 'song'):
                        url = result.get('url')
                        resolved_artist = result.get('primary_artist', {}).get('name') or result.get('artist_names')
                        resolved_song = result.get('title')
                        return {
                            'url': url,
                            'artist': resolved_artist or artist,
                            'song': resolved_song or song
                        }
        except Exception as e:
            print(f"Genius API search error: {e}")
    else:
        try:
            response = requests.get(GENIUS_WEB_SEARCH_URL, params={'q': query}, headers=build_genius_headers(), timeout=10)
            if response.status_code == 200:
                data = response.json()
                for section in data.get('response', {}).get('sections', []):
                    for hit in section.get('hits', []):
                        result = hit.get('result') or hit.get('hit', {}).get('result')
                        if result and (hit.get('type') == 'song' or result.get('_type') == 'song' or result.get('type') == 'song'):
                            url = result.get('url')
                            resolved_artist = result.get('primary_artist', {}).get('name') or result.get('artist_names')
                            resolved_song = result.get('title')
                            return {
                                'url': url,
                                'artist': resolved_artist or artist,
                                'song': resolved_song or song
                            }
        except Exception as e:
            print(f"Genius web search error: {e}")

    return None

def search_genius_song(artist: str, song: str) -> str | None:
    info = search_genius_song_details(artist, song)
    return info.get('url') if info else None

def fetch_genius_lyrics(song_url: str) -> str | None:
    headers = build_browser_headers()
    html_text = None

    # 1. Try curl_cffi with browser impersonation to bypass Cloudflare TLS fingerprint blocks (403 Forbidden)
    if HAS_CURL_CFFI:
        try:
            resp = c_requests.get(song_url, impersonate='chrome', headers=headers, timeout=12)
            if resp.status_code == 200:
                html_text = resp.text
            elif resp.status_code != 403:
                resp.raise_for_status()
        except Exception as ce:
            print(f"curl_cffi fetch error: {ce}")

    # 2. Fallback to standard requests if curl_cffi was unavailable or didn't fetch HTML
    if not html_text:
        try:
            response = requests.get(song_url, headers=headers, timeout=10)
            if response.status_code == 200:
                html_text = response.text
            else:
                print(f"Genius standard requests status: {response.status_code}")
        except Exception as re_err:
            print(f"Genius standard requests error: {re_err}")

    if not html_text:
        return None

    # 3. Parse lyrics using BeautifulSoup if available
    if HAS_BS4:
        soup = BeautifulSoup(html_text, 'html.parser')
        for el in soup.find_all(class_=lambda x: x and any(h in x for h in ['LyricsHeader', 'LyricsPlaceholder', 'SongDescription', 'InContentAd'])):
            el.decompose()

        containers = soup.find_all('div', attrs={'data-lyrics-container': 'true'})
        if containers:
            lyrics_chunks = []
            for c in containers:
                for br in c.find_all(['br', 'hr']):
                    next_sib = br.next_sibling
                    if next_sib and isinstance(next_sib, str) and next_sib.startswith('\n'):
                        br.replace_with('\n')
                        next_sib.replace_with(next_sib.lstrip('\r\n'))
                    else:
                        br.replace_with('\n')
                text = c.get_text()
                lines = [l.strip() for l in text.splitlines()]
                cleaned = '\n'.join(lines)
                cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
                if cleaned.strip():
                    lyrics_chunks.append(cleaned.strip())
            full_lyrics = '\n\n'.join(lyrics_chunks)
            full_lyrics = re.sub(r'^\d*\s*Contributors.*?Lyrics\n?', '', full_lyrics, flags=re.IGNORECASE)
            return full_lyrics.strip()

    # 4. Fallback regex parser
    lyrics = []
    start_matches = list(re.finditer(r'<div[^>]*data-lyrics-container="true"[^>]*>', html_text))

    if start_matches:
        for match in start_matches:
            start_idx = match.end()
            div_count = 1
            pos = start_idx
            end_idx = start_idx
            while div_count > 0 and pos < len(html_text):
                next_open = html_text.find('<div', pos)
                next_close = html_text.find('</div>', pos)
                if next_close == -1:
                    break
                if next_open != -1 and next_open < next_close:
                    div_count += 1
                    pos = next_open + 4
                else:
                    div_count -= 1
                    if div_count == 0:
                        end_idx = next_close
                    pos = next_close + 6

            block = html_text[start_idx:end_idx]
            block = re.sub(r'<div[^>]*class="[^"]*LyricsHeader[^"]*"[^>]*>.*?</div>', '', block, flags=re.DOTALL)
            block = re.sub(r'<br\s*/?>', '\n', block)
            block = re.sub(r'<.*?>', '', block)
            block = html.unescape(block)
            lyrics.append(block.strip())

        full_lyrics = '\n\n'.join(line for line in lyrics if line)
        full_lyrics = re.sub(r'^\d*\s*Contributors.*?Lyrics\n?', '', full_lyrics, flags=re.IGNORECASE)
        return full_lyrics.strip()

    match = re.search(r'<div class="lyrics">.*?<p>(.*?)</p>.*?</div>', html_text, flags=re.S)
    if match:
        text = match.group(1)
        text = re.sub(r'<br\s*/?>', '\n', text)
        text = re.sub(r'<.*?>', '', text)
        text = html.unescape(text)
        text = re.sub(r'^\d*\s*Contributors.*?Lyrics\n?', '', text, flags=re.IGNORECASE)
        return text.strip()

    return None

def run_server():
    global SERVER_PORT
    port = PORT
    try:
        server_address = (HOST, port)
        with http.server.ThreadingHTTPServer(server_address, CallingHoursRequestHandler) as httpd:
            SERVER_PORT = port
            display_host = '127.0.0.1' if HOST == '0.0.0.0' else HOST
            print(f'Calling Hours app running at http://{display_host}:{port} (listening on {HOST}:{port})')
            
            # Open browser automatically only if running locally (not in Cloud Run/Docker/headless)
            is_container = IS_CLOUD_RUN or bool(os.environ.get('PORT')) or HOST == '0.0.0.0'
            no_browser = is_container or os.environ.get('NO_BROWSER', '').lower() in ('1', 'true', 'yes')
            if not no_browser:
                threading.Timer(0.5, lambda: webbrowser.open(f'http://{display_host}:{port}')).start()

            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print('\nServer stopped')
    except OSError as e:
        print(f'Unable to bind to {HOST}:{port}: {e}')
        return

if __name__ == '__main__':
    run_server()