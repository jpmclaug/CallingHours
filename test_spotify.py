import os
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

import spotify
import database
import tempfile


class TestSpotify(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_spotify.db")
        database.init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_format_duration(self):
        self.assertEqual(spotify.format_duration(0), "0:00")
        self.assertEqual(spotify.format_duration(None), "0:00")
        self.assertEqual(spotify.format_duration(-500), "0:00")
        self.assertEqual(spotify.format_duration(65000), "1:05")
        self.assertEqual(spotify.format_duration(182000), "3:02")
        self.assertEqual(spotify.format_duration(305000), "5:05")

    def test_format_relative_time(self):
        now = datetime.now(timezone.utc)
        # Just now (20 seconds ago)
        t_recent = (now - timedelta(seconds=20)).isoformat()
        self.assertEqual(spotify.format_relative_time(t_recent), "Just now")

        # 5 minutes ago
        t_mins = (now - timedelta(minutes=5)).isoformat()
        self.assertEqual(spotify.format_relative_time(t_mins), "5m ago")

        # 3 hours ago
        t_hours = (now - timedelta(hours=3)).isoformat()
        self.assertEqual(spotify.format_relative_time(t_hours), "3h ago")

        # 3 days ago
        t_days = (now - timedelta(days=3)).isoformat()
        self.assertEqual(spotify.format_relative_time(t_days), "3d ago")

        # Invalid string
        self.assertEqual(spotify.format_relative_time("invalid-date"), "invalid-date")

    def test_normalize_track_item(self):
        raw_item = {
            "played_at": "2026-09-24T12:00:00Z",
            "track": {
                "id": "trk123",
                "name": "Sweetness",
                "duration_ms": 220000,
                "popularity": 75,
                "preview_url": "https://preview.spotify.com/mp3",
                "external_urls": {"spotify": "https://open.spotify.com/track/trk123"},
                "artists": [
                    {"id": "art1", "name": "Jimmy Eat World", "external_urls": {"spotify": "https://open.spotify.com/artist/art1"}},
                    {"id": "art2", "name": "Mark Trombino", "external_urls": {"spotify": "https://open.spotify.com/artist/art2"}},
                ],
                "album": {
                    "id": "alb1",
                    "name": "Bleed American",
                    "release_date": "2001-07-24",
                    "images": [{"url": "https://image.spotify.com/alb1.jpg", "height": 300, "width": 300}],
                },
            },
        }

        norm = spotify.normalize_track_item(raw_item)
        self.assertEqual(norm["track_id"], "trk123")
        self.assertEqual(norm["name"], "Sweetness")
        self.assertEqual(norm["artist"], "Jimmy Eat World")
        self.assertEqual(norm["all_artists"], "Jimmy Eat World, Mark Trombino")
        self.assertEqual(norm["album"], "Bleed American")
        self.assertEqual(norm["album_image"], "https://image.spotify.com/alb1.jpg")
        self.assertEqual(norm["release_year"], "2001")
        self.assertEqual(norm["duration_formatted"], "3:40")
        self.assertEqual(norm["popularity"], 75)
        self.assertEqual(norm["preview_url"], "https://preview.spotify.com/mp3")
        self.assertEqual(norm["played_at"], "2026-09-24T12:00:00Z")

    @patch("spotify.get_spotify_credentials")
    def test_get_auth_url(self, mock_creds):
        mock_creds.return_value = ("test_client_id", "test_secret", None)
        url = spotify.get_auth_url("http://127.0.0.1:8000/auth/spotify/callback", state="csrf123")
        self.assertIn("https://accounts.spotify.com/authorize", url)
        self.assertIn("client_id=test_client_id", url)
        self.assertIn("state=csrf123", url)
        self.assertIn("user-read-recently-played", url)

        # Unconfigured raises ValueError
        mock_creds.return_value = (None, None, None)
        with self.assertRaises(ValueError):
            spotify.get_auth_url("http://127.0.0.1:8000/auth/spotify/callback")

    @patch("requests.post")
    @patch("spotify.get_spotify_credentials")
    def test_exchange_code_for_token(self, mock_creds, mock_post):
        mock_creds.return_value = ("cid", "csecret", None)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "access_token": "acc_tok_123",
            "refresh_token": "ref_tok_456",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        mock_post.return_value = mock_resp

        res = spotify.exchange_code_for_token("auth_code_xyz", "http://127.0.0.1:8000/callback")
        self.assertEqual(res["access_token"], "acc_tok_123")
        self.assertEqual(res["refresh_token"], "ref_tok_456")
        self.assertTrue(mock_post.called)

    @patch("requests.get")
    def test_fetch_user_profile(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "id": "jpmclaughlin",
            "display_name": "JP McLaughlin",
            "email": "jp@example.com",
            "images": [{"url": "https://profile.jpg"}],
            "external_urls": {"spotify": "https://open.spotify.com/user/jpmclaughlin"},
            "followers": {"total": 85},
        }
        mock_get.return_value = mock_resp

        prof = spotify.fetch_user_profile("valid_token")
        self.assertEqual(prof["id"], "jpmclaughlin")
        self.assertEqual(prof["display_name"], "JP McLaughlin")
        self.assertEqual(prof["image_url"], "https://profile.jpg")
        self.assertEqual(prof["followers"], 85)

    @patch("requests.get")
    def test_fetch_recently_played(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "items": [
                {
                    "played_at": "2026-09-24T12:00:00Z",
                    "track": {
                        "id": "trk1",
                        "name": "Hear You Me",
                        "duration_ms": 280000,
                        "artists": [{"name": "Jimmy Eat World"}],
                        "album": {"name": "Bleed American", "release_date": "2001-07-24"},
                    },
                }
            ]
        }
        mock_get.return_value = mock_resp

        tracks = spotify.fetch_recently_played("token", limit=10)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["name"], "Hear You Me")
        self.assertEqual(tracks[0]["artist"], "Jimmy Eat World")

    @patch("requests.get")
    def test_fetch_currently_playing(self, mock_get):
        # 1. 204 No Content
        mock_204 = MagicMock()
        mock_204.status_code = 204
        mock_get.return_value = mock_204
        self.assertIsNone(spotify.fetch_currently_playing("token"))

        # 2. 200 with item
        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.json.return_value = {
            "is_playing": True,
            "progress_ms": 60000,
            "item": {
                "id": "trk_now",
                "name": "Spanish Love Songs",
                "duration_ms": 180000,
                "artists": [{"name": "Routine Pain"}],
                "album": {"name": "Brave Faces Everyone", "release_date": "2020-02-07"},
            },
        }
        mock_get.return_value = mock_200
        np = spotify.fetch_currently_playing("token")
        self.assertIsNotNone(np)
        self.assertTrue(np["is_playing"])
        self.assertEqual(np["progress_ms"], 60000)
        self.assertEqual(np["progress_formatted"], "1:00")
        self.assertEqual(np["progress_percent"], 33)

    def test_compute_analytics(self):
        # Test empty tracks
        empty_res = spotify.compute_analytics([])
        self.assertEqual(empty_res["total_tracks"], 0)
        self.assertEqual(empty_res["persona"], "Quiet Observer")

        # Test populated tracks
        tracks = [
            {
                "track_id": "t1",
                "name": "Song 1",
                "artist": "Jimmy Eat World",
                "album": "Album 1",
                "release_year": "2001",
                "duration_ms": 200000,
                "popularity": 70,
                "played_at": "2026-09-24T02:00:00Z",  # Night
            },
            {
                "track_id": "t2",
                "name": "Song 2",
                "artist": "Jimmy Eat World",
                "album": "Album 1",
                "release_year": "2001",
                "duration_ms": 180000,
                "popularity": 80,
                "played_at": "2026-09-24T04:00:00Z",  # Night
            },
            {
                "track_id": "t3",
                "name": "Song 3",
                "artist": "Slowdive",
                "album": "Album 2",
                "release_year": "2023",
                "duration_ms": 240000,
                "popularity": 60,
                "played_at": "2026-09-24T14:00:00Z",  # Afternoon
            },
        ]
        top_artists = [
            {"name": "Jimmy Eat World", "genres": ["emo", "rock"]},
            {"name": "Slowdive", "genres": ["shoegaze", "rock"]},
        ]

        analytics = spotify.compute_analytics(tracks, top_artists=top_artists)
        self.assertEqual(analytics["total_tracks"], 3)
        self.assertEqual(analytics["unique_artists_count"], 2)
        self.assertEqual(analytics["unique_albums_count"], 2)
        self.assertEqual(analytics["avg_popularity"], 70.0)
        self.assertIn("Chart Hits", analytics["popularity_vibe"])
        self.assertEqual(analytics["persona"], "The Night Owl")

        # Time of day
        tod = analytics["time_of_day"]
        self.assertEqual(tod["night"]["count"], 2)
        self.assertEqual(tod["afternoon"]["count"], 1)

        # Top artists
        top_art = analytics["top_artists"]
        self.assertEqual(top_art[0]["artist"], "Jimmy Eat World")
        self.assertEqual(top_art[0]["count"], 2)
        self.assertEqual(top_art[0]["percent"], 66.7)

        # Eras
        eras = analytics["release_eras"]
        self.assertEqual(eras["2000s"]["count"], 2)
        self.assertEqual(eras["2020s"]["count"], 1)

        # Genres
        genres = analytics["top_genres"]
        genre_dict = {g["genre"]: g["count"] for g in genres}
        self.assertEqual(genre_dict.get("Rock"), 2)
        self.assertEqual(genre_dict.get("Emo"), 1)
        self.assertEqual(genre_dict.get("Shoegaze"), 1)

    def test_demo_sample_data(self):
        prof, tracks, analytics = spotify.get_demo_sample_data()
        self.assertIsNotNone(prof)
        self.assertGreater(len(tracks), 0)
        self.assertIsNotNone(analytics)
        self.assertGreater(analytics["total_tracks"], 0)

    @patch('spotify.requests.get')
    def test_search_track(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "tracks": {
                "items": [{
                    "id": "trk_abc",
                    "uri": "spotify:track:trk_abc",
                    "name": "Sweetness",
                    "artists": [{"name": "Jimmy Eat World"}],
                    "album": {"name": "Bleed American", "images": [{"url": "https://img.jpg"}]},
                    "duration_ms": 220000,
                    "external_urls": {"spotify": "https://open.spotify.com/track/trk_abc"}
                }]
            }
        }
        mock_get.return_value = mock_resp

        res = spotify.search_track("token123", "Jimmy Eat World", "Sweetness")
        self.assertIsNotNone(res)
        self.assertEqual(res["id"], "trk_abc")
        self.assertEqual(res["uri"], "spotify:track:trk_abc")
        self.assertEqual(res["name"], "Sweetness")

    @patch('spotify.requests.post')
    def test_create_playlist(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {
            "id": "pl_123",
            "name": "Calling Hours Favorites",
            "uri": "spotify:playlist:pl_123",
            "external_urls": {"spotify": "https://open.spotify.com/playlist/pl_123"}
        }
        mock_post.return_value = mock_resp

        res = spotify.create_playlist("token123", "user_1", "Calling Hours Favorites", description="Best songs")
        self.assertEqual(res["id"], "pl_123")
        self.assertEqual(res["url"], "https://open.spotify.com/playlist/pl_123")

    @patch('spotify.requests.post')
    def test_create_playlist_403_raises_permission_error(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = '{"error": {"status": 403, "message": "Forbidden"}}'
        mock_post.return_value = mock_resp

        with self.assertRaises(RuntimeError) as ctx:
            spotify.create_playlist("token123", "user_1", "Test")
        self.assertIn("403", str(ctx.exception))
        self.assertIn("permission", str(ctx.exception).lower())

    @patch('spotify.requests.post')
    def test_add_tracks_to_playlist(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_post.return_value = mock_resp

        added = spotify.add_tracks_to_playlist("token123", "pl_123", ["spotify:track:1", "spotify:track:2"])
        self.assertEqual(added, 2)

    @patch('spotify.create_playlist')
    @patch('spotify.search_track')
    @patch('spotify.add_tracks_to_playlist')
    def test_export_songs_to_spotify_playlist(self, mock_add, mock_search, mock_create):
        mock_create.return_value = {
            "id": "pl_xyz",
            "name": "All Analyzed",
            "url": "https://open.spotify.com/playlist/pl_xyz"
        }
        mock_search.return_value = {
            "uri": "spotify:track:searched_1"
        }
        mock_add.return_value = 2

        songs = [
            {"artist": "Jimmy Eat World", "song": "Sweetness", "spotify_id": "spot_1"},
            {"artist": "Slowdive", "song": "Alison"}
        ]
        res = spotify.export_songs_to_spotify_playlist("token123", "user_1", "All Analyzed", songs)
        self.assertTrue(res["success"])
        self.assertEqual(res["playlist_id"], "pl_xyz")
        self.assertEqual(res["playlist_url"], "https://open.spotify.com/playlist/pl_xyz")
        self.assertEqual(res["tracks_requested"], 2)
        self.assertEqual(res["tracks_matched"], 2)
        self.assertEqual(res["tracks_added"], 2)


if __name__ == "__main__":
    unittest.main()
