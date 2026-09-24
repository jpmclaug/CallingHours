"""Spotify API and Analytics Client for Calling Hours.

Provides helpers for Spotify OAuth 2.0 authentication, fetching listening history
(recently played tracks), top artists & tracks, currently playing playback status,
and computing rich listening habit analytics.
"""
from __future__ import annotations

import base64
import os
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

import requests

SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE_URL = "https://api.spotify.com/v1"

SPOTIFY_SCOPES = [
    "user-read-recently-played",
    "user-top-read",
    "user-read-playback-state",
    "user-read-currently-playing",
    "user-read-private",
    "user-read-email",
    "playlist-modify-public",
    "playlist-modify-private",
]

DEFAULT_TIMEOUT = 10


def get_spotify_credentials() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Retrieve Spotify Client ID, Client Secret, and optional Redirect URI from env or secrets."""
    # 1. Environment variables take precedence (standard for Docker / Cloud Run)
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.environ.get("SPOTIFY_REDIRECT_URI")

    # 2. Local secrets fallback
    if not client_id or not client_secret:
        try:
            import calling_hours_secrets
            if not client_id:
                client_id = getattr(calling_hours_secrets, "SPOTIFY_CLIENT_ID", None)
            if not client_secret:
                client_secret = getattr(calling_hours_secrets, "SPOTIFY_CLIENT_SECRET", None)
            if not redirect_uri:
                redirect_uri = getattr(calling_hours_secrets, "SPOTIFY_REDIRECT_URI", None)
        except (ImportError, AttributeError):
            pass

    client_id = client_id.strip() if client_id and str(client_id).strip() else None
    client_secret = client_secret.strip() if client_secret and str(client_secret).strip() else None
    redirect_uri = redirect_uri.strip() if redirect_uri and str(redirect_uri).strip() else None

    return client_id, client_secret, redirect_uri


def is_spotify_configured() -> bool:
    """Return True if Spotify credentials are configured."""
    client_id, client_secret, _ = get_spotify_credentials()
    return bool(client_id and client_secret)


def resolve_spotify_redirect_uri(host_header: Optional[str] = None, is_secure: bool = False) -> str:
    """Resolve redirect URI using explicit config, or dynamically from request host."""
    _, _, configured_uri = get_spotify_credentials()
    if configured_uri:
        return configured_uri

    proto = "https" if is_secure else "http"
    host = host_header or "127.0.0.1:8000"
    return f"{proto}://{host}/auth/spotify/callback"


def get_auth_url(redirect_uri: str, state: Optional[str] = None) -> str:
    """Generate Spotify OAuth authorization URL."""
    client_id, _, _ = get_spotify_credentials()
    if not client_id:
        raise ValueError("Spotify Client ID is not configured.")

    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": " ".join(SPOTIFY_SCOPES),
        "show_dialog": "true",
    }
    if state:
        params["state"] = state

    return f"{SPOTIFY_AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code_for_token(code: str, redirect_uri: str) -> Dict[str, Any]:
    """Exchange authorization code for Spotify access & refresh tokens."""
    client_id, client_secret, _ = get_spotify_credentials()
    if not client_id or not client_secret:
        raise ValueError("Spotify Client ID and Secret must be configured.")

    auth_header = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("utf-8")
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }

    resp = requests.post(SPOTIFY_TOKEN_URL, headers=headers, data=data, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def refresh_access_token(refresh_token: str) -> Dict[str, Any]:
    """Refresh an expired Spotify access token using the refresh token."""
    client_id, client_secret, _ = get_spotify_credentials()
    if not client_id or not client_secret:
        raise ValueError("Spotify Client ID and Secret must be configured.")

    auth_header = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("utf-8")
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }

    resp = requests.post(SPOTIFY_TOKEN_URL, headers=headers, data=data, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def get_valid_access_token(user_email: str, db_module: Any = None) -> Optional[str]:
    """Retrieve stored access token for user, automatically refreshing if expired or expiring soon."""
    if db_module is None:
        import database
        db_module = database

    token_rec = db_module.get_spotify_token(user_email)
    if not token_rec:
        return None

    access_token = token_rec.get("access_token")
    refresh_tok = token_rec.get("refresh_token")
    expires_at = token_rec.get("expires_at")

    # Check if expired or within 60 seconds of expiration
    is_expired = False
    if expires_at:
        try:
            if isinstance(expires_at, str):
                exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            elif isinstance(expires_at, datetime):
                exp_dt = expires_at
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            else:
                exp_dt = datetime.fromtimestamp(float(expires_at), tz=timezone.utc)
            now_dt = datetime.now(timezone.utc)
            if (exp_dt - now_dt).total_seconds() < 60:
                is_expired = True
        except Exception:
            is_expired = False

    if is_expired and refresh_tok:
        try:
            refreshed = refresh_access_token(refresh_tok)
            new_access_token = refreshed.get("access_token")
            new_refresh_token = refreshed.get("refresh_token") or refresh_tok
            expires_in = refreshed.get("expires_in", 3600)
            new_exp_dt = datetime.now(timezone.utc).timestamp() + expires_in
            new_exp_iso = datetime.fromtimestamp(new_exp_dt, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

            db_module.save_spotify_token(
                user_email=user_email,
                access_token=new_access_token,
                refresh_token=new_refresh_token,
                expires_at=new_exp_iso,
                spotify_user_id=token_rec.get("spotify_user_id"),
                spotify_display_name=token_rec.get("spotify_display_name"),
                spotify_profile_url=token_rec.get("spotify_profile_url"),
                spotify_image_url=token_rec.get("spotify_image_url"),
            )
            return new_access_token
        except Exception as e:
            print(f"Spotify token refresh error for {user_email}: {e}")
            return None

    return access_token


def fetch_user_profile(access_token: str) -> Dict[str, Any]:
    """Fetch current authenticated Spotify user profile."""
    url = f"{SPOTIFY_API_BASE_URL}/me"
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    # Extract primary avatar image
    images = data.get("images") or []
    image_url = ""
    if images and isinstance(images, list):
        image_url = images[0].get("url", "")

    external_urls = data.get("external_urls") or {}
    spotify_url = external_urls.get("spotify", "")

    return {
        "id": data.get("id", ""),
        "display_name": data.get("display_name") or data.get("id") or "Spotify User",
        "email": data.get("email", ""),
        "country": data.get("country", ""),
        "product": data.get("product", ""),
        "image_url": image_url,
        "spotify_url": spotify_url,
        "followers": (data.get("followers") or {}).get("total", 0),
    }


def format_duration(ms: Optional[int]) -> str:
    """Format milliseconds into MM:SS string."""
    if not ms or ms <= 0:
        return "0:00"
    total_seconds = int(ms) // 1000
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes}:{seconds:02d}"


def format_relative_time(played_at_str: str) -> str:
    """Format an ISO timestamp into a human friendly relative time (e.g. '15m ago', 'Today at 3:12 PM')."""
    if not played_at_str:
        return ""
    try:
        dt = datetime.fromisoformat(played_at_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = now - dt
        seconds = int(diff.total_seconds())

        if seconds < 0:
            return "Just now"
        elif seconds < 60:
            return "Just now"
        elif seconds < 3600:
            m = seconds // 60
            return f"{m}m ago"
        elif seconds < 86400:
            h = seconds // 3600
            return f"{h}h ago"
        elif seconds < 172800:
            return f"Yesterday at {dt.strftime('%I:%M %p').lstrip('0')}"
        elif seconds < 604800:
            d = seconds // 86400
            return f"{d}d ago"
        else:
            return dt.strftime("%b %d, %Y")
    except Exception:
        return played_at_str


def normalize_track_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a raw Spotify track item (from recently played or top tracks) into clean structure."""
    track = item.get("track") if "track" in item else item
    played_at = item.get("played_at", "")

    track_id = track.get("id") or ""
    name = track.get("name") or "Unknown Track"
    duration_ms = track.get("duration_ms") or 0
    popularity = track.get("popularity") or 0
    preview_url = track.get("preview_url") or ""

    # External URLs
    external_urls = track.get("external_urls") or {}
    spotify_url = external_urls.get("spotify") or f"https://open.spotify.com/track/{track_id}" if track_id else ""

    # Artists
    raw_artists = track.get("artists") or []
    artists_list = []
    artist_names = []
    for a in raw_artists:
        if isinstance(a, dict):
            a_name = a.get("name", "").strip()
            if a_name:
                artist_names.append(a_name)
                a_urls = a.get("external_urls") or {}
                artists_list.append({
                    "id": a.get("id", ""),
                    "name": a_name,
                    "spotify_url": a_urls.get("spotify", ""),
                })
    primary_artist = artist_names[0] if artist_names else "Unknown Artist"
    all_artists_str = ", ".join(artist_names) if artist_names else primary_artist

    # Album
    album = track.get("album") or {}
    album_name = album.get("name") or "Unknown Album"
    album_id = album.get("id") or ""
    album_images = album.get("images") or []
    album_image_url = ""
    if album_images and isinstance(album_images, list):
        album_image_url = album_images[0].get("url", "")
    release_date = album.get("release_date") or ""
    release_year = release_date[:4] if release_date else ""

    return {
        "track_id": track_id,
        "name": name,
        "artist": primary_artist,
        "artists": artists_list,
        "all_artists": all_artists_str,
        "album": album_name,
        "album_id": album_id,
        "album_image": album_image_url,
        "release_date": release_date,
        "release_year": release_year,
        "duration_ms": duration_ms,
        "duration_formatted": format_duration(duration_ms),
        "popularity": popularity,
        "preview_url": preview_url,
        "spotify_url": spotify_url,
        "played_at": played_at,
        "relative_time": format_relative_time(played_at),
    }


def fetch_recently_played(
    access_token: str,
    limit: int = 50,
    before: Optional[int] = None,
    after: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Fetch user recently played tracks from Spotify Web API."""
    url = f"{SPOTIFY_API_BASE_URL}/me/player/recently-played"
    headers = {"Authorization": f"Bearer {access_token}"}
    params: Dict[str, Any] = {"limit": min(max(limit, 1), 50)}
    if before:
        params["before"] = before
    elif after:
        params["after"] = after

    resp = requests.get(url, headers=headers, params=params, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("items") or []

    results = []
    for item in items:
        if isinstance(item, dict) and item.get("track"):
            results.append(normalize_track_item(item))

    return results


def fetch_top_tracks(
    access_token: str,
    limit: int = 20,
    time_range: str = "short_term"
) -> List[Dict[str, Any]]:
    """Fetch user top tracks (short_term: 4 weeks, medium_term: 6 months, long_term: years)."""
    valid_ranges = {"short_term", "medium_term", "long_term"}
    if time_range not in valid_ranges:
        time_range = "short_term"

    url = f"{SPOTIFY_API_BASE_URL}/me/top/tracks"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"limit": min(max(limit, 1), 50), "time_range": time_range}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        items = resp.json().get("items") or []
        return [normalize_track_item(item) for item in items if isinstance(item, dict)]
    except Exception as e:
        print(f"Spotify fetch top tracks error: {e}")
        return []


def fetch_top_artists(
    access_token: str,
    limit: int = 20,
    time_range: str = "short_term"
) -> List[Dict[str, Any]]:
    """Fetch user top artists with genre tagging."""
    valid_ranges = {"short_term", "medium_term", "long_term"}
    if time_range not in valid_ranges:
        time_range = "short_term"

    url = f"{SPOTIFY_API_BASE_URL}/me/top/artists"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"limit": min(max(limit, 1), 50), "time_range": time_range}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        items = resp.json().get("items") or []

        artists = []
        for a in items:
            if not isinstance(a, dict):
                continue
            images = a.get("images") or []
            image_url = images[0].get("url") if images else ""
            external_urls = a.get("external_urls") or {}
            artists.append({
                "id": a.get("id", ""),
                "name": a.get("name", "Unknown"),
                "genres": a.get("genres", []),
                "popularity": a.get("popularity", 0),
                "image_url": image_url,
                "spotify_url": external_urls.get("spotify", ""),
            })
        return artists
    except Exception as e:
        print(f"Spotify fetch top artists error: {e}")
        return []


def fetch_currently_playing(access_token: str) -> Optional[Dict[str, Any]]:
    """Fetch currently playing track details if active."""
    url = f"{SPOTIFY_API_BASE_URL}/me/player/currently-playing"
    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        resp = requests.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
        if resp.status_code == 204:
            return None
        resp.raise_for_status()
        data = resp.json()
        if not data or not data.get("item"):
            return None

        track = data["item"]
        normalized = normalize_track_item(track)
        progress_ms = data.get("progress_ms") or 0
        duration_ms = normalized["duration_ms"]
        progress_pct = int((progress_ms / duration_ms) * 100) if duration_ms > 0 else 0

        normalized["is_playing"] = bool(data.get("is_playing", False))
        normalized["progress_ms"] = progress_ms
        normalized["progress_formatted"] = format_duration(progress_ms)
        normalized["progress_percent"] = min(max(progress_pct, 0), 100)
        return normalized
    except Exception as e:
        print(f"Spotify fetch currently playing error: {e}")
        return None


def compute_analytics(
    tracks: List[Dict[str, Any]],
    top_artists: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """Compute rich listening analytics across history tracks and top artists."""
    if not tracks:
        return {
            "total_tracks": 0,
            "total_duration_ms": 0,
            "total_duration_formatted": "0m",
            "unique_artists_count": 0,
            "unique_albums_count": 0,
            "avg_popularity": 0,
            "popularity_vibe": "No data",
            "persona": "Quiet Observer",
            "persona_desc": "Start listening on Spotify to generate your listening persona!",
            "time_of_day": {
                "night": {"count": 0, "percent": 0, "label": "Late Night (12 AM - 6 AM)"},
                "morning": {"count": 0, "percent": 0, "label": "Morning (6 AM - 12 PM)"},
                "afternoon": {"count": 0, "percent": 0, "label": "Afternoon (12 PM - 5 PM)"},
                "evening": {"count": 0, "percent": 0, "label": "Evening (5 PM - 12 AM)"},
            },
            "day_of_week": {day: {"count": 0, "percent": 0} for day in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]},
            "top_artists": [],
            "top_albums": [],
            "release_eras": {era: {"count": 0, "percent": 0} for era in ["2020s", "2010s", "2000s", "1990s", "1980s", "Classic"]},
            "top_genres": [],
            "repeat_tracks": [],
        }

    total_tracks = len(tracks)
    total_duration_ms = sum(t.get("duration_ms", 0) for t in tracks)

    # Format total duration
    total_mins = total_duration_ms // 60000
    if total_mins >= 60:
        hrs = total_mins // 60
        mins = total_mins % 60
        total_duration_formatted = f"{hrs}h {mins}m"
    else:
        total_duration_formatted = f"{total_mins}m"

    # Unique counts
    artists_set = set()
    albums_set = set()
    artist_counts: Dict[str, Dict[str, Any]] = {}
    album_counts: Dict[str, Dict[str, Any]] = {}
    track_counts: Dict[str, Dict[str, Any]] = {}

    pop_sum = 0
    pop_count = 0

    # Time distributions
    tod_counts = {"night": 0, "morning": 0, "afternoon": 0, "evening": 0}
    day_counts = {"Mon": 0, "Tue": 0, "Wed": 0, "Thu": 0, "Fri": 0, "Sat": 0, "Sun": 0}
    day_abbrs = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    era_counts = {"2020s": 0, "2010s": 0, "2000s": 0, "1990s": 0, "1980s": 0, "Classic": 0}

    for t in tracks:
        a_name = t.get("artist") or "Unknown"
        alb_name = t.get("album") or "Unknown"
        alb_img = t.get("album_image") or ""
        t_name = t.get("name") or "Unknown"
        t_id = t.get("track_id") or ""

        artists_set.add(a_name)
        albums_set.add(f"{alb_name} - {a_name}")

        # Artist counts
        if a_name not in artist_counts:
            artist_counts[a_name] = {
                "artist": a_name,
                "count": 0,
                "image": alb_img,
                "spotify_url": t.get("artists", [{}])[0].get("spotify_url", "") if t.get("artists") else "",
            }
        artist_counts[a_name]["count"] += 1
        if alb_img and not artist_counts[a_name]["image"]:
            artist_counts[a_name]["image"] = alb_img

        # Album counts
        alb_key = f"{alb_name}___{a_name}"
        if alb_key not in album_counts:
            album_counts[alb_key] = {
                "album": alb_name,
                "artist": a_name,
                "image": alb_img,
                "count": 0,
            }
        album_counts[alb_key]["count"] += 1

        # Track repeats
        track_key = f"{t_name}___{a_name}"
        if track_key not in track_counts:
            track_counts[track_key] = {
                "name": t_name,
                "artist": a_name,
                "album_image": alb_img,
                "track_id": t_id,
                "spotify_url": t.get("spotify_url", ""),
                "count": 0,
            }
        track_counts[track_key]["count"] += 1

        # Popularity
        pop = t.get("popularity")
        if pop is not None and isinstance(pop, (int, float)):
            pop_sum += pop
            pop_count += 1

        # Time distribution from played_at
        p_at = t.get("played_at")
        if p_at:
            try:
                dt = datetime.fromisoformat(p_at.replace("Z", "+00:00"))
                h = dt.hour
                if 0 <= h < 6:
                    tod_counts["night"] += 1
                elif 6 <= h < 12:
                    tod_counts["morning"] += 1
                elif 12 <= h < 17:
                    tod_counts["afternoon"] += 1
                else:
                    tod_counts["evening"] += 1

                day_abbr = day_abbrs[dt.weekday()]
                day_counts[day_abbr] += 1
            except Exception:
                pass

        # Era / Decade
        year_str = t.get("release_year")
        if year_str and str(year_str).isdigit():
            y = int(year_str)
            if y >= 2020:
                era_counts["2020s"] += 1
            elif y >= 2010:
                era_counts["2010s"] += 1
            elif y >= 2000:
                era_counts["2000s"] += 1
            elif y >= 1990:
                era_counts["1990s"] += 1
            elif y >= 1980:
                era_counts["1980s"] += 1
            else:
                era_counts["Classic"] += 1
        else:
            era_counts["2020s"] += 1  # default fallback

    # Averages
    avg_pop = round(pop_sum / pop_count, 1) if pop_count > 0 else 0
    if avg_pop >= 70:
        pop_vibe = "Chart Hits & Mainstream Waves"
    elif avg_pop >= 50:
        pop_vibe = "Balanced & Widely Loved"
    elif avg_pop >= 30:
        pop_vibe = "Indie, Alt & Deep Cuts"
    else:
        pop_vibe = "Underground Gems & Rarities"

    # Listening Archetype Persona
    total_timed = sum(tod_counts.values()) or 1
    night_pct = tod_counts["night"] / total_timed
    morning_pct = tod_counts["morning"] / total_timed
    recent_era_pct = era_counts["2020s"] / total_tracks
    retro_pct = (era_counts["1990s"] + era_counts["1980s"] + era_counts["Classic"]) / total_tracks

    if night_pct >= 0.35:
        persona = "The Night Owl"
        persona_desc = "Your music thrives in the quiet midnight hours, exploring nocturnal sounds and moody reflections."
    elif morning_pct >= 0.35:
        persona = "The Dawn Riser"
        persona_desc = "Music jumpstarts your morning rhythm, powering your first coffees and daily focus."
    elif retro_pct >= 0.40:
        persona = "The Nostalgia Seeker"
        persona_desc = "You have an ear for timeless classics, 90s anthems, and golden-era songwriting."
    elif avg_pop < 40:
        persona = "The Crate Digger"
        persona_desc = "You steer away from mainstream charts, diving deep into niche, underground, and indie discographies."
    elif recent_era_pct >= 0.70:
        persona = "The Modern Wave"
        persona_desc = "You stay on the pulse of modern music, streaming the newest releases and current artists."
    elif len(artists_set) / max(total_tracks, 1) >= 0.75:
        persona = "The Eclectic Explorer"
        persona_desc = "Never sticking to just one sound, your listening spans across distinct voices and artists."
    else:
        persona = "The Dedicated Loyalist"
        persona_desc = "When you love an artist or album, you put it on repeat and immerse yourself completely."

    # Top artists ranking
    sorted_artists = sorted(artist_counts.values(), key=lambda x: x["count"], reverse=True)
    for a in sorted_artists:
        a["percent"] = round((a["count"] / total_tracks) * 100, 1)

    # Top albums ranking
    sorted_albums = sorted(album_counts.values(), key=lambda x: x["count"], reverse=True)
    for alb in sorted_albums:
        alb["percent"] = round((alb["count"] / total_tracks) * 100, 1)

    # Repeats
    repeat_list = [v for v in track_counts.values() if v["count"] > 1]
    repeat_list.sort(key=lambda x: x["count"], reverse=True)

    # Time of Day dict with labels & percents
    tod_result = {
        "night": {
            "count": tod_counts["night"],
            "percent": round((tod_counts["night"] / total_timed) * 100, 1),
            "label": "Late Night (12 AM - 6 AM)",
        },
        "morning": {
            "count": tod_counts["morning"],
            "percent": round((tod_counts["morning"] / total_timed) * 100, 1),
            "label": "Morning (6 AM - 12 PM)",
        },
        "afternoon": {
            "count": tod_counts["afternoon"],
            "percent": round((tod_counts["afternoon"] / total_timed) * 100, 1),
            "label": "Afternoon (12 PM - 5 PM)",
        },
        "evening": {
            "count": tod_counts["evening"],
            "percent": round((tod_counts["evening"] / total_timed) * 100, 1),
            "label": "Evening (5 PM - 12 AM)",
        },
    }

    # Day of Week dict with counts & percents
    total_days = sum(day_counts.values()) or 1
    dow_result = {
        d: {
            "count": day_counts[d],
            "percent": round((day_counts[d] / total_days) * 100, 1),
        }
        for d in day_abbrs
    }

    # Eras dict with counts & percents
    era_result = {
        era: {
            "count": era_counts[era],
            "percent": round((era_counts[era] / total_tracks) * 100, 1),
        }
        for era in ["2020s", "2010s", "2000s", "1990s", "1980s", "Classic"]
    }

    # Genres (aggregated from top artists if provided)
    genre_counts: Dict[str, int] = {}
    if top_artists:
        for ta in top_artists:
            for g in ta.get("genres", []):
                clean_g = g.title()
                genre_counts[clean_g] = genre_counts.get(clean_g, 0) + 1

    sorted_genres = sorted(genre_counts.items(), key=lambda x: x[1], reverse=True)[:12]
    top_genres = [{"genre": g, "count": c} for g, c in sorted_genres]

    return {
        "total_tracks": total_tracks,
        "total_duration_ms": total_duration_ms,
        "total_duration_formatted": total_duration_formatted,
        "unique_artists_count": len(artists_set),
        "unique_albums_count": len(albums_set),
        "avg_popularity": avg_pop,
        "popularity_vibe": pop_vibe,
        "persona": persona,
        "persona_desc": persona_desc,
        "time_of_day": tod_result,
        "day_of_week": dow_result,
        "top_artists": sorted_artists[:10],
        "top_albums": sorted_albums[:8],
        "release_eras": era_result,
        "top_genres": top_genres,
        "repeat_tracks": repeat_list[:5],
    }


def get_demo_sample_data() -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, Any]]:
    """Return realistic demo profile, listening history, and analytics for preview mode."""
    profile = {
        "id": "callinghours_demo",
        "display_name": "Calling Hours Explorer",
        "email": "demo@callinghours.app",
        "product": "premium",
        "image_url": "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=200&h=200&fit=crop",
        "spotify_url": "https://open.spotify.com",
        "followers": 142,
    }

    sample_tracks = [
        {
            "track_id": "demo_1",
            "name": "Bleed American",
            "artist": "Jimmy Eat World",
            "artists": [{"name": "Jimmy Eat World", "id": "jew", "spotify_url": "https://open.spotify.com"}],
            "all_artists": "Jimmy Eat World",
            "album": "Bleed American",
            "album_id": "alb_jew",
            "album_image": "https://upload.wikimedia.org/wikipedia/en/2/29/Jimmy_Eat_World_-_Bleed_American_album_cover.jpg",
            "release_date": "2001-07-24",
            "release_year": "2001",
            "duration_ms": 182000,
            "duration_formatted": "3:02",
            "popularity": 71,
            "preview_url": "",
            "spotify_url": "https://open.spotify.com",
            "played_at": "2026-09-24T12:05:00Z",
            "relative_time": "10m ago",
        },
        {
            "track_id": "demo_2",
            "name": "Kisses",
            "artist": "Slowdive",
            "artists": [{"name": "Slowdive", "id": "slowdive", "spotify_url": "https://open.spotify.com"}],
            "all_artists": "Slowdive",
            "album": "everything is alive",
            "album_id": "alb_sd",
            "album_image": "https://upload.wikimedia.org/wikipedia/en/9/90/Slowdive_-_Everything_Is_Alive.png",
            "release_date": "2023-09-01",
            "release_year": "2023",
            "duration_ms": 236000,
            "duration_formatted": "3:56",
            "popularity": 64,
            "preview_url": "",
            "spotify_url": "https://open.spotify.com",
            "played_at": "2026-09-24T11:42:00Z",
            "relative_time": "33m ago",
        },
        {
            "track_id": "demo_3",
            "name": "Never Meant",
            "artist": "American Football",
            "artists": [{"name": "American Football", "id": "af", "spotify_url": "https://open.spotify.com"}],
            "all_artists": "American Football",
            "album": "American Football",
            "album_id": "alb_af",
            "album_image": "https://upload.wikimedia.org/wikipedia/en/e/e5/American_Football_album_cover.png",
            "release_date": "1999-09-14",
            "release_year": "1999",
            "duration_ms": 257000,
            "duration_formatted": "4:17",
            "popularity": 66,
            "preview_url": "",
            "spotify_url": "https://open.spotify.com",
            "played_at": "2026-09-24T10:15:00Z",
            "relative_time": "2h ago",
        },
        {
            "track_id": "demo_4",
            "name": "The Middle",
            "artist": "Jimmy Eat World",
            "artists": [{"name": "Jimmy Eat World", "id": "jew", "spotify_url": "https://open.spotify.com"}],
            "all_artists": "Jimmy Eat World",
            "album": "Bleed American",
            "album_id": "alb_jew",
            "album_image": "https://upload.wikimedia.org/wikipedia/en/2/29/Jimmy_Eat_World_-_Bleed_American_album_cover.jpg",
            "release_date": "2001-07-24",
            "release_year": "2001",
            "duration_ms": 166000,
            "duration_formatted": "2:46",
            "popularity": 82,
            "preview_url": "",
            "spotify_url": "https://open.spotify.com",
            "played_at": "2026-09-24T09:50:00Z",
            "relative_time": "2h ago",
        },
        {
            "track_id": "demo_5",
            "name": "When the Sun Hits",
            "artist": "Slowdive",
            "artists": [{"name": "Slowdive", "id": "slowdive", "spotify_url": "https://open.spotify.com"}],
            "all_artists": "Slowdive",
            "album": "Souvlaki",
            "album_id": "alb_souvlaki",
            "album_image": "https://upload.wikimedia.org/wikipedia/en/7/77/Slowdive_-_Souvlaki.png",
            "release_date": "1993-05-17",
            "release_year": "1993",
            "duration_ms": 285000,
            "duration_formatted": "4:45",
            "popularity": 73,
            "preview_url": "",
            "spotify_url": "https://open.spotify.com",
            "played_at": "2026-09-23T22:30:00Z",
            "relative_time": "Yesterday",
        },
        {
            "track_id": "demo_6",
            "name": "Disenchanted",
            "artist": "My Chemical Romance",
            "artists": [{"name": "My Chemical Romance", "id": "mcr", "spotify_url": "https://open.spotify.com"}],
            "all_artists": "My Chemical Romance",
            "album": "The Black Parade",
            "album_id": "alb_tbp",
            "album_image": "https://upload.wikimedia.org/wikipedia/en/e/ea/Theblackparadecover.jpg",
            "release_date": "2006-10-23",
            "release_year": "2006",
            "duration_ms": 295000,
            "duration_formatted": "4:55",
            "popularity": 70,
            "preview_url": "",
            "spotify_url": "https://open.spotify.com",
            "played_at": "2026-09-23T20:10:00Z",
            "relative_time": "Yesterday",
        },
        {
            "track_id": "demo_7",
            "name": "Calling Hours",
            "artist": "Bane",
            "artists": [{"name": "Bane", "id": "bane", "spotify_url": "https://open.spotify.com"}],
            "all_artists": "Bane",
            "album": "Don't Wait Up",
            "album_id": "alb_dwu",
            "album_image": "https://upload.wikimedia.org/wikipedia/en/3/30/Bane_-_Don%27t_Wait_Up.jpg",
            "release_date": "2014-05-13",
            "release_year": "2014",
            "duration_ms": 302000,
            "duration_formatted": "5:02",
            "popularity": 45,
            "preview_url": "",
            "spotify_url": "https://open.spotify.com",
            "played_at": "2026-09-23T19:00:00Z",
            "relative_time": "Yesterday",
        },
    ]

    sample_top_artists = [
        {"name": "Jimmy Eat World", "genres": ["emo", "alternative rock", "pop punk", "post-grunge"], "popularity": 74},
        {"name": "Slowdive", "genres": ["shoegaze", "dream pop", "indie rock"], "popularity": 69},
        {"name": "American Football", "genres": ["midwest emo", "math rock", "indie rock"], "popularity": 65},
        {"name": "Bane", "genres": ["hardcore punk", "melodic hardcore"], "popularity": 42},
    ]

    analytics = compute_analytics(sample_tracks, sample_top_artists)
    return profile, sample_tracks, analytics


def search_track(access_token: str, artist: str, song: str) -> Optional[Dict[str, Any]]:
    """Search Spotify for a track by artist and song title."""
    clean_artist = artist.strip()
    clean_song = song.strip()
    if not clean_artist or not clean_song:
        return None

    headers = {"Authorization": f"Bearer {access_token}"}
    query = f'track:"{clean_song}" artist:"{clean_artist}"'
    url = f"{SPOTIFY_API_BASE_URL}/search"
    params = {"q": query, "type": "track", "limit": 1}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=DEFAULT_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("tracks", {}).get("items", [])
            if items:
                item = items[0]
                album_imgs = item.get("album", {}).get("images", [])
                img_url = album_imgs[0].get("url") if album_imgs else ""
                artists = [a.get("name", "") for a in item.get("artists", [])]
                return {
                    "id": item.get("id"),
                    "uri": item.get("uri"),
                    "name": item.get("name"),
                    "artist": ", ".join(artists),
                    "album": item.get("album", {}).get("name", ""),
                    "album_image_url": img_url,
                    "duration_ms": item.get("duration_ms"),
                    "preview_url": item.get("preview_url"),
                    "spotify_url": item.get("external_urls", {}).get("spotify", ""),
                }

        # Fallback without strict quotes
        fallback_query = f"{clean_song} {clean_artist}"
        resp = requests.get(url, headers=headers, params={"q": fallback_query, "type": "track", "limit": 1}, timeout=DEFAULT_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("tracks", {}).get("items", [])
            if items:
                item = items[0]
                album_imgs = item.get("album", {}).get("images", [])
                img_url = album_imgs[0].get("url") if album_imgs else ""
                artists = [a.get("name", "") for a in item.get("artists", [])]
                return {
                    "id": item.get("id"),
                    "uri": item.get("uri"),
                    "name": item.get("name"),
                    "artist": ", ".join(artists),
                    "album": item.get("album", {}).get("name", ""),
                    "album_image_url": img_url,
                    "duration_ms": item.get("duration_ms"),
                    "preview_url": item.get("preview_url"),
                    "spotify_url": item.get("external_urls", {}).get("spotify", ""),
                }
    except Exception as e:
        print(f"Spotify search_track error for '{clean_artist} - {clean_song}': {e}")
    return None


def create_playlist(
    access_token: str,
    user_id: Optional[str] = None,
    name: str = "Calling Hours Playlist",
    description: str = "",
    public: bool = False
) -> Dict[str, Any]:
    """Create a new playlist on Spotify."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "name": name,
        "description": description or "Generated with Calling Hours Lyric Intelligence",
        "public": public
    }

    # /v1/me/playlists is standard in current Spotify Web API
    url = f"{SPOTIFY_API_BASE_URL}/me/playlists"
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=DEFAULT_TIMEOUT)
        if resp.status_code not in (200, 201) and user_id:
            url = f"{SPOTIFY_API_BASE_URL}/users/{user_id}/playlists"
            resp = requests.post(url, headers=headers, json=payload, timeout=DEFAULT_TIMEOUT)
        if resp.status_code in (200, 201):
            data = resp.json()
            return {
                "id": data.get("id"),
                "name": data.get("name"),
                "uri": data.get("uri"),
                "url": data.get("external_urls", {}).get("spotify", ""),
            }
        raise RuntimeError(f"Spotify API create playlist error ({resp.status_code}): {resp.text}")
    except Exception as e:
        print(f"Spotify create_playlist error: {e}")
        raise


def add_tracks_to_playlist(access_token: str, playlist_id: str, track_uris: List[str]) -> int:
    """Add a list of track URIs to a Spotify playlist in chunks of up to 100."""
    if not track_uris:
        return 0
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    url = f"{SPOTIFY_API_BASE_URL}/playlists/{playlist_id}/tracks"
    total_added = 0
    batch_size = 100

    for i in range(0, len(track_uris), batch_size):
        chunk = track_uris[i:i + batch_size]
        try:
            resp = requests.post(url, headers=headers, json={"uris": chunk}, timeout=DEFAULT_TIMEOUT)
            if resp.status_code in (200, 201):
                total_added += len(chunk)
            else:
                print(f"Spotify add_tracks batch error ({resp.status_code}): {resp.text}")
        except Exception as e:
            print(f"Spotify add_tracks request exception: {e}")
    return total_added


def export_songs_to_spotify_playlist(
    access_token: str,
    user_id: Optional[str],
    playlist_name: str,
    songs: List[Dict[str, Any]],
    description: str = ""
) -> Dict[str, Any]:
    """Resolve track URIs and generate a playlist directly in Spotify."""
    playlist_meta = create_playlist(access_token, user_id, playlist_name, description=description)
    playlist_id = playlist_meta["id"]

    resolved_uris: List[str] = []
    matched_count = 0
    unmatched: List[str] = []

    for item in songs:
        artist = item.get("artist", "")
        song = item.get("song", "")
        # First check direct or audiodb spotify_id
        spotify_id = item.get("spotify_id")
        if not spotify_id and item.get("theaudiodb_data"):
            audiodb = item["theaudiodb_data"]
            if isinstance(audiodb, str):
                try:
                    audiodb = json.loads(audiodb)
                except Exception:
                    audiodb = {}
            if isinstance(audiodb, dict):
                spotify_id = audiodb.get("spotify_id")

        if spotify_id and str(spotify_id).strip():
            resolved_uris.append(f"spotify:track:{str(spotify_id).strip()}")
            matched_count += 1
            continue

        # Otherwise search track
        track_res = search_track(access_token, artist, song)
        if track_res and track_res.get("uri"):
            resolved_uris.append(track_res["uri"])
            matched_count += 1
        else:
            unmatched.append(f"{artist} - {song}")

    # Remove duplicates preserving order
    seen = set()
    unique_uris = []
    for u in resolved_uris:
        if u not in seen:
            seen.add(u)
            unique_uris.append(u)

    added_count = add_tracks_to_playlist(access_token, playlist_id, unique_uris)
    return {
        "success": True,
        "playlist_id": playlist_id,
        "playlist_name": playlist_meta.get("name"),
        "playlist_url": playlist_meta.get("url"),
        "tracks_requested": len(songs),
        "tracks_matched": matched_count,
        "tracks_added": added_count,
        "unmatched": unmatched,
    }
