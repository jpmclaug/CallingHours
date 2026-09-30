#!/usr/bin/env python3
"""
Importer for Spotify Extended Streaming History into Calling Hours database.

Reads all Audio and Video extended streaming history JSON files from:
Spotify History/Spotify Extended Streaming History/
and batch-inserts them into spotify_history table using fast bulk operations
with ON CONFLICT / INSERT OR IGNORE deduplication.
"""

import glob
import json
import os
import sys
import time
from typing import Optional, List, Dict, Any, Tuple

# Ensure parent directory is on sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import database

DEFAULT_HISTORY_DIR = os.path.join(SCRIPT_DIR, "Spotify History", "Spotify Extended Streaming History")


def find_default_user_email() -> str:
    """Find the primary user's email in the database, defaulting to jpmclaug@gmail.com."""
    try:
        users = database.get_all_users()
        if users:
            for u in users:
                email = u.get("email")
                if email and "jpmclaug" in email.lower():
                    return email.lower().strip()
            # fallback to first user
            first_email = users[0].get("email")
            if first_email:
                return first_email.lower().strip()
    except Exception as e:
        print(f"Notice: Could not query users table ({e}); falling back to default email.")
    return "jpmclaug@gmail.com"


def parse_history_files(
    history_dir: str = DEFAULT_HISTORY_DIR,
    min_ms_played: int = 0
) -> Tuple[List[Dict[str, Any]], int, int]:
    """
    Parse all JSON history files in the target directory.
    Returns:
        (deduped_items, total_raw_records, skipped_podcasts_or_null)
    """
    if not os.path.isdir(history_dir):
        raise FileNotFoundError(f"History directory not found: {history_dir}")

    json_files = sorted(glob.glob(os.path.join(history_dir, "*.json")))
    if not json_files:
        raise FileNotFoundError(f"No JSON files found in {history_dir}")

    print(f"Found {len(json_files)} history JSON files in: {history_dir}")

    total_raw = 0
    skipped_non_tracks = 0
    seen_keys = set()
    valid_items: List[Dict[str, Any]] = []

    for file_path in json_files:
        filename = os.path.basename(file_path)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                items = json.load(f)
        except Exception as e:
            print(f"  Error reading {filename}: {e}")
            continue

        file_raw = len(items)
        total_raw += file_raw
        file_valid = 0

        for it in items:
            track_name = it.get("master_metadata_track_name")
            uri = it.get("spotify_track_uri")
            ts = it.get("ts")

            # Podcasts / audiobooks / video podcasts have null track_name or null uri
            if not track_name or not uri or not ts:
                skipped_non_tracks += 1
                continue

            if not uri.startswith("spotify:track:"):
                skipped_non_tracks += 1
                continue

            ms = it.get("ms_played") or 0
            if ms < min_ms_played:
                continue

            track_id = uri.split(":")[-1].strip()
            if not track_id:
                skipped_non_tracks += 1
                continue

            dedup_key = (track_id, ts)
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            artist_name = it.get("master_metadata_album_artist_name") or "Unknown Artist"
            album_name = it.get("master_metadata_album_album_name") or ""
            spotify_url = f"https://open.spotify.com/track/{track_id}"

            valid_items.append({
                "spotify_track_id": track_id,
                "played_at": ts,
                "track_name": track_name,
                "artist_name": artist_name,
                "album_name": album_name,
                "album_image_url": "",
                "duration_ms": ms,
                "popularity": 0,
                "preview_url": "",
                "spotify_url": spotify_url,
                "release_date": "",
            })
            file_valid += 1

        print(f"  Processed {filename}: {file_raw} entries -> {file_valid} music tracks")

    return valid_items, total_raw, skipped_non_tracks


def import_extended_history(
    user_email: Optional[str] = None,
    history_dir: str = DEFAULT_HISTORY_DIR,
    target_backend: Optional[str] = None,
    batch_size: int = 2500
) -> Dict[str, Any]:
    """
    Import Spotify Extended Streaming History into the specified database target.
    If target_backend is None, uses database.get_db_target().
    """
    start_time = time.time()
    database.init_db(target_backend)

    email = (user_email or find_default_user_email()).lower().strip()
    target = database.get_db_target(target_backend)
    backend_name = database.get_backend_name(target)

    print(f"\n========================================================")
    print(f"Starting Spotify Extended Streaming History Import")
    print(f"Target User:    {email}")
    print(f"Target DB:      {backend_name} ({target[:45]}...)")
    print(f"Source Dir:     {history_dir}")
    print(f"========================================================\n")

    items, total_raw, skipped = parse_history_files(history_dir)
    print(f"\nTotal raw records read:    {total_raw:,}")
    print(f"Skipped podcasts/nulls:    {skipped:,}")
    print(f"Unique track listens:      {len(items):,}")

    if not items:
        print("No items to import.")
        return {"total_raw": total_raw, "inserted": 0, "unique": 0}

    print(f"\nBulk inserting into {backend_name} in batches of {batch_size:,}...")

    total_inserted = 0
    total_items = len(items)

    with database.get_connection(target) as conn:
        cursor = conn.cursor()
        is_pg = database.is_postgres(target)

        for i in range(0, total_items, batch_size):
            chunk = items[i:i + batch_size]
            batch_tuples = [
                (
                    email,
                    it["spotify_track_id"],
                    it["played_at"],
                    it["track_name"],
                    it["artist_name"],
                    it["album_name"],
                    it["album_image_url"],
                    it["duration_ms"],
                    it["popularity"],
                    it["preview_url"],
                    it["spotify_url"],
                    it["release_date"],
                )
                for it in chunk
            ]

            if is_pg:
                import psycopg2.extras
                psycopg2.extras.execute_values(
                    cursor,
                    """
                    INSERT INTO spotify_history (
                        user_email, spotify_track_id, played_at, track_name, artist_name,
                        album_name, album_image_url, duration_ms, popularity,
                        preview_url, spotify_url, release_date, created_at
                    )
                    VALUES %s
                    ON CONFLICT (user_email, spotify_track_id, played_at) DO NOTHING;
                    """,
                    batch_tuples,
                    template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)",
                    page_size=min(batch_size, 2000)
                )
                batch_inserted = max(0, cursor.rowcount)
            else:
                cursor.executemany(
                    """
                    INSERT OR IGNORE INTO spotify_history (
                        user_email, spotify_track_id, played_at, track_name, artist_name,
                        album_name, album_image_url, duration_ms, popularity,
                        preview_url, spotify_url, release_date, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP);
                    """,
                    batch_tuples
                )
                batch_inserted = max(0, cursor.rowcount)

            total_inserted += batch_inserted
            pct = ((i + len(chunk)) / total_items) * 100
            print(f"  Batch {i // batch_size + 1:2d}/{(total_items + batch_size - 1) // batch_size:2d}: "
                  f"processed {i + len(chunk):,}/{total_items:,} ({pct:5.1f}%) "
                  f"| +{batch_inserted:,} newly inserted (total: {total_inserted:,})")

    database.notify_db_mutation()
    elapsed = time.time() - start_time

    final_count = database.get_spotify_history_count(email, db_path=target)

    print(f"\n========================================================")
    print(f"Import Finished in {elapsed:.2f} seconds!")
    print(f"Newly inserted:            {total_inserted:,}")
    print(f"Already in database:       {len(items) - total_inserted:,}")
    print(f"Total user history in DB:  {final_count:,}")
    print(f"========================================================\n")

    return {
        "total_raw": total_raw,
        "unique": len(items),
        "inserted": total_inserted,
        "final_count": final_count,
        "elapsed_seconds": round(elapsed, 2)
    }


if __name__ == "__main__":
    email_arg = sys.argv[1] if len(sys.argv) > 1 else None
    import_extended_history(user_email=email_arg)
