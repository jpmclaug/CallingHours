"""Setlist.fm API Client for Calling Hours.

Provides helpers to fetch artist tour history, identify co-performers / tour mates,
and check when an artist last performed in North Carolina (NC) via the Setlist.fm REST API v1.0.
"""
from __future__ import annotations

import os
import re
import json
import urllib.parse
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
import requests

SETLIST_FM_API_BASE_URL = "https://api.setlist.fm/rest/1.0"
DEFAULT_USER_AGENT = "CallingHours/1.0 (https://github.com/jpmclaug/CallingHours)"


def get_setlistfm_api_key() -> Optional[str]:
    """Retrieve the Setlist.fm API key from environment or secrets file."""
    # 1. Environment variable takes precedence (standard for Cloud Run / Docker)
    env_key = os.environ.get("SETLIST_FM_API_KEY")
    if env_key and env_key.strip():
        return env_key.strip()

    # 2. Local secrets file fallback
    try:
        import calling_hours_secrets
        key = getattr(calling_hours_secrets, "SETLIST_FM_API_KEY", None)
        if key and str(key).strip():
            return str(key).strip()
    except (ImportError, AttributeError):
        pass

    return None


def _get_headers(api_key: Optional[str] = None) -> Dict[str, str]:
    """Build HTTP headers required by Setlist.fm API."""
    key = api_key or get_setlistfm_api_key() or ""
    return {
        "x-api-key": key,
        "Accept": "application/json",
        "User-Agent": DEFAULT_USER_AGENT,
    }


def _format_event_date(date_str: Optional[str]) -> Optional[str]:
    """Convert Setlist.fm date format (DD-MM-YYYY) into a friendly string (e.g. 'July 16, 2025')."""
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str.strip(), "%d-%m-%Y")
        return dt.strftime("%B %d, %Y").replace(" 0", " ")
    except Exception:
        return date_str


def search_artist(
    artist_name: str,
    api_key: Optional[str] = None,
    timeout: int = 8
) -> Optional[Dict[str, Any]]:
    """
    Search for an artist on Setlist.fm and return their MusicBrainz ID (mbid) and details.
    Matches exact name (case-insensitive) if multiple results are returned.
    """
    if not artist_name or not artist_name.strip():
        return None

    clean_name = artist_name.strip()
    key = api_key or get_setlistfm_api_key()
    if not key:
        return None

    url = f"{SETLIST_FM_API_BASE_URL}/search/artists"
    headers = _get_headers(key)
    params = {
        "artistName": clean_name,
        "sort": "relevance",
    }

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            return None
        data = resp.json()
        artists = data.get("artist", [])
        if not artists:
            return None

        # Prioritize exact match
        for a in artists:
            if str(a.get("name", "")).strip().lower() == clean_name.lower():
                return {
                    "mbid": a.get("mbid"),
                    "name": a.get("name"),
                    "sortName": a.get("sortName"),
                    "disambiguation": a.get("disambiguation", ""),
                    "url": a.get("url"),
                }

        # Fallback to first relevant artist
        first = artists[0]
        return {
            "mbid": first.get("mbid"),
            "name": first.get("name"),
            "sortName": first.get("sortName"),
            "disambiguation": first.get("disambiguation", ""),
            "url": first.get("url"),
        }
    except Exception as e:
        print(f"Setlist.fm search_artist error for {clean_name}: {e}")
        return None


def fetch_last_nc_show(
    mbid: str,
    api_key: Optional[str] = None,
    timeout: int = 8
) -> Optional[Dict[str, Any]]:
    """
    Fetch the most recent show an artist played in North Carolina (stateCode='NC').
    Returns structured info including venue, city, tour name, and setlist link.
    """
    if not mbid:
        return None

    key = api_key or get_setlistfm_api_key()
    if not key:
        return None

    url = f"{SETLIST_FM_API_BASE_URL}/search/setlists"
    headers = _get_headers(key)
    params = {
        "artistMbid": mbid,
        "stateCode": "NC",
    }

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            return None
        data = resp.json()
        setlists = data.get("setlist", [])
        if not setlists:
            return None

        latest = setlists[0]
        venue = latest.get("venue", {})
        city = venue.get("city", {})
        tour = latest.get("tour", {})
        raw_date = latest.get("eventDate")

        # Count songs played
        song_count = 0
        sets = latest.get("sets", {}).get("set", [])
        for st in sets:
            song_count += len(st.get("song", []))

        return {
            "id": latest.get("id"),
            "event_date": raw_date,
            "date_formatted": _format_event_date(raw_date),
            "venue_name": venue.get("name"),
            "venue_id": venue.get("id"),
            "city": city.get("name"),
            "state": city.get("stateCode") or "NC",
            "tour_name": tour.get("name") if tour else None,
            "url": latest.get("url"),
            "info": latest.get("info"),
            "song_count": song_count,
            "total_nc_shows": data.get("total", len(setlists)),
        }
    except Exception as e:
        print(f"Setlist.fm fetch_last_nc_show error for {mbid}: {e}")
        return None


def fetch_co_performers_for_show(
    event_date: str,
    venue_id: str,
    artist_name: str,
    api_key: Optional[str] = None,
    timeout: int = 8
) -> List[str]:
    """
    Query Setlist.fm for all artists who performed at the specified venue on the given date,
    filtering out the target artist to find tour mates / co-performers / openers.
    """
    if not event_date or not venue_id:
        return []

    key = api_key or get_setlistfm_api_key()
    if not key:
        return []

    url = f"{SETLIST_FM_API_BASE_URL}/search/setlists"
    headers = _get_headers(key)
    params = {
        "date": event_date,
        "venueId": venue_id,
    }

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            return []
        data = resp.json()
        setlists = data.get("setlist", [])
        co_performers: List[str] = []
        target_norm = artist_name.strip().lower()

        for s in setlists:
            art = s.get("artist", {})
            name = art.get("name", "").strip()
            if name and name.lower() != target_norm and name not in co_performers:
                co_performers.append(name)

        return co_performers
    except Exception as e:
        print(f"Setlist.fm fetch_co_performers_for_show error for {event_date} / {venue_id}: {e}")
        return []


def _extract_coperformers_from_info(info_text: Optional[str], target_artist: str) -> List[str]:
    """Extract co-performing artists from show notes (e.g. 'Opening Act for My Chemical Romance')."""
    if not info_text:
        return []
    coperformers = []
    # Match patterns like 'Opening Act for X', 'Opened for X', 'With special guests X, Y', 'Support from X'
    patterns = [
        r"(?:opening\s+act\s+for|opened\s+for|support\s+for|direct\s+support\s+for|supporting)\s+([^.,;\n]+)",
        r"(?:with\s+special\s+guests?|special\s+guests?|with\s+support\s+from|joined\s+by|supported\s+by|support:\s*|with)\s+([^.,;\n]+)",
    ]
    for pat in patterns:
        m = re.search(pat, info_text, flags=re.IGNORECASE)
        if m:
            raw_match = m.group(1).strip()
            # Split by 'and', '&', or comma
            parts = re.split(r",|\s+and\s+|\s+&\s+", raw_match)
            for p in parts:
                cleaned = p.strip()
                if cleaned and cleaned.lower() != target_artist.lower() and len(cleaned) < 50:
                    if cleaned not in coperformers:
                        coperformers.append(cleaned)
    return coperformers


def fetch_last_tours(
    mbid: str,
    artist_name: str,
    api_key: Optional[str] = None,
    max_tours: int = 3,
    max_pages: int = 5,
    timeout: int = 8
) -> List[Dict[str, Any]]:
    """
    Find the last N distinct tours for an artist, discovering who they played with on each tour
    by sampling tour setlists and identifying co-bills, festival lineups, and openers.
    """
    if not mbid:
        return []

    key = api_key or get_setlistfm_api_key()
    if not key:
        return []

    headers = _get_headers(key)
    unique_tours: List[str] = []
    tour_sample_setlists: Dict[str, List[Dict[str, Any]]] = {}

    page = 1
    while len(unique_tours) < max_tours and page <= max_pages:
        url = f"{SETLIST_FM_API_BASE_URL}/artist/{mbid}/setlists"
        params = {"p": page}
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            if resp.status_code != 200:
                break
            data = resp.json()
            setlists = data.get("setlist", [])
            if not setlists:
                break

            for s in setlists:
                tour = s.get("tour")
                tour_name = tour.get("name", "").strip() if tour else ""
                if tour_name:
                    if tour_name not in unique_tours:
                        unique_tours.append(tour_name)
                        tour_sample_setlists[tour_name] = [s]
                    elif len(tour_sample_setlists[tour_name]) < 2:
                        tour_sample_setlists[tour_name].append(s)
            page += 1
        except Exception as e:
            print(f"Setlist.fm fetch_last_tours error on page {page}: {e}")
            break

    results = []
    for t_name in unique_tours[:max_tours]:
        sample_list = tour_sample_setlists.get(t_name, [])
        if not sample_list:
            continue
        primary_sample = sample_list[0]
        event_date = primary_sample.get("eventDate")
        venue = primary_sample.get("venue", {})
        venue_id = venue.get("id")
        city = venue.get("city", {})
        city_name = city.get("name", "")
        state_or_country = city.get("stateCode") or city.get("country", {}).get("name", "")
        location_str = f"{city_name}, {state_or_country}".strip(", ")
        info_note = primary_sample.get("info")

        # Discover co-performers
        played_with: List[str] = []

        # 1. Query venue bill on that date
        if event_date and venue_id:
            venue_coperformers = fetch_co_performers_for_show(
                event_date=event_date,
                venue_id=venue_id,
                artist_name=artist_name,
                api_key=key,
                timeout=timeout
            )
            for band in venue_coperformers:
                if band not in played_with:
                    played_with.append(band)

        # 2. Check second sample show if available and co-performers are still sparse
        if len(played_with) < 2 and len(sample_list) > 1:
            sec_sample = sample_list[1]
            sec_date = sec_sample.get("eventDate")
            sec_venue_id = sec_sample.get("venue", {}).get("id")
            if sec_date and sec_venue_id and sec_venue_id != venue_id:
                sec_coperformers = fetch_co_performers_for_show(
                    event_date=sec_date,
                    venue_id=sec_venue_id,
                    artist_name=artist_name,
                    api_key=key,
                    timeout=timeout
                )
                for band in sec_coperformers:
                    if band not in played_with:
                        played_with.append(band)

        # 3. Check notes for opener/headline mentions
        info_coperformers = _extract_coperformers_from_info(info_note, artist_name)
        for band in info_coperformers:
            if band not in played_with:
                played_with.append(band)

        # 4. Check guest artists on songs (sets.set[].song[].with)
        sets = primary_sample.get("sets", {}).get("set", [])
        for st in sets:
            for sng in st.get("song", []):
                guest = sng.get("with")
                if isinstance(guest, dict) and guest.get("name"):
                    g_name = guest.get("name").strip()
                    if g_name and g_name not in played_with and g_name.lower() != artist_name.lower():
                        played_with.append(f"{g_name} (Guest)")

        results.append({
            "tour_name": t_name,
            "sample_date": event_date,
            "sample_date_formatted": _format_event_date(event_date),
            "venue_name": venue.get("name"),
            "location": location_str,
            "setlist_url": primary_sample.get("url"),
            "played_with": played_with,
            "notes": info_note,
        })

    return results


def fetch_recent_setlists(
    mbid: str,
    api_key: Optional[str] = None,
    limit: int = 6,
    timeout: int = 8
) -> List[Dict[str, Any]]:
    """Fetch the most recent N concert setlists for an artist."""
    if not mbid:
        return []

    key = api_key or get_setlistfm_api_key()
    if not key:
        return []

    url = f"{SETLIST_FM_API_BASE_URL}/artist/{mbid}/setlists"
    headers = _get_headers(key)

    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            return []
        data = resp.json()
        raw_list = data.get("setlist", [])
        recent: List[Dict[str, Any]] = []

        for s in raw_list[:limit]:
            raw_date = s.get("eventDate")
            venue = s.get("venue", {})
            city = venue.get("city", {})
            tour = s.get("tour", {})

            # Count songs
            songs = []
            for st in s.get("sets", {}).get("set", []):
                for sng in st.get("song", []):
                    s_name = sng.get("name")
                    if s_name:
                        songs.append(s_name)

            recent.append({
                "id": s.get("id"),
                "event_date": raw_date,
                "date_formatted": _format_event_date(raw_date),
                "venue_name": venue.get("name"),
                "city": city.get("name"),
                "state_or_country": city.get("stateCode") or city.get("country", {}).get("name", ""),
                "tour_name": tour.get("name") if tour else None,
                "url": s.get("url"),
                "song_count": len(songs),
                "sample_songs": songs[:4],
                "info": s.get("info"),
            })

        return recent
    except Exception as e:
        print(f"Setlist.fm fetch_recent_setlists error for {mbid}: {e}")
        return []


def get_or_fetch_artist_setlist_data(
    artist: str,
    api_key: Optional[str] = None,
    force_refresh: bool = False,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get aggregated Setlist.fm artist intelligence from database cache if available,
    otherwise query Setlist.fm API and persist to database.
    """
    if not artist or not artist.strip():
        return {
            "has_key": bool(api_key or get_setlistfm_api_key()),
            "artist": "",
            "mbid": None,
            "last_nc_show": None,
            "last_3_tours": [],
            "recent_setlists": [],
            "total_concerts": 0,
            "url": None,
        }

    import database

    clean_artist = artist.strip()
    key = api_key or get_setlistfm_api_key()

    # 1. Check database cache if not forced refresh
    if not force_refresh:
        cached = database.get_artist_metadata(clean_artist, db_path=db_path)
        if cached and cached.get("setlistfm_data"):
            s_data = cached["setlistfm_data"]
            if isinstance(s_data, str):
                try:
                    s_data = json.loads(s_data)
                except Exception:
                    s_data = None
            if isinstance(s_data, dict) and s_data.get("mbid"):
                s_data["has_key"] = bool(key)
                s_data["cached"] = True
                return s_data

    if not key:
        return {
            "has_key": False,
            "artist": clean_artist,
            "mbid": None,
            "last_nc_show": None,
            "last_3_tours": [],
            "recent_setlists": [],
            "total_concerts": 0,
            "url": None,
            "cached": False,
        }

    # 2. Search artist
    art_info = search_artist(clean_artist, api_key=key)
    if not art_info or not art_info.get("mbid"):
        return {
            "has_key": True,
            "artist": clean_artist,
            "mbid": None,
            "last_nc_show": None,
            "last_3_tours": [],
            "recent_setlists": [],
            "total_concerts": 0,
            "url": None,
            "cached": False,
        }

    mbid = art_info["mbid"]
    official_name = art_info.get("name") or clean_artist
    setlist_url = art_info.get("url")

    # 3. Fetch NC show
    last_nc = fetch_last_nc_show(mbid, api_key=key)

    # 4. Fetch last 3 tours & co-performers
    tours = fetch_last_tours(mbid, official_name, api_key=key, max_tours=3)

    # 5. Fetch recent setlists
    recent_setlists = fetch_recent_setlists(mbid, api_key=key, limit=6)

    # 6. Total recorded concerts
    total_concerts = 0
    try:
        url = f"{SETLIST_FM_API_BASE_URL}/artist/{mbid}/setlists"
        resp = requests.get(url, headers=_get_headers(key), timeout=5)
        if resp.status_code == 200:
            total_concerts = resp.json().get("total", 0)
    except Exception:
        pass

    result: Dict[str, Any] = {
        "has_key": True,
        "artist": official_name,
        "mbid": mbid,
        "url": setlist_url,
        "last_nc_show": last_nc,
        "last_3_tours": tours,
        "recent_setlists": recent_setlists,
        "total_concerts": total_concerts,
        "cached": False,
    }

    # 7. Persist to database
    try:
        database.save_artist_metadata(
            clean_artist,
            setlistfm_data=result,
            db_path=db_path
        )
    except Exception as e:
        print(f"Database save_artist_metadata setlist error for {clean_artist}: {e}")

    return result
