import os
import sys
import unittest
import tempfile
import time
from unittest.mock import patch, MagicMock

import database
import spotify
import calling_hours
import import_spotify_extended_history


class TestSpotifyExtendedHistoryAndAutoSync(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.db_path = self.temp_db.name
        database.init_db(self.db_path)
        calling_hours._last_spotify_sync.clear()
        calling_hours._syncing_spotify_users.clear()

    def tearDown(self):
        if os.path.exists(self.db_path):
            try:
                os.unlink(self.db_path)
            except OSError:
                pass

    def test_save_extended_history_items_and_deduplication(self):
        """Verify extended streaming history items format is ingested without duplicates."""
        user_email = "test@example.com"
        items = [
            {
                "ts": "2023-01-15T10:00:00Z",
                "master_metadata_track_name": "Escape the Frame",
                "master_metadata_album_artist_name": "Hopesfall",
                "master_metadata_album_album_name": "The Satellite Years",
                "spotify_track_uri": "spotify:track:hopesfall_1",
                "ms_played": 210000,
            },
            {
                "ts": "2023-01-15T10:04:00Z",
                "master_metadata_track_name": "Can We Start Again",
                "master_metadata_album_artist_name": "Bane",
                "master_metadata_album_album_name": "Give Blood",
                "spotify_track_uri": "spotify:track:bane_1",
                "ms_played": 195000,
            }
        ]

        # First insert
        new_count = database.save_spotify_history_items(user_email, items, db_path=self.db_path)
        self.assertEqual(new_count, 2)
        self.assertEqual(database.get_spotify_history_count(user_email, db_path=self.db_path), 2)

        # Second insert with identical items: exactly 0 new records inserted (no doubling up)
        dup_count = database.save_spotify_history_items(user_email, items, db_path=self.db_path)
        self.assertEqual(dup_count, 0)
        self.assertEqual(database.get_spotify_history_count(user_email, db_path=self.db_path), 2)

        # Fetch history records and verify alias mappings
        history = database.get_spotify_history(user_email, limit=10, db_path=self.db_path)
        self.assertEqual(len(history), 2)
        # Ordered by played_at DESC: Bane is newer
        self.assertEqual(history[0]["name"], "Can We Start Again")
        self.assertEqual(history[0]["artist"], "Bane")
        self.assertEqual(history[0]["track_id"], "bane_1")
        self.assertEqual(history[0]["duration_ms"], 195000)
        self.assertEqual(history[0]["spotify_url"], "https://open.spotify.com/track/bane_1")

    def test_parse_history_files_filters_podcasts_and_nulls(self):
        """Verify parse_history_files correctly extracts music tracks and filters podcasts."""
        with tempfile.TemporaryDirectory() as temp_dir:
            sample_file = os.path.join(temp_dir, "Streaming_History_Audio_2023.json")
            sample_data = [
                {
                    "ts": "2023-05-01T12:00:00Z",
                    "master_metadata_track_name": "Hurt",
                    "master_metadata_album_artist_name": "Nine Inch Nails",
                    "master_metadata_album_album_name": "The Downward Spiral",
                    "spotify_track_uri": "spotify:track:nin_hurt",
                    "ms_played": 360000,
                },
                {
                    # Podcast: track name and uri are null
                    "ts": "2023-05-01T13:00:00Z",
                    "master_metadata_track_name": None,
                    "master_metadata_album_artist_name": None,
                    "master_metadata_album_album_name": None,
                    "spotify_track_uri": None,
                    "episode_name": "Joe Rogan Experience #2000",
                    "spotify_episode_uri": "spotify:episode:xyz",
                    "ms_played": 1200000,
                },
                {
                    # Duplicate stream of Hurt at same timestamp
                    "ts": "2023-05-01T12:00:00Z",
                    "master_metadata_track_name": "Hurt",
                    "master_metadata_album_artist_name": "Nine Inch Nails",
                    "master_metadata_album_album_name": "The Downward Spiral",
                    "spotify_track_uri": "spotify:track:nin_hurt",
                    "ms_played": 360000,
                }
            ]
            import json
            with open(sample_file, "w", encoding="utf-8") as f:
                json.dump(sample_data, f)

            items, total_raw, skipped = import_spotify_extended_history.parse_history_files(temp_dir)
            self.assertEqual(total_raw, 3)
            self.assertEqual(skipped, 1)  # Podcast was skipped
            self.assertEqual(len(items), 1)  # Duplicate timestamp/track_id was deduped
            self.assertEqual(items[0]["track_name"], "Hurt")
            self.assertEqual(items[0]["spotify_track_id"], "nin_hurt")

    @patch("calling_hours.database.get_spotify_token", return_value=None)
    def test_trigger_auto_spotify_sync_quick_execution_and_cooldown(self, mock_get_token):
        """Verify trigger_auto_spotify_sync executes quickly, runs in background, and enforces cooldown."""
        user_email = "sync_user@example.com"
        
        # 1. No token -> returns True immediately and background finishes cleanly
        t0 = time.time()
        res1 = calling_hours.trigger_auto_spotify_sync(user_email)
        duration = time.time() - t0
        self.assertTrue(res1)
        self.assertLess(duration, 0.05, "Auto-sync trigger should return in under 50ms")

        # Give the background thread a moment to finish
        time.sleep(0.05)

        # 2. Within cooldown interval (default 60s) -> should return False immediately
        res2 = calling_hours.trigger_auto_spotify_sync(user_email)
        self.assertFalse(res2, "Second trigger within cooldown should return False")

        # 3. With force=True -> bypasses cooldown
        res3 = calling_hours.trigger_auto_spotify_sync(user_email, force=True)
        self.assertTrue(res3, "Forced trigger should bypass cooldown")

    def test_auto_sync_background_incremental_ingest(self):
        """Verify background worker fetches last 50 tracks and inserts them without doubling up."""
        user_email = "connected_user@example.com"
        mock_token = {"access_token": "valid_token_123", "refresh_token": "ref_123"}
        mock_tracks = [
            {
                "track_id": "stream_trk_101",
                "played_at": "2026-09-29T20:00:00Z",
                "name": "Blood On The Radio",
                "artist": "Thank You Scientist",
                "album": "Maps of Non-Existent Places",
                "album_image": "",
                "duration_ms": 560000,
                "popularity": 45,
                "preview_url": "",
                "spotify_url": "https://open.spotify.com/track/stream_trk_101",
                "release_date": "2012-06-08",
            },
            {
                "track_id": "stream_trk_102",
                "played_at": "2026-09-29T20:10:00Z",
                "name": "Glow",
                "artist": "Slowdive",
                "album": "Slowdive",
                "album_image": "",
                "duration_ms": 312000,
                "popularity": 60,
                "preview_url": "",
                "spotify_url": "https://open.spotify.com/track/stream_trk_102",
                "release_date": "2017-05-05",
            }
        ]

        with patch("calling_hours.database.get_spotify_token", return_value=mock_token), \
             patch("calling_hours.spotify.get_valid_access_token", return_value="valid_token_123"), \
             patch("calling_hours.spotify.fetch_recently_played", return_value=mock_tracks):

            # First app load trigger
            called = calling_hours.trigger_auto_spotify_sync(user_email, force=True)
            self.assertTrue(called)

            # Wait for background thread to execute
            time.sleep(0.15)

            # Check that tracks were saved into database
            count = database.get_spotify_history_count(user_email)
            self.assertGreaterEqual(count, 2)

            # Trigger again (e.g. next page load with force=True) with identical 50 songs
            called_again = calling_hours.trigger_auto_spotify_sync(user_email, force=True)
            self.assertTrue(called_again)
            time.sleep(0.15)

            # Count should NOT increase (no double up on listens)
            count_after = database.get_spotify_history_count(user_email)
            self.assertEqual(count, count_after)


if __name__ == "__main__":
    unittest.main()

