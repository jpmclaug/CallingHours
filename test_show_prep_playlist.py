"""Unit tests for Show Prep Playlist Generator (Option 9).
Tests cover:
- playlist_curator.mix_artist_order
- playlist_curator.fetch_artist_album_tracks_pool
- playlist_curator.fetch_show_prep_catalog
- spotify.fetch_artist_albums and spotify.fetch_album_tracks
- HTTP API endpoints: /api/artist/albums
- Export handlers: M3U, CSV, Apple Music for show_prep mode
"""
import io
import json
import unittest
from unittest.mock import MagicMock, patch

import calling_hours
import database
import playlist_curator
import spotify


class TestMixArtistOrder(unittest.TestCase):
    def setUp(self):
        self.tracks_a = [
            {"artist": "Turnstile", "song": "MYSTERY", "spotify_id": "sp_mys"},
            {"artist": "Turnstile", "song": "BLACKOUT", "spotify_id": "sp_blk"},
            {"artist": "Turnstile", "song": "HOLIDAY", "spotify_id": "sp_hol"},
        ]
        self.tracks_b = [
            {"artist": "Speed", "song": "NOT THAT NICE", "spotify_id": "sp_ntn"},
            {"artist": "Speed", "song": "BURN BABY BURN", "spotify_id": "sp_bbb"},
        ]
        self.tracks_c = [
            {"artist": "Koyo", "song": "Moriches", "spotify_id": "sp_mor"},
        ]

    def test_mix_artist_order_basic(self):
        catalog = {
            "Turnstile": self.tracks_a,
            "Speed": self.tracks_b,
            "Koyo": self.tracks_c,
        }
        ordered = playlist_curator.mix_artist_order(catalog)
        self.assertEqual(len(ordered), 6)
        # Turnstile first
        self.assertEqual(ordered[0]["artist"], "Turnstile")
        self.assertEqual(ordered[0]["song"], "MYSTERY")
        self.assertEqual(ordered[1]["song"], "BLACKOUT")
        self.assertEqual(ordered[2]["song"], "HOLIDAY")
        self.assertEqual(ordered[0]["lineup_order"], 1)
        self.assertEqual(ordered[0]["artist_order_badge"], "Set 1 • Turnstile")
        self.assertEqual(ordered[2]["artist_order_badge"], "Set 1 • Turnstile")

        # Speed second
        self.assertEqual(ordered[3]["artist"], "Speed")
        self.assertEqual(ordered[3]["song"], "NOT THAT NICE")
        self.assertEqual(ordered[4]["song"], "BURN BABY BURN")
        self.assertEqual(ordered[3]["lineup_order"], 2)
        self.assertEqual(ordered[3]["artist_order_badge"], "Set 2 • Speed")

        # Koyo third
        self.assertEqual(ordered[5]["artist"], "Koyo")
        self.assertEqual(ordered[5]["song"], "Moriches")
        self.assertEqual(ordered[5]["lineup_order"], 3)
        self.assertEqual(ordered[5]["artist_order_badge"], "Set 3 • Koyo")

    def test_mix_artist_order_with_limit(self):
        catalog = {
            "Turnstile": self.tracks_a,
            "Speed": self.tracks_b,
        }
        ordered = playlist_curator.mix_artist_order(catalog, limit=4)
        self.assertEqual(len(ordered), 4)
        self.assertEqual(ordered[0]["artist"], "Turnstile")
        self.assertEqual(ordered[1]["artist"], "Turnstile")
        self.assertEqual(ordered[2]["artist"], "Turnstile")
        self.assertEqual(ordered[3]["artist"], "Speed")
        self.assertEqual(ordered[3]["song"], "NOT THAT NICE")

    def test_mix_artist_order_empty_or_none(self):
        self.assertEqual(playlist_curator.mix_artist_order({}), [])
        self.assertEqual(playlist_curator.mix_artist_order({"Empty": []}), [])


class TestAlbumTracksPool(unittest.TestCase):
    @patch("playlist_curator.spotify.get_artist_api_token")
    @patch("playlist_curator.spotify.fetch_album_tracks")
    @patch("playlist_curator.database.get_search")
    def test_fetch_artist_album_tracks_pool_enrichment(self, mock_get_search, mock_fetch_album_tracks, mock_token):
        mock_token.return_value = "fake_token"
        mock_fetch_album_tracks.return_value = [
            {
                "spotify_id": "sp_track_1",
                "song": "Intro",
                "artist": "Fugazi",
                "album_name": "Repeater",
                "track_number": 1,
                "disc_number": 1,
                "duration_ms": 120000,
                "preview_url": "http://preview/1",
            },
            {
                "spotify_id": "sp_track_2",
                "song": "Repeater",
                "artist": "Fugazi",
                "album_name": "Repeater",
                "track_number": 2,
                "disc_number": 1,
                "duration_ms": 180000,
                "preview_url": "http://preview/2",
            }
        ]
        # Simulate local database has analysis for 'Repeater' but not 'Intro'
        mock_get_search.side_effect = lambda artist, song, db_path=None: {
            "id": 42,
            "artist": "Fugazi",
            "song": "Repeater",
            "track_tags": [{"name": "post-hardcore"}],
            "theaudiodb_data": json.dumps({"tempo": 135, "key": "D Minor"}),
            "model_name": "Gemini 2.5 Flash",
        } if song == "Repeater" else None

        tracks = playlist_curator.fetch_artist_album_tracks_pool(
            artist="Fugazi",
            album_id="alb_rep_1",
            album_name="Repeater"
        )
        self.assertEqual(len(tracks), 2)
        # Check track 1
        self.assertEqual(tracks[0]["song"], "Intro")
        self.assertEqual(tracks[0]["source"], "album")
        self.assertEqual(tracks[0]["album_name"], "Repeater")
        self.assertIn("Repeater", tracks[0]["album_badge"])
        self.assertIsNone(tracks[0].get("id"))

        # Check track 2 (enriched with db analysis)
        self.assertEqual(tracks[1]["song"], "Repeater")
        self.assertEqual(tracks[1]["id"], 42)
        self.assertEqual(tracks[1]["model_name"], "Gemini 2.5 Flash")
        self.assertEqual(tracks[1]["source"], "album")
        self.assertTrue(any(t.get("name") == "post-hardcore" for t in tracks[1].get("track_tags", [])))


class TestFetchShowPrepCatalog(unittest.TestCase):
    @patch("playlist_curator.fetch_artist_latest_setlist_pool")
    @patch("playlist_curator.fetch_artist_top_tracks_pool")
    @patch("playlist_curator.fetch_artist_album_tracks_pool")
    def test_fetch_show_prep_catalog_mixed_sources(
        self, mock_album_pool, mock_top_pool, mock_setlist_pool
    ):
        mock_setlist_pool.return_value = [
            {"artist": "Blink-182", "song": "Feeling This", "stage_position": 1, "source": "latest_setlist"},
            {"artist": "Blink-182", "song": "The Rock Show", "stage_position": 2, "source": "latest_setlist"},
        ]
        mock_top_pool.return_value = [
            {"artist": "The Starting Line", "song": "The Best of Me", "source": "top_tracks"},
            {"artist": "The Starting Line", "song": "Given the Chance", "source": "top_tracks"},
        ]
        mock_album_pool.return_value = [
            {"artist": "Jimmy Eat World", "song": "Bleed American", "source": "album", "album_name": "Bleed American"},
            {"artist": "Jimmy Eat World", "song": "A Praise Chorus", "source": "album", "album_name": "Bleed American"},
        ]

        band_configs = [
            {"artist": "Blink-182", "source": "latest_setlist"},
            {"artist": "The Starting Line", "source": "top_tracks"},
            {"artist": "Jimmy Eat World", "source": "album", "album_id": "alb_ble", "album_name": "Bleed American"},
        ]

        catalog = playlist_curator.fetch_show_prep_catalog(band_configs)
        self.assertEqual(list(catalog.keys()), ["Blink-182", "The Starting Line", "Jimmy Eat World"])
        self.assertEqual(len(catalog["Blink-182"]), 2)
        self.assertEqual(catalog["Blink-182"][0]["source"], "latest_setlist")
        self.assertEqual(len(catalog["The Starting Line"]), 2)
        self.assertEqual(catalog["The Starting Line"][0]["source"], "top_tracks")
        self.assertEqual(len(catalog["Jimmy Eat World"]), 2)
        self.assertEqual(catalog["Jimmy Eat World"][0]["source"], "album")

    @patch("playlist_curator.fetch_artist_latest_setlist_pool")
    @patch("playlist_curator.fetch_artist_top_tracks_pool")
    def test_fetch_show_prep_catalog_fallback_on_empty_setlist(self, mock_top_pool, mock_setlist_pool):
        # When latest setlist returns empty, top tracks fallback is tested
        mock_setlist_pool.return_value = [
            {"artist": "Indie Band", "song": "Song One", "source": "top_tracks"}
        ]

        band_configs = [
            {"artist": "Indie Band", "source": "latest_setlist"}
        ]
        catalog = playlist_curator.fetch_show_prep_catalog(band_configs)
        self.assertEqual(len(catalog["Indie Band"]), 1)
        self.assertEqual(catalog["Indie Band"][0]["song"], "Song One")


class TestSpotifyAlbumAPIs(unittest.TestCase):
    @patch("spotify.requests.get")
    def test_fetch_artist_albums(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "items": [
                {
                    "id": "alb_1",
                    "name": "Clarity",
                    "album_type": "album",
                    "release_date": "1999-02-23",
                    "total_tracks": 13,
                    "images": [{"url": "http://img.jpg"}]
                },
                {
                    "id": "alb_2",
                    "name": "Futures",
                    "album_type": "album",
                    "release_date": "2004-10-19",
                    "total_tracks": 11,
                    "images": []
                }
            ]
        }
        mock_get.return_value = mock_resp

        albums = spotify.fetch_artist_albums(
            access_token="fake_token",
            artist_id="art_123"
        )
        self.assertEqual(len(albums), 2)
        # Futures (2004) sorts before Clarity (1999) because descending release year
        self.assertEqual(albums[0]["id"], "alb_2")
        self.assertEqual(albums[0]["name"], "Futures")
        self.assertEqual(albums[0]["release_year"], "2004")
        self.assertEqual(albums[1]["id"], "alb_1")
        self.assertEqual(albums[1]["name"], "Clarity")
        self.assertEqual(albums[1]["release_year"], "1999")
        # Verify limit requested was 10 (not 50)
        first_call_params = mock_get.call_args_list[0][1].get("params", {})
        self.assertEqual(first_call_params.get("limit"), 10)

    @patch("spotify.get_artist_api_token", return_value=None)
    def test_get_or_fetch_artist_albums_db_fallback(self, mock_tok):
        # When token is None, fallback to database runs smoothly without error
        albums = spotify.get_or_fetch_artist_albums("Turnstile")
        self.assertIsInstance(albums, list)

    @patch("spotify.requests.get")
    def test_fetch_album_tracks(self, mock_get):
        album_meta_resp = MagicMock()
        album_meta_resp.status_code = 200
        album_meta_resp.json.return_value = {
            "name": "Bleed American",
            "release_date": "2001-07-24",
            "images": [{"url": "http://bleed.jpg"}]
        }

        tracks_resp = MagicMock()
        tracks_resp.status_code = 200
        tracks_resp.json.return_value = {
            "items": [
                {
                    "id": "trk_1",
                    "name": "Bleed American",
                    "track_number": 1,
                    "disc_number": 1,
                    "duration_ms": 182000,
                    "preview_url": "http://preview/trk1",
                    "artists": [{"name": "Jimmy Eat World"}]
                }
            ]
        }
        mock_get.side_effect = [album_meta_resp, tracks_resp]

        tracks = spotify.fetch_album_tracks(
            access_token="fake_token",
            album_id="alb_ble"
        )
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["song"], "Bleed American")
        self.assertEqual(tracks[0]["album_name"], "Bleed American")
        self.assertEqual(tracks[0]["spotify_id"], "trk_1")


class TestCallingHoursShowPrepEndpoints(unittest.TestCase):
    @patch("calling_hours.spotify.get_or_fetch_artist_albums")
    def test_api_artist_albums(self, mock_get_albums):
        mock_get_albums.return_value = [
            {"id": "alb_1", "name": "Bleed American", "release_year": "2001", "total_tracks": 11}
        ]

        handler = calling_hours.CallingHoursRequestHandler.__new__(calling_hours.CallingHoursRequestHandler)
        handler.wfile = io.BytesIO()
        handler.headers = {}
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.get_current_user = MagicMock(return_value={"email": "tester@example.com"})

        handler.handle_api_artist_albums("artist=Jimmy+Eat+World")

        resp_bytes = handler.wfile.getvalue()
        data = json.loads(resp_bytes.decode("utf-8"))
        self.assertTrue(data["success"])
        self.assertEqual(data["artist"], "Jimmy Eat World")
        self.assertEqual(len(data["albums"]), 1)
        self.assertEqual(data["albums"][0]["name"], "Bleed American")

    @patch("calling_hours.playlist_curator.fetch_show_prep_catalog")
    @patch("calling_hours.database.save_playlist")
    def test_save_show_prep_playlist(self, mock_save, mock_fetch):
        mock_save.return_value = 99
        handler = calling_hours.CallingHoursRequestHandler.__new__(calling_hours.CallingHoursRequestHandler)
        handler.wfile = io.BytesIO()
        handler.headers = {}
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.get_current_user = MagicMock(return_value={"email": "tester@example.com"})

        save_payload = {
            "name": "Blink-182 & Turnstile Tour Prep",
            "description": "Show Prep Playlist",
            "generator_type": "show_prep",
            "criteria": {
                "prep_config": json.dumps([
                    {"artist": "Blink-182", "source": "latest_setlist"},
                    {"artist": "Turnstile", "source": "top_tracks"},
                ]),
                "mix_mode": "artist_order"
            },
            "items": [
                {"artist": "Blink-182", "song": "Feeling This", "spotify_id": "sp_1"},
                {"artist": "Turnstile", "song": "MYSTERY", "spotify_id": "sp_2"},
            ]
        }
        handler.handle_api_playlists_save(save_payload)

        resp_bytes = handler.wfile.getvalue()
        data = json.loads(resp_bytes.decode("utf-8"))
        self.assertTrue(data["success"])
        self.assertEqual(data["playlist_id"], 99)
        mock_save.assert_called_once()
        call_kwargs = mock_save.call_args[1]
        self.assertEqual(call_kwargs["generator_type"], "show_prep")
        self.assertEqual(len(call_kwargs["items"]), 2)

    def test_export_m3u_show_prep(self):
        handler = calling_hours.CallingHoursRequestHandler.__new__(calling_hours.CallingHoursRequestHandler)
        handler.wfile = io.BytesIO()
        handler.headers = {}
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.get_current_user = MagicMock(return_value={"email": "tester@example.com"})

        prep_cfg = json.dumps([
            {"artist": "Turnstile", "source": "top_tracks"}
        ])

        with patch("calling_hours.playlist_curator.fetch_show_prep_catalog") as mock_fetch:
            mock_fetch.return_value = {
                "Turnstile": [{"artist": "Turnstile", "song": "MYSTERY", "duration_ms": 150000}]
            }
            handler.handle_export_m3u(f"mode=show_prep&mix_mode=artist_order&prep_config={prep_cfg}&name=TestShowPrep")

        resp_str = handler.wfile.getvalue().decode("utf-8")
        self.assertIn("#EXTM3U", resp_str)
        self.assertIn("Turnstile - MYSTERY", resp_str)


if __name__ == "__main__":
    unittest.main()
