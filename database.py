import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, date
from typing import Optional, List, Dict, Any

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

def normalize_text(text: str) -> str:
    return text.strip().lower() if text else ""

def save_search(
    artist: str,
    song: str,
    lyrics: Optional[str] = None,
    source: Optional[str] = None,
    song_url: Optional[str] = None,
    db_path: Optional[str] = None
) -> int:
    """
    Save or update a search result. If the artist and song already exist,
    updates lyrics/source/song_url (if provided) and updates timestamp while preserving previous analysis.
    Returns the record ID.
    """
    artist_clean = artist.strip()
    song_clean = song.strip()
    artist_norm = normalize_text(artist_clean)
    song_norm = normalize_text(song_clean)
    target = get_db_target(db_path)

    with get_connection(target) as conn:
        if is_postgres(target):
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute(
                "SELECT id, lyrics, source, song_url FROM searches WHERE artist_normalized = %s AND song_normalized = %s",
                (artist_norm, song_norm)
            )
            row = cursor.fetchone()

            if row:
                record_id = row['id']
                new_lyrics = lyrics if (lyrics and lyrics.strip()) else row['lyrics']
                new_source = source if source else row['source']
                new_url = song_url if song_url else row['song_url']
                cursor.execute("""
                    UPDATE searches
                    SET artist = %s,
                        song = %s,
                        lyrics = %s,
                        source = %s,
                        song_url = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (artist_clean, song_clean, new_lyrics, new_source, new_url, record_id))
                return record_id
            else:
                cursor.execute("""
                    INSERT INTO searches (
                        artist, song, artist_normalized, song_normalized,
                        lyrics, source, song_url, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    RETURNING id;
                """, (artist_clean, song_clean, artist_norm, song_norm, lyrics, source, song_url))
                return cursor.fetchone()['id']
        else:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, lyrics, source, song_url FROM searches WHERE artist_normalized = ? AND song_normalized = ?",
                (artist_norm, song_norm)
            )
            row = cursor.fetchone()

            if row:
                record_id = row['id']
                new_lyrics = lyrics if (lyrics and lyrics.strip()) else row['lyrics']
                new_source = source if source else row['source']
                new_url = song_url if song_url else row['song_url']
                cursor.execute("""
                    UPDATE searches
                    SET artist = ?,
                        song = ?,
                        lyrics = ?,
                        source = ?,
                        song_url = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (artist_clean, song_clean, new_lyrics, new_source, new_url, record_id))
                return record_id
            else:
                cursor.execute("""
                    INSERT INTO searches (
                        artist, song, artist_normalized, song_normalized,
                        lyrics, source, song_url, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """, (artist_clean, song_clean, artist_norm, song_norm, lyrics, source, song_url))
                return cursor.lastrowid

def save_analysis(
    artist: str,
    song: str,
    analysis: str,
    model_name: Optional[str] = None,
    prompt_name: Optional[str] = None,
    db_path: Optional[str] = None
) -> None:
    """Save or update the Gemini analysis result for a given artist and song."""
    artist_clean = artist.strip()
    song_clean = song.strip()
    artist_norm = normalize_text(artist_clean)
    song_norm = normalize_text(song_clean)
    target = get_db_target(db_path)

    with get_connection(target) as conn:
        if is_postgres(target):
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute(
                "SELECT id FROM searches WHERE artist_normalized = %s AND song_normalized = %s",
                (artist_norm, song_norm)
            )
            row = cursor.fetchone()

            if row:
                cursor.execute("""
                    UPDATE searches
                    SET analysis = %s,
                        model_name = %s,
                        prompt_name = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (analysis, model_name, prompt_name, row['id']))
            else:
                cursor.execute("""
                    INSERT INTO searches (
                        artist, song, artist_normalized, song_normalized,
                        analysis, model_name, prompt_name, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """, (artist_clean, song_clean, artist_norm, song_norm, analysis, model_name, prompt_name))
        else:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM searches WHERE artist_normalized = ? AND song_normalized = ?",
                (artist_norm, song_norm)
            )
            row = cursor.fetchone()

            if row:
                cursor.execute("""
                    UPDATE searches
                    SET analysis = ?,
                        model_name = ?,
                        prompt_name = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (analysis, model_name, prompt_name, row['id']))
            else:
                cursor.execute("""
                    INSERT INTO searches (
                        artist, song, artist_normalized, song_normalized,
                        analysis, model_name, prompt_name, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """, (artist_clean, song_clean, artist_norm, song_norm, analysis, model_name, prompt_name))

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
                MAX(artist) as artist,
                COUNT(*) as song_count,
                MAX(updated_at) as last_searched
            FROM searches
            GROUP BY artist_normalized
            ORDER BY last_searched DESC, MAX(id) DESC
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

