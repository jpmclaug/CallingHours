"""Multi-Artist Top Tracks Playlist Curator for Calling Hours.

Provides high-performance multi-artist catalog aggregation, alternating
round-robin sequencing, and deep Gemini-powered thematic analysis mixing
with deterministic audio-feature and genre-tag heuristic fallbacks.
"""
from __future__ import annotations

import base64
import concurrent.futures
import html
import io
import json
import os
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple, Union

from PIL import Image, ImageDraw, ImageFont

try:
    from google import genai
except ImportError:
    genai = None

import database
import lastfm
import spotify
import theaudiodb

DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
DEFAULT_TIMEOUT = 8


def normalize_artist_name(name: str) -> str:
    """Clean and normalize artist name."""
    if not name:
        return ""
    clean = name.strip()
    return re.sub(r'\s+', ' ', clean)


def fetch_artist_top_tracks_pool(
    artist: str,
    limit: int = 10,
    user_email: Optional[str] = None,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Fetch top tracks for an artist from Spotify, Last.fm, or local database catalog.
    Enriches each track with Calling Hours analyses, TheAudioDB features, and Last.fm tags.
    """
    clean_artist = normalize_artist_name(artist)
    if not clean_artist:
        return []

    tracks: List[Dict[str, Any]] = []

    # 1. Try Spotify intelligence (cached or live API)
    try:
        spot_data = spotify.get_or_fetch_artist_spotify_data(
            clean_artist,
            user_email=user_email,
            force_refresh=False,
            db_path=db_path
        )
        if spot_data and spot_data.get("top_tracks"):
            for t in spot_data["top_tracks"][:limit]:
                tracks.append({
                    "artist": clean_artist,
                    "song": t.get("name", "").strip(),
                    "spotify_id": t.get("id") or "",
                    "spotify_url": t.get("spotify_url") or "",
                    "preview_url": t.get("preview_url") or "",
                    "album_name": t.get("album_name") or "",
                    "album_image": t.get("album_image") or "",
                    "release_year": t.get("release_year") or "",
                    "duration_ms": t.get("duration_ms") or 0,
                    "duration_formatted": t.get("duration_formatted") or "",
                    "popularity": t.get("popularity", 0),
                    "source": "spotify",
                })
    except Exception as e:
        print(f"Error fetching Spotify top tracks for {clean_artist}: {e}")

    # 2. If Spotify returned nothing or unconfigured, try Last.fm
    if not tracks:
        try:
            lastfm_key = getattr(lastfm, "get_lastfm_api_key", lambda: None)()
            lfm_tracks = lastfm.fetch_artist_top_tracks(clean_artist, api_key=lastfm_key, limit=limit)
            if lfm_tracks:
                for t in lfm_tracks[:limit]:
                    s_name = t.get("name", "").strip()
                    if s_name:
                        tracks.append({
                            "artist": clean_artist,
                            "song": s_name,
                            "playcount": t.get("playcount", 0),
                            "listeners": t.get("listeners", 0),
                            "rank": t.get("rank", 0),
                            "source": "lastfm",
                        })
        except Exception as le:
            print(f"Error fetching Last.fm top tracks for {clean_artist}: {le}")

    # 3. If still empty, check local Calling Hours analyzed library & search history
    if not tracks:
        try:
            analyzed = database.get_analyzed_songs(artist=clean_artist, limit=limit, db_path=db_path)
            if analyzed:
                for a in analyzed:
                    tracks.append({
                        "artist": a.get("artist") or clean_artist,
                        "song": a.get("song", "").strip(),
                        "id": a.get("id"),
                        "search_id": a.get("id"),
                        "model_name": a.get("model_name"),
                        "source": "database_analyzed",
                    })
            else:
                band_songs = database.get_songs_by_band(clean_artist, db_path=db_path)
                for b in band_songs[:limit]:
                    tracks.append({
                        "artist": b.get("artist") or clean_artist,
                        "song": b.get("song", "").strip(),
                        "id": b.get("id"),
                        "search_id": b.get("id"),
                        "source": "database_history",
                    })
        except Exception as de:
            print(f"Error fetching database songs for {clean_artist}: {de}")

    # 4. Enrich tracks with existing Calling Hours lyrics analysis, tags, and audio features
    enriched: List[Dict[str, Any]] = []
    seen_titles = set()

    for t in tracks:
        s_title = t.get("song", "").strip()
        if not s_title:
            continue
        norm_key = s_title.lower()
        if norm_key in seen_titles:
            continue
        seen_titles.add(norm_key)

        item = dict(t)
        # Search DB for lyrics analysis or metadata
        try:
            db_search = database.get_search(clean_artist, s_title, db_path=db_path)
            if db_search:
                if not item.get("id"):
                    item["id"] = db_search.get("id")
                    item["search_id"] = db_search.get("id")
                item["is_analyzed"] = bool(db_search.get("analysis"))
                item["model_name"] = db_search.get("model_name") or item.get("model_name")
                item["analysis"] = db_search.get("analysis") or ""
                item["lyrics"] = db_search.get("lyrics") or ""
                if not item.get("track_tags") and db_search.get("track_tags"):
                    tags_raw = db_search["track_tags"]
                    if isinstance(tags_raw, str):
                        try:
                            item["track_tags"] = json.loads(tags_raw)
                        except Exception:
                            item["track_tags"] = []
                    else:
                        item["track_tags"] = tags_raw
                if not item.get("theaudiodb_data") and db_search.get("theaudiodb_data"):
                    adb_raw = db_search["theaudiodb_data"]
                    if isinstance(adb_raw, str):
                        try:
                            item["theaudiodb_data"] = json.loads(adb_raw)
                        except Exception:
                            item["theaudiodb_data"] = None
                    else:
                        item["theaudiodb_data"] = adb_raw
            else:
                item["is_analyzed"] = bool(item.get("source") == "database_analyzed")
        except Exception:
            pass

        enriched.append(item)

    return enriched[:limit]


def fetch_multi_artist_catalog(
    artists: List[str],
    limit_per_artist: int = 10,
    user_email: Optional[str] = None,
    db_path: Optional[str] = None
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Concurrently fetch top tracks for multiple artists using ThreadPoolExecutor.
    Returns a dict mapping artist name -> list of track dicts.
    """
    unique_artists = []
    seen = set()
    for a in artists:
        norm = normalize_artist_name(a)
        if norm and norm.lower() not in seen:
            seen.add(norm.lower())
            unique_artists.append(norm)

    if not unique_artists:
        return {}

    catalog: Dict[str, List[Dict[str, Any]]] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(unique_artists), 6)) as executor:
        future_to_artist = {
            executor.submit(
                fetch_artist_top_tracks_pool,
                art,
                limit=limit_per_artist,
                user_email=user_email,
                db_path=db_path
            ): art
            for art in unique_artists
        }

        for future in concurrent.futures.as_completed(future_to_artist):
            art = future_to_artist[future]
            try:
                tracks = future.result()
                catalog[art] = tracks
            except Exception as e:
                print(f"Error fetching catalog for {art}: {e}")
                catalog[art] = []

    # Preserve input ordering
    return {art: catalog.get(art, []) for art in unique_artists}


def mix_alternating(
    tracks_by_artist: Dict[str, List[Dict[str, Any]]],
    limit: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Fair round-robin sequence alternating tracks between each artist.
    Sequence: Artist 1 #1, Artist 2 #1, Artist N #1, Artist 1 #2, Artist 2 #2...
    Continues cycling until all artists are exhausted or limit is reached.
    """
    artists = list(tracks_by_artist.keys())
    if not artists:
        return []

    max_tracks = max(len(t) for t in tracks_by_artist.values()) if tracks_by_artist else 0
    playlist: List[Dict[str, Any]] = []

    for round_idx in range(max_tracks):
        for art_idx, art in enumerate(artists, 1):
            art_tracks = tracks_by_artist.get(art, [])
            if round_idx < len(art_tracks):
                track = dict(art_tracks[round_idx])
                track["mix_mode"] = "alternating"
                track["rotation_round"] = round_idx + 1
                track["artist_order"] = art_idx
                track["alternating_badge"] = f"Round {round_idx + 1} • {art}"
                playlist.append(track)
                if limit and len(playlist) >= limit:
                    return playlist

    return playlist


# ---------------------------------------------------------------------------
# Thematic Analysis & Theme Detection Engine
# ---------------------------------------------------------------------------

HEURISTIC_THEME_DEFINITIONS = [
    {
        "id": "catharsis",
        "name": "High-Energy Catharsis & Anthems",
        "icon": "⚡",
        "description": "High-velocity rhythms, raw vocal intensity, and driving guitars exploring cathartic emotional release.",
        "keywords": ["rock", "punk", "emo", "post-hardcore", "alternative", "energetic", "heavy", "fast", "anthem", "grunge"],
        "min_energy": 60,
        "min_bpm": 125,
    },
    {
        "id": "melancholy",
        "name": "Bittersweet Melancholy & Regret",
        "icon": "🌧️",
        "description": "Minor-key harmonic vulnerability, nostalgic longing, and introspective explorations of heartbreak and distance.",
        "keywords": ["sad", "melancholy", "ballad", "slow", "heartbreak", "breakup", "regret", "depress", "dark", "cry", "gloom"],
        "max_valence": 45,
    },
    {
        "id": "atmospheric",
        "name": "Atmospheric Introspection & Shimmer",
        "icon": "🌌",
        "description": "Reverberant textures, layered melodies, and contemplative dreamscapes creating intimate spatial depth.",
        "keywords": ["shoegaze", "dream pop", "ambient", "indie", "atmospheric", "reverb", "introspective", "space", "psychedelic", "chill"],
        "min_acousticness": 35,
    },
    {
        "id": "groove",
        "name": "Dynamic Rhythmic Resonance",
        "icon": "💃",
        "description": "Propulsive drum patterns, memorable hooks, and syncopated kinetic momentum that bridges styles seamlessly.",
        "keywords": ["dance", "groove", "rhythm", "pop", "beat", "funk", "upbeat", "bouncy"],
        "min_danceability": 50,
    },
    {
        "id": "anthems",
        "name": "Defining Signature Classics",
        "icon": "✦",
        "description": "Core catalog essentials and fan favorites that showcase the essential identity and hallmark sound of each artist.",
        "keywords": [],
    }
]


def _extract_track_descriptors(track: Dict[str, Any]) -> Tuple[List[str], Dict[str, Any]]:
    """Extract tags and audio features for heuristic matching."""
    tags: List[str] = []
    track_tags = track.get("track_tags") or []
    for t in track_tags:
        if isinstance(t, dict):
            tags.append((t.get("name") or "").lower())
        elif isinstance(t, str):
            tags.append(t.lower())

    audiodb = track.get("theaudiodb_data") or {}
    if isinstance(audiodb, str):
        try:
            audiodb = json.loads(audiodb)
        except Exception:
            audiodb = {}

    return tags, audiodb


def _score_track_for_theme(theme_def: Dict[str, Any], track: Dict[str, Any]) -> int:
    """Calculate match score for a track against a heuristic theme definition."""
    tags, audiodb = _extract_track_descriptors(track)
    title_lower = (track.get("song") or "").lower()
    lyrics_lower = (track.get("lyrics") or "").lower()[:500]
    analysis_lower = (track.get("analysis") or "").lower()[:500]
    score = 0

    # Keyword matching
    for kw in theme_def.get("keywords", []):
        if any(kw in t for t in tags):
            score += 3
        if kw in title_lower:
            score += 2
        if kw in analysis_lower:
            score += 2
        if kw in lyrics_lower:
            score += 1

    # Audio feature matching
    energy = audiodb.get("energy") or 0
    valence = audiodb.get("valence") or 0
    danceability = audiodb.get("danceability") or 0
    acousticness = audiodb.get("acousticness") or 0
    tempo = audiodb.get("tempo") or 0
    mood_str = (audiodb.get("mood") or "").lower()

    if theme_def["id"] == "catharsis":
        if energy >= theme_def.get("min_energy", 60):
            score += 4
        if tempo >= theme_def.get("min_bpm", 125):
            score += 3
        if any(w in mood_str for w in ["energy", "fast", "hard", "heavy", "upbeat"]):
            score += 4
    elif theme_def["id"] == "melancholy":
        if 0 < valence <= theme_def.get("max_valence", 45):
            score += 4
        if any(w in mood_str for w in ["sad", "dark", "melanchol", "gloom", "slow"]):
            score += 4
    elif theme_def["id"] == "atmospheric":
        if acousticness >= theme_def.get("min_acousticness", 35):
            score += 4
        if any(w in mood_str for w in ["mellow", "ambient", "calm", "relax"]):
            score += 4
    elif theme_def["id"] == "groove":
        if danceability >= theme_def.get("min_danceability", 50):
            score += 4
        if any(w in mood_str for w in ["dance", "groove"]):
            score += 4

    return score


def mix_thematic_heuristic(
    tracks_by_artist: Dict[str, List[Dict[str, Any]]],
    limit: Optional[int] = None
) -> Dict[str, Any]:
    """
    Deterministic fallback for thematic mixing based on audio characteristics,
    Last.fm community tags, and lyrical keyword heuristics.
    Clusters tracks by shared musical and emotional themes and interleaves artists.
    """
    artists = [a for a in tracks_by_artist.keys() if tracks_by_artist[a]]
    if not artists:
        return {
            "tracks": [],
            "themes": [],
            "playlist_title": "Multi-Artist Blend",
            "playlist_description": "Curated multi-artist playlist.",
            "curator_notes": ""
        }

    # Gather all candidate tracks
    all_candidate_tracks: List[Dict[str, Any]] = []
    for art in artists:
        for t in tracks_by_artist[art]:
            tr = dict(t)
            tr["artist"] = art
            all_candidate_tracks.append(tr)

    # Assign each track to its highest-scoring theme
    theme_buckets: Dict[str, List[Dict[str, Any]]] = {td["id"]: [] for td in HEURISTIC_THEME_DEFINITIONS}
    
    for tr in all_candidate_tracks:
        best_theme_id = "anthems"
        best_score = 0
        for td in HEURISTIC_THEME_DEFINITIONS[:-1]:  # exclude anthems fallback initially
            sc = _score_track_for_theme(td, tr)
            if sc > best_score:
                best_score = sc
                best_theme_id = td["id"]

        theme_buckets[best_theme_id].append(tr)

    # Filter to themes that have tracks
    active_theme_defs = [td for td in HEURISTIC_THEME_DEFINITIONS if theme_buckets.get(td["id"])]
    
    # If tracks are clustered into only 1 theme, distribute evenly to create a varied narrative arc
    if len(active_theme_defs) < 2 and len(all_candidate_tracks) >= 4:
        # Re-partition across catharsis, melancholy, and atmospheric
        theme_buckets = {td["id"]: [] for td in HEURISTIC_THEME_DEFINITIONS}
        for idx, tr in enumerate(all_candidate_tracks):
            assigned_td = HEURISTIC_THEME_DEFINITIONS[idx % 3]
            theme_buckets[assigned_td["id"]].append(tr)
        active_theme_defs = [td for td in HEURISTIC_THEME_DEFINITIONS if theme_buckets.get(td["id"])]

    ordered_tracks: List[Dict[str, Any]] = []
    themes_summary: List[Dict[str, Any]] = []

    # Sequence through themes and interleave artists within each theme
    for td in active_theme_defs:
        b_tracks = theme_buckets[td["id"]]
        if not b_tracks:
            continue

        # Group tracks within this theme by artist
        by_art: Dict[str, List[Dict[str, Any]]] = {}
        for tr in b_tracks:
            by_art.setdefault(tr["artist"], []).append(tr)

        # Interleave artists inside this theme
        theme_interleaved: List[Dict[str, Any]] = []
        max_t = max(len(l) for l in by_art.values())
        for r in range(max_t):
            for art in artists:
                if art in by_art and r < len(by_art[art]):
                    theme_interleaved.append(by_art[art][r])

        # Decorate each track with theme data
        theme_artists = list(by_art.keys())
        other_artists_str = " & ".join(a for a in artists if a not in theme_artists) or ("other tracks in the mix")

        for tr in theme_interleaved:
            other_artists = [a for a in artists if a != tr["artist"]]
            partner_art = other_artists[0] if other_artists else "catalog"
            note = f"Connects through {td['description'].lower().rstrip('.')} alongside {partner_art}."
            
            tr["theme"] = td["name"]
            tr["theme_icon"] = td["icon"]
            tr["theme_id"] = td["id"]
            tr["theme_description"] = td["description"]
            tr["connection_note"] = note
            tr["mix_mode"] = "thematic"
            ordered_tracks.append(tr)

        themes_summary.append({
            "id": td["id"],
            "name": td["name"],
            "icon": td["icon"],
            "description": td["description"],
            "track_count": len(theme_interleaved),
            "artists_represented": theme_artists,
        })

    if limit and len(ordered_tracks) > limit:
        ordered_tracks = ordered_tracks[:limit]

    # Format title & description
    art_parenthetical = ", ".join(artists)
    primary_theme = themes_summary[0]["name"] if themes_summary else "Thematic Harmony"
    playlist_title = f"{primary_theme} ({art_parenthetical})"
    playlist_desc = f"Thematically woven mix of top tracks from {art_parenthetical}, structured around {len(themes_summary)} distinct musical and emotional themes."

    return {
        "tracks": ordered_tracks,
        "themes": themes_summary,
        "playlist_title": playlist_title,
        "playlist_description": playlist_desc,
        "curator_notes": f"Explores shared sonic and lyrical dimensions across {art_parenthetical}.",
        "engine": "heuristic"
    }


def mix_thematic_gemini(
    tracks_by_artist: Dict[str, List[Dict[str, Any]]],
    gemini_api_key: str,
    model_name: Optional[str] = None,
    limit: Optional[int] = None
) -> Dict[str, Any]:
    """
    Deep lyrical, poetic, and audio analysis mixing powered by Google Gemini AI.
    Analyzes all candidate tracks, identifies shared cross-artist themes,
    structures a seamless narrative arc, and annotates each song with a connection note.
    Falls back to heuristic engine if Gemini call or JSON decoding fails.
    """
    if not genai or not gemini_api_key:
        return mix_thematic_heuristic(tracks_by_artist, limit=limit)

    artists = [a for a in tracks_by_artist.keys() if tracks_by_artist[a]]
    if len(artists) < 2:
        return mix_thematic_heuristic(tracks_by_artist, limit=limit)

    # Prepare catalog payload for Gemini
    track_lookup: Dict[Tuple[str, str], Dict[str, Any]] = {}
    track_summaries = []

    for art in artists:
        for tr in tracks_by_artist[art]:
            s_name = tr.get("song", "").strip()
            lookup_key = (art.lower(), s_name.lower())
            track_lookup[lookup_key] = tr

            tags, audiodb = _extract_track_descriptors(tr)
            tags_str = ", ".join(tags[:4]) if tags else "None"
            audio_info = []
            if audiodb.get("tempo"):
                audio_info.append(f"{audiodb['tempo']} BPM")
            if audiodb.get("key"):
                audio_info.append(f"Key: {audiodb['key']}")
            if audiodb.get("energy"):
                audio_info.append(f"Energy: {int(audiodb['energy'])}%")
            if audiodb.get("valence"):
                audio_info.append(f"Valence: {int(audiodb['valence'])}%")
            audio_str = ", ".join(audio_info) if audio_info else "Standard sonic profile"

            lyric_prev = (tr.get("lyrics") or "").strip()[:180].replace("\n", " ")
            analysis_prev = (tr.get("analysis") or "").strip()[:200].replace("\n", " ")

            summary = f'- Artist: "{art}" | Song: "{s_name}" | Tags: [{tags_str}] | Audio: [{audio_str}]'
            if lyric_prev:
                summary += f' | Lyrics excerpt: "{lyric_prev}"'
            if analysis_prev:
                summary += f' | Existing Analysis note: "{analysis_prev}"'
            track_summaries.append(summary)

    candidates_block = "\n".join(track_summaries)
    chosen_model = model_name or DEFAULT_GEMINI_MODEL

    prompt = f"""You are an elite music intelligence curator and poetic lyric analyst for Calling Hours.
You are given the top tracks from {len(artists)} distinct artists: {', '.join(artists)}.

Candidate Tracks:
{candidates_block}

Your Mission:
1. Deeply analyze the lyrical themes, emotional resonance, audio characteristics (energy, tempo, mood), and poetic motifs across these tracks.
2. Identify 2 to 4 compelling, overarching themes that authentically unite songs across these artists (e.g., "Bittersweet Nostalgia & Reckless Youth", "High-Octane Catharsis & Frustration", "Atmospheric Despair & Minor Key Resonance", "Vulnerable Self-Discovery").
   Crucial: Each theme should connect tracks from MULTIPLE artists so the artists converse with each other in the playlist.
3. Sequence the tracks into a narrative-driven playlist flow that naturally transitions between themes (e.g. from high-energy urgency into introspective atmospheric vulnerability, building to an emotional climax).
4. For every track, assign:
   - "theme": The name of the detected theme it belongs to (matching one of your identified themes)
   - "connection_note": An insightful 1-2 sentence explanation of why this song fits this theme and how its lyrical/musical spirit connects to the other artist(s).
5. Produce a short, punchy 2-4 word theme phrase describing the musical/emotional synergy WITHOUT artist names (e.g. "Passionate Hardcore", "Cathartic Crescendo", "Atmospheric Reckoning") and an insightful curator summary.

Respond ONLY with a valid JSON object matching this exact structure:
{{
  "playlist_theme": "Short 2-4 word theme phrase (e.g. 'Passionate Hardcore')",
  "playlist_title": "Short Theme Phrase",
  "playlist_description": "2-3 sentence overview of the playlist and thematic synergy between the artists.",
  "curator_notes": "Deep curator note on the lyrical and sonic chemistry between these bands.",
  "themes": [
    {{
      "name": "Theme Name",
      "description": "Brief explanation of this shared theme."
    }}
  ],
  "tracks": [
    {{
      "artist": "Exact Artist Name",
      "song": "Exact Song Title",
      "theme": "Theme Name",
      "connection_note": "1-2 sentence explanation of lyrical/musical connection."
    }}
  ]
}}
"""

    try:
        client = genai.Client(api_key=gemini_api_key)
        # Use interactions.create or models.generate_content
        text_resp = ""
        try:
            interaction = client.interactions.create(
                model=chosen_model,
                input=prompt
            )
            text_resp = interaction.output_text or ""
        except Exception:
            # Fallback to models.generate_content
            res = client.models.generate_content(
                model=chosen_model,
                contents=prompt
            )
            text_resp = res.text or ""

        if not text_resp:
            raise ValueError("Empty response from Gemini")

        # Strip code block wrappers if present
        cleaned_json = text_resp.strip()
        if cleaned_json.startswith("```"):
            cleaned_json = re.sub(r"^```(?:json)?\s*", "", cleaned_json)
            cleaned_json = re.sub(r"\s*```$", "", cleaned_json)

        data = json.loads(cleaned_json)
        raw_tracks = data.get("tracks", [])
        if not raw_tracks:
            raise ValueError("No tracks returned in Gemini JSON")

        ordered_tracks: List[Dict[str, Any]] = []
        themes_map = {t.get("name", ""): t.get("description", "") for t in data.get("themes", [])}
        theme_counts: Dict[str, int] = {}
        theme_artists: Dict[str, set] = {}

        for t_item in raw_tracks:
            t_art = t_item.get("artist", "").strip()
            t_sng = t_item.get("song", "").strip()
            th_name = t_item.get("theme", "Thematic Harmony").strip()
            c_note = t_item.get("connection_note", "").strip()

            # Find matching original rich track
            matched = track_lookup.get((t_art.lower(), t_sng.lower()))
            if not matched:
                # Fuzzy match on song title
                for (l_art, l_sng), obj in track_lookup.items():
                    if l_sng == t_sng.lower() or t_sng.lower() in l_sng or l_sng in t_sng.lower():
                        matched = obj
                        break

            tr_copy = dict(matched) if matched else {
                "artist": t_art,
                "song": t_sng,
                "source": "gemini_curated"
            }

            tr_copy["theme"] = th_name
            tr_copy["theme_description"] = themes_map.get(th_name, "")
            tr_copy["connection_note"] = c_note
            tr_copy["mix_mode"] = "thematic"
            ordered_tracks.append(tr_copy)

            theme_counts[th_name] = theme_counts.get(th_name, 0) + 1
            theme_artists.setdefault(th_name, set()).add(tr_copy["artist"])

        # Also add any tracks from the candidate pool that Gemini might have omitted
        seen_keys = {(t.get("artist", "").lower(), t.get("song", "").lower()) for t in ordered_tracks}
        for (l_art, l_sng), obj in track_lookup.items():
            if (l_art, l_sng) not in seen_keys:
                fallback_tr = dict(obj)
                fallback_tr["theme"] = "Extended Resonance"
                fallback_tr["connection_note"] = "Curated extension connecting the catalog's emotional palette."
                fallback_tr["mix_mode"] = "thematic"
                ordered_tracks.append(fallback_tr)
                theme_counts["Extended Resonance"] = theme_counts.get("Extended Resonance", 0) + 1
                theme_artists.setdefault("Extended Resonance", set()).add(fallback_tr["artist"])

        if limit and len(ordered_tracks) > limit:
            ordered_tracks = ordered_tracks[:limit]

        final_themes = []
        for th in data.get("themes", []):
            th_name = th.get("name", "")
            if th_name in theme_counts:
                final_themes.append({
                    "name": th_name,
                    "description": th.get("description", ""),
                    "track_count": theme_counts[th_name],
                    "artists_represented": sorted(list(theme_artists.get(th_name, [])))
                })

        # Format: <Short Theme> (<Band 1>, <Band 2>, ...)
        art_parenthetical = ", ".join(artists)
        raw_theme = (data.get("playlist_theme") or "").strip()
        if not raw_theme:
            raw_title = (data.get("playlist_title") or "Thematic Harmony").strip()
            if ":" in raw_title:
                parts = raw_title.split(":", 1)
                if any(a.lower() in parts[0].lower() for a in artists):
                    raw_theme = parts[1].strip()
                else:
                    raw_theme = parts[0].strip()
            else:
                raw_theme = re.sub(r"\s*\(.*?\)$", "", raw_title).strip()
        clean_theme = re.sub(r"\s*\(.*?\)$", "", raw_theme).strip() or "Thematic Harmony"
        final_playlist_title = f"{clean_theme} ({art_parenthetical})"

        return {
            "tracks": ordered_tracks,
            "themes": final_themes,
            "playlist_theme": clean_theme,
            "playlist_title": final_playlist_title,
            "playlist_description": data.get("playlist_description") or f"Gemini thematic lyric & audio blend for {art_parenthetical}.",
            "curator_notes": data.get("curator_notes") or "",
            "engine": "gemini",
            "model": chosen_model
        }

    except Exception as e:
        print(f"Gemini thematic mixing error, falling back to heuristic engine: {e}")
        return mix_thematic_heuristic(tracks_by_artist, limit=limit)


def mix_thematic(
    tracks_by_artist: Dict[str, List[Dict[str, Any]]],
    gemini_api_key: Optional[str] = None,
    model_name: Optional[str] = None,
    limit: Optional[int] = None
) -> Dict[str, Any]:
    """
    Main entry point for thematic mixing.
    Invokes Gemini when an API key is available, otherwise uses the heuristic engine.
    """
    if gemini_api_key and gemini_api_key.strip() and genai:
        return mix_thematic_gemini(
            tracks_by_artist,
            gemini_api_key=gemini_api_key.strip(),
            model_name=model_name,
            limit=limit
        )
    return mix_thematic_heuristic(tracks_by_artist, limit=limit)


# ---------------------------------------------------------------------------
# Spotify AI Cover Art Generator
# ---------------------------------------------------------------------------

def _generate_procedural_cover_art(
    playlist_name: str,
    artists: List[str],
    themes: Optional[List[Dict[str, Any]]] = None
) -> bytes:
    """Generate a clean, high-resolution 640x640 Spotify cover art using Pillow.
    
    Guarantees RGB JPEG output <= 250 KB (Spotify limit is 256 KB).
    """
    width, height = 640, 640
    img = Image.new("RGB", (width, height), (11, 30, 63))
    draw = ImageDraw.Draw(img)

    # 1. Subtle cosmic background gradient
    for y in range(height):
        ratio = y / height
        r = int(5 + (20 - 5) * ratio)
        g = int(10 + (45 - 10) * ratio)
        b = int(25 + (95 - 25) * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    # Concentric vinyl record ring / waveform accents
    center_x, center_y = width // 2, height // 2 - 20
    for radius in [180, 230, 280]:
        draw.arc(
            [center_x - radius, center_y - radius, center_x + radius, center_y + radius],
            start=0, end=360,
            fill=(40, 75, 130),
            width=1
        )

    # Accent decorative borders
    draw.rectangle([24, 24, width - 24, height - 24], outline=(165, 200, 255), width=2)
    draw.rectangle([28, 28, width - 28, height - 28], outline=(25, 70, 133), width=1)

    # Load font
    font_bold_path = "C:/Windows/Fonts/arialbd.ttf"
    try:
        if os.path.exists(font_bold_path):
            font_title = ImageFont.truetype(font_bold_path, 34)
            font_artists = ImageFont.truetype(font_bold_path, 20)
            font_badge = ImageFont.truetype(font_bold_path, 13)
        else:
            font_title = ImageFont.load_default()
            font_artists = ImageFont.load_default()
            font_badge = ImageFont.load_default()
    except Exception:
        font_title = ImageFont.load_default()
        font_artists = ImageFont.load_default()
        font_badge = ImageFont.load_default()

    # 2. Header badge
    badge_text = "CALLING HOURS  •  CURATED BLEND"
    draw.text((width // 2, 60), badge_text, fill=(165, 200, 255), font=font_badge, anchor="mm")

    # 3. Playlist title (extract short theme name if parenthetical exists)
    clean_name = playlist_name.strip()
    match = re.match(r"^(.*?)\s*\((.*?)\)$", clean_name)
    if match:
        main_title = match.group(1).strip()
        sub_artists = match.group(2).strip()
    else:
        main_title = clean_name
        sub_artists = ", ".join(artists)

    # Word wrap main title
    words = main_title.split()
    lines = []
    cur_line = []
    for w in words:
        cur_line.append(w)
        if len(" ".join(cur_line)) > 18:
            lines.append(" ".join(cur_line))
            cur_line = []
    if cur_line:
        lines.append(" ".join(cur_line))

    # Draw title
    y_start = 230 - (len(lines) * 22)
    for i, line in enumerate(lines[:3]):
        draw.text((width // 2, y_start + (i * 44)), line, fill=(255, 255, 255), font=font_title, anchor="mm")

    # 4. Accent divider line
    div_y = y_start + (len(lines[:3]) * 44) + 16
    draw.line([(width // 2 - 80, div_y), (width // 2 + 80, div_y)], fill=(90, 240, 165), width=2)

    # 5. Artists in parentheses
    art_disp = f"({sub_artists})"
    if len(art_disp) > 42:
        art_parts = sub_artists.split(",")
        mid = len(art_parts) // 2
        p1 = ", ".join(art_parts[:mid]).strip()
        p2 = ", ".join(art_parts[mid:]).strip()
        draw.text((width // 2, div_y + 36), f"({p1},", fill=(197, 184, 255), font=font_artists, anchor="mm")
        draw.text((width // 2, div_y + 64), f"{p2})", fill=(197, 184, 255), font=font_artists, anchor="mm")
    else:
        draw.text((width // 2, div_y + 38), art_disp, fill=(197, 184, 255), font=font_artists, anchor="mm")

    # 6. Bottom footer tag
    if themes:
        first_th = themes[0].get("name", "")
        if first_th:
            draw.text((width // 2, height - 60), f"THEME: {first_th.upper()}", fill=(165, 200, 255), font=font_badge, anchor="mm")

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def generate_playlist_cover_art(
    playlist_name: str,
    artists: List[str],
    themes: Optional[List[Dict[str, Any]]] = None,
    curator_notes: Optional[str] = None,
    gemini_api_key: Optional[str] = None
) -> Dict[str, Any]:
    """Generate custom 640x640 album cover artwork for Spotify.
    
    Uses Gemini native image model (gemini-3.1-flash-image) when configured,
    and falls back to procedural Pillow art.
    Guarantees valid JPEG <= 250 KB (Spotify limit is 256 KB).
    """
    clean_name = playlist_name.strip()
    match = re.match(r"^(.*?)\s*\((.*?)\)$", clean_name)
    theme_label = match.group(1).strip() if match else clean_name
    artists_str = ", ".join(artists[:5])
    theme_names = [t.get("name", "") for t in (themes or []) if t.get("name")]
    theme_desc = ", ".join(theme_names) if theme_names else theme_label

    jpeg_bytes: Optional[bytes] = None
    engine_used = "pillow"

    if gemini_api_key and genai:
        try:
            client = genai.Client(api_key=gemini_api_key.strip())
            prompt = (
                f"Square album cover art for a curated music playlist titled '{theme_label}' "
                f"featuring bands: {artists_str}. "
                f"Musical themes and mood: {theme_desc}. "
                f"Curator essence: {curator_notes or 'Intense emotion, melodic resonance, and powerful energy'}. "
                f"Visual style: striking modern album cover jacket, cinematic atmospheric lighting, dramatic contrast, "
                f"minimalist graphic design, vinyl LP aesthetics, 1:1 aspect ratio, high resolution. "
                f"Do not include watermarks or illegible text."
            )
            resp = client.models.generate_content(
                model="gemini-3.1-flash-image",
                contents=[prompt],
            )
            raw_img = None
            if resp.parts:
                for part in resp.parts:
                    if getattr(part, "inline_data", None) and part.inline_data.data:
                        raw_data = part.inline_data.data
                        if isinstance(raw_data, str):
                            raw_bytes = base64.b64decode(raw_data)
                        else:
                            raw_bytes = raw_data
                        raw_img = Image.open(io.BytesIO(raw_bytes))
                        break
                    elif hasattr(part, "as_image"):
                        raw_img = part.as_image()
                        break

            if raw_img:
                if raw_img.mode != "RGB":
                    raw_img = raw_img.convert("RGB")
                resized = raw_img.resize((640, 640), Image.Resampling.LANCZOS)
                
                # Compress under 250 KB (Spotify limit is 256 KB)
                for q in (85, 75, 65, 55):
                    buf = io.BytesIO()
                    resized.save(buf, format="JPEG", quality=q)
                    if len(buf.getvalue()) <= 250000:
                        jpeg_bytes = buf.getvalue()
                        engine_used = "gemini"
                        break
        except Exception as e:
            print(f"Gemini cover art generation notice (using procedural fallback): {e}")

    if not jpeg_bytes:
        jpeg_bytes = _generate_procedural_cover_art(playlist_name, artists, themes)
        engine_used = "pillow"

    b64_str = base64.b64encode(jpeg_bytes).decode("utf-8")
    return {
        "image_bytes": jpeg_bytes,
        "image_b64": b64_str,
        "data_url": f"data:image/jpeg;base64,{b64_str}",
        "engine": engine_used,
        "size_bytes": len(jpeg_bytes),
    }
