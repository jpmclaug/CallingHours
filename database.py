from __future__ import annotations

import os
import sqlite3
import secrets
import json
import re
import time
from contextlib import contextmanager
from datetime import datetime, date, timedelta, timezone
from typing import Optional, List, Dict, Any, Union, Tuple

PRIMARY_ADMIN_EMAIL = "jpmclaug@gmail.com"

try:
    import psycopg2
    import psycopg2.extras
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

try:
    import calling_hours_secrets
except ImportError:
    calling_hours_secrets = None

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SQLITE_PATH = os.path.join(SCRIPT_DIR, 'calling_hours.db')

def get_db_target(custom_target: Optional[str] = None) -> str:
    """
    Resolve the database target connection string or file path.
    Precedence:
    1. custom_target argument
    2. DATABASE_URL environment variable
    3. NEON_DATABASE_URL environment variable
    4. DATABASE_PATH environment variable (used for test isolation)
    5. NEON_DATABASE_URL or DATABASE_URL in calling_hours_secrets.py
    6. Default local SQLite file (calling_hours.db)
    """
    if custom_target:
        return custom_target
    if os.environ.get('DATABASE_URL'):
        return os.environ['DATABASE_URL']
    if os.environ.get('NEON_DATABASE_URL'):
        return os.environ['NEON_DATABASE_URL']
    if os.environ.get('DATABASE_PATH'):
        return os.environ['DATABASE_PATH']
    if calling_hours_secrets:
        secret_url = getattr(calling_hours_secrets, 'NEON_DATABASE_URL', None) or getattr(calling_hours_secrets, 'DATABASE_URL', None)
        if secret_url and secret_url.strip():
            return secret_url.strip()
    return DEFAULT_SQLITE_PATH

# Backwards compatibility alias
get_db_path = get_db_target

_db_mutation_version: int = 0

def get_db_mutation_version() -> int:
    """Return monotonic version incremented on database writes."""
    return _db_mutation_version

def notify_db_mutation() -> None:
    """Increment the mutation version counter."""
    global _db_mutation_version
    _db_mutation_version += 1

def is_postgres(target: Optional[str] = None) -> bool:
    """Return True if the resolved database target is a PostgreSQL/Neon connection string."""
    resolved = get_db_target(target)
    return resolved.startswith('postgresql://') or resolved.startswith('postgres://')

def get_backend_name(target: Optional[str] = None) -> str:
    """Return human-readable backend name."""
    return "Neon PostgreSQL" if is_postgres(target) else "SQLite"

def _format_datetime(val: Any) -> Optional[str]:
    if isinstance(val, (datetime, date)):
        return val.strftime('%Y-%m-%d %H:%M:%S')
    return str(val) if val is not None else None

def _format_search_record(rec: Any) -> Optional[Dict[str, Any]]:
    if not rec:
        return None
    d = dict(rec)
    if 'created_at' in d and d['created_at'] is not None:
        d['created_at'] = _format_datetime(d['created_at'])
    if 'updated_at' in d and d['updated_at'] is not None:
        d['updated_at'] = _format_datetime(d['updated_at'])
    if 'has_lyrics' in d:
        d['has_lyrics'] = bool(d['has_lyrics'])
    elif 'lyrics' in d:
        d['has_lyrics'] = bool(d['lyrics'] and str(d['lyrics']).strip())
    if 'has_analysis' in d:
        d['has_analysis'] = bool(d['has_analysis'])
    elif 'analysis' in d:
        d['has_analysis'] = bool(d['analysis'] and str(d['analysis']).strip())
    if 'track_tags' in d and d['track_tags'] is not None:
        if isinstance(d['track_tags'], str):
            try:
                d['track_tags'] = json.loads(d['track_tags'])
            except Exception:
                d['track_tags'] = []
        elif not isinstance(d['track_tags'], list):
            d['track_tags'] = []
    else:
        d['track_tags'] = []
    if 'theaudiodb_data' in d and d['theaudiodb_data'] is not None:
        if isinstance(d['theaudiodb_data'], str):
            try:
                d['theaudiodb_data'] = json.loads(d['theaudiodb_data'])
            except Exception:
                d['theaudiodb_data'] = None
        elif not isinstance(d['theaudiodb_data'], dict):
            d['theaudiodb_data'] = None
    else:
        d['theaudiodb_data'] = None
    return d

def _format_artist_record(rec: Any) -> Optional[Dict[str, Any]]:
    if not rec:
        return None
    d = dict(rec)
    if 'created_at' in d and d['created_at'] is not None:
        d['created_at'] = _format_datetime(d['created_at'])
    if 'updated_at' in d and d['updated_at'] is not None:
        d['updated_at'] = _format_datetime(d['updated_at'])
    for field in ('tags', 'top_tracks', 'similar_artists'):
        if field in d and d[field] is not None:
            if isinstance(d[field], str):
                try:
                    d[field] = json.loads(d[field])
                except Exception:
                    d[field] = []
            elif not isinstance(d[field], list):
                d[field] = []
        else:
            d[field] = []
    for dict_field in ('setlistfm_data', 'theaudiodb_data', 'spotify_data'):
        if dict_field in d and d[dict_field] is not None:
            if isinstance(d[dict_field], str):
                try:
                    d[dict_field] = json.loads(d[dict_field])
                except Exception:
                    d[dict_field] = None
            elif not isinstance(d[dict_field], dict):
                d[dict_field] = None
        else:
            d[dict_field] = None
    return d

def _format_user_record(rec: Any) -> Optional[Dict[str, Any]]:
    if not rec:
        return None
    d = dict(rec)
    if 'created_at' in d and d['created_at'] is not None:
        d['created_at'] = _format_datetime(d['created_at'])
    if 'last_login_at' in d and d['last_login_at'] is not None:
        d['last_login_at'] = _format_datetime(d['last_login_at'])
    if 'is_admin' in d:
        d['is_admin'] = bool(d['is_admin'])
    if 'is_active' in d:
        d['is_active'] = bool(d['is_active'])
    return d

def _format_spotify_token_record(rec: Any) -> Optional[Dict[str, Any]]:
    if not rec:
        return None
    d = dict(rec)
    if 'created_at' in d and d['created_at'] is not None:
        d['created_at'] = _format_datetime(d['created_at'])
    if 'updated_at' in d and d['updated_at'] is not None:
        d['updated_at'] = _format_datetime(d['updated_at'])
    if 'expires_at' in d and d['expires_at'] is not None:
        d['expires_at'] = _format_datetime(d['expires_at'])
    return d

def _format_spotify_history_record(rec: Any) -> Optional[Dict[str, Any]]:
    if not rec:
        return None
    d = dict(rec)
    if 'created_at' in d and d['created_at'] is not None:
        d['created_at'] = _format_datetime(d['created_at'])
    if 'played_at' in d and d['played_at'] is not None:
        d['played_at'] = _format_datetime(d['played_at'])
    if 'duration_ms' in d and d['duration_ms'] is not None:
        try:
            d['duration_ms'] = int(d['duration_ms'])
        except (ValueError, TypeError):
            d['duration_ms'] = 0
    if 'popularity' in d and d['popularity'] is not None:
        try:
            d['popularity'] = int(d['popularity'])
        except (ValueError, TypeError):
            d['popularity'] = 0

    # Provide normalized aliases matching Spotify Web API track dicts
    if 'name' not in d and 'track_name' in d:
        d['name'] = d['track_name']
    if 'artist' not in d and 'artist_name' in d:
        d['artist'] = d['artist_name']
    if 'album' not in d and 'album_name' in d:
        d['album'] = d['album_name']
    if 'album_image' not in d and 'album_image_url' in d:
        d['album_image'] = d['album_image_url']
    if 'track_id' not in d and 'spotify_track_id' in d:
        d['track_id'] = d['spotify_track_id']
    if 'spotify_url' not in d or not d['spotify_url']:
        tid = d.get('track_id') or d.get('spotify_track_id')
        d['spotify_url'] = f"https://open.spotify.com/track/{tid}" if tid else ""
    if 'duration_formatted' not in d:
        ms = d.get('duration_ms') or 0
        total_sec = int(ms) // 1000
        d['duration_formatted'] = f"{total_sec // 60}:{total_sec % 60:02d}"
    if 'release_year' not in d:
        rel = str(d.get('release_date') or '')
        d['release_year'] = rel[:4] if len(rel) >= 4 and rel[:4].isdigit() else ''

    return d

def _format_playlist_record(rec: Any) -> Optional[Dict[str, Any]]:
    if not rec:
        return None
    d = dict(rec)
    if 'created_at' in d and d['created_at'] is not None:
        d['created_at'] = _format_datetime(d['created_at'])
    if 'updated_at' in d and d['updated_at'] is not None:
        d['updated_at'] = _format_datetime(d['updated_at'])
    if 'track_count' in d and d['track_count'] is not None:
        try:
            d['track_count'] = int(d['track_count'])
        except (ValueError, TypeError):
            d['track_count'] = 0
    if 'criteria_json' in d and d['criteria_json'] is not None:
        if isinstance(d['criteria_json'], str):
            try:
                d['criteria'] = json.loads(d['criteria_json'])
            except Exception:
                d['criteria'] = {}
        elif isinstance(d['criteria_json'], dict):
            d['criteria'] = d['criteria_json']
    else:
        d['criteria'] = {}
    return d

def _format_playlist_item_record(rec: Any) -> Optional[Dict[str, Any]]:
    if not rec:
        return None
    d = dict(rec)
    if 'created_at' in d and d['created_at'] is not None:
        d['created_at'] = _format_datetime(d['created_at'])
    if 'position' in d and d['position'] is not None:
        try:
            d['position'] = int(d['position'])
        except (ValueError, TypeError):
            d['position'] = 0
    return d

def _format_band_rating_record(rec: Any) -> Optional[Dict[str, Any]]:
    if not rec:
        return None
    d = dict(rec)
    if 'created_at' in d and d['created_at'] is not None:
        d['created_at'] = _format_datetime(d['created_at'])
    if 'updated_at' in d and d['updated_at'] is not None:
        d['updated_at'] = _format_datetime(d['updated_at'])
    if 'rating' in d and d['rating'] is not None:
        try:
            d['rating'] = int(d['rating'])
        except (ValueError, TypeError):
            d['rating'] = 0
    return d

def _format_song_analysis_record(rec: Any) -> Optional[Dict[str, Any]]:
    if not rec:
        return None
    d = dict(rec)
    if 'created_at' in d and d['created_at'] is not None:
        d['created_at'] = _format_datetime(d['created_at'])
    if 'updated_at' in d and d['updated_at'] is not None:
        d['updated_at'] = _format_datetime(d['updated_at'])
    return d


@contextmanager
def get_connection(db_path: Optional[str] = None):
    """Context manager for database connections (Neon PostgreSQL or SQLite)."""
    target = get_db_target(db_path)
    if is_postgres(target):
        if not PSYCOPG2_AVAILABLE:
            raise RuntimeError(
                "psycopg2 is required for Neon PostgreSQL connections. "
                "Run: pip install psycopg2-binary"
            )
        clean_url = target
        if clean_url.startswith('postgres://'):
            clean_url = clean_url.replace('postgres://', 'postgresql://', 1)
        conn = psycopg2.connect(clean_url, connect_timeout=15)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(target, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

def init_db(db_path: Optional[str] = None) -> None:
    """Initialize the database schema and indexes for Neon PostgreSQL or SQLite."""
    target = get_db_target(db_path)
    with get_connection(target) as conn:
        cursor = conn.cursor()
        if is_postgres(target):
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS searches (
                    id SERIAL PRIMARY KEY,
                    artist TEXT NOT NULL,
                    song TEXT NOT NULL,
                    artist_normalized TEXT NOT NULL,
                    song_normalized TEXT NOT NULL,
                    lyrics TEXT,
                    source TEXT,
                    song_url TEXT,
                    analysis TEXT,
                    model_name TEXT,
                    prompt_name TEXT,
                    track_tags TEXT,
                    theaudiodb_data TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(artist_normalized, song_normalized)
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_searches_artist 
                ON searches(artist_normalized);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_searches_updated_at 
                ON searches(updated_at DESC);
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    name TEXT,
                    picture TEXT,
                    is_admin BOOLEAN DEFAULT FALSE,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login_at TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_users_email 
                ON users(email);
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    email TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_email 
                ON sessions(email);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_expires_at 
                ON sessions(expires_at);
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS artist_metadata (
                    id SERIAL PRIMARY KEY,
                    artist TEXT NOT NULL,
                    artist_normalized TEXT NOT NULL UNIQUE,
                    tags TEXT,
                    top_tracks TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_artist_meta_normalized 
                ON artist_metadata(artist_normalized);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_artist_meta_updated_at 
                ON artist_metadata(updated_at DESC);
            """)
            cursor.execute("""
                ALTER TABLE searches ADD COLUMN IF NOT EXISTS track_tags TEXT;
            """)
            cursor.execute("""
                ALTER TABLE searches ADD COLUMN IF NOT EXISTS theaudiodb_data TEXT;
            """)
            cursor.execute("""
                ALTER TABLE artist_metadata ADD COLUMN IF NOT EXISTS setlistfm_data TEXT;
            """)
            cursor.execute("""
                ALTER TABLE artist_metadata ADD COLUMN IF NOT EXISTS theaudiodb_data TEXT;
            """)
            cursor.execute("""
                ALTER TABLE artist_metadata ADD COLUMN IF NOT EXISTS bio TEXT;
            """)
            cursor.execute("""
                ALTER TABLE artist_metadata ADD COLUMN IF NOT EXISTS similar_artists TEXT;
            """)
            cursor.execute("""
                ALTER TABLE artist_metadata ADD COLUMN IF NOT EXISTS listeners BIGINT;
            """)
            cursor.execute("""
                ALTER TABLE artist_metadata ADD COLUMN IF NOT EXISTS playcount BIGINT;
            """)
            cursor.execute("""
                ALTER TABLE artist_metadata ADD COLUMN IF NOT EXISTS image_url TEXT;
            """)
            cursor.execute("""
                ALTER TABLE artist_metadata ADD COLUMN IF NOT EXISTS banner_url TEXT;
            """)
            cursor.execute("""
                ALTER TABLE artist_metadata ADD COLUMN IF NOT EXISTS formed_year INT;
            """)
            cursor.execute("""
                ALTER TABLE artist_metadata ADD COLUMN IF NOT EXISTS country TEXT;
            """)
            cursor.execute("""
                ALTER TABLE artist_metadata ADD COLUMN IF NOT EXISTS spotify_data TEXT;
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS spotify_tokens (
                    id SERIAL PRIMARY KEY,
                    user_email TEXT NOT NULL UNIQUE,
                    access_token TEXT NOT NULL,
                    refresh_token TEXT,
                    expires_at TIMESTAMP NOT NULL,
                    spotify_user_id TEXT,
                    spotify_display_name TEXT,
                    spotify_profile_url TEXT,
                    spotify_image_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_spotify_tokens_email 
                ON spotify_tokens(user_email);
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS spotify_history (
                    id SERIAL PRIMARY KEY,
                    user_email TEXT NOT NULL,
                    spotify_track_id TEXT NOT NULL,
                    played_at TIMESTAMP NOT NULL,
                    track_name TEXT NOT NULL,
                    artist_name TEXT NOT NULL,
                    album_name TEXT,
                    album_image_url TEXT,
                    duration_ms INTEGER,
                    popularity INTEGER,
                    preview_url TEXT,
                    spotify_url TEXT,
                    release_date TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_email, spotify_track_id, played_at)
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_spotify_hist_user 
                ON spotify_history(user_email);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_spotify_hist_played 
                ON spotify_history(played_at DESC);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_spotify_hist_email_played 
                ON spotify_history(user_email, played_at DESC);
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS playlists (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    generator_type TEXT NOT NULL,
                    criteria_json TEXT,
                    track_count INTEGER DEFAULT 0,
                    spotify_playlist_id TEXT,
                    spotify_playlist_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_playlists_updated_at 
                ON playlists(updated_at DESC);
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS playlist_items (
                    id SERIAL PRIMARY KEY,
                    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    search_id INTEGER,
                    artist TEXT NOT NULL,
                    song TEXT NOT NULL,
                    lyrics_preview TEXT,
                    model_name TEXT,
                    spotify_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_playlist_items_playlist_id 
                ON playlist_items(playlist_id);
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS thematic_curation_cache (
                    id SERIAL PRIMARY KEY,
                    cache_key TEXT NOT NULL UNIQUE,
                    mode TEXT NOT NULL,
                    prompt TEXT,
                    artists TEXT,
                    result_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_thematic_cache_key 
                ON thematic_curation_cache(cache_key);
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS band_ratings (
                    id SERIAL PRIMARY KEY,
                    user_email TEXT NOT NULL,
                    artist TEXT NOT NULL,
                    artist_normalized TEXT NOT NULL,
                    rating INTEGER NOT NULL CHECK (rating >= 0 AND rating <= 5),
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_email, artist_normalized)
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_band_ratings_user_artist 
                ON band_ratings(user_email, artist_normalized);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_band_ratings_user_rating 
                ON band_ratings(user_email, rating DESC);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_band_ratings_user_updated 
                ON band_ratings(user_email, updated_at DESC);
            """)
            cursor.execute("""
                INSERT INTO users (email, is_admin, is_active, created_at)
                VALUES (%s, TRUE, TRUE, CURRENT_TIMESTAMP)
                ON CONFLICT (email) DO UPDATE SET is_admin = TRUE, is_active = TRUE;
            """, (PRIMARY_ADMIN_EMAIL.lower().strip(),))
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS song_analyses (
                    id SERIAL PRIMARY KEY,
                    artist TEXT NOT NULL,
                    song TEXT NOT NULL,
                    artist_normalized TEXT NOT NULL,
                    song_normalized TEXT NOT NULL,
                    prompt_name TEXT NOT NULL,
                    prompt_text TEXT,
                    analysis TEXT NOT NULL,
                    model_name TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(artist_normalized, song_normalized, prompt_name)
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_song_analyses_artist_song 
                ON song_analyses(artist_normalized, song_normalized);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_song_analyses_prompt 
                ON song_analyses(artist_normalized, song_normalized, prompt_name);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_song_analyses_updated 
                ON song_analyses(updated_at DESC);
            """)
            try:
                cursor.execute("""
                    INSERT INTO song_analyses (
                        artist, song, artist_normalized, song_normalized,
                        prompt_name, analysis, model_name, created_at, updated_at
                    )
                    SELECT 
                        artist, song, artist_normalized, song_normalized,
                        COALESCE(NULLIF(TRIM(prompt_name), ''), 'Default Analysis'),
                        analysis, model_name, created_at, updated_at
                    FROM searches
                    WHERE analysis IS NOT NULL AND LENGTH(TRIM(analysis)) > 0
                    ON CONFLICT (artist_normalized, song_normalized, prompt_name) DO NOTHING;
                """)
            except Exception as bfe:
                print(f"Warning: Postgres song_analyses backfill skipped ({bfe})")
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS searches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artist TEXT NOT NULL,
                    song TEXT NOT NULL,
                    artist_normalized TEXT NOT NULL,
                    song_normalized TEXT NOT NULL,
                    lyrics TEXT,
                    source TEXT,
                    song_url TEXT,
                    analysis TEXT,
                    model_name TEXT,
                    prompt_name TEXT,
                    track_tags TEXT,
                    theaudiodb_data TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(artist_normalized, song_normalized)
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_searches_artist 
                ON searches(artist_normalized);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_searches_updated_at 
                ON searches(updated_at DESC);
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS artist_metadata (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artist TEXT NOT NULL,
                    artist_normalized TEXT NOT NULL UNIQUE,
                    tags TEXT,
                    top_tracks TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_artist_meta_normalized 
                ON artist_metadata(artist_normalized);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_artist_meta_updated_at 
                ON artist_metadata(updated_at DESC);
            """)
            cursor.execute("PRAGMA table_info(searches);")
            existing_cols = [col[1] for col in cursor.fetchall()]
            if 'track_tags' not in existing_cols:
                cursor.execute("ALTER TABLE searches ADD COLUMN track_tags TEXT;")
            if 'theaudiodb_data' not in existing_cols:
                cursor.execute("ALTER TABLE searches ADD COLUMN theaudiodb_data TEXT;")

            cursor.execute("PRAGMA table_info(artist_metadata);")
            art_cols = [col[1] for col in cursor.fetchall()]
            art_col_defs = [
                ('setlistfm_data', 'TEXT'),
                ('theaudiodb_data', 'TEXT'),
                ('bio', 'TEXT'),
                ('similar_artists', 'TEXT'),
                ('listeners', 'INTEGER'),
                ('playcount', 'INTEGER'),
                ('image_url', 'TEXT'),
                ('banner_url', 'TEXT'),
                ('formed_year', 'INTEGER'),
                ('country', 'TEXT'),
                ('spotify_data', 'TEXT'),
            ]
            for col_name, col_type in art_col_defs:
                if col_name not in art_cols:
                    cursor.execute(f"ALTER TABLE artist_metadata ADD COLUMN {col_name} {col_type};")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    name TEXT,
                    picture TEXT,
                    is_admin INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login_at TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_users_email 
                ON users(email);
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    email TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_email 
                ON sessions(email);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_expires_at 
                ON sessions(expires_at);
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS spotify_tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_email TEXT NOT NULL UNIQUE,
                    access_token TEXT NOT NULL,
                    refresh_token TEXT,
                    expires_at TIMESTAMP NOT NULL,
                    spotify_user_id TEXT,
                    spotify_display_name TEXT,
                    spotify_profile_url TEXT,
                    spotify_image_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_spotify_tokens_email 
                ON spotify_tokens(user_email);
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS spotify_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_email TEXT NOT NULL,
                    spotify_track_id TEXT NOT NULL,
                    played_at TIMESTAMP NOT NULL,
                    track_name TEXT NOT NULL,
                    artist_name TEXT NOT NULL,
                    album_name TEXT,
                    album_image_url TEXT,
                    duration_ms INTEGER,
                    popularity INTEGER,
                    preview_url TEXT,
                    spotify_url TEXT,
                    release_date TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_email, spotify_track_id, played_at)
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_spotify_hist_user 
                ON spotify_history(user_email);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_spotify_hist_played 
                ON spotify_history(played_at DESC);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_spotify_hist_email_played 
                ON spotify_history(user_email, played_at DESC);
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS playlists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    description TEXT,
                    generator_type TEXT NOT NULL,
                    criteria_json TEXT,
                    track_count INTEGER DEFAULT 0,
                    spotify_playlist_id TEXT,
                    spotify_playlist_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_playlists_updated_at 
                ON playlists(updated_at DESC);
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS playlist_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    playlist_id INTEGER NOT NULL,
                    position INTEGER NOT NULL,
                    search_id INTEGER,
                    artist TEXT NOT NULL,
                    song TEXT NOT NULL,
                    lyrics_preview TEXT,
                    model_name TEXT,
                    spotify_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(playlist_id) REFERENCES playlists(id) ON DELETE CASCADE
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_playlist_items_playlist_id 
                ON playlist_items(playlist_id);
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS thematic_curation_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cache_key TEXT NOT NULL UNIQUE,
                    mode TEXT NOT NULL,
                    prompt TEXT,
                    artists TEXT,
                    result_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_thematic_cache_key 
                ON thematic_curation_cache(cache_key);
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS band_ratings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_email TEXT NOT NULL,
                    artist TEXT NOT NULL,
                    artist_normalized TEXT NOT NULL,
                    rating INTEGER NOT NULL CHECK (rating >= 0 AND rating <= 5),
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_email, artist_normalized)
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_band_ratings_user_artist 
                ON band_ratings(user_email, artist_normalized);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_band_ratings_user_rating 
                ON band_ratings(user_email, rating DESC);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_band_ratings_user_updated 
                ON band_ratings(user_email, updated_at DESC);
            """)
            cursor.execute("""
                INSERT INTO users (email, is_admin, is_active, created_at)
                VALUES (?, 1, 1, CURRENT_TIMESTAMP)
                ON CONFLICT (email) DO UPDATE SET is_admin = 1, is_active = 1;
            """, (PRIMARY_ADMIN_EMAIL.lower().strip(),))
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS song_analyses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artist TEXT NOT NULL,
                    song TEXT NOT NULL,
                    artist_normalized TEXT NOT NULL,
                    song_normalized TEXT NOT NULL,
                    prompt_name TEXT NOT NULL,
                    prompt_text TEXT,
                    analysis TEXT NOT NULL,
                    model_name TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(artist_normalized, song_normalized, prompt_name)
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_song_analyses_artist_song 
                ON song_analyses(artist_normalized, song_normalized);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_song_analyses_prompt 
                ON song_analyses(artist_normalized, song_normalized, prompt_name);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_song_analyses_updated 
                ON song_analyses(updated_at DESC);
            """)
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO song_analyses (
                        artist, song, artist_normalized, song_normalized,
                        prompt_name, analysis, model_name, created_at, updated_at
                    )
                    SELECT 
                        artist, song, artist_normalized, song_normalized,
                        COALESCE(NULLIF(TRIM(prompt_name), ''), 'Default Analysis'),
                        analysis, model_name, created_at, updated_at
                    FROM searches
                    WHERE analysis IS NOT NULL AND LENGTH(TRIM(analysis)) > 0;
                """)
            except Exception as bfe:
                print(f"Warning: SQLite song_analyses backfill skipped ({bfe})")

def normalize_text(text: str) -> str:
    return text.strip().lower() if text else ""

def save_search(
    artist: str,
    song: str,
    lyrics: Optional[str] = None,
    source: Optional[str] = None,
    song_url: Optional[str] = None,
    track_tags: Optional[Union[str, List[Dict[str, Any]]]] = None,
    theaudiodb_data: Optional[Union[str, Dict[str, Any]]] = None,
    db_path: Optional[str] = None
) -> int:
    """
    Save or update a search result. If the artist and song already exist,
    updates lyrics/source/song_url/track_tags/theaudiodb_data (if provided) and updates timestamp while preserving previous analysis.
    Returns the record ID.
    """
    artist_clean = artist.strip()
    song_clean = song.strip()
    artist_norm = normalize_text(artist_clean)
    song_norm = normalize_text(song_clean)
    tags_json = json.dumps(track_tags) if isinstance(track_tags, (list, dict)) else track_tags
    audiodb_json = json.dumps(theaudiodb_data) if isinstance(theaudiodb_data, (list, dict)) else theaudiodb_data
    target = get_db_target(db_path)

    with get_connection(target) as conn:
        if is_postgres(target):
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute(
                "SELECT id, lyrics, source, song_url, track_tags, theaudiodb_data FROM searches WHERE artist_normalized = %s AND song_normalized = %s",
                (artist_norm, song_norm)
            )
            row = cursor.fetchone()

            if row:
                record_id = row['id']
                new_lyrics = lyrics if (lyrics and lyrics.strip()) else row['lyrics']
                new_source = source if source else row['source']
                new_url = song_url if song_url else row['song_url']
                new_tags = tags_json if tags_json is not None else row['track_tags']
                new_audiodb = audiodb_json if audiodb_json is not None else row['theaudiodb_data']
                cursor.execute("""
                    UPDATE searches
                    SET artist = %s,
                        song = %s,
                        lyrics = %s,
                        source = %s,
                        song_url = %s,
                        track_tags = %s,
                        theaudiodb_data = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (artist_clean, song_clean, new_lyrics, new_source, new_url, new_tags, new_audiodb, record_id))
                notify_db_mutation()
                return record_id
            else:
                cursor.execute("""
                    INSERT INTO searches (
                        artist, song, artist_normalized, song_normalized,
                        lyrics, source, song_url, track_tags, theaudiodb_data, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    RETURNING id;
                """, (artist_clean, song_clean, artist_norm, song_norm, lyrics, source, song_url, tags_json, audiodb_json))
                new_id = cursor.fetchone()['id']
                notify_db_mutation()
                return new_id
        else:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, lyrics, source, song_url, track_tags, theaudiodb_data FROM searches WHERE artist_normalized = ? AND song_normalized = ?",
                (artist_norm, song_norm)
            )
            row = cursor.fetchone()

            if row:
                record_id = row['id']
                new_lyrics = lyrics if (lyrics and lyrics.strip()) else row['lyrics']
                new_source = source if source else row['source']
                new_url = song_url if song_url else row['song_url']
                new_tags = tags_json if tags_json is not None else row['track_tags']
                new_audiodb = audiodb_json if audiodb_json is not None else row['theaudiodb_data']
                cursor.execute("""
                    UPDATE searches
                    SET artist = ?,
                        song = ?,
                        lyrics = ?,
                        source = ?,
                        song_url = ?,
                        track_tags = ?,
                        theaudiodb_data = ?,
                        updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now')
                    WHERE id = ?
                """, (artist_clean, song_clean, new_lyrics, new_source, new_url, new_tags, new_audiodb, record_id))
                notify_db_mutation()
                return record_id
            else:
                cursor.execute("""
                    INSERT INTO searches (
                        artist, song, artist_normalized, song_normalized,
                        lyrics, source, song_url, track_tags, theaudiodb_data, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%d %H:%M:%f', 'now'), strftime('%Y-%m-%d %H:%M:%f', 'now'))
                """, (artist_clean, song_clean, artist_norm, song_norm, lyrics, source, song_url, tags_json, audiodb_json))
                new_id = cursor.lastrowid
                notify_db_mutation()
                return new_id

def save_analysis(
    artist: str,
    song: str,
    analysis: str,
    model_name: Optional[str] = None,
    prompt_name: Optional[str] = None,
    prompt_text: Optional[str] = None,
    lyrics: Optional[str] = None,
    track_tags: Optional[Union[str, List[Dict[str, Any]]]] = None,
    theaudiodb_data: Optional[Union[str, Dict[str, Any]]] = None,
    db_path: Optional[str] = None
) -> None:
    """Save or update the Gemini analysis result for a given artist, song, and prompt.
    
    Persists the analysis into `song_analyses` keyed by (artist, song, prompt_name)
    so multiple prompt analyses are preserved across sessions, while also updating
    the primary `searches` record for immediate access and backward compatibility.
    """
    artist_clean = artist.strip()
    song_clean = song.strip()
    artist_norm = normalize_text(artist_clean)
    song_norm = normalize_text(song_clean)
    effective_prompt_name = (prompt_name or 'Default Analysis').strip()
    tags_json = json.dumps(track_tags) if isinstance(track_tags, (list, dict)) else track_tags
    audiodb_json = json.dumps(theaudiodb_data) if isinstance(theaudiodb_data, (list, dict)) else theaudiodb_data
    target = get_db_target(db_path)

    with get_connection(target) as conn:
        if is_postgres(target):
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            # 1. Upsert into song_analyses table
            try:
                cursor.execute("""
                    INSERT INTO song_analyses (
                        artist, song, artist_normalized, song_normalized,
                        prompt_name, prompt_text, analysis, model_name, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT (artist_normalized, song_normalized, prompt_name) DO UPDATE
                    SET analysis = EXCLUDED.analysis,
                        model_name = COALESCE(EXCLUDED.model_name, song_analyses.model_name),
                        prompt_text = COALESCE(EXCLUDED.prompt_text, song_analyses.prompt_text),
                        artist = EXCLUDED.artist,
                        song = EXCLUDED.song,
                        updated_at = CURRENT_TIMESTAMP;
                """, (artist_clean, song_clean, artist_norm, song_norm, effective_prompt_name, prompt_text, analysis, model_name))
            except Exception as sae:
                print(f"Warning: save_analysis Postgres song_analyses upsert error: {sae}")

            # 2. Update searches table
            cursor.execute(
                "SELECT id, lyrics, source, track_tags, theaudiodb_data FROM searches WHERE artist_normalized = %s AND song_normalized = %s",
                (artist_norm, song_norm)
            )
            row = cursor.fetchone()

            if row:
                new_lyrics = lyrics if (lyrics and lyrics.strip()) else row['lyrics']
                new_source = row['source'] or ('Manual' if new_lyrics else None)
                new_tags = tags_json if tags_json is not None else row['track_tags']
                new_audiodb = audiodb_json if audiodb_json is not None else row['theaudiodb_data']
                cursor.execute("""
                    UPDATE searches
                    SET artist = %s,
                        song = %s,
                        lyrics = %s,
                        source = %s,
                        analysis = %s,
                        model_name = %s,
                        prompt_name = %s,
                        track_tags = %s,
                        theaudiodb_data = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (artist_clean, song_clean, new_lyrics, new_source, analysis, model_name, effective_prompt_name, new_tags, new_audiodb, row['id']))
            else:
                new_source = 'Manual' if (lyrics and lyrics.strip()) else None
                cursor.execute("""
                    INSERT INTO searches (
                        artist, song, artist_normalized, song_normalized,
                        lyrics, source, analysis, model_name, prompt_name, track_tags, theaudiodb_data, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """, (artist_clean, song_clean, artist_norm, song_norm, lyrics, new_source, analysis, model_name, effective_prompt_name, tags_json, audiodb_json))
        else:
            cursor = conn.cursor()
            # 1. Upsert into song_analyses table
            try:
                cursor.execute("""
                    INSERT INTO song_analyses (
                        artist, song, artist_normalized, song_normalized,
                        prompt_name, prompt_text, analysis, model_name, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%d %H:%M:%f', 'now'), strftime('%Y-%m-%d %H:%M:%f', 'now'))
                    ON CONFLICT (artist_normalized, song_normalized, prompt_name) DO UPDATE
                    SET analysis = excluded.analysis,
                        model_name = COALESCE(excluded.model_name, song_analyses.model_name),
                        prompt_text = COALESCE(excluded.prompt_text, song_analyses.prompt_text),
                        artist = excluded.artist,
                        song = excluded.song,
                        updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now');
                """, (artist_clean, song_clean, artist_norm, song_norm, effective_prompt_name, prompt_text, analysis, model_name))
            except Exception as sae:
                print(f"Warning: save_analysis SQLite song_analyses upsert error: {sae}")

            # 2. Update searches table
            cursor.execute(
                "SELECT id, lyrics, source, track_tags, theaudiodb_data FROM searches WHERE artist_normalized = ? AND song_normalized = ?",
                (artist_norm, song_norm)
            )
            row = cursor.fetchone()

            if row:
                new_lyrics = lyrics if (lyrics and lyrics.strip()) else row['lyrics']
                new_source = row['source'] or ('Manual' if new_lyrics else None)
                new_tags = tags_json if tags_json is not None else row['track_tags']
                new_audiodb = audiodb_json if audiodb_json is not None else row['theaudiodb_data']
                cursor.execute("""
                    UPDATE searches
                    SET artist = ?,
                        song = ?,
                        lyrics = ?,
                        source = ?,
                        analysis = ?,
                        model_name = ?,
                        prompt_name = ?,
                        track_tags = ?,
                        theaudiodb_data = ?,
                        updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now')
                    WHERE id = ?
                """, (artist_clean, song_clean, new_lyrics, new_source, analysis, model_name, effective_prompt_name, new_tags, new_audiodb, row['id']))
            else:
                new_source = 'Manual' if (lyrics and lyrics.strip()) else None
                cursor.execute("""
                    INSERT INTO searches (
                        artist, song, artist_normalized, song_normalized,
                        lyrics, source, analysis, model_name, prompt_name, track_tags, theaudiodb_data, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%d %H:%M:%f', 'now'), strftime('%Y-%m-%d %H:%M:%f', 'now'))
                """, (artist_clean, song_clean, artist_norm, song_norm, lyrics, new_source, analysis, model_name, effective_prompt_name, tags_json, audiodb_json))
        notify_db_mutation()


def get_analysis(
    artist: str,
    song: str,
    prompt_name: Optional[str] = None,
    db_path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Retrieve saved analysis for a song, optionally matching a specific prompt_name.
    
    If prompt_name is provided, checks song_analyses table for an exact match.
    Falls back to searches table if not found in song_analyses.
    If prompt_name is None, returns the most recent analysis for this song.
    """
    artist_norm = normalize_text(artist)
    song_norm = normalize_text(song)
    target = get_db_target(db_path)

    with get_connection(target) as conn:
        ph = "%s" if is_postgres(target) else "?"
        if is_postgres(target):
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        else:
            cursor = conn.cursor()

        if prompt_name and prompt_name.strip():
            p_name = prompt_name.strip()
            # 1. Check song_analyses table
            try:
                cursor.execute(
                    f"SELECT * FROM song_analyses WHERE artist_normalized = {ph} AND song_normalized = {ph} AND prompt_name = {ph}",
                    (artist_norm, song_norm, p_name)
                )
                row = cursor.fetchone()
                if row:
                    res = _format_song_analysis_record(row)
                    if res and res.get('analysis') and str(res['analysis']).strip():
                        return res
            except Exception:
                pass

            # 2. Fall back to searches table if prompt_name matches
            cursor.execute(
                f"SELECT * FROM searches WHERE artist_normalized = {ph} AND song_normalized = {ph} AND (prompt_name = {ph} OR (prompt_name IS NULL AND {ph} = 'Default Analysis'))",
                (artist_norm, song_norm, p_name, p_name)
            )
            row = cursor.fetchone()
            if row:
                rec = _format_search_record(row)
                if rec and rec.get('analysis') and str(rec['analysis']).strip():
                    return {
                        'artist': rec['artist'],
                        'song': rec['song'],
                        'prompt_name': rec.get('prompt_name') or 'Default Analysis',
                        'prompt_text': None,
                        'analysis': rec['analysis'],
                        'model_name': rec.get('model_name'),
                        'created_at': rec.get('created_at'),
                        'updated_at': rec.get('updated_at')
                    }
            return None
        else:
            # Most recent analysis
            try:
                cursor.execute(
                    f"SELECT * FROM song_analyses WHERE artist_normalized = {ph} AND song_normalized = {ph} ORDER BY updated_at DESC LIMIT 1",
                    (artist_norm, song_norm)
                )
                row = cursor.fetchone()
                if row:
                    res = _format_song_analysis_record(row)
                    if res and res.get('analysis') and str(res['analysis']).strip():
                        return res
            except Exception:
                pass

            cursor.execute(
                f"SELECT * FROM searches WHERE artist_normalized = {ph} AND song_normalized = {ph}",
                (artist_norm, song_norm)
            )
            row = cursor.fetchone()
            if row:
                rec = _format_search_record(row)
                if rec and rec.get('analysis') and str(rec['analysis']).strip():
                    return {
                        'artist': rec['artist'],
                        'song': rec['song'],
                        'prompt_name': rec.get('prompt_name') or 'Default Analysis',
                        'prompt_text': None,
                        'analysis': rec['analysis'],
                        'model_name': rec.get('model_name'),
                        'created_at': rec.get('created_at'),
                        'updated_at': rec.get('updated_at')
                    }
            return None


def get_song_analyses(
    artist: str,
    song: str,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Retrieve all saved analyses for a song across all prompts."""
    artist_norm = normalize_text(artist)
    song_norm = normalize_text(song)
    target = get_db_target(db_path)
    analyses: List[Dict[str, Any]] = []
    seen_prompts: set = set()

    with get_connection(target) as conn:
        ph = "%s" if is_postgres(target) else "?"
        if is_postgres(target):
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        else:
            cursor = conn.cursor()

        try:
            cursor.execute(
                f"SELECT * FROM song_analyses WHERE artist_normalized = {ph} AND song_normalized = {ph} ORDER BY updated_at DESC",
                (artist_norm, song_norm)
            )
            rows = cursor.fetchall()
            for r in rows:
                item = _format_song_analysis_record(r)
                if item and item.get('analysis') and str(item['analysis']).strip():
                    p_name = item.get('prompt_name')
                    if p_name and p_name not in seen_prompts:
                        seen_prompts.add(p_name)
                        analyses.append(item)
        except Exception:
            pass

        # Also check searches table for any prompt not yet present
        try:
            cursor.execute(
                f"SELECT * FROM searches WHERE artist_normalized = {ph} AND song_normalized = {ph}",
                (artist_norm, song_norm)
            )
            row = cursor.fetchone()
            if row:
                rec = _format_search_record(row)
                if rec and rec.get('analysis') and str(rec['analysis']).strip():
                    p_name = rec.get('prompt_name') or 'Default Analysis'
                    if p_name not in seen_prompts:
                        seen_prompts.add(p_name)
                        analyses.append({
                            'artist': rec['artist'],
                            'song': rec['song'],
                            'prompt_name': p_name,
                            'prompt_text': None,
                            'analysis': rec['analysis'],
                            'model_name': rec.get('model_name'),
                            'created_at': rec.get('created_at'),
                            'updated_at': rec.get('updated_at')
                        })
        except Exception:
            pass

    return analyses


def save_track_tags(
    artist: str,
    song: str,
    track_tags: Union[str, List[Dict[str, Any]]],
    db_path: Optional[str] = None
) -> None:
    """Save or update track top tags for a given artist and song in searches table."""
    artist_norm = normalize_text(artist)
    song_norm = normalize_text(song)
    tags_json = json.dumps(track_tags) if isinstance(track_tags, (list, dict)) else track_tags
    target = get_db_target(db_path)
    with get_connection(target) as conn:
        cursor = conn.cursor()
        ph = "%s" if is_postgres(target) else "?"
        if is_postgres(target):
            cursor.execute(f"""
                UPDATE searches
                SET track_tags = {ph}, updated_at = CURRENT_TIMESTAMP
                WHERE artist_normalized = {ph} AND song_normalized = {ph}
            """, (tags_json, artist_norm, song_norm))
        else:
            cursor.execute(f"""
                UPDATE searches
                SET track_tags = {ph}, updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now')
                WHERE artist_normalized = {ph} AND song_normalized = {ph}
            """, (tags_json, artist_norm, song_norm))


def save_theaudiodb_data(
    artist: str,
    song: str,
    theaudiodb_data: Union[str, Dict[str, Any]],
    db_path: Optional[str] = None
) -> None:
    """Save or update TheAudioDB track metadata for a given artist and song in searches table."""
    artist_clean = artist.strip()
    song_clean = song.strip()
    artist_norm = normalize_text(artist_clean)
    song_norm = normalize_text(song_clean)
    audiodb_json = json.dumps(theaudiodb_data) if isinstance(theaudiodb_data, (list, dict)) else theaudiodb_data
    target = get_db_target(db_path)
    with get_connection(target) as conn:
        cursor = conn.cursor()
        ph = "%s" if is_postgres(target) else "?"
        if is_postgres(target):
            cursor.execute(f"""
                UPDATE searches
                SET theaudiodb_data = {ph}, updated_at = CURRENT_TIMESTAMP
                WHERE artist_normalized = {ph} AND song_normalized = {ph}
            """, (audiodb_json, artist_norm, song_norm))
            if cursor.rowcount == 0:
                cursor.execute("""
                    INSERT INTO searches (
                        artist, song, artist_normalized, song_normalized, theaudiodb_data, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT (artist_normalized, song_normalized) DO UPDATE
                    SET theaudiodb_data = EXCLUDED.theaudiodb_data, updated_at = CURRENT_TIMESTAMP
                """, (artist_clean, song_clean, artist_norm, song_norm, audiodb_json))
        else:
            cursor.execute(f"""
                UPDATE searches
                SET theaudiodb_data = {ph}, updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now')
                WHERE artist_normalized = {ph} AND song_normalized = {ph}
            """, (audiodb_json, artist_norm, song_norm))
            if cursor.rowcount == 0:
                cursor.execute("""
                    INSERT INTO searches (
                        artist, song, artist_normalized, song_normalized, theaudiodb_data, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, strftime('%Y-%m-%d %H:%M:%f', 'now'), strftime('%Y-%m-%d %H:%M:%f', 'now'))
                    ON CONFLICT (artist_normalized, song_normalized) DO UPDATE
                    SET theaudiodb_data = excluded.theaudiodb_data, updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now')
                """, (artist_clean, song_clean, artist_norm, song_norm, audiodb_json))


def get_theaudiodb_data(
    artist: str,
    song: str,
    db_path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Retrieve TheAudioDB track metadata for a given artist and song."""
    rec = get_search(artist, song, db_path=db_path)
    if rec and rec.get("theaudiodb_data"):
        return rec["theaudiodb_data"]
    return None


def save_artist_metadata(
    artist: str,
    tags: Optional[Union[str, List[Dict[str, Any]]]] = None,
    top_tracks: Optional[Union[str, List[Dict[str, Any]]]] = None,
    setlistfm_data: Optional[Union[str, Dict[str, Any]]] = None,
    theaudiodb_data: Optional[Union[str, Dict[str, Any]]] = None,
    bio: Optional[str] = None,
    similar_artists: Optional[Union[str, List[Any]]] = None,
    listeners: Optional[int] = None,
    playcount: Optional[int] = None,
    image_url: Optional[str] = None,
    banner_url: Optional[str] = None,
    formed_year: Optional[int] = None,
    country: Optional[str] = None,
    spotify_data: Optional[Union[str, Dict[str, Any]]] = None,
    db_path: Optional[str] = None
) -> int:
    """
    Save or update comprehensive artist metadata (Last.fm, Setlist.fm, TheAudioDB, Spotify).
    Returns the record ID.
    """
    artist_clean = artist.strip()
    artist_norm = normalize_text(artist_clean)
    tags_json = json.dumps(tags) if isinstance(tags, (list, dict)) else tags
    tracks_json = json.dumps(top_tracks) if isinstance(top_tracks, (list, dict)) else top_tracks
    setlist_json = json.dumps(setlistfm_data) if isinstance(setlistfm_data, (list, dict)) else setlistfm_data
    audiodb_json = json.dumps(theaudiodb_data) if isinstance(theaudiodb_data, (list, dict)) else theaudiodb_data
    similar_json = json.dumps(similar_artists) if isinstance(similar_artists, (list, dict)) else similar_artists
    spotify_json = json.dumps(spotify_data) if isinstance(spotify_data, (list, dict)) else spotify_data

    target = get_db_target(db_path)

    with get_connection(target) as conn:
        if is_postgres(target):
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute(
                "SELECT * FROM artist_metadata WHERE artist_normalized = %s",
                (artist_norm,)
            )
            row = cursor.fetchone()
            if row:
                record_id = row['id']
                new_tags = tags_json if tags_json is not None else row.get('tags')
                new_tracks = tracks_json if tracks_json is not None else row.get('top_tracks')
                new_setlist = setlist_json if setlist_json is not None else row.get('setlistfm_data')
                new_audiodb = audiodb_json if audiodb_json is not None else row.get('theaudiodb_data')
                new_bio = bio if bio is not None else row.get('bio')
                new_similar = similar_json if similar_json is not None else row.get('similar_artists')
                new_listeners = listeners if listeners is not None else row.get('listeners')
                new_playcount = playcount if playcount is not None else row.get('playcount')
                new_image = image_url if image_url is not None else row.get('image_url')
                new_banner = banner_url if banner_url is not None else row.get('banner_url')
                new_formed = formed_year if formed_year is not None else row.get('formed_year')
                new_country = country if country is not None else row.get('country')
                new_spotify = spotify_json if spotify_json is not None else row.get('spotify_data')

                cursor.execute("""
                    UPDATE artist_metadata
                    SET artist = %s,
                        tags = %s,
                        top_tracks = %s,
                        setlistfm_data = %s,
                        theaudiodb_data = %s,
                        bio = %s,
                        similar_artists = %s,
                        listeners = %s,
                        playcount = %s,
                        image_url = %s,
                        banner_url = %s,
                        formed_year = %s,
                        country = %s,
                        spotify_data = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (
                    artist_clean, new_tags, new_tracks, new_setlist, new_audiodb,
                    new_bio, new_similar, new_listeners, new_playcount,
                    new_image, new_banner, new_formed, new_country, new_spotify, record_id
                ))
                return record_id
            else:
                cursor.execute("""
                    INSERT INTO artist_metadata (
                        artist, artist_normalized, tags, top_tracks,
                        setlistfm_data, theaudiodb_data, bio, similar_artists,
                        listeners, playcount, image_url, banner_url, formed_year, country, spotify_data,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    RETURNING id;
                """, (
                    artist_clean, artist_norm, tags_json, tracks_json,
                    setlist_json, audiodb_json, bio, similar_json,
                    listeners, playcount, image_url, banner_url, formed_year, country, spotify_json
                ))
                return cursor.fetchone()['id']
        else:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM artist_metadata WHERE artist_normalized = ?",
                (artist_norm,)
            )
            row = cursor.fetchone()
            if row:
                row_dict = dict(row)
                record_id = row_dict['id']
                new_tags = tags_json if tags_json is not None else row_dict.get('tags')
                new_tracks = tracks_json if tracks_json is not None else row_dict.get('top_tracks')
                new_setlist = setlist_json if setlist_json is not None else row_dict.get('setlistfm_data')
                new_audiodb = audiodb_json if audiodb_json is not None else row_dict.get('theaudiodb_data')
                new_bio = bio if bio is not None else row_dict.get('bio')
                new_similar = similar_json if similar_json is not None else row_dict.get('similar_artists')
                new_listeners = listeners if listeners is not None else row_dict.get('listeners')
                new_playcount = playcount if playcount is not None else row_dict.get('playcount')
                new_image = image_url if image_url is not None else row_dict.get('image_url')
                new_banner = banner_url if banner_url is not None else row_dict.get('banner_url')
                new_formed = formed_year if formed_year is not None else row_dict.get('formed_year')
                new_country = country if country is not None else row_dict.get('country')
                new_spotify = spotify_json if spotify_json is not None else row_dict.get('spotify_data')

                cursor.execute("""
                    UPDATE artist_metadata
                    SET artist = ?,
                        tags = ?,
                        top_tracks = ?,
                        setlistfm_data = ?,
                        theaudiodb_data = ?,
                        bio = ?,
                        similar_artists = ?,
                        listeners = ?,
                        playcount = ?,
                        image_url = ?,
                        banner_url = ?,
                        formed_year = ?,
                        country = ?,
                        spotify_data = ?,
                        updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now')
                    WHERE id = ?
                """, (
                    artist_clean, new_tags, new_tracks, new_setlist, new_audiodb,
                    new_bio, new_similar, new_listeners, new_playcount,
                    new_image, new_banner, new_formed, new_country, new_spotify, record_id
                ))
                return record_id
            else:
                cursor.execute("""
                    INSERT INTO artist_metadata (
                        artist, artist_normalized, tags, top_tracks,
                        setlistfm_data, theaudiodb_data, bio, similar_artists,
                        listeners, playcount, image_url, banner_url, formed_year, country, spotify_data,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%d %H:%M:%f', 'now'), strftime('%Y-%m-%d %H:%M:%f', 'now'))
                """, (
                    artist_clean, artist_norm, tags_json, tracks_json,
                    setlist_json, audiodb_json, bio, similar_json,
                    listeners, playcount, image_url, banner_url, formed_year, country, spotify_json
                ))
                return cursor.lastrowid


def get_artist_metadata(artist: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieve artist metadata (tags & top tracks) by artist name."""
    artist_norm = normalize_text(artist)
    target = get_db_target(db_path)
    with get_connection(target) as conn:
        if is_postgres(target):
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            ph = "%s"
        else:
            cursor = conn.cursor()
            ph = "?"
        cursor.execute(f"SELECT * FROM artist_metadata WHERE artist_normalized = {ph}", (artist_norm,))
        row = cursor.fetchone()
        return _format_artist_record(row) if row else None


def get_cached_average_setlist(artist: str, year: Union[str, int], db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieve cached Setlist.fm average setlist for an artist and year."""
    if not artist or not str(year).strip():
        return None
    clean_artist = artist.strip()
    artist_meta = get_artist_metadata(clean_artist, db_path=db_path)
    if not artist_meta or not artist_meta.get('setlistfm_data'):
        base_artist = re.sub(r'[\s\-_]+(?:617|\d{3,4}|\([^\)]+\))$', '', clean_artist, flags=re.IGNORECASE).strip()
        if base_artist and base_artist.lower() != clean_artist.lower():
            artist_meta = get_artist_metadata(base_artist, db_path=db_path)
    if not artist_meta or not artist_meta.get('setlistfm_data'):
        return None
    s_data = artist_meta['setlistfm_data']
    if isinstance(s_data, str):
        try:
            s_data = json.loads(s_data)
        except Exception:
            s_data = None
    if isinstance(s_data, dict):
        avg_dict = s_data.get('average_setlists', {})
        if isinstance(avg_dict, dict):
            return avg_dict.get(str(year).strip())
    return None


def save_cached_average_setlist(artist: str, year: Union[str, int], setlist_data: Dict[str, Any], db_path: Optional[str] = None) -> bool:
    """Cache Setlist.fm average setlist data for an artist and year."""
    if not artist or not str(year).strip() or not setlist_data:
        return False
    clean_artist = artist.strip()
    str_year = str(year).strip()
    artist_meta = get_artist_metadata(clean_artist, db_path=db_path)
    s_data = {}
    if artist_meta and isinstance(artist_meta.get('setlistfm_data'), dict):
        s_data = dict(artist_meta['setlistfm_data'])
    if 'average_setlists' not in s_data or not isinstance(s_data['average_setlists'], dict):
        s_data['average_setlists'] = {}
    s_data['average_setlists'][str_year] = setlist_data
    save_artist_metadata(clean_artist, setlistfm_data=s_data, db_path=db_path)
    return True


def touch_search(artist: str, song: str, db_path: Optional[str] = None) -> None:
    """Update updated_at timestamp for an existing search to bring it to the top of recent history."""
    artist_norm = normalize_text(artist)
    song_norm = normalize_text(song)
    target = get_db_target(db_path)
    with get_connection(target) as conn:
        cursor = conn.cursor()
        if is_postgres(target):
            cursor.execute("""
                UPDATE searches
                SET updated_at = CURRENT_TIMESTAMP
                WHERE artist_normalized = %s AND song_normalized = %s
            """, (artist_norm, song_norm))
        else:
            cursor.execute("""
                UPDATE searches
                SET updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now')
                WHERE artist_normalized = ? AND song_normalized = ?
            """, (artist_norm, song_norm))


def get_distinct_bands(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Returns list of distinct bands/artists ordered by most recent search activity.
    Each item contains 'artist', 'song_count', and 'last_searched'.
    """
    target = get_db_target(db_path)
    with get_connection(target) as conn:
        if is_postgres(target):
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        else:
            cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                s.artist,
                sub.song_count,
                sub.last_searched
            FROM (
                SELECT 
                    artist_normalized,
                    COUNT(*) as song_count,
                    MAX(updated_at) as last_searched,
                    MAX(id) as max_id
                FROM searches
                GROUP BY artist_normalized
            ) sub
            JOIN searches s ON s.id = sub.max_id
            ORDER BY sub.last_searched DESC, sub.max_id DESC
        """)
        rows = cursor.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if 'last_searched' in d and d['last_searched'] is not None:
                d['last_searched'] = _format_datetime(d['last_searched'])
            result.append(d)
        return result

def get_songs_by_band(artist: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns all searched songs for a specific band/artist ordered by most recent activity."""
    raw_art = artist.strip()
    artist_norm = normalize_text(raw_art)
    base_art = re.sub(r'[\s\-_]+(?:617|\d{3,4}|\([^\)]+\))$', '', raw_art, flags=re.IGNORECASE).strip()
    base_norm = normalize_text(base_art)

    target = get_db_target(db_path)
    with get_connection(target) as conn:
        if is_postgres(target):
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            ph = "%s"
        else:
            cursor = conn.cursor()
            ph = "?"

        where_parts = [f"artist_normalized = {ph}"]
        params = [artist_norm]
        if base_norm and base_norm != artist_norm:
            where_parts.append(f"artist_normalized = {ph}")
            params.append(base_norm)
        search_base = base_norm if base_norm else artist_norm
        where_parts.append(f"artist_normalized LIKE {ph}")
        params.append(search_base + " %")

        cursor.execute(f"""
            SELECT 
                id,
                artist,
                song,
                source,
                song_url,
                track_tags,
                theaudiodb_data,
                (lyrics IS NOT NULL AND LENGTH(TRIM(lyrics)) > 0) as has_lyrics,
                (analysis IS NOT NULL AND LENGTH(TRIM(analysis)) > 0) as has_analysis,
                model_name,
                prompt_name,
                created_at,
                updated_at
            FROM searches
            WHERE ({' OR '.join(where_parts)})
            ORDER BY updated_at DESC, id DESC
        """, tuple(params))
        rows = cursor.fetchall()
        return [_format_search_record(r) for r in rows]

def get_search(artist: str, song: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieve a search record by artist and song title."""
    artist_norm = normalize_text(artist)
    song_norm = normalize_text(song)
    target = get_db_target(db_path)
    with get_connection(target) as conn:
        if is_postgres(target):
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            ph = "%s"
        else:
            cursor = conn.cursor()
            ph = "?"
        cursor.execute(
            f"SELECT * FROM searches WHERE artist_normalized = {ph} AND song_normalized = {ph}",
            (artist_norm, song_norm)
        )
        row = cursor.fetchone()
        return _format_search_record(row) if row else None

def get_search_by_id(search_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieve a search record by ID."""
    target = get_db_target(db_path)
    with get_connection(target) as conn:
        if is_postgres(target):
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            ph = "%s"
        else:
            cursor = conn.cursor()
            ph = "?"
        cursor.execute(f"SELECT * FROM searches WHERE id = {ph}", (search_id,))
        row = cursor.fetchone()
        return _format_search_record(row) if row else None

def get_recent_searches(limit: int = 100, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve recent searches across all bands for history viewing."""
    target = get_db_target(db_path)
    with get_connection(target) as conn:
        if is_postgres(target):
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            ph = "%s"
        else:
            cursor = conn.cursor()
            ph = "?"
        cursor.execute(f"""
            SELECT 
                id,
                artist,
                song,
                source,
                song_url,
                track_tags,
                theaudiodb_data,
                (lyrics IS NOT NULL AND LENGTH(TRIM(lyrics)) > 0) as has_lyrics,
                (analysis IS NOT NULL AND LENGTH(TRIM(analysis)) > 0) as has_analysis,
                model_name,
                prompt_name,
                created_at,
                updated_at
            FROM searches
            ORDER BY updated_at DESC, id DESC
            LIMIT {ph}
        """, (limit,))
        rows = cursor.fetchall()
        return [_format_search_record(r) for r in rows]

def delete_search(search_id: int, db_path: Optional[str] = None) -> bool:
    """Delete a search record by ID and remove associated prompt analyses."""
    target = get_db_target(db_path)
    with get_connection(target) as conn:
        cursor = conn.cursor()
        ph = "%s" if is_postgres(target) else "?"
        try:
            cursor.execute(f"SELECT artist_normalized, song_normalized FROM searches WHERE id = {ph}", (search_id,))
            row = cursor.fetchone()
            if row:
                if is_postgres(target):
                    a_norm, s_norm = row.get('artist_normalized'), row.get('song_normalized')
                else:
                    a_norm, s_norm = row['artist_normalized'], row['song_normalized']
                if a_norm and s_norm:
                    cursor.execute(f"DELETE FROM song_analyses WHERE artist_normalized = {ph} AND song_normalized = {ph}", (a_norm, s_norm))
        except Exception as de:
            print(f"Warning: delete_search song_analyses cleanup error: {de}")

        cursor.execute(f"DELETE FROM searches WHERE id = {ph}", (search_id,))
        success = cursor.rowcount > 0
        if success:
            notify_db_mutation()
        return success


def global_search(query: str, limit: int = 15, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Search artists, songs, and tags across the database."""
    clean_q = query.strip()
    if not clean_q:
        return {"artists": [], "songs": []}

    target = get_db_target(db_path)
    like_pat = f"%{clean_q}%"
    with get_connection(target) as conn:
        if is_postgres(target):
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            ph = "%s"
            ilike = "ILIKE"
        else:
            cursor = conn.cursor()
            ph = "?"
            ilike = "LIKE"

        # 1. Matching songs
        cursor.execute(f"""
            SELECT id, artist, song, track_tags,
                   (analysis IS NOT NULL AND LENGTH(TRIM(analysis)) > 0) as has_analysis
            FROM searches
            WHERE artist {ilike} {ph} OR song {ilike} {ph} OR track_tags {ilike} {ph}
            ORDER BY has_analysis DESC, updated_at DESC
            LIMIT {ph}
        """, (like_pat, like_pat, like_pat, limit))
        song_rows = [_format_search_record(r) for r in cursor.fetchall()]

        # 2. Distinct matching artists
        cursor.execute(f"""
            SELECT artist, COUNT(id) as song_count
            FROM searches
            WHERE artist {ilike} {ph}
            GROUP BY artist
            ORDER BY song_count DESC
            LIMIT {ph}
        """, (like_pat, 8))
        artist_rows = [{"artist": r["artist"], "song_count": r["song_count"]} for r in cursor.fetchall()]

        return {
            "query": clean_q,
            "artists": artist_rows,
            "songs": song_rows,
        }


# ==========================================
# User & Session Management
# ==========================================

def get_user(email: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieve user record by email."""
    clean_email = email.lower().strip()
    target = get_db_target(db_path)
    with get_connection(target) as conn:
        if is_postgres(target):
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute("SELECT * FROM users WHERE email = %s", (clean_email,))
        else:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE email = ?", (clean_email,))
        row = cursor.fetchone()
        return _format_user_record(row) if row else None


def get_all_users(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve all users ordered by admin status and creation date."""
    target = get_db_target(db_path)
    with get_connection(target) as conn:
        if is_postgres(target):
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        else:
            cursor = conn.cursor()
        cursor.execute("SELECT * FROM users ORDER BY is_admin DESC, created_at ASC")
        rows = cursor.fetchall()
        return [_format_user_record(r) for r in rows]


def upsert_user(
    email: str,
    name: Optional[str] = None,
    picture: Optional[str] = None,
    is_admin: bool = False,
    is_active: bool = True,
    db_path: Optional[str] = None
) -> int:
    """
    Insert a new user or update an existing user's role and status.
    If the email matches PRIMARY_ADMIN_EMAIL, is_admin and is_active are permanently enforced.
    Returns the user ID.
    """
    clean_email = email.lower().strip()
    if clean_email == PRIMARY_ADMIN_EMAIL.lower().strip():
        is_admin = True
        is_active = True

    target = get_db_target(db_path)
    with get_connection(target) as conn:
        if is_postgres(target):
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute("SELECT id, name, picture FROM users WHERE email = %s", (clean_email,))
            row = cursor.fetchone()
            if row:
                user_id = row['id']
                final_name = name if (name and name.strip()) else row['name']
                final_picture = picture if (picture and picture.strip()) else row['picture']
                cursor.execute("""
                    UPDATE users
                    SET name = %s, picture = %s, is_admin = %s, is_active = %s
                    WHERE id = %s
                """, (final_name, final_picture, is_admin, is_active, user_id))
                return user_id
            else:
                cursor.execute("""
                    INSERT INTO users (email, name, picture, is_admin, is_active, created_at)
                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    RETURNING id;
                """, (clean_email, name, picture, is_admin, is_active))
                return cursor.fetchone()['id']
        else:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, picture FROM users WHERE email = ?", (clean_email,))
            row = cursor.fetchone()
            if row:
                user_id = row['id']
                final_name = name if (name and name.strip()) else row['name']
                final_picture = picture if (picture and picture.strip()) else row['picture']
                cursor.execute("""
                    UPDATE users
                    SET name = ?, picture = ?, is_admin = ?, is_active = ?
                    WHERE id = ?
                """, (final_name, final_picture, 1 if is_admin else 0, 1 if is_active else 0, user_id))
                return user_id
            else:
                cursor.execute("""
                    INSERT INTO users (email, name, picture, is_admin, is_active, created_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (clean_email, name, picture, 1 if is_admin else 0, 1 if is_active else 0))
                return cursor.lastrowid


def update_user_last_login(
    email: str,
    name: Optional[str] = None,
    picture: Optional[str] = None,
    db_path: Optional[str] = None
) -> None:
    """Update user last_login timestamp and optionally name and picture."""
    clean_email = email.lower().strip()
    target = get_db_target(db_path)
    with get_connection(target) as conn:
        cursor = conn.cursor()
        if is_postgres(target):
            cursor.execute("""
                UPDATE users
                SET last_login_at = CURRENT_TIMESTAMP,
                    name = CASE WHEN %s != '' THEN %s ELSE name END,
                    picture = CASE WHEN %s != '' THEN %s ELSE picture END
                WHERE email = %s
            """, (name or '', name or '', picture or '', picture or '', clean_email))
        else:
            cursor.execute("""
                UPDATE users
                SET last_login_at = CURRENT_TIMESTAMP,
                    name = CASE WHEN ? != '' THEN ? ELSE name END,
                    picture = CASE WHEN ? != '' THEN ? ELSE picture END
                WHERE email = ?
            """, (name or '', name or '', picture or '', picture or '', clean_email))


def set_user_active_status(email: str, is_active: bool, db_path: Optional[str] = None) -> bool:
    """Toggle user active status. Primary superadmin cannot be deactivated."""
    clean_email = email.lower().strip()
    if clean_email == PRIMARY_ADMIN_EMAIL.lower().strip() and not is_active:
        return False
    target = get_db_target(db_path)
    with get_connection(target) as conn:
        cursor = conn.cursor()
        if is_postgres(target):
            cursor.execute("UPDATE users SET is_active = %s WHERE email = %s", (is_active, clean_email))
            updated = cursor.rowcount > 0
            if not is_active:
                cursor.execute("DELETE FROM sessions WHERE LOWER(email) = %s", (clean_email,))
        else:
            cursor.execute("UPDATE users SET is_active = ? WHERE email = ?", (1 if is_active else 0, clean_email))
            updated = cursor.rowcount > 0
            if not is_active:
                cursor.execute("DELETE FROM sessions WHERE LOWER(email) = ?", (clean_email,))
        return updated


def set_user_admin_role(email: str, is_admin: bool, db_path: Optional[str] = None) -> bool:
    """Set user admin role. Primary superadmin cannot be demoted."""
    clean_email = email.lower().strip()
    if clean_email == PRIMARY_ADMIN_EMAIL.lower().strip() and not is_admin:
        return False
    target = get_db_target(db_path)
    with get_connection(target) as conn:
        cursor = conn.cursor()
        if is_postgres(target):
            cursor.execute("UPDATE users SET is_admin = %s WHERE email = %s", (is_admin, clean_email))
        else:
            cursor.execute("UPDATE users SET is_admin = ? WHERE email = ?", (1 if is_admin else 0, clean_email))
        return cursor.rowcount > 0


def delete_user(email: str, db_path: Optional[str] = None) -> bool:
    """Delete a user and their sessions. Primary superadmin cannot be deleted."""
    clean_email = email.lower().strip()
    if clean_email == PRIMARY_ADMIN_EMAIL.lower().strip():
        return False
    target = get_db_target(db_path)
    with get_connection(target) as conn:
        cursor = conn.cursor()
        ph = "%s" if is_postgres(target) else "?"
        cursor.execute(f"DELETE FROM sessions WHERE LOWER(email) = {ph}", (clean_email,))
        cursor.execute(f"DELETE FROM users WHERE email = {ph}", (clean_email,))
        return cursor.rowcount > 0



def create_session(email: str, duration_days: int = 30, db_path: Optional[str] = None) -> str:
    """Create a persistent session token for an authorized user."""
    clean_email = email.lower().strip()
    session_id = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(days=duration_days)).strftime('%Y-%m-%d %H:%M:%S')
    created_at = now.strftime('%Y-%m-%d %H:%M:%S')

    target = get_db_target(db_path)
    with get_connection(target) as conn:
        cursor = conn.cursor()
        if is_postgres(target):
            cursor.execute("""
                INSERT INTO sessions (session_id, email, created_at, expires_at)
                VALUES (%s, %s, %s, %s)
            """, (session_id, clean_email, created_at, expires_at))
        else:
            cursor.execute("""
                INSERT INTO sessions (session_id, email, created_at, expires_at)
                VALUES (?, ?, ?, ?)
            """, (session_id, clean_email, created_at, expires_at))
    return session_id


def get_session_user(session_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Look up active user for an unexpired session."""
    if not session_id or not str(session_id).strip():
        return None
    target = get_db_target(db_path)
    with get_connection(target) as conn:
        if is_postgres(target):
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute("""
                SELECT u.*
                FROM sessions s
                JOIN users u ON LOWER(s.email) = LOWER(u.email)
                WHERE s.session_id = %s
                  AND s.expires_at > CURRENT_TIMESTAMP
                  AND u.is_active = TRUE
            """, (session_id,))
        else:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT u.*
                FROM sessions s
                JOIN users u ON LOWER(s.email) = LOWER(u.email)
                WHERE s.session_id = ?
                  AND s.expires_at > CURRENT_TIMESTAMP
                  AND u.is_active = 1
            """, (session_id,))
        row = cursor.fetchone()
        return _format_user_record(row) if row else None


def delete_session(session_id: str, db_path: Optional[str] = None) -> None:
    """Delete a session by token."""
    if not session_id:
        return
    target = get_db_target(db_path)
    with get_connection(target) as conn:
        cursor = conn.cursor()
        ph = "%s" if is_postgres(target) else "?"
        cursor.execute(f"DELETE FROM sessions WHERE session_id = {ph}", (session_id,))


def delete_user_sessions(email: str, db_path: Optional[str] = None) -> None:
    """Delete all sessions for a user email."""
    clean_email = email.lower().strip()
    target = get_db_target(db_path)
    with get_connection(target) as conn:
        cursor = conn.cursor()
        ph = "%s" if is_postgres(target) else "?"
        cursor.execute(f"DELETE FROM sessions WHERE LOWER(email) = {ph}", (clean_email,))


# ---------------------------------------------------------
# Spotify Integration Persistence
# ---------------------------------------------------------

def save_spotify_token(
    user_email: str,
    access_token: str,
    refresh_token: Optional[str] = None,
    expires_at: Optional[Union[str, datetime]] = None,
    spotify_user_id: Optional[str] = None,
    spotify_display_name: Optional[str] = None,
    spotify_profile_url: Optional[str] = None,
    spotify_image_url: Optional[str] = None,
    db_path: Optional[str] = None
) -> None:
    """Save or update Spotify OAuth tokens for a user, retaining refresh_token if omitted."""
    clean_email = user_email.lower().strip()
    if not clean_email or not access_token:
        return

    exp_val = expires_at
    if isinstance(exp_val, (datetime, date)):
        exp_val = exp_val.strftime('%Y-%m-%d %H:%M:%S')

    target = get_db_target(db_path)
    with get_connection(target) as conn:
        cursor = conn.cursor()
        if is_postgres(target):
            cursor.execute("""
                INSERT INTO spotify_tokens (
                    user_email, access_token, refresh_token, expires_at,
                    spotify_user_id, spotify_display_name, spotify_profile_url, spotify_image_url,
                    created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (user_email) DO UPDATE SET
                    access_token = EXCLUDED.access_token,
                    refresh_token = COALESCE(EXCLUDED.refresh_token, spotify_tokens.refresh_token),
                    expires_at = EXCLUDED.expires_at,
                    spotify_user_id = COALESCE(EXCLUDED.spotify_user_id, spotify_tokens.spotify_user_id),
                    spotify_display_name = COALESCE(EXCLUDED.spotify_display_name, spotify_tokens.spotify_display_name),
                    spotify_profile_url = COALESCE(EXCLUDED.spotify_profile_url, spotify_tokens.spotify_profile_url),
                    spotify_image_url = COALESCE(EXCLUDED.spotify_image_url, spotify_tokens.spotify_image_url),
                    updated_at = CURRENT_TIMESTAMP;
            """, (
                clean_email, access_token, refresh_token, exp_val,
                spotify_user_id, spotify_display_name, spotify_profile_url, spotify_image_url
            ))
        else:
            cursor.execute("""
                INSERT INTO spotify_tokens (
                    user_email, access_token, refresh_token, expires_at,
                    spotify_user_id, spotify_display_name, spotify_profile_url, spotify_image_url,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (user_email) DO UPDATE SET
                    access_token = excluded.access_token,
                    refresh_token = COALESCE(excluded.refresh_token, spotify_tokens.refresh_token),
                    expires_at = excluded.expires_at,
                    spotify_user_id = COALESCE(excluded.spotify_user_id, spotify_tokens.spotify_user_id),
                    spotify_display_name = COALESCE(excluded.spotify_display_name, spotify_tokens.spotify_display_name),
                    spotify_profile_url = COALESCE(excluded.spotify_profile_url, spotify_tokens.spotify_profile_url),
                    spotify_image_url = COALESCE(excluded.spotify_image_url, spotify_tokens.spotify_image_url),
                    updated_at = CURRENT_TIMESTAMP;
            """, (
                clean_email, access_token, refresh_token, exp_val,
                spotify_user_id, spotify_display_name, spotify_profile_url, spotify_image_url
            ))


def get_spotify_token(user_email: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieve Spotify tokens and profile metadata for a user email."""
    clean_email = user_email.lower().strip()
    if not clean_email:
        return None

    target = get_db_target(db_path)
    with get_connection(target) as conn:
        if is_postgres(target):
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute("""
                SELECT * FROM spotify_tokens 
                WHERE LOWER(user_email) = %s 
                LIMIT 1
            """, (clean_email,))
        else:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM spotify_tokens 
                WHERE LOWER(user_email) = ? 
                LIMIT 1
            """, (clean_email,))
        row = cursor.fetchone()
        return _format_spotify_token_record(row) if row else None


def delete_spotify_token(user_email: str, db_path: Optional[str] = None) -> None:
    """Delete Spotify token for user (disconnect Spotify)."""
    clean_email = user_email.lower().strip()
    if not clean_email:
        return

    target = get_db_target(db_path)
    with get_connection(target) as conn:
        cursor = conn.cursor()
        ph = "%s" if is_postgres(target) else "?"
        cursor.execute(f"DELETE FROM spotify_tokens WHERE LOWER(user_email) = {ph}", (clean_email,))


def save_spotify_history_items(
    user_email: str,
    items: List[Dict[str, Any]],
    db_path: Optional[str] = None
) -> int:
    """Batch save recently played tracks to spotify_history (ignoring duplicates)."""
    clean_email = user_email.lower().strip()
    if not clean_email or not items:
        return 0

    target = get_db_target(db_path)
    rows_to_insert = []
    for item in items:
        track_id = item.get("track_id") or item.get("spotify_track_id") or ""
        if not track_id and item.get("spotify_track_uri"):
            uri = str(item["spotify_track_uri"])
            if uri.startswith("spotify:track:"):
                track_id = uri.split(":")[-1].strip()

        played_at = item.get("played_at") or item.get("ts") or ""
        track_name = item.get("name") or item.get("track_name") or item.get("master_metadata_track_name") or "Unknown Track"
        artist_name = item.get("artist") or item.get("artist_name") or item.get("master_metadata_album_artist_name") or "Unknown Artist"
        album_name = item.get("album") or item.get("album_name") or item.get("master_metadata_album_album_name") or ""
        album_image = item.get("album_image") or item.get("album_image_url") or ""
        duration_ms = item.get("duration_ms") if item.get("duration_ms") is not None else item.get("ms_played", 0)
        popularity = item.get("popularity") or 0
        preview_url = item.get("preview_url") or ""
        spotify_url = item.get("spotify_url") or (f"https://open.spotify.com/track/{track_id}" if track_id else "")
        release_date = item.get("release_date") or ""

        if not track_id or not played_at:
            continue

        rows_to_insert.append((
            clean_email, track_id, played_at, track_name, artist_name,
            album_name, album_image, duration_ms, popularity,
            preview_url, spotify_url, release_date
        ))

    if not rows_to_insert:
        return 0

    inserted = 0
    with get_connection(target) as conn:
        cursor = conn.cursor()
        if is_postgres(target):
            import psycopg2.extras
            psycopg2.extras.execute_values(
                cursor,
                """
                INSERT INTO spotify_history (
                    user_email, spotify_track_id, played_at, track_name, artist_name,
                    album_name, album_image_url, duration_ms, popularity,
                    preview_url, spotify_url, release_date, created_at
                )
                VALUES %s
                ON CONFLICT (user_email, spotify_track_id, played_at) DO NOTHING;
                """,
                rows_to_insert,
                template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)",
                page_size=min(len(rows_to_insert), 2000)
            )
            inserted = max(0, cursor.rowcount)
        else:
            cursor.executemany(
                """
                INSERT OR IGNORE INTO spotify_history (
                    user_email, spotify_track_id, played_at, track_name, artist_name,
                    album_name, album_image_url, duration_ms, popularity,
                    preview_url, spotify_url, release_date, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP);
                """,
                rows_to_insert
            )
            inserted = max(0, cursor.rowcount)

    if inserted > 0:
        notify_db_mutation()

    return inserted


def get_spotify_history(
    user_email: str,
    limit: int = 50,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Retrieve saved recently played tracks for user, ordered by played_at DESC."""
    clean_email = user_email.lower().strip()
    if not clean_email:
        return []

    target = get_db_target(db_path)
    limit = max(1, min(limit, 500))
    with get_connection(target) as conn:
        if is_postgres(target):
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute("""
                SELECT * FROM spotify_history
                WHERE LOWER(user_email) = %s
                ORDER BY played_at DESC
                LIMIT %s
            """, (clean_email, limit))
        else:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM spotify_history
                WHERE LOWER(user_email) = ?
                ORDER BY played_at DESC
                LIMIT ?
            """, (clean_email, limit))
        rows = cursor.fetchall()
        return [_format_spotify_history_record(r) for r in rows if r]


def get_spotify_history_count(user_email: str, db_path: Optional[str] = None) -> int:
    """Return total count of historical tracks saved for a user."""
    clean_email = user_email.lower().strip()
    if not clean_email:
        return 0

    target = get_db_target(db_path)
    with get_connection(target) as conn:
        cursor = conn.cursor()
        ph = "%s" if is_postgres(target) else "?"
        cursor.execute(f"SELECT COUNT(*) FROM spotify_history WHERE LOWER(user_email) = {ph}", (clean_email,))
        row = cursor.fetchone()
        return row[0] if row else 0


def clear_spotify_history(user_email: str, db_path: Optional[str] = None) -> None:
    """Clear saved listening history for user."""
    clean_email = user_email.lower().strip()
    if not clean_email:
        return

    target = get_db_target(db_path)
    with get_connection(target) as conn:
        cursor = conn.cursor()
        ph = "%s" if is_postgres(target) else "?"
        cursor.execute(f"DELETE FROM spotify_history WHERE LOWER(user_email) = {ph}", (clean_email,))


_spotify_stats_cache: Dict[str, Tuple[float, int, Dict[str, Any]]] = {}

def get_spotify_lifetime_stats(user_email: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Retrieve all-time lifetime listening intelligence across the entire personal history archive."""
    clean_email = user_email.lower().strip()
    if not clean_email:
        return {}

    target = get_db_target(db_path)
    now = time.time()
    mut_ver = get_db_mutation_version()
    cache_entry = _spotify_stats_cache.get(f"{clean_email}:{target}")
    if cache_entry:
        cached_time, cached_ver, cached_data = cache_entry
        if (now - cached_time < 300) and (cached_ver == mut_ver):
            return cached_data

    with get_connection(target) as conn:
        cursor = conn.cursor()
        ph = "%s" if is_postgres(target) else "?"

        cursor.execute(f"""
            SELECT 
                COUNT(*), 
                COALESCE(SUM(duration_ms), 0), 
                COUNT(DISTINCT artist_name),
                MIN(played_at),
                MAX(played_at)
            FROM spotify_history 
            WHERE LOWER(user_email) = {ph}
        """, (clean_email,))
        agg = cursor.fetchone() or (0, 0, 0, None, None)
        total_tracks, total_ms, unique_artists, min_played, max_played = agg
        total_hours = round((total_ms or 0) / (1000 * 3600), 1)

        # Top 10 All-Time Artists
        cursor.execute(f"""
            SELECT artist_name, COUNT(*) as play_count 
            FROM spotify_history 
            WHERE LOWER(user_email) = {ph} 
            GROUP BY artist_name 
            ORDER BY play_count DESC 
            LIMIT 10
        """, (clean_email,))
        top_artists = [{"artist": r[0], "count": int(r[1])} for r in cursor.fetchall()]

        # Top 10 All-Time Tracks
        cursor.execute(f"""
            SELECT track_name, artist_name, COUNT(*) as play_count, MAX(spotify_url) as s_url 
            FROM spotify_history 
            WHERE LOWER(user_email) = {ph} 
            GROUP BY track_name, artist_name 
            ORDER BY play_count DESC 
            LIMIT 10
        """, (clean_email,))
        top_tracks = [{"name": r[0], "artist": r[1], "count": int(r[2]), "spotify_url": r[3] or ""} for r in cursor.fetchall()]

    result = {
        "total_tracks": total_tracks or 0,
        "total_ms": total_ms or 0,
        "total_hours": total_hours,
        "unique_artists": unique_artists or 0,
        "first_played": _format_datetime(min_played) if min_played else None,
        "last_played": _format_datetime(max_played) if max_played else None,
        "first_year": str(min_played)[:4] if min_played else "",
        "last_year": str(max_played)[:4] if max_played else "",
        "top_artists": top_artists,
        "top_tracks": top_tracks,
    }
    _spotify_stats_cache[f"{clean_email}:{target}"] = (now, mut_ver, result)
    return result


def search_spotify_history(
    user_email: str,
    query: Optional[str] = None,
    page: int = 1,
    per_page: int = 50,
    db_path: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], int, int]:
    """
    Search and paginate through the user's complete Spotify stream archive.
    Returns: (list_of_tracks, total_matching_count, total_pages)
    """
    clean_email = user_email.lower().strip()
    if not clean_email:
        return [], 0, 0

    target = get_db_target(db_path)
    page = max(1, page)
    per_page = max(1, min(per_page, 200))
    offset = (page - 1) * per_page
    clean_q = query.strip() if query else ""

    with get_connection(target) as conn:
        ph = "%s" if is_postgres(target) else "?"
        
        if clean_q:
            like_param = f"%{clean_q.lower()}%"
            where_sql = f"LOWER(user_email) = {ph} AND (LOWER(track_name) LIKE {ph} OR LOWER(artist_name) LIKE {ph} OR LOWER(album_name) LIKE {ph})"
            params = (clean_email, like_param, like_param, like_param)
        else:
            where_sql = f"LOWER(user_email) = {ph}"
            params = (clean_email,)

        # Total count
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM spotify_history WHERE {where_sql}", params)
        total_count = cur.fetchone()[0] or 0

        total_pages = max(1, (total_count + per_page - 1) // per_page)
        if page > total_pages and total_count > 0:
            page = total_pages
            offset = (page - 1) * per_page

        # Select items
        if is_postgres(target):
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        else:
            cursor = conn.cursor()

        query_sql = f"""
            SELECT * FROM spotify_history 
            WHERE {where_sql} 
            ORDER BY played_at DESC 
            LIMIT {ph} OFFSET {ph}
        """
        cursor.execute(query_sql, params + (per_page, offset))
        rows = cursor.fetchall()
        tracks = [_format_spotify_history_record(r) for r in rows if r]

    return tracks, total_count, total_pages


def get_artist_spotify_stats(
    user_email: str,
    artist_name: str,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """Return user's personal Spotify listening metrics for a specific artist."""
    clean_email = user_email.lower().strip()
    clean_art = artist_name.strip()
    if not clean_email or not clean_art:
        return {"play_count": 0, "total_hours": 0.0, "first_played": None, "last_played": None, "top_tracks": []}

    target = get_db_target(db_path)
    with get_connection(target) as conn:
        cursor = conn.cursor()
        ph = "%s" if is_postgres(target) else "?"

        cursor.execute(f"""
            SELECT 
                COUNT(*), 
                COALESCE(SUM(duration_ms), 0),
                MIN(played_at),
                MAX(played_at)
            FROM spotify_history 
            WHERE LOWER(user_email) = {ph} AND LOWER(artist_name) = LOWER({ph})
        """, (clean_email, clean_art))
        row = cursor.fetchone() or (0, 0, None, None)
        cnt, dur, first_p, last_p = row
        hours = round((dur or 0) / (1000 * 3600), 1)

        cursor.execute(f"""
            SELECT track_name, COUNT(*) as c, MAX(spotify_url) as s_url 
            FROM spotify_history 
            WHERE LOWER(user_email) = {ph} AND LOWER(artist_name) = LOWER({ph})
            GROUP BY track_name 
            ORDER BY c DESC 
            LIMIT 5
        """, (clean_email, clean_art))
        top = [{"name": r[0], "count": int(r[1]), "spotify_url": r[2] or ""} for r in cursor.fetchall()]

    return {
        "play_count": cnt or 0,
        "total_hours": hours,
        "first_played": _format_datetime(first_p) if first_p else None,
        "last_played": _format_datetime(last_p) if last_p else None,
        "first_year": str(first_p)[:4] if first_p else "",
        "last_year": str(last_p)[:4] if last_p else "",
        "top_tracks": top,
    }



def get_analyzed_songs(
    artist: Optional[str] = None,
    tag: Optional[str] = None,
    order_by: str = 'updated_at DESC',
    limit: Optional[int] = None,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Retrieve songs that have an existing Gemini lyrics analysis."""
    target = get_db_target(db_path)
    with get_connection(target) as conn:
        if is_postgres(target):
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            ph = "%s"
        else:
            cursor = conn.cursor()
            ph = "?"

        query = """
            SELECT 
                id,
                artist,
                song,
                source,
                song_url,
                analysis,
                model_name,
                prompt_name,
                track_tags,
                theaudiodb_data,
                lyrics,
                (lyrics IS NOT NULL AND LENGTH(TRIM(lyrics)) > 0) as has_lyrics,
                TRUE as has_analysis,
                created_at,
                updated_at
            FROM searches
            WHERE analysis IS NOT NULL AND LENGTH(TRIM(analysis)) > 0
        """
        params: List[Any] = []
        if artist and artist.strip():
            raw_art = artist.strip()
            artist_norm = normalize_text(raw_art)
            base_art = re.sub(r'[\s\-_]+(?:617|\d{3,4}|\([^\)]+\))$', '', raw_art, flags=re.IGNORECASE).strip()
            base_norm = normalize_text(base_art)

            where_parts = [f"artist_normalized = {ph}"]
            params.append(artist_norm)
            if base_norm and base_norm != artist_norm:
                where_parts.append(f"artist_normalized = {ph}")
                params.append(base_norm)
            search_base = base_norm if base_norm else artist_norm
            where_parts.append(f"artist_normalized LIKE {ph}")
            params.append(search_base + " %")
            query += f" AND ({' OR '.join(where_parts)})"

        valid_orders = {
            'updated_at DESC': 'updated_at DESC, id DESC',
            'updated_at ASC': 'updated_at ASC, id ASC',
            'artist ASC': 'artist ASC, song ASC',
            'song ASC': 'song ASC, artist ASC',
        }
        order_clause = valid_orders.get(order_by, 'updated_at DESC, id DESC')
        query += f" ORDER BY {order_clause}"

        # If tag filter is provided, we fetch without SQL limit first to filter in Python, then apply limit
        if not tag and limit and limit > 0:
            query += f" LIMIT {ph}"
            params.append(limit)

        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()
        results = [_format_search_record(r) for r in rows]

        if tag and tag.strip():
            tag_norm = tag.strip().lower()
            filtered = []
            for r in results:
                tags = r.get('track_tags') or []
                matched = False
                for t in tags:
                    t_name = t.get('name', '').lower() if isinstance(t, dict) else str(t).lower()
                    if tag_norm in t_name:
                        matched = True
                        break
                if matched:
                    filtered.append(r)
            if limit and limit > 0:
                filtered = filtered[:limit]
            return filtered

        return results


def get_analyzed_songs_count(artist: Optional[str] = None, db_path: Optional[str] = None) -> int:
    """Get the total count of songs with analysis."""
    target = get_db_target(db_path)
    with get_connection(target) as conn:
        cursor = conn.cursor()
        ph = "%s" if is_postgres(target) else "?"
        if artist and artist.strip():
            cursor.execute(
                f"SELECT COUNT(*) FROM searches WHERE analysis IS NOT NULL AND LENGTH(TRIM(analysis)) > 0 AND artist_normalized = {ph}",
                (normalize_text(artist.strip()),)
            )
        else:
            cursor.execute("SELECT COUNT(*) FROM searches WHERE analysis IS NOT NULL AND LENGTH(TRIM(analysis)) > 0")
        row = cursor.fetchone()
        return row[0] if row else 0


def get_analyzed_artists(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve distinct artists who have at least one analyzed song."""
    target = get_db_target(db_path)
    with get_connection(target) as conn:
        if is_postgres(target):
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        else:
            cursor = conn.cursor()
        cursor.execute("""
            SELECT artist, COUNT(*) as song_count
            FROM searches
            WHERE analysis IS NOT NULL AND LENGTH(TRIM(analysis)) > 0
            GROUP BY artist, artist_normalized
            ORDER BY artist ASC
        """)
        rows = cursor.fetchall()
        return [dict(r) for r in rows]


def get_analyzed_tags(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve distinct Last.fm tags from analyzed songs with occurrence counts."""
    songs = get_analyzed_songs(db_path=db_path)
    tag_counts: Dict[str, int] = {}
    for s in songs:
        tags = s.get('track_tags') or []
        for t in tags:
            t_name = t.get('name', '').strip() if isinstance(t, dict) else str(t).strip()
            if t_name:
                key = t_name.title()
                tag_counts[key] = tag_counts.get(key, 0) + 1
    sorted_tags = sorted(tag_counts.items(), key=lambda x: (-x[1], x[0]))
    return [{'tag': k, 'count': v} for k, v in sorted_tags]


def save_playlist(
    name: str,
    generator_type: str,
    items: List[Dict[str, Any]],
    description: Optional[str] = None,
    criteria: Optional[Dict[str, Any]] = None,
    spotify_playlist_id: Optional[str] = None,
    spotify_playlist_url: Optional[str] = None,
    db_path: Optional[str] = None
) -> int:
    """Save a generated playlist and its ordered tracks."""
    target = get_db_target(db_path)
    criteria_json = json.dumps(criteria) if criteria is not None else None
    track_count = len(items)

    with get_connection(target) as conn:
        cursor = conn.cursor()
        if is_postgres(target):
            cursor.execute("""
                INSERT INTO playlists (
                    name, description, generator_type, criteria_json, track_count,
                    spotify_playlist_id, spotify_playlist_url, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                RETURNING id;
            """, (name.strip(), description, generator_type, criteria_json, track_count, spotify_playlist_id, spotify_playlist_url))
            playlist_id = cursor.fetchone()[0]
            for idx, itm in enumerate(items, 1):
                lyrics_prev = (itm.get('lyrics') or '')[:200] if itm.get('lyrics') else None
                spotify_id = itm.get('spotify_id')
                if not spotify_id and itm.get('theaudiodb_data'):
                    audiodb = itm['theaudiodb_data']
                    if isinstance(audiodb, dict):
                        spotify_id = audiodb.get('spotify_id')
                cursor.execute("""
                    INSERT INTO playlist_items (
                        playlist_id, position, search_id, artist, song, lyrics_preview, model_name, spotify_id, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                """, (
                    playlist_id,
                    idx,
                    itm.get('id') or itm.get('search_id'),
                    itm.get('artist', '').strip(),
                    itm.get('song', '').strip(),
                    lyrics_prev,
                    itm.get('model_name'),
                    spotify_id,
                ))
            return playlist_id
        else:
            cursor.execute("""
                INSERT INTO playlists (
                    name, description, generator_type, criteria_json, track_count,
                    spotify_playlist_id, spotify_playlist_url, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%d %H:%M:%f', 'now'), strftime('%Y-%m-%d %H:%M:%f', 'now'))
            """, (name.strip(), description, generator_type, criteria_json, track_count, spotify_playlist_id, spotify_playlist_url))
            playlist_id = cursor.lastrowid
            for idx, itm in enumerate(items, 1):
                lyrics_prev = (itm.get('lyrics') or '')[:200] if itm.get('lyrics') else None
                spotify_id = itm.get('spotify_id')
                if not spotify_id and itm.get('theaudiodb_data'):
                    audiodb = itm['theaudiodb_data']
                    if isinstance(audiodb, dict):
                        spotify_id = audiodb.get('spotify_id')
                cursor.execute("""
                    INSERT INTO playlist_items (
                        playlist_id, position, search_id, artist, song, lyrics_preview, model_name, spotify_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%d %H:%M:%f', 'now'))
                """, (
                    playlist_id,
                    idx,
                    itm.get('id') or itm.get('search_id'),
                    itm.get('artist', '').strip(),
                    itm.get('song', '').strip(),
                    lyrics_prev,
                    itm.get('model_name'),
                    spotify_id,
                ))
            return playlist_id


def get_saved_playlists(limit: int = 50, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve saved playlists ordered by updated_at descending."""
    target = get_db_target(db_path)
    with get_connection(target) as conn:
        if is_postgres(target):
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            ph = "%s"
        else:
            cursor = conn.cursor()
            ph = "?"
        cursor.execute(f"SELECT * FROM playlists ORDER BY updated_at DESC, id DESC LIMIT {ph}", (limit,))
        rows = cursor.fetchall()
        return [_format_playlist_record(r) for r in rows]


def get_saved_playlist(playlist_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieve a single saved playlist with all its items."""
    target = get_db_target(db_path)
    with get_connection(target) as conn:
        if is_postgres(target):
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            ph = "%s"
        else:
            cursor = conn.cursor()
            ph = "?"
        cursor.execute(f"SELECT * FROM playlists WHERE id = {ph}", (playlist_id,))
        row = cursor.fetchone()
        if not row:
            return None
        playlist = _format_playlist_record(row)
        cursor.execute(f"SELECT * FROM playlist_items WHERE playlist_id = {ph} ORDER BY position ASC, id ASC", (playlist_id,))
        items_rows = cursor.fetchall()
        playlist['items'] = [_format_playlist_item_record(ir) for ir in items_rows]
        return playlist


def delete_saved_playlist(playlist_id: int, db_path: Optional[str] = None) -> bool:
    """Delete a saved playlist and cascaded items."""
    target = get_db_target(db_path)
    with get_connection(target) as conn:
        cursor = conn.cursor()
        ph = "%s" if is_postgres(target) else "?"
        cursor.execute(f"DELETE FROM playlist_items WHERE playlist_id = {ph}", (playlist_id,))
        cursor.execute(f"DELETE FROM playlists WHERE id = {ph}", (playlist_id,))
        return cursor.rowcount > 0


def update_playlist_spotify_info(playlist_id: int, spotify_id: str, spotify_url: str, db_path: Optional[str] = None) -> bool:
    """Update Spotify ID and URL for a saved playlist."""
    target = get_db_target(db_path)
    with get_connection(target) as conn:
        cursor = conn.cursor()
        if is_postgres(target):
            cursor.execute("""
                UPDATE playlists
                SET spotify_playlist_id = %s, spotify_playlist_url = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (spotify_id, spotify_url, playlist_id))
        else:
            cursor.execute("""
                UPDATE playlists
                SET spotify_playlist_id = ?, spotify_playlist_url = ?, updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now')
                WHERE id = ?
            """, (spotify_id, spotify_url, playlist_id))
        return cursor.rowcount > 0


def save_thematic_curation(
    cache_key: str,
    mode: str,
    result: Dict[str, Any],
    prompt: Optional[str] = None,
    artists: Optional[Union[str, List[str]]] = None,
    db_path: Optional[str] = None
) -> bool:
    """
    Save or update a generated thematic/mood playlist curation in thematic_curation_cache.
    Prevents redundant LLM calls when regenerating, viewing, or exporting.
    """
    if not cache_key or not str(cache_key).strip() or not result:
        return False
    target = get_db_target(db_path)
    artists_str = json.dumps(artists) if isinstance(artists, (list, set, tuple)) else (str(artists) if artists is not None else None)
    result_str = json.dumps(result, ensure_ascii=False)

    try:
        with get_connection(target) as conn:
            cursor = conn.cursor()
            if is_postgres(target):
                cursor.execute("""
                    INSERT INTO thematic_curation_cache (cache_key, mode, prompt, artists, result_json, updated_at)
                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (cache_key) DO UPDATE SET
                        mode = EXCLUDED.mode,
                        prompt = EXCLUDED.prompt,
                        artists = EXCLUDED.artists,
                        result_json = EXCLUDED.result_json,
                        updated_at = CURRENT_TIMESTAMP
                """, (cache_key, mode, prompt, artists_str, result_str))
            else:
                cursor.execute("""
                    INSERT INTO thematic_curation_cache (cache_key, mode, prompt, artists, result_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT (cache_key) DO UPDATE SET
                        mode = excluded.mode,
                        prompt = excluded.prompt,
                        artists = excluded.artists,
                        result_json = excluded.result_json,
                        updated_at = CURRENT_TIMESTAMP
                """, (cache_key, mode, prompt, artists_str, result_str))
            return True
    except Exception:
        return False


def get_thematic_curation(
    cache_key: str,
    max_age_days: Optional[int] = 30,
    db_path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Retrieve cached thematic/mood curation result by cache_key.
    Returns parsed dictionary with '_cached': True and '_cached_at' if found, else None.
    """
    if not cache_key or not str(cache_key).strip():
        return None
    target = get_db_target(db_path)
    try:
        with get_connection(target) as conn:
            if is_postgres(target):
                cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                ph = "%s"
            else:
                cursor = conn.cursor()
                ph = "?"
            cursor.execute(f"SELECT cache_key, mode, prompt, artists, result_json, created_at, updated_at FROM thematic_curation_cache WHERE cache_key = {ph}", (cache_key,))
            row = cursor.fetchone()
            if not row:
                return None
            record = dict(row)
            if max_age_days is not None:
                updated_at = record.get('updated_at') or record.get('created_at')
                if updated_at:
                    dt = None
                    if isinstance(updated_at, datetime):
                        dt = updated_at
                    elif isinstance(updated_at, str):
                        try:
                            clean_ts = updated_at.replace('Z', '+00:00')
                            dt = datetime.fromisoformat(clean_ts)
                        except Exception:
                            dt = None
                    if dt:
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        if (datetime.now(timezone.utc) - dt).days > max_age_days:
                            return None
            raw_json = record.get('result_json')
            if isinstance(raw_json, str):
                parsed = json.loads(raw_json)
            elif isinstance(raw_json, dict):
                parsed = raw_json
            else:
                return None
            if isinstance(parsed, dict):
                parsed['_cached'] = True
                parsed['_cached_at'] = str(record.get('updated_at') or record.get('created_at'))
            return parsed
    except Exception:
        return False


def clear_thematic_curation_cache(cache_key: Optional[str] = None, db_path: Optional[str] = None) -> bool:
    """Clear specific cache entry or all thematic curations."""
    target = get_db_target(db_path)
    try:
        with get_connection(target) as conn:
            cursor = conn.cursor()
            if cache_key:
                ph = "%s" if is_postgres(target) else "?"
                cursor.execute(f"DELETE FROM thematic_curation_cache WHERE cache_key = {ph}", (cache_key,))
            else:
                cursor.execute("DELETE FROM thematic_curation_cache")
            return True
    except Exception:
        return False


# ============================================================================
# Band Ratings (0 to 5) Persistence & Analytics
# ============================================================================

def save_band_rating(
    artist: str,
    rating: int,
    user_email: Optional[str] = None,
    notes: Optional[str] = None,
    db_path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Save or update a 0 to 5 band rating for a user.
    Rating scale:
      0: Know nothing about them (unrated / unknown marker)
      1: Dislike
      2: Is ok
      3: Likes
      4: Really enjoy them
      5: One of your absolute favorites
    """
    if not artist or not artist.strip():
        return None

    clean_artist = artist.strip()
    norm_artist = normalize_text(clean_artist)
    clean_email = (user_email or PRIMARY_ADMIN_EMAIL).lower().strip()

    try:
        r_int = int(rating)
    except (ValueError, TypeError):
        r_int = 0
    clamped_rating = max(0, min(5, r_int))

    target = get_db_target(db_path)
    with get_connection(target) as conn:
        cursor = conn.cursor()
        if is_postgres(target):
            cursor.execute("""
                INSERT INTO band_ratings (user_email, artist, artist_normalized, rating, notes, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (user_email, artist_normalized) DO UPDATE SET
                    artist = EXCLUDED.artist,
                    rating = EXCLUDED.rating,
                    notes = EXCLUDED.notes,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id, user_email, artist, artist_normalized, rating, notes, created_at, updated_at;
            """, (clean_email, clean_artist, norm_artist, clamped_rating, notes))
            row = cursor.fetchone()
            notify_db_mutation()
            if row:
                col_names = [desc[0] for desc in cursor.description]
                return _format_band_rating_record(dict(zip(col_names, row)))
            return None
        else:
            cursor.execute("""
                INSERT INTO band_ratings (user_email, artist, artist_normalized, rating, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, strftime('%Y-%m-%d %H:%M:%f', 'now'), strftime('%Y-%m-%d %H:%M:%f', 'now'))
                ON CONFLICT (user_email, artist_normalized) DO UPDATE SET
                    artist = excluded.artist,
                    rating = excluded.rating,
                    notes = excluded.notes,
                    updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now');
            """, (clean_email, clean_artist, norm_artist, clamped_rating, notes))
            notify_db_mutation()
            cursor.execute("""
                SELECT id, user_email, artist, artist_normalized, rating, notes, created_at, updated_at
                FROM band_ratings
                WHERE LOWER(user_email) = ? AND artist_normalized = ?
            """, (clean_email, norm_artist))
            row = cursor.fetchone()
            if row:
                col_names = [desc[0] for desc in cursor.description]
                return _format_band_rating_record(dict(zip(col_names, row)))
            return None


def get_band_rating(
    artist: str,
    user_email: Optional[str] = None,
    db_path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Retrieve a band's rating record for a user."""
    if not artist or not artist.strip():
        return None
    clean_email = (user_email or PRIMARY_ADMIN_EMAIL).lower().strip()
    norm_artist = normalize_text(artist)

    target = get_db_target(db_path)
    with get_connection(target) as conn:
        cursor = conn.cursor()
        ph = "%s" if is_postgres(target) else "?"
        cursor.execute(f"""
            SELECT id, user_email, artist, artist_normalized, rating, notes, created_at, updated_at
            FROM band_ratings
            WHERE LOWER(user_email) = {ph} AND artist_normalized = {ph}
        """, (clean_email, norm_artist))
        row = cursor.fetchone()
        if not row:
            return None
        col_names = [desc[0] for desc in cursor.description]
        return _format_band_rating_record(dict(zip(col_names, row)))


def get_band_ratings(
    user_email: Optional[str] = None,
    min_rating: Optional[int] = None,
    rating_filter: Optional[int] = None,
    order_by: str = 'rating DESC, updated_at DESC',
    limit: Optional[int] = None,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Retrieve all band ratings for a user with optional filtering by rating or minimum rating.
    """
    clean_email = (user_email or PRIMARY_ADMIN_EMAIL).lower().strip()
    target = get_db_target(db_path)
    with get_connection(target) as conn:
        cursor = conn.cursor()
        ph = "%s" if is_postgres(target) else "?"
        query = f"SELECT id, user_email, artist, artist_normalized, rating, notes, created_at, updated_at FROM band_ratings WHERE LOWER(user_email) = {ph}"
        params: List[Any] = [clean_email]

        if rating_filter is not None:
            query += f" AND rating = {ph}"
            params.append(int(rating_filter))
        elif min_rating is not None:
            query += f" AND rating >= {ph}"
            params.append(int(min_rating))

        order_map = {
            'rating DESC, updated_at DESC': 'rating DESC, updated_at DESC, artist ASC',
            'rating ASC, updated_at DESC': 'rating ASC, updated_at DESC, artist ASC',
            'updated_at DESC': 'updated_at DESC, id DESC',
            'artist ASC': 'artist ASC, rating DESC',
            'rating DESC': 'rating DESC, artist ASC',
        }
        order_clause = order_map.get(order_by, 'rating DESC, updated_at DESC, artist ASC')
        query += f" ORDER BY {order_clause}"

        if limit and limit > 0:
            query += f" LIMIT {ph}"
            params.append(limit)

        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()
        col_names = [desc[0] for desc in cursor.description]
        return [_format_band_rating_record(dict(zip(col_names, r))) for r in rows]


def delete_band_rating(
    artist: str,
    user_email: Optional[str] = None,
    db_path: Optional[str] = None
) -> bool:
    """Delete a band rating record for a user."""
    if not artist or not artist.strip():
        return False
    clean_email = (user_email or PRIMARY_ADMIN_EMAIL).lower().strip()
    norm_artist = normalize_text(artist)

    target = get_db_target(db_path)
    with get_connection(target) as conn:
        cursor = conn.cursor()
        ph = "%s" if is_postgres(target) else "?"
        cursor.execute(f"DELETE FROM band_ratings WHERE LOWER(user_email) = {ph} AND artist_normalized = {ph}", (clean_email, norm_artist))
        deleted = cursor.rowcount > 0
        if deleted:
            notify_db_mutation()
        return deleted


def get_rated_artists_map(
    user_email: Optional[str] = None,
    db_path: Optional[str] = None
) -> Dict[str, int]:
    """Return dictionary of {artist_normalized: rating} for quick UI lookups."""
    ratings = get_band_ratings(user_email=user_email, db_path=db_path)
    return {r['artist_normalized']: r['rating'] for r in ratings if r and 'artist_normalized' in r}


def get_analyzed_artists_with_ratings(
    user_email: Optional[str] = None,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Retrieve distinct analyzed artists from searches together with the user's band ratings.
    """
    analyzed_artists = get_analyzed_artists(db_path=db_path)
    rated_map = get_rated_artists_map(user_email=user_email, db_path=db_path)

    results = []
    for a in analyzed_artists:
        art_name = a.get('artist', '').strip()
        norm = normalize_text(art_name)
        rating_val = rated_map.get(norm)
        results.append({
            'artist': art_name,
            'song_count': a.get('song_count', 0),
            'rating': rating_val,  # None if never rated, or 0..5
            'has_rating': (rating_val is not None),
        })
    return results


def get_band_rating_stats(
    user_email: Optional[str] = None,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """Return summary statistics of ratings for a user."""
    ratings = get_band_ratings(user_email=user_email, db_path=db_path)
    by_rating = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for r in ratings:
        val = r.get('rating', 0)
        if val in by_rating:
            by_rating[val] += 1

    ranked_ratings = [r['rating'] for r in ratings if r.get('rating', 0) > 0]
    avg_rating = round(sum(ranked_ratings) / len(ranked_ratings), 2) if ranked_ratings else 0.0

    return {
        'total_rated': len(ratings),
        'total_ranked': len(ranked_ratings),  # 1 to 5
        'by_rating': by_rating,
        'average_ranked_rating': avg_rating,
    }




