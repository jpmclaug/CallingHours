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
            base_name = re.sub(r'[\s\-_]+(?:617|\d{3,4}|\([^\)]+\))$', '', clean_name, flags=re.IGNORECASE).strip()
            if base_name and base_name.lower() != clean_name.lower():
                return search_artist(base_name, api_key=key, timeout=timeout)
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
                if _is_valid_band_name(name):
                    co_performers.append(name)

        return co_performers
    except Exception as e:
        print(f"Setlist.fm fetch_co_performers_for_show error for {event_date} / {venue_id}: {e}")
        return []


def _is_valid_band_name(candidate: Optional[str]) -> bool:
    """Validate that candidate string is a plausible band/artist name and not noise or instrument credit."""
    if not candidate or not isinstance(candidate, str):
        return False
    clean = candidate.strip()
    if len(clean) < 2 or len(clean) > 55:
        return False
    c_lower = clean.lower()

    # Generic or non-artist terms
    invalid_words = {
        "more", "others", "tba", "tbd", "various artists", "special guests", "special guest",
        "openers", "opener", "opening act", "support", "guest", "guests", "full band",
        "acoustic set", "acoustic", "strings", "horns", "choir", "crowd", "interlude", "encore",
        "soundcheck", "solo", "unknown", "various", "and more", "plus more", "rythm guitar",
        "rhythm guitar", "lead guitar", "bass guitar", "keys", "drums", "tour", "live", "band"
    }
    if c_lower in invalid_words:
        return False

    # Instrument / performance clauses (e.g., "Robin Vining on keys", "rythm guitar")
    instrument_patterns = [
        r'\bon\s+(?:keys|keyboards?|guitar|bass|drums?|piano|horns?|sax|saxophone|trumpet|percussion|vocals?|harmonica|organ)\b',
        r'\b(?:rhythm|rythm|lead|bass|acoustic|electric|pedal\s+steel)\s+guitar\b',
        r'\b(?:backing\s+vocals?|guest\s+vocals?|lead\s+vocals?)\b',
        r'\b(?:string\s+quartet|brass\s+section|horn\s+section|choir)\b',
    ]
    for pat in instrument_patterns:
        if re.search(pat, c_lower):
            return False

    # Phrases starting with prepositions, verbs, or conjunctions
    if re.match(r'^(?:playing|singing|performing|featuring|feat\.?|joined\s+by|supported\s+by|with|and|or)\b', c_lower):
        return False

    return True


def _extract_coperformers_from_info(info_text: Optional[str], target_artist: str) -> List[str]:
    """Extract co-performing artists from show notes."""
    if not info_text:
        return []
    coperformers = []
    target_norm = target_artist.strip().lower()
    patterns = [
        r"(?:co-headlin\w*\s+with|co-headlin\w*\s+tour\s+with|co-bill\w*\s+with)\s+([^.,;\n]+)",
        r"(?:opening\s+act\s+for|opened\s+for|openers?:\s*|support\s+for|direct\s+support\s+for|supporting)\s+([^.,;\n]+)",
        r"(?:with\s+special\s+guests?|special\s+guests?:\s*|with\s+support\s+from|joined\s+by|supported\s+by|support:\s*)\s+([^.,;\n]+)",
        r"(?:alongside|sharing\s+the\s+stage\s+with|tour\s+lineup:\s*|bill:\s*)\s+([^.,;\n]+)",
        r"(?:\bwith)\s+([^.,;\n]+)",
    ]
    for pat in patterns:
        m = re.search(pat, info_text, flags=re.IGNORECASE)
        if m:
            raw_match = m.group(1).strip()
            # Split by 'and', '&', or comma
            parts = re.split(r",|\s+and\s+|\s+&\s+|\s*\+\s*", raw_match)
            for p in parts:
                cleaned = re.sub(r'[\(\)\[\]]', '', p).strip()
                if cleaned and cleaned.lower() != target_norm and target_norm not in cleaned.lower():
                    if _is_valid_band_name(cleaned) and cleaned not in coperformers:
                        coperformers.append(cleaned)
    return coperformers


def _extract_coperformers_from_tour_name(tour_name: Optional[str], target_artist: str) -> List[str]:
    """Extract co-headliner bands from multi-band tour names."""
    if not tour_name or not tour_name.strip():
        return []
    clean_tour = tour_name.strip()
    target_clean = target_artist.strip().lower()
    coperformers = []

    # Check for 'with X'
    with_match = re.search(r'\bwith\s+([^:/\n]+)', clean_tour, flags=re.IGNORECASE)
    if with_match:
        cand = with_match.group(1).strip()
        cand = re.sub(r'\s+(?:tour|live|anniversary|summer|spring|fall|winter|\d{4}).*$', '', cand, flags=re.IGNORECASE).strip()
        if cand and cand.lower() != target_clean and len(cand) > 1 and len(cand) < 50:
            if _is_valid_band_name(cand) and cand not in coperformers:
                coperformers.append(cand)

    # Check for co-headline split: 'A & B', 'A / B', 'A and B', 'A + B'
    base_title = re.split(r'[:–—]', clean_tour)[0].strip()
    parts = re.split(r'\s*(?:/|&|\band\b|\+)\s*', base_title, flags=re.IGNORECASE)
    if len(parts) >= 2:
        has_target = any(target_clean in p.lower() or p.lower() in target_clean for p in parts)
        if has_target:
            for p in parts:
                cleaned = re.sub(r'\s+(?:tour|live|co-headlin\w*|\d{4}).*$', '', p, flags=re.IGNORECASE).strip()
                if cleaned and cleaned.lower() != target_clean and target_clean not in cleaned.lower() and len(cleaned) > 1 and len(cleaned) < 50:
                    if _is_valid_band_name(cleaned) and cleaned not in coperformers:
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


def fetch_top_coperformers(
    mbid: str,
    artist_name: str,
    api_key: Optional[str] = None,
    max_pages: int = 5,
    timeout: int = 8
) -> List[Dict[str, Any]]:
    """
    Find the top 10 bands that have performed the most with an artist across their Setlist.fm history.
    Discovers co-performers from tour co-headliners, show notes/openers, guest song appearances,
    and venue billings, tracking shows shared, tours shared, roles, and recency.
    """
    if not mbid:
        return []

    key = api_key or get_setlistfm_api_key()
    if not key:
        return []

    headers = _get_headers(key)
    target_clean = artist_name.strip()
    target_norm = target_clean.lower()

    band_stats: Dict[str, Dict[str, Any]] = {}
    sampled_venues: set = set()
    venue_searches_run = 0
    max_venue_searches = 6

    page = 1
    while page <= max_pages:
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
                event_date = s.get("eventDate")
                venue = s.get("venue", {})
                venue_id = venue.get("id")
                venue_name = venue.get("name") or "Venue"
                city = venue.get("city", {})
                city_name = city.get("name", "")
                state_or_country = city.get("stateCode") or city.get("country", {}).get("name", "")
                location_str = f"{city_name}, {state_or_country}".strip(", ")
                tour = s.get("tour", {})
                tour_name = tour.get("name", "").strip() if tour else ""
                info_note = s.get("info") or ""
                setlist_url = s.get("url")
                date_formatted = _format_event_date(event_date) or event_date or "Unknown Date"
                event_year = event_date[-4:] if event_date and len(event_date) >= 4 and event_date[-4:].isdigit() else ""

                current_show_co_performers: Dict[str, str] = {}

                # 1. From tour name
                if tour_name:
                    tour_coperformers = _extract_coperformers_from_tour_name(tour_name, target_clean)
                    for b in tour_coperformers:
                        current_show_co_performers[b] = "Co-Headliner"

                # 2. From info note
                if info_note:
                    info_coperformers = _extract_coperformers_from_info(info_note, target_clean)
                    is_cohead = "co-headlin" in info_note.lower()
                    for b in info_coperformers:
                        if b not in current_show_co_performers:
                            current_show_co_performers[b] = "Co-Headliner" if is_cohead else "Tour Mate / Support"

                # 3. From guest artists on songs
                sets = s.get("sets", {}).get("set", [])
                for st in sets:
                    for sng in st.get("song", []):
                        guest = sng.get("with")
                        if isinstance(guest, dict) and guest.get("name"):
                            g_name = guest.get("name", "").strip()
                            if g_name and g_name.lower() != target_norm and _is_valid_band_name(g_name):
                                current_show_co_performers[g_name] = "Stage Guest"

                # 4. Sample venue co-performers
                if event_date and venue_id and venue_searches_run < max_venue_searches:
                    venue_key = f"{event_date}_{venue_id}"
                    if venue_key not in sampled_venues:
                        sampled_venues.add(venue_key)
                        venue_bands = fetch_co_performers_for_show(
                            event_date=event_date,
                            venue_id=venue_id,
                            artist_name=target_clean,
                            api_key=key,
                            timeout=timeout
                        )
                        venue_searches_run += 1
                        is_fest = "fest" in (tour_name + " " + venue_name + " " + info_note).lower()
                        for vb in venue_bands:
                            if vb not in current_show_co_performers and _is_valid_band_name(vb):
                                current_show_co_performers[vb] = "Festival Co-Bill" if is_fest else "Tour Mate"

                # Record all co-performers for this show
                for band_name, role in current_show_co_performers.items():
                    norm_k = band_name.strip().lower()
                    if not norm_k or norm_k == target_norm or not _is_valid_band_name(band_name):
                        continue
                    if norm_k not in band_stats:
                        band_stats[norm_k] = {
                            "name": band_name.strip(),
                            "shows_shared": 0,
                            "tours": set(),
                            "roles": set(),
                            "first_year": event_year,
                            "last_year": event_year,
                            "latest_show": {
                                "date": event_date,
                                "date_formatted": date_formatted,
                                "venue_name": venue_name,
                                "location": location_str,
                                "tour_name": tour_name or "Concert",
                                "url": setlist_url,
                            }
                        }

                    entry = band_stats[norm_k]
                    entry["shows_shared"] += 1
                    if tour_name:
                        entry["tours"].add(tour_name)
                    entry["roles"].add(role)
                    if event_year:
                        if not entry["first_year"] or event_year < entry["first_year"]:
                            entry["first_year"] = event_year
                        if not entry["last_year"] or event_year > entry["last_year"]:
                            entry["last_year"] = event_year

            page += 1
        except Exception as e:
            print(f"Setlist.fm fetch_top_coperformers error on page {page}: {e}")
            break

    if not band_stats:
        return []

    sorted_bands = sorted(
        band_stats.values(),
        key=lambda x: (x["shows_shared"], len(x["tours"]), x.get("last_year") or ""),
        reverse=True
    )

    top_10 = []
    for idx, b in enumerate(sorted_bands[:10]):
        roles = b["roles"]
        if "Co-Headliner" in roles:
            pri_role = "Co-Headliner"
        elif "Stage Guest" in roles and len(roles) == 1:
            pri_role = "Stage Guest"
        elif "Tour Mate / Support" in roles:
            pri_role = "Tour Mate / Support"
        elif "Festival Co-Bill" in roles:
            pri_role = "Festival Co-Bill"
        else:
            pri_role = "Tour Mate"

        y_first = b.get("first_year")
        y_last = b.get("last_year")
        if y_first and y_last:
            y_span = f"{y_first} - {y_last}" if y_first != y_last else f"{y_last}"
        elif y_last:
            y_span = f"{y_last}"
        else:
            y_span = "Recorded Shows"

        top_10.append({
            "rank": idx + 1,
            "band": b["name"],
            "shows_shared": b["shows_shared"],
            "tours_shared": len(b["tours"]),
            "tours": sorted(list(b["tours"])),
            "primary_role": pri_role,
            "years_active": y_span,
            "latest_show": b["latest_show"],
        })

    return top_10


def get_demo_top_coperformers(artist_name: str) -> List[Dict[str, Any]]:
    """Return realistic top 10 co-performers for preview mode."""
    clean = artist_name.strip() or "Jimmy Eat World"
    if "jimmy" in clean.lower():
        bands = [
            ("Manchester Orchestra", 34, 3, ["The Amplified Echoes Tour", "Summer Tour 2018", "Fall Tour 2021"], "Co-Headliner", "2018 - 2023", "Red Rocks Amphitheatre", "Morrison, CO"),
            ("The Starting Line", 26, 2, ["Bleed American Anniversary Tour", "Holiday Tour 2015"], "Tour Mate / Support", "2003 - 2022", "Starland Ballroom", "Sayreville, NJ"),
            ("Taking Back Sunday", 22, 2, ["Co-Headline US Tour", "Live in Chicago"], "Co-Headliner", "2004 - 2019", "Aragon Ballroom", "Chicago, IL"),
            ("Green Day", 18, 1, ["Pop Disaster Tour 2002"], "Tour Mate / Support", "2002 - 2017", "Shoreline Amphitheatre", "Mountain View, CA"),
            ("Weezer", 16, 2, ["Weezer & Jimmy Eat World US Tour", "Summer Stadium Tour"], "Co-Headliner", "2005 - 2021", "Madison Square Garden", "New York, NY"),
            ("Dashboard Confessional", 15, 2, ["Surviving The Truth Tour", "Acoustic Tour"], "Tour Mate / Support", "2011 - 2022", "The Orange Peel", "Asheville, NC"),
            ("The Promise Ring", 14, 1, ["Midwest Emo Showcase Tour"], "Tour Mate / Support", "1997 - 2000", "Metro Chicago", "Chicago, IL"),
            ("Paramore", 12, 1, ["Monumentour / Special Guests"], "Tour Mate / Support", "2008 - 2014", "PNC Music Pavilion", "Charlotte, NC"),
            ("Middle Kids", 11, 1, ["Amplify The Noise Tour"], "Tour Mate / Support", "2023", "Red Hat Amphitheater", "Raleigh, NC"),
            ("Sense Field", 10, 1, ["Clarity Tour 1999"], "Tour Mate / Support", "1999 - 2002", "Troubadour", "West Hollywood, CA"),
        ]
    else:
        bands = [
            ("Foo Fighters", 28, 3, ["Stadium World Tour", "Sonic Highways Tour", "Summer Festivals"], "Tour Mate / Support", "2014 - 2023", "Citi Field", "New York, NY"),
            ("Queens of the Stone Age", 22, 2, ["Villains World Tour", "Desert Rock Summit"], "Co-Headliner", "2017 - 2022", "The Forum", "Inglewood, CA"),
            ("Turnstile", 19, 2, ["Glow On World Tour", "Hardcore Revival Tour"], "Tour Mate / Support", "2021 - 2024", "Shrine Expo Hall", "Los Angeles, CA"),
            ("The Strokes", 16, 2, ["Global Stadium Tour", "All Points East"], "Co-Headliner", "2020 - 2023", "Victoria Park", "London, UK"),
            ("Idles", 14, 1, ["Crawler International Tour"], "Tour Mate / Support", "2022 - 2023", "Brixton Academy", "London, UK"),
            ("Fontaines D.C.", 13, 1, ["Skinty Fia Tour"], "Tour Mate / Support", "2022 - 2024", "Terminal 5", "New York, NY"),
            ("Manchester Orchestra", 12, 1, ["A Black Mile Across North America"], "Tour Mate / Support", "2018 - 2021", "Ryman Auditorium", "Nashville, TN"),
            ("Deftones", 11, 1, ["Dia De Los Deftones", "North American Tour"], "Tour Mate / Support", "2019 - 2022", "Petco Park", "San Diego, CA"),
            ("Jimmy Eat World", 10, 1, ["Amplify Tour", "Co-Headline Series"], "Co-Headliner", "2019 - 2023", "Red Rocks Amphitheatre", "Morrison, CO"),
            ("Blink-182", 9, 1, ["One More Time World Tour"], "Tour Mate / Support", "2023 - 2024", "PNC Arena", "Raleigh, NC"),
        ]

    demo_list = []
    for idx, (b_name, shows, t_cnt, t_names, role, yrs, venue, loc) in enumerate(bands):
        demo_list.append({
            "rank": idx + 1,
            "band": b_name,
            "shows_shared": shows,
            "tours_shared": t_cnt,
            "tours": t_names,
            "primary_role": role,
            "years_active": yrs,
            "latest_show": {
                "date": "2023-08-20",
                "date_formatted": "August 20, 2023",
                "venue_name": venue,
                "location": loc,
                "tour_name": t_names[0] if t_names else "Concert Tour",
                "url": "https://www.setlist.fm",
            }
        })
    return demo_list


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
            "most_played_with": [],
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
                mpw = s_data.get("most_played_with") or []
                demo_markers = {"foo fighters", "queens of the stone age", "turnstile", "the strokes"}
                has_demo_contamination = bool(key) and any(b.get("band", "").lower() in demo_markers for b in mpw[:3]) and clean_artist.lower() not in demo_markers
                has_noise = any(not _is_valid_band_name(b.get("band", "")) for b in mpw)
                if not (has_demo_contamination or has_noise):
                    return s_data

    if not key:
        return {
            "has_key": False,
            "artist": clean_artist,
            "mbid": None,
            "last_nc_show": None,
            "last_3_tours": [],
            "recent_setlists": [],
            "most_played_with": get_demo_top_coperformers(clean_artist),
            "total_concerts": 0,
            "url": None,
            "cached": False,
            "is_demo": True,
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
            "most_played_with": [],
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

    # 6. Fetch top 10 co-performers / bands played with most
    most_played_with = fetch_top_coperformers(mbid, official_name, api_key=key, max_pages=5)

    # Merge co-performers discovered across tours into most_played_with
    existing_bands_lower = {b["band"].strip().lower(): b for b in most_played_with if b.get("band")}
    for t in tours:
        t_name = t.get("tour_name", "Concert Tour")
        for b in t.get("played_with", []):
            b_clean = b.replace(" (Guest)", "").strip()
            if not _is_valid_band_name(b_clean) or b_clean.lower() == official_name.lower():
                continue
            b_lower = b_clean.lower()
            if b_lower in existing_bands_lower:
                entry = existing_bands_lower[b_lower]
                if t_name and t_name not in entry.get("tours", []):
                    entry.setdefault("tours", []).append(t_name)
                    entry["tours_shared"] = len(entry["tours"])
            else:
                new_entry = {
                    "rank": len(most_played_with) + 1,
                    "band": b_clean,
                    "shows_shared": 1,
                    "tours_shared": 1,
                    "tours": [t_name],
                    "primary_role": "Tour Mate" if "(Guest)" not in b else "Stage Guest",
                    "years_active": t.get("sample_date_formatted", "")[-4:] if t.get("sample_date_formatted") else "Recent Tours",
                    "latest_show": {
                        "date_formatted": t.get("sample_date_formatted", ""),
                        "venue_name": t.get("venue_name", ""),
                        "location": t.get("location", ""),
                        "tour_name": t_name,
                        "url": t.get("setlist_url", ""),
                    }
                }
                most_played_with.append(new_entry)
                existing_bands_lower[b_lower] = new_entry

    if most_played_with:
        most_played_with.sort(key=lambda x: (x.get("shows_shared", 0), x.get("tours_shared", 0)), reverse=True)
        for idx, b in enumerate(most_played_with[:10]):
            b["rank"] = idx + 1
        most_played_with = most_played_with[:10]
    elif not key:
        most_played_with = get_demo_top_coperformers(official_name)
    else:
        most_played_with = []

    # 7. Total recorded concerts
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
        "most_played_with": most_played_with,
        "total_concerts": total_concerts,
        "cached": False,
    }

    # 8. Persist to database
    try:
        database.save_artist_metadata(
            clean_artist,
            setlistfm_data=result,
            db_path=db_path
        )
    except Exception as e:
        print(f"Database save_artist_metadata setlist error for {clean_artist}: {e}")

    return result


def get_demo_average_setlist(artist_name: str, year: Union[str, int]) -> Dict[str, Any]:
    """Return realistic demo average setlist when API key is unavailable, offline, or testing."""
    clean_artist = artist_name.strip() or "Jimmy Eat World"
    clean_year = str(year).strip() or "2023"

    if "jimmy" in clean_artist.lower():
        songs = [
            ("Pain", 47, 47, 7.9),
            ("Sweetness", 47, 47, 3.0),
            ("Something Loud", 46, 47, 4.1),
            ("Just Tonight...", 45, 47, 4.7),
            ("For Me This Is Heaven", 44, 47, 5.2),
            ("Kill", 42, 47, 6.4),
            ("555", 38, 47, 8.0),
            ("Bleed American", 47, 47, 8.5),
            ("Lucky Denver Mint", 46, 47, 9.3),
            ("Big Casino", 35, 47, 9.3),
            ("A Praise Chorus", 47, 47, 10.9),
            ("Hear You Me", 47, 47, 12.2),
            ("Work", 47, 47, 12.6),
            ("Blister", 44, 47, 13.0),
            ("23", 47, 47, 14.8),
            ("The Middle", 47, 47, 16.6),
        ]
    elif "manchester" in clean_artist.lower():
        songs = [
            ("The Pride", 42, 45, 1.2),
            ("KeelRow", 40, 45, 2.4),
            ("Bed Head", 45, 45, 3.1),
            ("I Can Barely Breathe", 38, 45, 4.5),
            ("April Fool", 36, 45, 5.8),
            ("Pale Black Eye", 35, 45, 7.0),
            ("The Maze", 44, 45, 8.2),
            ("The Gold", 45, 45, 9.5),
            ("The Alien", 44, 45, 10.8),
            ("The Sunshine", 44, 45, 11.9),
            ("The Grocery", 43, 45, 13.2),
            ("Simple Math", 42, 45, 14.5),
            ("Shake It Out", 45, 45, 15.8),
            ("The Silence", 45, 45, 17.0),
        ]
    elif "blink" in clean_artist.lower():
        songs = [
            ("Anthem Part Two", 48, 50, 1.1),
            ("The Rock Show", 49, 50, 2.3),
            ("Family Reunion", 40, 50, 3.0),
            ("Man Overboard", 46, 50, 4.2),
            ("Feeling This", 50, 50, 5.4),
            ("Reckless Abandon", 42, 50, 6.7),
            ("Dumpweed", 44, 50, 8.0),
            ("MORE THAN YOU KNOW", 45, 50, 9.2),
            ("EDGING", 48, 50, 10.5),
            ("Dance With Me", 46, 50, 11.8),
            ("Stay Together for the Kids", 48, 50, 13.0),
            ("Down", 47, 50, 14.2),
            ("I Miss You", 50, 50, 15.5),
            ("What's My Age Again?", 50, 50, 16.7),
            ("First Date", 49, 50, 18.0),
            ("All the Small Things", 50, 50, 19.2),
            ("Dammit", 50, 50, 20.4),
            ("ONE MORE TIME", 48, 50, 21.6),
        ]
    else:
        titles = [
            "Intro Anthem", "Speed of Sound", "Broken Neon", "Static Waves",
            "Midnight Echo", "Electric Pulse", "Fading Pictures", "Hollow Bones",
            "Silver Lining", "Last Horizon", "Encore: Starlight", "Final Bow"
        ]
        songs = [(t, 24, 25, float(i + 1)) for i, t in enumerate(titles)]

    tracks = []
    for idx, (s_name, count, total, pos) in enumerate(songs, 1):
        tracks.append({
            "position": idx,
            "song": s_name,
            "artist": clean_artist,
            "play_count": count,
            "total_concerts": total,
            "play_ratio": round(count / total, 2) if total else 1.0,
            "avg_position": pos,
        })

    return {
        "artist": clean_artist,
        "year": clean_year,
        "mbid": None,
        "url": f"https://www.setlist.fm/stats/average-setlist/{urllib.parse.quote(clean_artist.lower())}.html?year={clean_year}",
        "total_concerts": tracks[0]["total_concerts"] if tracks else 0,
        "considered_concerts": tracks[0]["total_concerts"] if tracks else 0,
        "average_set_length": len(tracks),
        "tracks": tracks,
        "method": "demo",
        "is_demo": True,
    }


def fetch_average_setlist_by_year(
    artist_name: str,
    year: Union[str, int],
    mbid: Optional[str] = None,
    api_key: Optional[str] = None,
    force_refresh: bool = False,
    db_path: Optional[str] = None,
    timeout: int = 8
) -> Dict[str, Any]:
    """
    Fetch the authentic average tour setlist for an artist in a given year.
    Tries Setlist.fm web statistics page first, falling back to authenticated REST API
    setlist aggregation (frequency ranking and stage position order).
    Caches results in database to respect API rate limits.
    """
    if not artist_name or not str(year).strip():
        return {
            "artist": "",
            "year": str(year).strip(),
            "total_concerts": 0,
            "considered_concerts": 0,
            "average_set_length": 0,
            "tracks": [],
            "error": "Artist and year are required",
        }

    import database

    clean_artist = artist_name.strip()
    clean_year = str(year).strip()
    base_artist = re.sub(r'[\s\-_]+(?:617|\d{3,4}|\([^\)]+\))$', '', clean_artist, flags=re.IGNORECASE).strip()
    key = api_key or get_setlistfm_api_key()

    # 1. Check database cache unless refresh forced
    if not force_refresh:
        cached = database.get_cached_average_setlist(clean_artist, clean_year, db_path=db_path)
        if (not cached or not cached.get("tracks")) and base_artist and base_artist.lower() != clean_artist.lower():
            cached = database.get_cached_average_setlist(base_artist, clean_year, db_path=db_path)
        if cached and isinstance(cached, dict) and cached.get("tracks"):
            cached["cached"] = True
            return cached

    # 2. Check if no API key is available
    if not key:
        demo = get_demo_average_setlist(clean_artist, clean_year)
        demo["cached"] = False
        return demo

    # 3. Resolve artist MBID and Setlist.fm URL
    official_name = clean_artist
    setlist_url = None
    target_mbid = mbid

    # Check cached artist metadata first to save an API search call
    cached_meta = database.get_artist_metadata(clean_artist, db_path=db_path)
    if not cached_meta and base_artist and base_artist.lower() != clean_artist.lower():
        cached_meta = database.get_artist_metadata(base_artist, db_path=db_path)

    if cached_meta and cached_meta.get("setlistfm_data"):
        s_meta = cached_meta["setlistfm_data"]
        if isinstance(s_meta, dict) and s_meta.get("mbid"):
            target_mbid = s_meta.get("mbid")
            official_name = s_meta.get("artist") or clean_artist
            setlist_url = s_meta.get("url")

    if not target_mbid or not setlist_url:
        art_info = search_artist(clean_artist, api_key=key, timeout=timeout)
        if (not art_info or not art_info.get("mbid")) and base_artist and base_artist.lower() != clean_artist.lower():
            art_info = search_artist(base_artist, api_key=key, timeout=timeout)
        if art_info:
            target_mbid = art_info.get("mbid")
            official_name = art_info.get("name") or clean_artist
            setlist_url = art_info.get("url")

    if not target_mbid:
        if base_artist and base_artist.lower() != clean_artist.lower():
            return fetch_average_setlist_by_year(base_artist, clean_year, api_key=key, force_refresh=force_refresh, db_path=db_path, timeout=timeout)
        return get_demo_average_setlist(clean_artist, clean_year)

    # 4. Method 1: Web Scraping Setlist.fm stats/average-setlist
    if setlist_url:
        try:
            stats_url = setlist_url.replace("/setlists/", "/stats/average-setlist/") + f"?year={clean_year}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
            resp = requests.get(stats_url, headers=headers, timeout=timeout)
            if resp.status_code == 200 and resp.text:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(resp.text, "html.parser")
                song_links = soup.select(".setlistList li a.songLabel")
                if song_links:
                    considered_count = 0
                    total_count = 0
                    for p in soup.select("p, div.statsDescription, span"):
                        p_text = p.get_text()
                        m = re.search(r"only considered\s+(\d+)\s+of\s+(\d+)\s+setlists", p_text, flags=re.IGNORECASE)
                        if m:
                            considered_count = int(m.group(1))
                            total_count = int(m.group(2))
                            break

                    tracks = []
                    for idx, a in enumerate(song_links, 1):
                        s_name = a.get_text(strip=True)
                        if s_name:
                            tracks.append({
                                "position": idx,
                                "song": s_name,
                                "artist": official_name,
                                "play_count": considered_count or total_count or len(song_links),
                                "total_concerts": total_count or considered_count or len(song_links),
                                "play_ratio": 1.0,
                                "avg_position": float(idx),
                            })

                    if tracks:
                        result = {
                            "artist": official_name,
                            "year": clean_year,
                            "mbid": target_mbid,
                            "url": stats_url,
                            "total_concerts": total_count or len(tracks),
                            "considered_concerts": considered_count or len(tracks),
                            "average_set_length": len(tracks),
                            "tracks": tracks,
                            "method": "web",
                            "cached": False,
                        }
                        database.save_cached_average_setlist(clean_artist, clean_year, result, db_path=db_path)
                        if base_artist and base_artist.lower() != clean_artist.lower():
                            database.save_cached_average_setlist(base_artist, clean_year, result, db_path=db_path)
                        return result
        except Exception as we:
            print(f"Setlist.fm web stats retrieval error for {clean_artist} ({clean_year}): {we}")

    # 5. Method 2: Fallback to authenticated REST API aggregation
    try:
        api_headers = _get_headers(key)
        all_setlists = []
        url = f"{SETLIST_FM_API_BASE_URL}/search/setlists"
        params = {"artistMbid": target_mbid, "year": clean_year, "p": 1}

        r = requests.get(url, headers=api_headers, params=params, timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            total_shows_year = data.get("total", 0)
            page_setlists = data.get("setlist", [])
            all_setlists.extend(page_setlists)

            # If more shows exist, optionally fetch page 2
            if total_shows_year > 20 and len(all_setlists) < 40:
                try:
                    import time
                    time.sleep(0.5)  # Pace requests to respect Setlist.fm rate limit
                    r2 = requests.get(url, headers=api_headers, params={"artistMbid": target_mbid, "year": clean_year, "p": 2}, timeout=timeout)
                    if r2.status_code == 200:
                        all_setlists.extend(r2.json().get("setlist", []))
                except Exception:
                    pass

        if not all_setlists:
            # If this was a suffix variation (e.g. "Haywire 617"), try the base artist ("Haywire")
            if base_artist and base_artist.lower() != clean_artist.lower():
                fallback_res = fetch_average_setlist_by_year(
                    artist_name=base_artist,
                    year=clean_year,
                    api_key=key,
                    force_refresh=force_refresh,
                    db_path=db_path,
                    timeout=timeout
                )
                if fallback_res and fallback_res.get("tracks"):
                    database.save_cached_average_setlist(clean_artist, clean_year, fallback_res, db_path=db_path)
                    return fallback_res

            empty_result = {
                "artist": official_name,
                "year": clean_year,
                "mbid": target_mbid,
                "url": f"{setlist_url or 'https://www.setlist.fm'}",
                "total_concerts": 0,
                "considered_concerts": 0,
                "average_set_length": 0,
                "tracks": [],
                "method": "api",
                "cached": False,
                "notice": f"No concerts recorded for {official_name} in {clean_year} on Setlist.fm.",
            }
            return empty_result

        # Process setlists into song counts and stage positions
        from collections import defaultdict
        song_counts = defaultdict(int)
        song_positions = defaultdict(list)
        set_lengths = []
        valid_setlists_count = 0

        for s in all_setlists:
            sets = s.get("sets", {}).get("set", [])
            cur_set = []
            for st in sets:
                for sng in st.get("song", []):
                    name = sng.get("name")
                    if name and name.strip():
                        cur_set.append(name.strip())
            # Exclude empty or strikingly short snippet sets (< 5 songs)
            if len(cur_set) >= 5:
                valid_setlists_count += 1
                set_lengths.append(len(cur_set))
                for pos, name in enumerate(cur_set, 1):
                    song_counts[name] += 1
                    song_positions[name].append(pos)

        # Fallback for short sets (e.g. festivals)
        if not set_lengths and all_setlists:
            for s in all_setlists:
                sets = s.get("sets", {}).get("set", [])
                cur_set = []
                for st in sets:
                    for sng in st.get("song", []):
                        name = sng.get("name")
                        if name and name.strip():
                            cur_set.append(name.strip())
                if cur_set:
                    valid_setlists_count += 1
                    set_lengths.append(len(cur_set))
                    for pos, name in enumerate(cur_set, 1):
                        song_counts[name] += 1
                        song_positions[name].append(pos)

        if not song_counts:
            return {
                "artist": official_name,
                "year": clean_year,
                "mbid": target_mbid,
                "url": setlist_url,
                "total_concerts": len(all_setlists),
                "considered_concerts": 0,
                "average_set_length": 0,
                "tracks": [],
                "method": "api",
                "cached": False,
                "notice": f"Setlist details were not recorded for {official_name} in {clean_year}.",
            }

        avg_length = round(sum(set_lengths) / len(set_lengths)) if set_lengths else len(song_counts)
        target_track_count = max(8, min(avg_length, 30))

        # Sort songs by frequency (most played first)
        top_by_freq = sorted(
            song_counts.items(),
            key=lambda x: (x[1], -sum(song_positions[x[0]]) / len(song_positions[x[0]])),
            reverse=True
        )[:target_track_count]

        # Order selected songs by average stage position
        ordered_by_pos = sorted(top_by_freq, key=lambda x: sum(song_positions[x[0]]) / len(song_positions[x[0]]))

        tracks = []
        for idx, (s_name, count) in enumerate(ordered_by_pos, 1):
            avg_p = sum(song_positions[s_name]) / len(song_positions[s_name])
            ratio = round(count / valid_setlists_count, 2) if valid_setlists_count else 1.0
            tracks.append({
                "position": idx,
                "song": s_name,
                "artist": official_name,
                "play_count": count,
                "total_concerts": valid_setlists_count,
                "play_ratio": ratio,
                "avg_position": round(avg_p, 1),
            })

        result = {
            "artist": official_name,
            "year": clean_year,
            "mbid": target_mbid,
            "url": f"{setlist_url or 'https://www.setlist.fm'}",
            "total_concerts": len(all_setlists),
            "considered_concerts": valid_setlists_count,
            "average_set_length": len(tracks),
            "tracks": tracks,
            "method": "api",
            "cached": False,
        }

        database.save_cached_average_setlist(clean_artist, clean_year, result, db_path=db_path)
        if base_artist and base_artist.lower() != clean_artist.lower():
            database.save_cached_average_setlist(base_artist, clean_year, result, db_path=db_path)
        return result

    except Exception as ae:
        print(f"Setlist.fm API aggregation error for {clean_artist} ({clean_year}): {ae}")
        return get_demo_average_setlist(clean_artist, clean_year)


def get_artist_touring_years(
    artist_name: str,
    mbid: Optional[str] = None,
    api_key: Optional[str] = None,
    max_years: int = 12
) -> List[str]:
    """
    Return distinct touring years for an artist.
    Discovers years from recent Setlist.fm concerts, or provides a smart default list of recent years.
    """
    clean_artist = artist_name.strip()
    key = api_key or get_setlistfm_api_key()
    discovered_years: set = set()

    if key and clean_artist:
        try:
            target_mbid = mbid
            if not target_mbid:
                art_info = search_artist(clean_artist, api_key=key, timeout=5)
                if art_info:
                    target_mbid = art_info.get("mbid")

            if target_mbid:
                url = f"{SETLIST_FM_API_BASE_URL}/artist/{target_mbid}/setlists"
                resp = requests.get(url, headers=_get_headers(key), timeout=5)
                if resp.status_code == 200:
                    setlists = resp.json().get("setlist", [])
                    for s in setlists:
                        dt = s.get("eventDate")
                        if dt and len(dt) >= 4 and dt[-4:].isdigit():
                            discovered_years.add(dt[-4:])
        except Exception:
            pass

    current_year = datetime.now().year
    default_years = [str(y) for y in range(current_year, current_year - 15, -1)]

    all_years = sorted(list(discovered_years.union(default_years)), reverse=True)
    return all_years[:max_years]
