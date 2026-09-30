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


    def test_lifetime_stats_and_archive_search(self):
        """Verify get_spotify_lifetime_stats and search_spotify_history."""
        user = "test_lifetime@example.com"
        # Seed 3 streams for 2 artists
        items = [
            {
                "track_id": "trk_1",
                "played_at": "2020-01-01T12:00:00Z",
                "name": "Song A",
                "artist": "Artist Alpha",
                "album": "Album 1",
                "duration_ms": 180000,
            },
            {
                "track_id": "trk_2",
                "played_at": "2022-06-01T12:00:00Z",
                "name": "Song B",
                "artist": "Artist Alpha",
                "album": "Album 2",
                "duration_ms": 240000,
            },
            {
                "track_id": "trk_3",
                "played_at": "2024-03-01T12:00:00Z",
                "name": "Song C",
                "artist": "Artist Beta",
                "album": "Album 3",
                "duration_ms": 300000,
            }
        ]
        database.save_spotify_history_items(user, items, db_path=self.db_path)

        # 1. Lifetime Stats
        stats = database.get_spotify_lifetime_stats(user, db_path=self.db_path)
        self.assertEqual(stats["total_tracks"], 3)
        self.assertEqual(stats["unique_artists"], 2)
        self.assertAlmostEqual(stats["total_hours"], 0.2, places=1)
        self.assertEqual(stats["first_year"], "2020")
        self.assertEqual(stats["last_year"], "2024")
        self.assertEqual(len(stats["top_artists"]), 2)
        self.assertEqual(stats["top_artists"][0]["artist"], "Artist Alpha")
        self.assertEqual(stats["top_artists"][0]["count"], 2)

        # 2. Archive Search
        # Search all
        tracks, total, pages = database.search_spotify_history(user, query="", page=1, per_page=2, db_path=self.db_path)
        self.assertEqual(total, 3)
        self.assertEqual(pages, 2)
        self.assertEqual(len(tracks), 2)
        self.assertEqual(tracks[0]["name"], "Song C")  # Most recent first

        # Page 2
        tracks_p2, total2, pages2 = database.search_spotify_history(user, query="", page=2, per_page=2, db_path=self.db_path)
        self.assertEqual(len(tracks_p2), 1)
        self.assertEqual(tracks_p2[0]["name"], "Song A")

        # Search with filter
        filtered_tracks, f_total, f_pages = database.search_spotify_history(user, query="Alpha", page=1, per_page=10, db_path=self.db_path)
        self.assertEqual(f_total, 2)
        self.assertEqual(len(filtered_tracks), 2)
        self.assertEqual(filtered_tracks[0]["artist"], "Artist Alpha")

        # 3. Artist Spotify Stats
        artist_stats = database.get_artist_spotify_stats(user, "Artist Alpha", db_path=self.db_path)
        self.assertEqual(artist_stats["play_count"], 2)
        self.assertEqual(artist_stats["first_year"], "2020")
        self.assertEqual(artist_stats["last_year"], "2022")
        self.assertEqual(len(artist_stats["top_tracks"]), 2)

    def test_ui_rendering_spotify_and_artist_page(self):
        """Verify UI rendering incorporates lifetime intelligence, search, and artist metrics."""
        handler = calling_hours.CallingHoursRequestHandler.__new__(calling_hours.CallingHoursRequestHandler)
        mock_user = {"email": "jpmclaug@gmail.com", "id": 1, "is_admin": True}

        # Mock user & response output buffer
        handler.headers = {"Host": "localhost:8080", "Accept-Encoding": ""}
        handler.is_request_secure = MagicMock(return_value=False)
        handler.get_current_user = MagicMock(return_value=mock_user)
        output_chunks = []
        handler.wfile = MagicMock()
        handler.wfile.write = MagicMock(side_effect=lambda b: output_chunks.append(b))
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()

        # 1. Render Spotify Page in Demo Mode
        handler.render_spotify_page(demo=True)
        rendered_spotify_html = b"".join(output_chunks).decode("utf-8", errors="ignore")
        self.assertIn("Spotify Listening Intelligence", rendered_spotify_html)
        self.assertIn("15-Year Personal Streaming Intelligence Archive", rendered_spotify_html)
        self.assertIn("Lifetime Plays", rendered_spotify_html)
        self.assertIn("spotify-pagination-bar", rendered_spotify_html)
        self.assertIn("Search 98,000+ songs", rendered_spotify_html)

        # 2. Render Artist Page with user history
        output_chunks.clear()
        handler.render_artist_page(selected_artist="Bane")
        rendered_artist_html = b"".join(output_chunks).decode("utf-8", errors="ignore")
        self.assertIn("Bane", rendered_artist_html)
        # Should surface personal plays and personal history card if present in db
        if "Your Personal Listening History with Bane" in rendered_artist_html:
            self.assertIn("Lifetime Plays", rendered_artist_html)
            self.assertIn("Your Most Streamed Bane Songs", rendered_artist_html)


if __name__ == "__main__":
    unittest.main()

