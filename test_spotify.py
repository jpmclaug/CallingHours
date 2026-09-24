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

    @patch("spotify.get_spotify_credentials")
    @patch("spotify.requests.post")
    def test_get_spotify_app_token(self, mock_post, mock_creds):
        # 1. Unconfigured credentials
        mock_creds.return_value = (None, None, None)
        spotify._app_token_cache["token"] = None
        spotify._app_token_cache["expires_at"] = 0
        token = spotify.get_spotify_app_token()
        self.assertIsNone(token)

        # 2. Configured credentials
        mock_creds.return_value = ("test_cid", "test_secret", None)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "app_token_999",
            "expires_in": 3600,
            "token_type": "Bearer"
        }
        mock_post.return_value = mock_resp

        token = spotify.get_spotify_app_token(force_refresh=True)
        self.assertEqual(token, "app_token_999")
        self.assertTrue(mock_post.called)

        # 3. Cached token reuse
        mock_post.reset_mock()
        token2 = spotify.get_spotify_app_token(force_refresh=False)
        self.assertEqual(token2, "app_token_999")
        mock_post.assert_not_called()

    @patch("spotify.requests.get")
    def test_search_artist_profile(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "artists": {
                "items": [
                    {
                        "id": "other_id",
                        "name": "Jimmy Eat World Tribute Band",
                        "genres": ["cover"],
                        "popularity": 20,
                        "followers": {"total": 500},
                        "images": [],
                        "external_urls": {"spotify": "https://open.spotify.com/artist/other_id"}
                    },
                    {
                        "id": "jew_id",
                        "name": "Jimmy Eat World",
                        "genres": ["emo", "pop punk", "alternative rock"],
                        "popularity": 74,
                        "followers": {"total": 1400000},
                        "images": [{"url": "https://img.spotify.com/jew.jpg"}],
                        "external_urls": {"spotify": "https://open.spotify.com/artist/jew_id"}
                    }
                ]
            }
        }
        mock_get.return_value = mock_resp

        profile = spotify.search_artist_profile("app_token", "Jimmy Eat World")
        self.assertIsNotNone(profile)
        self.assertEqual(profile["id"], "jew_id")
        self.assertEqual(profile["name"], "Jimmy Eat World")
        self.assertEqual(profile["popularity"], 74)
        self.assertEqual(profile["followers"], 1400000)
        self.assertEqual(profile["image_url"], "https://img.spotify.com/jew.jpg")
        self.assertEqual(profile["spotify_url"], "https://open.spotify.com/artist/jew_id")

    @patch("spotify.requests.get")
    def test_fetch_artist_top_tracks(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "tracks": [
                {
                    "id": "t1",
                    "name": "The Middle",
                    "duration_ms": 166000,
                    "popularity": 85,
                    "preview_url": "https://preview.mp3",
                    "external_urls": {"spotify": "https://open.spotify.com/track/t1"},
                    "album": {
                        "name": "Bleed American",
                        "release_date": "2001-07-24",
                        "images": [{"url": "https://img.spotify.com/bleed.jpg"}]
                    }
                }
            ]
        }
        mock_get.return_value = mock_resp

        tracks = spotify.fetch_artist_top_tracks("app_token", "jew_id")
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["name"], "The Middle")
        self.assertEqual(tracks[0]["duration_formatted"], "2:46")
        self.assertEqual(tracks[0]["popularity"], 85)
        self.assertEqual(tracks[0]["preview_url"], "https://preview.mp3")
        self.assertEqual(tracks[0]["album_name"], "Bleed American")
        self.assertEqual(tracks[0]["release_year"], "2001")

    @patch("spotify.requests.get")
    def test_fetch_artist_discography_stats(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "items": [
                {
                    "name": "Surviving",
                    "album_group": "album",
                    "release_date": "2019-10-18",
                    "images": [{"url": "https://img.spotify.com/surv.jpg"}],
                    "external_urls": {"spotify": "https://open.spotify.com/album/surv"}
                },
                {
                    "name": "Bleed American",
                    "album_group": "album",
                    "release_date": "2001-07-24",
                    "images": [{"url": "https://img.spotify.com/bleed.jpg"}],
                    "external_urls": {"spotify": "https://open.spotify.com/album/bleed"}
                },
                {
                    "name": "Static Prevails",
                    "album_group": "album",
                    "release_date": "1996-07-23",
                    "images": [{"url": "https://img.spotify.com/static.jpg"}],
                    "external_urls": {"spotify": "https://open.spotify.com/album/static"}
                },
                {
                    "name": "Something Loud",
                    "album_group": "single",
                    "release_date": "2022-06-10",
                    "images": [],
                    "external_urls": {"spotify": "https://open.spotify.com/album/loud"}
                }
            ]
        }
        mock_get.return_value = mock_resp

        disco = spotify.fetch_artist_discography_stats("app_token", "jew_id")
        self.assertEqual(disco["albums_count"], 3)
        self.assertEqual(disco["singles_count"], 1)
        self.assertEqual(disco["total_releases"], 4)
        self.assertIn("1996 - 2022 (26 yrs)", disco["years_active_span"])
        self.assertEqual(disco["active_decades"], ["1990s", "2000s", "2010s", "2020s"])
        self.assertEqual(disco["latest_release"]["name"], "Surviving")
        self.assertEqual(disco["latest_release"]["type"], "Album")

    def test_format_followers_and_popularity_tier(self):
        # format_followers
        self.assertEqual(spotify.format_followers(1_400_000), "1.4M")
        self.assertEqual(spotify.format_followers(2_000_000), "2M")
        self.assertEqual(spotify.format_followers(450_000), "450K")
        self.assertEqual(spotify.format_followers(1_000), "1K")
        self.assertEqual(spotify.format_followers(850), "850")

        # compute_artist_popularity_tier
        self.assertEqual(spotify.compute_artist_popularity_tier(92), "Global Superstar / Chart Topper")
        self.assertEqual(spotify.compute_artist_popularity_tier(75), "Mainstream Heavyweight")
        self.assertEqual(spotify.compute_artist_popularity_tier(55), "Established Act / Wide Audience")
        self.assertEqual(spotify.compute_artist_popularity_tier(42), "Cult Favorite / Strong Base")
        self.assertEqual(spotify.compute_artist_popularity_tier(20), "Indie / Underground Gem")

    def test_compute_artist_analytics(self):
        profile = {
            "popularity": 74,
            "followers": 1400000,
            "genres": ["emo", "pop punk", "alternative rock"]
        }
        top_tracks = [
            {"popularity": 85},
            {"popularity": 75},
            {"popularity": 65},
        ]
        disco = {"albums_count": 10, "singles_count": 5}

        res = spotify.compute_artist_analytics(profile, top_tracks, disco)
        self.assertEqual(res["popularity"], 74)
        self.assertEqual(res["popularity_tier"], "Mainstream Heavyweight")
        self.assertEqual(res["followers"], 1400000)
        self.assertEqual(res["followers_formatted"], "1.4M")
        self.assertEqual(res["genres"], ["Emo", "Pop Punk", "Alternative Rock"])
        self.assertEqual(res["avg_track_popularity"], 75.0)
        self.assertEqual(res["discography"], disco)
        self.assertEqual(len(res["top_tracks"]), 3)

    def test_get_demo_artist_spotify_data(self):
        demo = spotify.get_demo_artist_spotify_data("Jimmy Eat World")
        self.assertTrue(demo["found"])
        self.assertTrue(demo["is_demo"])
        self.assertEqual(demo["artist"], "Jimmy Eat World")
        self.assertGreater(demo["popularity"], 50)
        self.assertGreater(len(demo["top_tracks"]), 0)
        self.assertGreater(demo["discography"]["albums_count"], 0)

    @patch("spotify.is_spotify_configured")
    @patch("spotify.get_artist_api_token")
    @patch("spotify.search_artist_profile")
    @patch("spotify.fetch_artist_top_tracks")
    @patch("spotify.fetch_artist_discography_stats")
    def test_get_or_fetch_artist_spotify_data_flow(
        self, mock_disco, mock_tracks, mock_search, mock_token, mock_configured
    ):
        # 1. Unconfigured -> returns demo
        mock_configured.return_value = False
        res_unconf = spotify.get_or_fetch_artist_spotify_data("Jimmy Eat World", db_path=self.db_path)
        self.assertTrue(res_unconf.get("found"))
        self.assertTrue(res_unconf.get("is_demo"))

        # 2. Configured and fresh fetch
        mock_configured.return_value = True
        mock_token.return_value = "token_xyz"
        mock_search.return_value = {
            "id": "jew_123",
            "name": "Jimmy Eat World",
            "genres": ["emo", "rock"],
            "popularity": 74,
            "followers": 1400000,
            "image_url": "https://img.jpg",
            "spotify_url": "https://open.spotify.com/artist/jew_123"
        }
        mock_tracks.return_value = [{"id": "t1", "name": "The Middle", "popularity": 85}]
        mock_disco.return_value = {"albums_count": 10, "singles_count": 5, "total_releases": 15}

        res_fresh = spotify.get_or_fetch_artist_spotify_data("Jimmy Eat World", force_refresh=True, db_path=self.db_path)
        self.assertTrue(res_fresh.get("found"))
        self.assertFalse(res_fresh.get("cached"))
        self.assertEqual(res_fresh["artist_id"], "jew_123")
        self.assertEqual(res_fresh["followers_formatted"], "1.4M")
        self.assertEqual(res_fresh["popularity_tier"], "Mainstream Heavyweight")

        # 3. Cached retrieval from database
        mock_search.reset_mock()
        res_cached = spotify.get_or_fetch_artist_spotify_data("Jimmy Eat World", force_refresh=False, db_path=self.db_path)
        self.assertTrue(res_cached.get("found"))
        self.assertTrue(res_cached.get("cached"))
        self.assertEqual(res_cached["artist_id"], "jew_123")
        mock_search.assert_not_called()


if __name__ == "__main__":
    unittest.main()

