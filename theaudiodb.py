"""TheAudioDB V1 API Client for Calling Hours.

Provides helpers to fetch track details, musical attributes (BPM, key, mood,
time signature, audio features), story/history, and media links via TheAudioDB V1 API.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Union
import requests

THEAUDIODB_API_BASE_URL = "https://www.theaudiodb.com/api/v1/json/"
DEFAULT_FREE_API_KEY = "123"
DEFAULT_USER_AGENT = "CallingHours/1.0 (https://github.com/jpmclaug/CallingHours)"


def get_theaudiodb_api_key() -> str:
    """
    Retrieve TheAudioDB API key from environment or secrets file.
    Falls back to TheAudioDB free test key ("123") if none configured.
    """
    env_key = os.environ.get("THEAUDIODB_API_KEY")
    if env_key and env_key.strip():
        return env_key.strip()

    try:
        import calling_hours_secrets
        key = getattr(calling_hours_secrets, "THEAUDIODB_API_KEY", None)
        if key and str(key).strip():
            return str(key).strip()
    except (ImportError, AttributeError):
        pass

    return DEFAULT_FREE_API_KEY


def _to_int(val: Any) -> Optional[int]:
    """Safely convert value to int or None."""
    if val is None:
        return None
    try:
        s = str(val).strip()
        if not s or s.lower() == "null":
            return None
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _clean_str(val: Any) -> Optional[str]:
    """Strip whitespace and return None if empty."""
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() == "null":
        return None
    return s


def _format_duration(duration_ms: Optional[int]) -> Optional[str]:
    """Convert millisecond duration to mm:ss format."""
    if not duration_ms or duration_ms <= 0:
        return None
    total_seconds = duration_ms // 1000
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes}:{seconds:02d}"


def _format_compact_number(num: Optional[int]) -> Optional[str]:
    """Format large numbers like 1,500,000 to '1.5M'."""
    if num is None:
        return None
    if num >= 1_000_000_000:
        return f"{num / 1_000_000_000:.1f}B".replace(".0B", "B")
    if num >= 1_000_000:
        return f"{num / 1_000_000:.1f}M".replace(".0M", "M")
    if num >= 1_000:
        return f"{num / 1_000:.1f}K".replace(".0K", "K")
    return str(num)


def clean_track_data(raw_track: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract, normalize, and type track metadata from TheAudioDB track payload.
    Includes the complete raw dictionary under 'raw' so no information is lost.
    """
    if not raw_track or not isinstance(raw_track, dict):
        return {}

    duration_ms = _to_int(raw_track.get("intDuration"))
    vid_views = _to_int(raw_track.get("intMusicVidViews"))
    vid_likes = _to_int(raw_track.get("intMusicVidLikes"))
    total_plays = _to_int(raw_track.get("intTotalPlays"))
    total_listeners = _to_int(raw_track.get("intTotalListeners"))

    cleaned: Dict[str, Any] = {
        # Core Identification
        "id_track": _clean_str(raw_track.get("idTrack")),
        "id_album": _clean_str(raw_track.get("idAlbum")),
        "id_artist": _clean_str(raw_track.get("idArtist")),
        "track": _clean_str(raw_track.get("strTrack")),
        "album": _clean_str(raw_track.get("strAlbum")),
        "artist": _clean_str(raw_track.get("strArtist")),
        "artist_alternate": _clean_str(raw_track.get("strArtistAlternate")),

        # Musical Attributes
        "genre": _clean_str(raw_track.get("strGenre")),
        "style": _clean_str(raw_track.get("strStyle")),
        "mood": _clean_str(raw_track.get("strMood")),
        "theme": _clean_str(raw_track.get("strTheme")),
        "tempo": _to_int(raw_track.get("intTempo")),
        "key": _clean_str(raw_track.get("strKey")),
        "open_key": _clean_str(raw_track.get("strOpenKey")),
        "time_signature": _clean_str(raw_track.get("strTimeSignature")),
        "duration_ms": duration_ms,
        "duration_formatted": _format_duration(duration_ms),
        "track_number": _to_int(raw_track.get("intTrackNumber")),

        # Audio Feature Scores (0-100)
        "danceability": _to_int(raw_track.get("intDanceability")),
        "acousticness": _to_int(raw_track.get("intAcousticness")),
        "valence": _to_int(raw_track.get("intValence")),
        "energy": _to_int(raw_track.get("intEnergy")),
        "liveness": _to_int(raw_track.get("intLiveness")),
        "instrumentalness": _to_int(raw_track.get("intInstrumentalness")),
        "speechiness": _to_int(raw_track.get("intSpeechiness")),

        # Background Story / History
        "description": _clean_str(raw_track.get("strDescriptionEN")),

        # Artwork & Media
        "thumbnail_url": _clean_str(raw_track.get("strTrackThumb")),
        "case_3d_url": _clean_str(raw_track.get("strTrack3DCase")),
        "music_vid_url": _clean_str(raw_track.get("strMusicVid")),
        "music_vid_director": _clean_str(raw_track.get("strMusicVidDirector")),
        "music_vid_company": _clean_str(raw_track.get("strMusicVidCompany")),
        "music_vid_views": vid_views,
        "music_vid_views_formatted": _format_compact_number(vid_views),
        "music_vid_likes": vid_likes,
        "music_vid_screens": [
            url for url in [
                _clean_str(raw_track.get("strMusicVidScreen1")),
                _clean_str(raw_track.get("strMusicVidScreen2")),
                _clean_str(raw_track.get("strMusicVidScreen3")),
                _clean_str(raw_track.get("strMusicVidScreen4")),
            ] if url
        ],

        # Community Stats & External IDs
        "score": _clean_str(raw_track.get("intScore")),
        "popularity": _to_int(raw_track.get("intPopularity")),
        "total_listeners": total_listeners,
        "total_plays": total_plays,
        "total_plays_formatted": _format_compact_number(total_plays),
        "spotify_id": _clean_str(raw_track.get("strSpotifyID")),
        "musicbrainz_id": _clean_str(raw_track.get("strMusicBrainzID")),
        "musicbrainz_album_id": _clean_str(raw_track.get("strMusicBrainzAlbumID")),
        "musicbrainz_artist_id": _clean_str(raw_track.get("strMusicBrainzArtistID")),
        "isrc": _clean_str(raw_track.get("strISRC")),

        # Complete unadulterated payload
        "raw": raw_track,
    }

    return cleaned


def _clean_query_title(title: str) -> str:
    """
    Remove parenthetical or bracketed notes such as (Remastered 2021),
    [Official Video], (Live at...), or feat. tags that may prevent exact match.
    """
    cleaned = re.sub(r"\s*[\(\[][^\)\]]*(?:remaster|live|version|edit|mono|stereo|bonus|deluxe|explicit|clean|audio|video|mix)[^\)\]]*[\)\]]", "", title, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+(?:feat\.|featuring)\s+.*$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def fetch_track_details(
    artist: str,
    track: str,
    api_key: Optional[str] = None,
    timeout: int = 8
) -> Optional[Dict[str, Any]]:
    """
    Fetch track details from TheAudioDB searchtrack.php endpoint.
    Returns cleaned track dictionary or None if not found.
    """
    key = api_key or get_theaudiodb_api_key()
    if not key or not artist or not track:
        return None

    clean_artist = artist.strip()
    clean_track = track.strip()
    if not clean_artist or not clean_track:
        return None

    headers = {"User-Agent": DEFAULT_USER_AGENT}
    url = f"{THEAUDIODB_API_BASE_URL}{key}/searchtrack.php"

    def _query(s_val: str, t_val: str) -> Optional[Dict[str, Any]]:
        try:
            resp = requests.get(url, params={"s": s_val, "t": t_val}, headers=headers, timeout=timeout)
            if resp.status_code != 200:
                return None
            data = resp.json()
            if not isinstance(data, dict):
                return None
            track_list = data.get("track")
            if isinstance(track_list, list) and track_list and isinstance(track_list[0], dict):
                return clean_track_data(track_list[0])
        except Exception as e:
            print(f"TheAudioDB fetch_track_details error for {s_val} - {t_val}: {e}")
        return None

    # 1. Primary search with given artist and track title
    result = _query(clean_artist, clean_track)
    if result:
        return result

    # 2. Fallback: stripped title without extra parentheticals or featured artists
    simplified_track = _clean_query_title(clean_track)
    if simplified_track and simplified_track.lower() != clean_track.lower():
        result = _query(clean_artist, simplified_track)
        if result:
            return result

    return None


def get_or_fetch_track_metadata(
    artist: str,
    song: str,
    api_key: Optional[str] = None,
    force_refresh: bool = False,
    db_path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Get TheAudioDB track metadata from database cache if available,
    otherwise fetch from TheAudioDB and persist to database.
    """
    if not artist or not song:
        return None

    clean_artist = artist.strip()
    clean_song = song.strip()
    if not clean_artist or not clean_song:
        return None

    import database

    # 1. Check database cache first if not forcing refresh
    if not force_refresh:
        cached_data = database.get_theaudiodb_data(clean_artist, clean_song, db_path=db_path)
        if cached_data:
            return cached_data

    # 2. Fetch from TheAudioDB
    data = fetch_track_details(clean_artist, clean_song, api_key=api_key)
    if data:
        try:
            database.save_theaudiodb_data(clean_artist, clean_song, data, db_path=db_path)
        except Exception as e:
            print(f"Database save_theaudiodb_data error for {clean_artist} - {clean_song}: {e}")

    return data
