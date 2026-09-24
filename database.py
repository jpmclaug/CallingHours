from __future__ import annotations

import os
import sqlite3
import secrets
import json
from contextlib import contextmanager
from datetime import datetime, date, timedelta, timezone
from typing import Optional, List, Dict, Any

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
    for dict_field in ('setlistfm_data', 'theaudiodb_data'):
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
                INSERT INTO users (email, is_admin, is_active, created_at)
                VALUES (%s, TRUE, TRUE, CURRENT_TIMESTAMP)
                ON CONFLICT (email) DO UPDATE SET is_admin = TRUE, is_active = TRUE;
            """, (PRIMARY_ADMIN_EMAIL.lower().strip(),))
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
                INSERT INTO users (email, is_admin, is_active, created_at)
                VALUES (?, 1, 1, CURRENT_TIMESTAMP)
                ON CONFLICT (email) DO UPDATE SET is_admin = 1, is_active = 1;
            """, (PRIMARY_ADMIN_EMAIL.lower().strip(),))

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
                return record_id
            else:
                cursor.execute("""
                    INSERT INTO searches (
                        artist, song, artist_normalized, song_normalized,
                        lyrics, source, song_url, track_tags, theaudiodb_data, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    RETURNING id;
                """, (artist_clean, song_clean, artist_norm, song_norm, lyrics, source, song_url, tags_json, audiodb_json))
                return cursor.fetchone()['id']
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
                return record_id
            else:
                cursor.execute("""
                    INSERT INTO searches (
                        artist, song, artist_normalized, song_normalized,
                        lyrics, source, song_url, track_tags, theaudiodb_data, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%d %H:%M:%f', 'now'), strftime('%Y-%m-%d %H:%M:%f', 'now'))
                """, (artist_clean, song_clean, artist_norm, song_norm, lyrics, source, song_url, tags_json, audiodb_json))
                return cursor.lastrowid

def save_analysis(
    artist: str,
    song: str,
    analysis: str,
    model_name: Optional[str] = None,
    prompt_name: Optional[str] = None,
    lyrics: Optional[str] = None,
    track_tags: Optional[Union[str, List[Dict[str, Any]]]] = None,
    theaudiodb_data: Optional[Union[str, Dict[str, Any]]] = None,
    db_path: Optional[str] = None
) -> None:
    """Save or update the Gemini analysis result for a given artist and song, ensuring lyrics are preserved."""
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
                """, (artist_clean, song_clean, new_lyrics, new_source, analysis, model_name, prompt_name, new_tags, new_audiodb, row['id']))
            else:
                new_source = 'Manual' if (lyrics and lyrics.strip()) else None
                cursor.execute("""
                    INSERT INTO searches (
                        artist, song, artist_normalized, song_normalized,
                        lyrics, source, analysis, model_name, prompt_name, track_tags, theaudiodb_data, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """, (artist_clean, song_clean, artist_norm, song_norm, lyrics, new_source, analysis, model_name, prompt_name, tags_json, audiodb_json))
        else:
            cursor = conn.cursor()
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
                """, (artist_clean, song_clean, new_lyrics, new_source, analysis, model_name, prompt_name, new_tags, new_audiodb, row['id']))
            else:
                new_source = 'Manual' if (lyrics and lyrics.strip()) else None
                cursor.execute("""
                    INSERT INTO searches (
                        artist, song, artist_normalized, song_normalized,
                        lyrics, source, analysis, model_name, prompt_name, track_tags, theaudiodb_data, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%d %H:%M:%f', 'now'), strftime('%Y-%m-%d %H:%M:%f', 'now'))
                """, (artist_clean, song_clean, artist_norm, song_norm, lyrics, new_source, analysis, model_name, prompt_name, tags_json, audiodb_json))


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
    db_path: Optional[str] = None
) -> int:
    """
    Save or update comprehensive artist metadata (Last.fm, Setlist.fm, TheAudioDB).
    Returns the record ID.
    """
    artist_clean = artist.strip()
    artist_norm = normalize_text(artist_clean)
    tags_json = json.dumps(tags) if isinstance(tags, (list, dict)) else tags
    tracks_json = json.dumps(top_tracks) if isinstance(top_tracks, (list, dict)) else top_tracks
    setlist_json = json.dumps(setlistfm_data) if isinstance(setlistfm_data, (list, dict)) else setlistfm_data
    audiodb_json = json.dumps(theaudiodb_data) if isinstance(theaudiodb_data, (list, dict)) else theaudiodb_data
    similar_json = json.dumps(similar_artists) if isinstance(similar_artists, (list, dict)) else similar_artists

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
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (
                    artist_clean, new_tags, new_tracks, new_setlist, new_audiodb,
                    new_bio, new_similar, new_listeners, new_playcount,
                    new_image, new_banner, new_formed, new_country, record_id
                ))
                return record_id
            else:
                cursor.execute("""
                    INSERT INTO artist_metadata (
                        artist, artist_normalized, tags, top_tracks,
                        setlistfm_data, theaudiodb_data, bio, similar_artists,
                        listeners, playcount, image_url, banner_url, formed_year, country,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    RETURNING id;
                """, (
                    artist_clean, artist_norm, tags_json, tracks_json,
                    setlist_json, audiodb_json, bio, similar_json,
                    listeners, playcount, image_url, banner_url, formed_year, country
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
                        updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now')
                    WHERE id = ?
                """, (
                    artist_clean, new_tags, new_tracks, new_setlist, new_audiodb,
                    new_bio, new_similar, new_listeners, new_playcount,
                    new_image, new_banner, new_formed, new_country, record_id
                ))
                return record_id
            else:
                cursor.execute("""
                    INSERT INTO artist_metadata (
                        artist, artist_normalized, tags, top_tracks,
                        setlistfm_data, theaudiodb_data, bio, similar_artists,
                        listeners, playcount, image_url, banner_url, formed_year, country,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%d %H:%M:%f', 'now'), strftime('%Y-%m-%d %H:%M:%f', 'now'))
                """, (
                    artist_clean, artist_norm, tags_json, tracks_json,
                    setlist_json, audiodb_json, bio, similar_json,
                    listeners, playcount, image_url, banner_url, formed_year, country
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
    artist_norm = normalize_text(artist)
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
            WHERE artist_normalized = {ph}
            ORDER BY updated_at DESC, id DESC
        """, (artist_norm,))
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
    """Delete a search record by ID."""
    target = get_db_target(db_path)
    with get_connection(target) as conn:
        cursor = conn.cursor()
        ph = "%s" if is_postgres(target) else "?"
        cursor.execute(f"DELETE FROM searches WHERE id = {ph}", (search_id,))
        return cursor.rowcount > 0


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
    inserted = 0
    with get_connection(target) as conn:
        cursor = conn.cursor()
        for item in items:
            track_id = item.get("track_id") or ""
            played_at = item.get("played_at") or ""
            track_name = item.get("name") or "Unknown Track"
            artist_name = item.get("artist") or "Unknown Artist"
            album_name = item.get("album") or ""
            album_image = item.get("album_image") or ""
            duration_ms = item.get("duration_ms") or 0
            popularity = item.get("popularity") or 0
            preview_url = item.get("preview_url") or ""
            spotify_url = item.get("spotify_url") or ""
            release_date = item.get("release_date") or ""

            if not track_id or not played_at:
                continue

            if is_postgres(target):
                cursor.execute("""
                    INSERT INTO spotify_history (
                        user_email, spotify_track_id, played_at, track_name, artist_name,
                        album_name, album_image_url, duration_ms, popularity,
                        preview_url, spotify_url, release_date, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (user_email, spotify_track_id, played_at) DO NOTHING;
                """, (
                    clean_email, track_id, played_at, track_name, artist_name,
                    album_name, album_image, duration_ms, popularity,
                    preview_url, spotify_url, release_date
                ))
            else:
                cursor.execute("""
                    INSERT OR IGNORE INTO spotify_history (
                        user_email, spotify_track_id, played_at, track_name, artist_name,
                        album_name, album_image_url, duration_ms, popularity,
                        preview_url, spotify_url, release_date, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP);
                """, (
                    clean_email, track_id, played_at, track_name, artist_name,
                    album_name, album_image, duration_ms, popularity,
                    preview_url, spotify_url, release_date
                ))
            inserted += 1

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
            query += f" AND artist_normalized = {ph}"
            params.append(normalize_text(artist.strip()))

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



