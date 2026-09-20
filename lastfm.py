"""Last.fm API Client for Calling Hours.

Provides helpers to fetch top tags for tracks, top tags for artists,
and top tracks for artists via the Last.fm Web Services (v2.0) REST API.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
from typing import Any, Dict, List, Optional
import requests

LASTFM_API_BASE_URL = "https://ws.audioscrobbler.com/2.0/"
DEFAULT_USER_AGENT = "CallingHours/1.0 (https://github.com/jpmclaug/CallingHours)"


def get_lastfm_api_key() -> Optional[str]:
    """Retrieve the Last.fm API key from environment or secrets file."""
    # 1. Environment variable takes precedence (standard for Cloud Run / Docker)
    env_key = os.environ.get("LASTFM_API_KEY")
    if env_key and env_key.strip():
        return env_key.strip()

    # 2. Local secrets file fallback
    try:
        import calling_hours_secrets
        key = getattr(calling_hours_secrets, "LASTFM_API_KEY", None)
        if key and str(key).strip():
            return str(key).strip()
    except (ImportError, AttributeError):
        pass

    return None


def _clean_tag_list(raw_tags: Any, limit: int = 15) -> List[Dict[str, Any]]:
    """Normalize raw Last.fm tag items (which can be a list, single dict, or None) into a clean list."""
    if not raw_tags:
        return []

    if isinstance(raw_tags, dict):
        raw_tags = [raw_tags]
    elif not isinstance(raw_tags, list):
        return []

    cleaned = []
    for item in raw_tags:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue

        raw_count = item.get("count", 0)
        try:
            count = int(raw_count)
        except (ValueError, TypeError):
            count = 0

        url = item.get("url", f"https://www.last.fm/tag/{urllib.parse.quote_plus(name)}")

        cleaned.append({
            "name": name,
            "count": count,
            "url": url,
        })

    # Sort by count descending if counts differ, then cap to limit
    cleaned.sort(key=lambda t: t.get("count", 0), reverse=True)
    return cleaned[:limit]


def _clean_track_list(raw_tracks: Any, limit: int = 10) -> List[Dict[str, Any]]:
    """Normalize raw Last.fm artist top tracks into a clean list."""
    if not raw_tracks:
        return []

    if isinstance(raw_tracks, dict):
        raw_tracks = [raw_tracks]
    elif not isinstance(raw_tracks, list):
        return []

    cleaned = []
    for idx, item in enumerate(raw_tracks):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue

        raw_playcount = item.get("playcount", 0)
        try:
            playcount = int(raw_playcount)
        except (ValueError, TypeError):
            playcount = 0

        raw_listeners = item.get("listeners", 0)
        try:
            listeners = int(raw_listeners)
        except (ValueError, TypeError):
            listeners = 0

        # Last.fm supplies @attr.rank as string in some responses
        attr_data = item.get("@attr", {})
        raw_rank = attr_data.get("rank") if isinstance(attr_data, dict) else None
        try:
            rank = int(raw_rank) if raw_rank is not None else idx + 1
        except (ValueError, TypeError):
            rank = idx + 1

        url = item.get("url", "")

        cleaned.append({
            "name": name,
            "playcount": playcount,
            "listeners": listeners,
            "rank": rank,
            "url": url,
        })

    cleaned.sort(key=lambda t: t.get("rank", 999))
    return cleaned[:limit]


def fetch_track_top_tags(
    artist: str,
    track: str,
    api_key: Optional[str] = None,
    limit: int = 15,
    timeout: int = 8
) -> List[Dict[str, Any]]:
    """
    Fetch top community tags for a track using Last.fm track.getTopTags.
    Returns list of dicts: [{'name': '...', 'count': 100, 'url': '...'}]
    """
    key = api_key or get_lastfm_api_key()
    if not key or not artist or not track:
        return []

    params = {
        "method": "track.getTopTags",
        "artist": artist.strip(),
        "track": track.strip(),
        "api_key": key,
        "format": "json",
        "autocorrect": "1",
    }
    headers = {"User-Agent": DEFAULT_USER_AGENT}

    try:
        resp = requests.get(LASTFM_API_BASE_URL, params=params, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not isinstance(data, dict) or "error" in data:
            return []

        raw_tags = data.get("toptags", {}).get("tag", [])
        return _clean_tag_list(raw_tags, limit=limit)
    except Exception as e:
        print(f"Last.fm fetch_track_top_tags error for {artist} - {track}: {e}")
        return []


def fetch_artist_top_tags(
    artist: str,
    api_key: Optional[str] = None,
    limit: int = 15,
    timeout: int = 8
) -> List[Dict[str, Any]]:
    """
    Fetch top community tags for an artist using Last.fm artist.getTopTags.
    Returns list of dicts: [{'name': '...', 'count': 100, 'url': '...'}]
    """
    key = api_key or get_lastfm_api_key()
    if not key or not artist:
        return []

    params = {
        "method": "artist.getTopTags",
        "artist": artist.strip(),
        "api_key": key,
        "format": "json",
        "autocorrect": "1",
    }
    headers = {"User-Agent": DEFAULT_USER_AGENT}

    try:
        resp = requests.get(LASTFM_API_BASE_URL, params=params, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not isinstance(data, dict) or "error" in data:
            return []

        raw_tags = data.get("toptags", {}).get("tag", [])
        return _clean_tag_list(raw_tags, limit=limit)
    except Exception as e:
        print(f"Last.fm fetch_artist_top_tags error for {artist}: {e}")
        return []


def fetch_artist_top_tracks(
    artist: str,
    api_key: Optional[str] = None,
    limit: int = 10,
    timeout: int = 8
) -> List[Dict[str, Any]]:
    """
    Fetch top tracks for an artist using Last.fm artist.getTopTracks.
    Returns list of dicts: [{'name': '...', 'playcount': 12345, 'listeners': 678, 'rank': 1, 'url': '...'}]
    """
    key = api_key or get_lastfm_api_key()
    if not key or not artist:
        return []

    params = {
        "method": "artist.getTopTracks",
        "artist": artist.strip(),
        "api_key": key,
        "format": "json",
        "autocorrect": "1",
        "limit": str(limit),
    }
    headers = {"User-Agent": DEFAULT_USER_AGENT}

    try:
        resp = requests.get(LASTFM_API_BASE_URL, params=params, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not isinstance(data, dict) or "error" in data:
            return []

        raw_tracks = data.get("toptracks", {}).get("track", [])
        return _clean_track_list(raw_tracks, limit=limit)
    except Exception as e:
        print(f"Last.fm fetch_artist_top_tracks error for {artist}: {e}")
        return []


def fetch_artist_bio_and_stats(
    artist: str,
    api_key: Optional[str] = None,
    timeout: int = 8
) -> Dict[str, Any]:
    """
    Fetch artist biography, listener/scrobble stats, and similar artists via Last.fm artist.getInfo.
    """
    key = api_key or get_lastfm_api_key()
    if not key or not artist:
        return {}

    clean_artist = artist.strip()
    params = {
        "method": "artist.getinfo",
        "artist": clean_artist,
        "api_key": key,
        "format": "json",
        "autocorrect": "1",
    }
    headers = {"User-Agent": DEFAULT_USER_AGENT}

    try:
        resp = requests.get(LASTFM_API_BASE_URL, params=params, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        if not isinstance(data, dict) or "error" in data:
            return {}

        art_data = data.get("artist", {})
        stats = art_data.get("stats", {})
        bio = art_data.get("bio", {})
        raw_similar = art_data.get("similar", {}).get("artist", [])

        # Parse similar artists
        similar_artists = []
        if isinstance(raw_similar, list):
            for s in raw_similar:
                if isinstance(s, dict) and s.get("name"):
                    similar_artists.append({
                        "name": s.get("name"),
                        "url": s.get("url", ""),
                    })

        # Strip trailing "User-contributed text is available under..." or link tags from bio summary
        summary = str(bio.get("summary", "")).strip()
        summary = re.sub(r'<a\s+href="[^"]*">Read more on Last\.fm</a>.*$', '', summary, flags=re.IGNORECASE).strip()

        listeners_raw = stats.get("listeners", 0)
        playcount_raw = stats.get("playcount", 0)
        try:
            listeners = int(listeners_raw)
        except (ValueError, TypeError):
            listeners = 0
        try:
            playcount = int(playcount_raw)
        except (ValueError, TypeError):
            playcount = 0

        return {
            "name": art_data.get("name") or clean_artist,
            "listeners": listeners,
            "playcount": playcount,
            "bio_summary": summary,
            "bio_content": str(bio.get("content", "")).strip(),
            "similar_artists": similar_artists,
            "url": art_data.get("url"),
        }
    except Exception as e:
        print(f"Last.fm fetch_artist_bio_and_stats error for {clean_artist}: {e}")
        return {}


def get_or_fetch_artist_metadata(
    artist: str,
    api_key: Optional[str] = None,
    force_refresh: bool = False,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get artist metadata (tags, top tracks, bio & stats) from database cache if available,
    otherwise fetch from Last.fm and persist to database.
    """
    if not artist or not artist.strip():
        return {"artist": "", "tags": [], "top_tracks": [], "bio": "", "stats": {}}

    import database

    clean_artist = artist.strip()

    # Check database cache first if not forcing refresh
    if not force_refresh:
        cached = database.get_artist_metadata(clean_artist, db_path=db_path)
        if cached and (cached.get("tags") or cached.get("top_tracks") or cached.get("bio")):
            sim_list = cached.get("similar_artists")
            if isinstance(sim_list, str):
                try:
                    sim_list = json.loads(sim_list)
                except Exception:
                    sim_list = []
            return {
                "artist": cached.get("artist") or clean_artist,
                "tags": cached.get("tags") or [],
                "top_tracks": cached.get("top_tracks") or [],
                "bio": cached.get("bio") or "",
                "listeners": cached.get("listeners") or 0,
                "playcount": cached.get("playcount") or 0,
                "similar_artists": sim_list or [],
                "cached": True,
            }

    # Fetch from Last.fm
    tags = fetch_artist_top_tags(clean_artist, api_key=api_key)
    top_tracks = fetch_artist_top_tracks(clean_artist, api_key=api_key)
    bio_stats = fetch_artist_bio_and_stats(clean_artist, api_key=api_key)

    bio_summary = bio_stats.get("bio_summary", "")
    listeners = bio_stats.get("listeners", 0)
    playcount = bio_stats.get("playcount", 0)
    similar = bio_stats.get("similar_artists", [])

    if tags or top_tracks or bio_summary:
        try:
            database.save_artist_metadata(
                clean_artist,
                tags=tags,
                top_tracks=top_tracks,
                bio=bio_summary,
                listeners=listeners,
                playcount=playcount,
                similar_artists=similar,
                db_path=db_path
            )
        except Exception as e:
            print(f"Database save_artist_metadata error for {clean_artist}: {e}")

    return {
        "artist": clean_artist,
        "tags": tags,
        "top_tracks": top_tracks,
        "bio": bio_summary,
        "listeners": listeners,
        "playcount": playcount,
        "similar_artists": similar,
        "cached": False,
    }


def get_or_fetch_track_tags(
    artist: str,
    song: str,
    api_key: Optional[str] = None,
    force_refresh: bool = False,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Get track tags from database search record if available,
    otherwise fetch from Last.fm and update the search record.
    """
    if not artist or not song:
        return []

    import database

    clean_artist = artist.strip()
    clean_song = song.strip()

    if not force_refresh:
        cached_search = database.get_search(clean_artist, clean_song, db_path=db_path)
        if cached_search and cached_search.get("track_tags"):
            tags = cached_search["track_tags"]
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except Exception:
                    tags = []
            if isinstance(tags, list) and tags:
                return tags

    # Fetch from Last.fm
    tags = fetch_track_top_tags(clean_artist, clean_song, api_key=api_key)
    if tags:
        try:
            database.save_track_tags(clean_artist, clean_song, tags, db_path=db_path)
        except Exception as e:
            print(f"Database save_track_tags error for {clean_artist} - {clean_song}: {e}")

    return tags
