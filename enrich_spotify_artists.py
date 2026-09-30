#!/usr/bin/env python3
"""
enrich_spotify_artists.py

Batch metadata enrichment utility for Calling Hours.
Fetches Last.fm genre tags, bios, listener counts, and similar/related artists
plus TheAudioDB formed year, country, and images for all unique artists
in the user's Spotify extended listening history archive.

Usage:
    python enrich_spotify_artists.py [--limit N] [--delay 0.3] [--batch 20]
"""

import os
import sys
import time
import argparse
from typing import Dict, Any

# Ensure project root is in sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import database
import lastfm
import theaudiodb


def enrich_artists(limit: int = 50, delay: float = 0.3):
    database.init_db()
    
    status_before = database.get_enrichment_status()
    print("=" * 60)
    print("SPOTIFY ARTIST ENRICHMENT ENGINE")
    print(f"Current coverage: {status_before['fetched']:,} / {status_before['total_artists_in_history']:,} artists ({status_before['coverage_pct']}%)")
    print(f"Fetching metadata for next {limit} prioritized artists...")
    print("=" * 60)

    artists = database.get_artists_needing_enrichment(limit=limit)
    if not artists:
        print("All artists in your listening history are already enriched!")
        return

    lf_key = lastfm.get_lastfm_api_key()
    adb_key = theaudiodb.get_theaudiodb_api_key()

    print(f"Using Last.fm API Key: {'Available' if lf_key else 'Missing'}")
    print(f"Using TheAudioDB Key: {'Available' if adb_key else 'Missing'}")
    print("-" * 60)

    success_count = 0
    error_count = 0

    for i, item in enumerate(artists, 1):
        artist_name = item['artist']
        play_cnt = item['play_count']
        sys.stdout.write(f"[{i}/{len(artists)}] Enriching '{artist_name}' ({play_cnt:,} plays)... ")
        sys.stdout.flush()

        try:
            meta = lastfm.get_or_fetch_artist_metadata(artist_name, api_key=lf_key) if lf_key else {}
            adb = theaudiodb.get_or_fetch_artist_details(artist_name, api_key=adb_key) if adb_key else {}

            genre = None
            if adb and adb.get('genre'):
                genre = adb.get('genre')
            elif meta and meta.get('tags'):
                genre = meta['tags'][0].get('name', '').title()

            bio = None
            if meta and meta.get('bio'):
                bio = meta.get('bio')
            elif adb and adb.get('biography'):
                bio = adb.get('biography')
            if bio and len(bio) > 500:
                bio = bio[:497] + '...'

            similar = meta.get('similar_artists', []) if meta else []
            tags = meta.get('tags', []) if meta else []

            payload: Dict[str, Any] = {
                'tags': tags,
                'similar': similar,
                'genre': genre,
                'style': adb.get('style') if adb else None,
                'mood': adb.get('mood') if adb else None,
                'formed_year': adb.get('formed_year') if adb else None,
                'country': adb.get('country') if adb else None,
                'bio_summary': bio,
                'image_url': (adb.get('thumbnail_url') if adb else None) or (meta.get('image_url') if meta else None),
                'listeners': meta.get('listeners') if meta else None,
                'playcount': meta.get('playcount') if meta else None,
                'fetch_status': 'fetched' if (meta or adb) else 'not_found'
            }

            database.upsert_artist_enrichment(artist_name, payload)
            sim_names = [s.get('name') for s in similar[:3] if s.get('name')]
            sim_str = f"Related: {', '.join(sim_names)}" if sim_names else "No related artists"
            genre_str = f"[{genre}]" if genre else ""
            print(f"DONE {genre_str} ({sim_str})")
            success_count += 1
            time.sleep(delay)

        except Exception as e:
            print(f"ERROR: {e}")
            database.upsert_artist_enrichment(artist_name, {'fetch_status': 'error', 'error_msg': str(e)})
            error_count += 1

    status_after = database.get_enrichment_status()
    print("=" * 60)
    print(f"Enrichment batch complete! Processed: {success_count}, Errors: {error_count}")
    print(f"New coverage: {status_after['fetched']:,} / {status_after['total_artists_in_history']:,} artists ({status_after['coverage_pct']}%)")
    print("=" * 60)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Enrich Spotify artists with Last.fm & TheAudioDB metadata.")
    parser.add_argument('--limit', type=int, default=25, help="Number of artists to enrich in this run (default: 25)")
    parser.add_argument('--delay', type=float, default=0.25, help="Delay in seconds between requests (default: 0.25)")
    args = parser.parse_args()

    enrich_artists(limit=args.limit, delay=args.delay)
