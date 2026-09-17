import os
import sqlite3
from contextlib import contextmanager
from typing import Optional, List, Dict, Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.environ.get('DATABASE_PATH') or os.path.join(SCRIPT_DIR, 'calling_hours.db')

def get_db_path(custom_path: Optional[str] = None) -> str:
    return custom_path or os.environ.get('DATABASE_PATH') or DEFAULT_DB_PATH

@contextmanager
def get_connection(db_path: Optional[str] = None):
    path = get_db_path(db_path)
    conn = sqlite3.connect(path, timeout=30.0)
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
    """Initialize the database schema and indexes."""
    with get_connection(db_path) as conn:
        conn.execute("""
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
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_searches_artist 
            ON searches(artist_normalized);
        """)
        conn.execute("""
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

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        # Check if record already exists
        cursor.execute(
            "SELECT id, lyrics, source, song_url FROM searches WHERE artist_normalized = ? AND song_normalized = ?",
            (artist_norm, song_norm)
        )
        row = cursor.fetchone()

        if row:
            record_id = row['id']
            # Only overwrite existing fields if new non-empty values are provided
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

    with get_connection(db_path) as conn:
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
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                artist,
                COUNT(*) as song_count,
                MAX(updated_at) as last_searched
            FROM searches
            GROUP BY artist_normalized
            ORDER BY last_searched DESC, MAX(id) DESC
        """)
        return [dict(row) for row in cursor.fetchall()]

def get_songs_by_band(artist: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns all searched songs for a specific band/artist ordered by most recent activity."""
    artist_norm = normalize_text(artist)
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
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
            WHERE artist_normalized = ?
            ORDER BY updated_at DESC, id DESC
        """, (artist_norm,))
        return [dict(row) for row in cursor.fetchall()]

def get_search(artist: str, song: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieve a search record by artist and song title."""
    artist_norm = normalize_text(artist)
    song_norm = normalize_text(song)
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM searches WHERE artist_normalized = ? AND song_normalized = ?",
            (artist_norm, song_norm)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

def get_search_by_id(search_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieve a search record by ID."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM searches WHERE id = ?", (search_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_recent_searches(limit: int = 100, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve recent searches across all bands for history viewing."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
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
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]

def delete_search(search_id: int, db_path: Optional[str] = None) -> bool:
    """Delete a search record by ID."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM searches WHERE id = ?", (search_id,))
        return cursor.rowcount > 0
