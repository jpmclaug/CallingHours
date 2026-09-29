"""Band Recommender Module for Calling Hours.

Generates personalized band recommendations and scene discovery based on user's 0-5 band ratings:
- Rating 5: Absolute Favorite (Primary recommendation driver, 3.5x weight)
- Rating 4: Really Enjoy (Strong recommendation driver, 2.5x weight)
- Rating 3: Likes (Positive recommendation driver, 1.2x weight)
- Rating 2: Is ok (Minor recommendation driver, 0.3x weight)
- Rating 1: Dislike (Excluded from seeds; recommendations matching disliked bands are suppressed)
- Rating 0: Know nothing about them (Unranked; treated as candidate for discovery)
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
from typing import Any, Dict, List, Optional, Set
import urllib.parse

import database
import lastfm

RATING_LABELS = {
    0: "Know nothing about them",
    1: "Dislike",
    2: "Is ok",
    3: "Likes",
    4: "Really enjoy them",
    5: "Absolute favorite",
}

RATING_WEIGHTS = {
    5: 3.5,
    4: 2.5,
    3: 1.2,
    2: 0.3,
    1: 0.0,
    0: 0.0,
}


def _get_lastfm_key() -> Optional[str]:
    return lastfm.get_lastfm_api_key()


def _fetch_similar_for_artist(
    artist: str,
    api_key: Optional[str] = None,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Fetch similar artists for a given artist using database cache first, then Last.fm."""
    clean_artist = artist.strip()
    if not clean_artist:
        return []

    # 1. Check cached artist metadata in database
    meta = database.get_artist_metadata(clean_artist, db_path=db_path)
    if meta and meta.get("similar_artists"):
        sim_list = meta["similar_artists"]
        if isinstance(sim_list, str):
            try:
                sim_list = json.loads(sim_list)
            except Exception:
                sim_list = []
        if isinstance(sim_list, list) and sim_list:
            formatted = []
            for item in sim_list:
                name = item.get("name") if isinstance(item, dict) else str(item)
                if name and name.strip():
                    formatted.append({
                        "name": name.strip(),
                        "match": float(item.get("match", 0.6)) if isinstance(item, dict) else 0.6,
                        "url": item.get("url", f"https://www.last.fm/music/{urllib.parse.quote_plus(name)}") if isinstance(item, dict) else "",
                        "image_url": item.get("image_url", "") if isinstance(item, dict) else "",
                    })
            if len(formatted) >= 5:
                return formatted

    # 2. Query Last.fm artist.getSimilar endpoint
    key = api_key or _get_lastfm_key()
    if key:
        fetched = lastfm.fetch_artist_similar(clean_artist, api_key=key, limit=25)
        if fetched:
            return fetched

    # 3. Fallback to similar artists from Last.fm artist.getInfo
    if key:
        bio_stats = lastfm.fetch_artist_bio_and_stats(clean_artist, api_key=key)
        sim_info = bio_stats.get("similar_artists", [])
        if sim_info:
            return [
                {
                    "name": s["name"],
                    "match": 0.65,
                    "url": s.get("url", ""),
                    "image_url": "",
                }
                for s in sim_info if isinstance(s, dict) and s.get("name")
            ]

    return []


def get_related_band_recommendations(
    user_email: Optional[str] = None,
    limit: int = 24,
    include_already_rated: bool = False,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Generate personalized recommendations based on the user's band ratings.
    Returns:
      {
        "recommendations": [ ... ],
        "seed_artists": [ ... ],
        "stats": { ... },
        "has_ratings": bool,
      }
    """
    ratings = database.get_band_ratings(user_email=user_email, db_path=db_path)
    rated_map = database.get_rated_artists_map(user_email=user_email, db_path=db_path)

    # Disliked artists (rating 1)
    disliked_names: Set[str] = {
        database.normalize_text(r["artist"])
        for r in ratings
        if r.get("rating") == 1
    }

    # Positive seeds (ratings 5, 4, 3, and minor 2)
    positive_seeds = [
        r for r in ratings
        if r.get("rating", 0) in (2, 3, 4, 5)
    ]
    positive_seeds.sort(key=lambda x: (x.get("rating", 0), x.get("updated_at", "")), reverse=True)

    # If no ratings yet, fallback to analyzed artists in library as unranked seeds
    analyzed_artists = database.get_analyzed_artists(db_path=db_path)
    fallback_used = False
    if not positive_seeds and analyzed_artists:
        fallback_used = True
        positive_seeds = [
            {"artist": a["artist"], "rating": 3, "fallback": True}
            for a in analyzed_artists[:6]
        ]

    if not positive_seeds:
        return {
            "recommendations": [],
            "seed_artists": [],
            "stats": database.get_band_rating_stats(user_email=user_email, db_path=db_path),
            "has_ratings": False,
            "fallback_used": False,
        }

    # Select top seeds (up to 8 to maintain quick response times and high relevance)
    top_seeds = positive_seeds[:8]
    seed_names_norm: Set[str] = {database.normalize_text(s["artist"]) for s in top_seeds}

    # Query similar artists in parallel across seeds
    api_key = _get_lastfm_key()
    seed_similarities: Dict[str, List[Dict[str, Any]]] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(top_seeds))) as executor:
        future_to_seed = {
            executor.submit(_fetch_similar_for_artist, s["artist"], api_key, db_path): s
            for s in top_seeds
        }
        for future in concurrent.futures.as_completed(future_to_seed):
            s_item = future_to_seed[future]
            try:
                similar_list = future.result() or []
            except Exception as e:
                print(f"Error fetching similar for {s_item['artist']}: {e}")
                similar_list = []
            seed_similarities[s_item["artist"]] = similar_list

    # Aggregate candidate scores
    candidates: Dict[str, Dict[str, Any]] = {}

    for seed in top_seeds:
        s_artist = seed["artist"]
        s_rating = seed.get("rating", 3)
        s_weight = RATING_WEIGHTS.get(s_rating, 1.0)
        similar_list = seed_similarities.get(s_artist, [])

        for sim in similar_list:
            raw_name = sim.get("name", "").strip()
            if not raw_name:
                continue

            norm_name = database.normalize_text(raw_name)

            # Suppress disliked artists and the seed artist itself
            if norm_name in disliked_names or norm_name == database.normalize_text(s_artist):
                continue

            # Check if user already rated this band
            user_rating = rated_map.get(norm_name)
            if not include_already_rated and user_rating is not None and user_rating > 0:
                continue

            match_val = float(sim.get("match", 0.5))
            score_contribution = s_weight * (0.4 + 0.6 * match_val)

            if norm_name not in candidates:
                candidates[norm_name] = {
                    "artist": raw_name,
                    "artist_normalized": norm_name,
                    "score": 0.0,
                    "sources": [],
                    "url": sim.get("url", f"https://www.last.fm/music/{urllib.parse.quote_plus(raw_name)}"),
                    "image_url": sim.get("image_url", ""),
                    "user_rating": user_rating,  # None, 0..5
                }

            candidates[norm_name]["score"] += score_contribution
            candidates[norm_name]["sources"].append({
                "artist": s_artist,
                "rating": s_rating,
                "rating_label": RATING_LABELS.get(s_rating, ""),
                "match": match_val,
            })
            if not candidates[norm_name].get("image_url") and sim.get("image_url"):
                candidates[norm_name]["image_url"] = sim["image_url"]

    # Library presence cross-reference
    analyzed_map = {
        database.normalize_text(a["artist"]): a.get("song_count", 0)
        for a in analyzed_artists
    }

    # Multi-source synergy boost and build reason
    scored_list: List[Dict[str, Any]] = []
    for cand in candidates.values():
        sources = cand["sources"]
        sources.sort(key=lambda s: (s["rating"], s["match"]), reverse=True)

        # Multi-seed bonus: if related to 2+ bands you like, boost score
        if len(sources) > 1:
            cand["score"] *= (1.0 + 0.4 * (len(sources) - 1))

        # Check library presence
        cand_norm = cand["artist_normalized"]
        analyzed_songs = analyzed_map.get(cand_norm, 0)
        cand["in_library"] = cand_norm in analyzed_map
        cand["analyzed_songs"] = analyzed_songs

        # Build natural human rationale
        if len(sources) >= 2:
            s1, s2 = sources[0], sources[1]
            cand["reason"] = f"Related to {s1['artist']} ({s1['rating']}★) and {s2['artist']} ({s2['rating']}★)"
        elif sources:
            s1 = sources[0]
            cand["reason"] = f"Because you rated {s1['artist']} {s1['rating']}★ ({s1['rating_label']})"
        else:
            cand["reason"] = "Recommended from your music profile"

        cand["score"] = round(cand["score"], 2)
        scored_list.append(cand)

    # Sort by composite recommendation score descending
    scored_list.sort(key=lambda x: x["score"], reverse=True)
    top_recommendations = scored_list[:limit]

    # Enrich top recommendations with genre tags / artwork asynchronously
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(top_recommendations) or 1)) as executor:
        future_to_rec = {
            executor.submit(database.get_artist_metadata, rec["artist"], db_path=db_path): rec
            for rec in top_recommendations
        }
        for future in concurrent.futures.as_completed(future_to_rec):
            rec_item = future_to_rec[future]
            try:
                meta = future.result()
                if meta:
                    raw_tags = meta.get("tags") or []
                    if isinstance(raw_tags, str):
                        try:
                            raw_tags = json.loads(raw_tags)
                        except Exception:
                            raw_tags = []
                    rec_item["tags"] = [
                        t.get("name", "") if isinstance(t, dict) else str(t)
                        for t in raw_tags[:3]
                        if t
                    ]
                    if not rec_item.get("image_url") and meta.get("image_url"):
                        rec_item["image_url"] = meta["image_url"]
                else:
                    rec_item["tags"] = []
            except Exception:
                rec_item["tags"] = []

    return {
        "recommendations": top_recommendations,
        "seed_artists": [
            {
                "artist": s["artist"],
                "rating": s["rating"],
                "rating_label": RATING_LABELS.get(s["rating"], ""),
            }
            for s in top_seeds
        ],
        "stats": database.get_band_rating_stats(user_email=user_email, db_path=db_path),
        "has_ratings": len(ratings) > 0,
        "fallback_used": fallback_used,
    }


def get_recommendations_for_single_artist(
    artist: str,
    user_email: Optional[str] = None,
    limit: int = 12,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get recommendations related to a single artist (e.g. when user types into the quick search box).
    """
    clean_artist = artist.strip()
    if not clean_artist:
        return {"recommendations": [], "artist": ""}

    api_key = _get_lastfm_key()
    sim_list = _fetch_similar_for_artist(clean_artist, api_key, db_path=db_path)

    rated_map = database.get_rated_artists_map(user_email=user_email, db_path=db_path)
    analyzed_artists = database.get_analyzed_artists(db_path=db_path)
    analyzed_map = {database.normalize_text(a["artist"]): a.get("song_count", 0) for a in analyzed_artists}

    results = []
    for item in sim_list[:limit]:
        raw_name = item.get("name", "").strip()
        if not raw_name or database.normalize_text(raw_name) == database.normalize_text(clean_artist):
            continue

        norm = database.normalize_text(raw_name)
        user_rating = rated_map.get(norm)
        match_score = float(item.get("match", 0.5))

        results.append({
            "artist": raw_name,
            "artist_normalized": norm,
            "score": round(match_score * 5.0, 2),
            "match": match_score,
            "url": item.get("url", f"https://www.last.fm/music/{urllib.parse.quote_plus(raw_name)}"),
            "image_url": item.get("image_url", ""),
            "user_rating": user_rating,
            "in_library": norm in analyzed_map,
            "analyzed_songs": analyzed_map.get(norm, 0),
            "reason": f"Directly related to {clean_artist}",
            "tags": [],
        })

    return {
        "artist": clean_artist,
        "recommendations": results,
    }
