"""Unit tests for playlist_curator.py: Multi-Artist Top Tracks and Mixing Modes."""
import json
import unittest
from unittest.mock import MagicMock, patch

import playlist_curator


class TestMultiArtistPlaylistCurator(unittest.TestCase):
    def setUp(self):
        self.artist_a = "Jimmy Eat World"
        self.artist_b = "Taking Back Sunday"
        self.artist_c = "The Starting Line"

        self.tracks_a = [
            {"artist": self.artist_a, "song": "The Middle", "spotify_id": "sp_mid", "popularity": 85, "track_tags": [{"name": "emo"}, {"name": "rock"}]},
            {"artist": self.artist_a, "song": "Sweetness", "spotify_id": "sp_sw", "popularity": 75, "track_tags": [{"name": "emo"}, {"name": "anthem"}]},
            {"artist": self.artist_a, "song": "Hear You Me", "spotify_id": "sp_hym", "popularity": 70, "track_tags": [{"name": "acoustic"}, {"name": "sad"}]},
        ]
        self.tracks_b = [
            {"artist": self.artist_b, "song": "MakeDamnSure", "spotify_id": "sp_mds", "popularity": 80, "track_tags": [{"name": "post-hardcore"}, {"name": "emo"}]},
            {"artist": self.artist_b, "song": "Cute Without the 'E'", "spotify_id": "sp_cute", "popularity": 78, "track_tags": [{"name": "emo"}, {"name": "rock"}]},
        ]
        self.tracks_c = [
            {"artist": self.artist_c, "song": "The Best of Me", "spotify_id": "sp_bom", "popularity": 72, "track_tags": [{"name": "pop-punk"}]},
        ]

    def test_mix_alternating_equal_tracks(self):
        catalog = {
            self.artist_a: self.tracks_a[:2],
            self.artist_b: self.tracks_b[:2]
        }
        mixed = playlist_curator.mix_alternating(catalog)
        self.assertEqual(len(mixed), 4)
        self.assertEqual(mixed[0]["artist"], self.artist_a)
        self.assertEqual(mixed[0]["song"], "The Middle")
        self.assertEqual(mixed[1]["artist"], self.artist_b)
        self.assertEqual(mixed[1]["song"], "MakeDamnSure")
        self.assertEqual(mixed[2]["artist"], self.artist_a)
        self.assertEqual(mixed[2]["song"], "Sweetness")
        self.assertEqual(mixed[3]["artist"], self.artist_b)
        self.assertEqual(mixed[3]["song"], "Cute Without the 'E'")
        self.assertEqual(mixed[0]["mix_mode"], "alternating")
        self.assertEqual(mixed[0]["rotation_round"], 1)
        self.assertEqual(mixed[2]["rotation_round"], 2)

    def test_mix_alternating_unequal_tracks(self):
        catalog = {
            self.artist_a: self.tracks_a,      # 3 tracks
            self.artist_b: self.tracks_b[:1]   # 1 track
        }
        mixed = playlist_curator.mix_alternating(catalog)
        self.assertEqual(len(mixed), 4)
        # Round 1: A1, B1
        self.assertEqual(mixed[0]["song"], "The Middle")
        self.assertEqual(mixed[1]["song"], "MakeDamnSure")
        # Round 2: A2
        self.assertEqual(mixed[2]["song"], "Sweetness")
        # Round 3: A3
        self.assertEqual(mixed[3]["song"], "Hear You Me")

    def test_mix_alternating_three_artists_with_limit(self):
        catalog = {
            self.artist_a: self.tracks_a,
            self.artist_b: self.tracks_b,
            self.artist_c: self.tracks_c
        }
        mixed = playlist_curator.mix_alternating(catalog, limit=4)
        self.assertEqual(len(mixed), 4)
        self.assertEqual(mixed[0]["artist"], self.artist_a)
        self.assertEqual(mixed[1]["artist"], self.artist_b)
        self.assertEqual(mixed[2]["artist"], self.artist_c)
        self.assertEqual(mixed[3]["artist"], self.artist_a)

    def test_mix_thematic_heuristic(self):
        catalog = {
            self.artist_a: self.tracks_a,
            self.artist_b: self.tracks_b
        }
        res = playlist_curator.mix_thematic_heuristic(catalog)
        self.assertIn("tracks", res)
        self.assertIn("themes", res)
        self.assertIn("playlist_title", res)
        self.assertIn("playlist_description", res)
        self.assertEqual(res["engine"], "heuristic")
        self.assertGreater(len(res["tracks"]), 0)
        self.assertGreater(len(res["themes"]), 0)

        # Check track properties
        first = res["tracks"][0]
        self.assertEqual(first["mix_mode"], "thematic")
        self.assertTrue(first.get("theme"))
        self.assertTrue(first.get("connection_note"))

    @patch("playlist_curator.genai")
    def test_mix_thematic_gemini_success(self, mock_genai):
        catalog = {
            self.artist_a: self.tracks_a[:2],
            self.artist_b: self.tracks_b[:2]
        }

        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client

        gemini_json_payload = json.dumps({
            "playlist_title": "Jimmy Eat World & Taking Back Sunday: Emo Renaissance",
            "playlist_description": "An explosive journey from introspective angst to anthemic catharsis.",
            "curator_notes": "Both bands defined 2000s alternative rock through raw vulnerability.",
            "themes": [
                {
                    "name": "High-Energy Catharsis",
                    "description": "Explosive vocal tension and driving guitars."
                },
                {
                    "name": "Bittersweet Longing",
                    "description": "Emotional resonance exploring distance and heartbreak."
                }
            ],
            "tracks": [
                {
                    "artist": self.artist_a,
                    "song": "Sweetness",
                    "theme": "High-Energy Catharsis",
                    "connection_note": "Soaring anthemic refrain matches Taking Back Sunday's raw energy."
                },
                {
                    "artist": self.artist_b,
                    "song": "MakeDamnSure",
                    "theme": "High-Energy Catharsis",
                    "connection_note": "Dual-vocal attack echoes the urgent vocal catharsis of Sweetness."
                },
                {
                    "artist": self.artist_a,
                    "song": "The Middle",
                    "theme": "Bittersweet Longing",
                    "connection_note": "Uplifting lyrical optimism grounds the relationship turbulence."
                },
                {
                    "artist": self.artist_b,
                    "song": "Cute Without the 'E'",
                    "theme": "Bittersweet Longing",
                    "connection_note": "Acoustic-driven verse building into bitter relationship reflection."
                }
            ]
        })

        mock_interaction = MagicMock()
        mock_interaction.output_text = gemini_json_payload
        mock_client.interactions.create.return_value = mock_interaction

        res = playlist_curator.mix_thematic(
            catalog,
            gemini_api_key="dummy_gemini_key",
            model_name="gemini-3.8-flash"
        )

        self.assertEqual(res["engine"], "gemini")
        self.assertEqual(res["playlist_title"], "Emo Renaissance (Jimmy Eat World, Taking Back Sunday)")
        self.assertEqual(res["playlist_theme"], "Emo Renaissance")
        self.assertEqual(len(res["themes"]), 2)
        self.assertEqual(len(res["tracks"]), 4)
        self.assertEqual(res["tracks"][0]["song"], "Sweetness")
        self.assertEqual(res["tracks"][0]["theme"], "High-Energy Catharsis")
        self.assertIn("refrain matches", res["tracks"][0]["connection_note"])
        # Original track properties preserved
        self.assertEqual(res["tracks"][0]["spotify_id"], "sp_sw")

    @patch("playlist_curator.spotify.get_or_fetch_artist_spotify_data")
    @patch("playlist_curator.database.get_search")
    def test_fetch_artist_top_tracks_pool_spotify(self, mock_get_search, mock_spot_data):
        mock_spot_data.return_value = {
            "top_tracks": [
                {
                    "name": "The Middle",
                    "id": "sp_123",
                    "spotify_url": "https://open.spotify.com/track/sp_123",
                    "preview_url": "https://p.scdn.co/mp3-preview/123",
                    "album_name": "Bleed American",
                    "release_year": "2001",
                    "duration_ms": 166000,
                    "duration_formatted": "2:46",
                    "popularity": 85
                }
            ]
        }
        mock_get_search.return_value = {
            "id": 42,
            "analysis": "A masterpiece of adolescent encouragement.",
            "model_name": "gemini-3.8-flash",
            "track_tags": json.dumps([{"name": "emo"}, {"name": "rock"}]),
            "theaudiodb_data": json.dumps({"tempo": 162, "energy": 88})
        }

        tracks = playlist_curator.fetch_artist_top_tracks_pool("Jimmy Eat World", limit=5)
        self.assertEqual(len(tracks), 1)
        tr = tracks[0]
        self.assertEqual(tr["artist"], "Jimmy Eat World")
        self.assertEqual(tr["song"], "The Middle")
        self.assertEqual(tr["spotify_id"], "sp_123")
        self.assertEqual(tr["id"], 42)
        self.assertTrue(tr["is_analyzed"])
        self.assertEqual(tr["model_name"], "gemini-3.8-flash")
        self.assertEqual(tr["theaudiodb_data"]["tempo"], 162)

    @patch("playlist_curator.spotify.get_or_fetch_artist_spotify_data")
    @patch("playlist_curator.lastfm.fetch_artist_top_tracks")
    def test_fetch_artist_top_tracks_pool_lastfm_fallback(self, mock_lfm_tracks, mock_spot_data):
        # Spotify returns nothing
        mock_spot_data.return_value = {"top_tracks": []}
        mock_lfm_tracks.return_value = [
            {"name": "MakeDamnSure", "playcount": 120000, "listeners": 50000, "rank": 1}
        ]

        tracks = playlist_curator.fetch_artist_top_tracks_pool("Taking Back Sunday", limit=5)
        self.assertEqual(len(tracks), 1)
        tr = tracks[0]
        self.assertEqual(tr["artist"], "Taking Back Sunday")
        self.assertEqual(tr["song"], "MakeDamnSure")
        self.assertEqual(tr["source"], "lastfm")
        self.assertEqual(tr["playcount"], 120000)

    @patch("playlist_curator.fetch_artist_top_tracks_pool")
    def test_fetch_multi_artist_catalog(self, mock_fetch_pool):
        mock_fetch_pool.side_effect = lambda art, limit, user_email, db_path: [
            {"artist": art, "song": f"{art} Hit 1"},
            {"artist": art, "song": f"{art} Hit 2"},
        ]

        artists = ["Jimmy Eat World", "Taking Back Sunday", "Jimmy Eat World"]  # Test deduplication
        catalog = playlist_curator.fetch_multi_artist_catalog(artists, limit_per_artist=2)

        self.assertEqual(len(catalog), 2)
        self.assertIn("Jimmy Eat World", catalog)
        self.assertIn("Taking Back Sunday", catalog)
        self.assertEqual(len(catalog["Jimmy Eat World"]), 2)
        self.assertEqual(len(catalog["Taking Back Sunday"]), 2)

    def test_heuristic_parenthetical_title_format(self):
        catalog = {
            self.artist_a: self.tracks_a,
            self.artist_b: self.tracks_b
        }
        res = playlist_curator.mix_thematic_heuristic(catalog)
        title = res["playlist_title"]
        self.assertTrue(title.endswith(f"({self.artist_a}, {self.artist_b})"))
        self.assertNotIn(":", title)

    def test_generate_playlist_cover_art_pillow_fallback(self):
        import io
        from PIL import Image

        res = playlist_curator.generate_playlist_cover_art(
            playlist_name="Passionate Hardcore (Bane, Have Heart, Comeback Kid)",
            artists=["Bane", "Have Heart", "Comeback Kid"],
            themes=[{"name": "Aggressive Catharsis", "description": "Fast riffs and raw vocals."}],
            gemini_api_key=None
        )
        self.assertEqual(res["engine"], "pillow")
        self.assertLessEqual(res["size_bytes"], 250000)
        self.assertTrue(res["data_url"].startswith("data:image/jpeg;base64,"))
        self.assertTrue(len(res["image_b64"]) > 100)

        # Verify image properties
        img = Image.open(io.BytesIO(res["image_bytes"]))
        self.assertEqual(img.size, (640, 640))
        self.assertEqual(img.format, "JPEG")

    @patch("playlist_curator.genai")
    def test_generate_playlist_cover_art_gemini(self, mock_genai):
        import io
        from PIL import Image

        # Create a simple test image in memory
        test_img = Image.new("RGB", (800, 800), color=(30, 45, 90))
        buf = io.BytesIO()
        test_img.save(buf, format="JPEG")
        raw_jpeg_bytes = buf.getvalue()

        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_part = MagicMock()
        mock_part.inline_data.data = raw_jpeg_bytes
        mock_resp = MagicMock()
        mock_resp.parts = [mock_part]
        mock_client.models.generate_content.return_value = mock_resp

        res = playlist_curator.generate_playlist_cover_art(
            playlist_name="Passionate Hardcore (Bane, Have Heart, Comeback Kid)",
            artists=["Bane", "Have Heart", "Comeback Kid"],
            themes=[{"name": "Catharsis"}],
            gemini_api_key="mock_key"
        )
        self.assertEqual(res["engine"], "gemini")
        self.assertLessEqual(res["size_bytes"], 250000)
        self.assertTrue(res["data_url"].startswith("data:image/jpeg;base64,"))

        # Check resized to 640x640
        out_img = Image.open(io.BytesIO(res["image_bytes"]))
        self.assertEqual(out_img.size, (640, 640))
        self.assertEqual(out_img.format, "JPEG")

    @patch("requests.put")
    def test_spotify_upload_playlist_cover_image(self, mock_put):
        import spotify

        # Success case
        mock_resp_ok = MagicMock()
        mock_resp_ok.status_code = 202
        mock_put.return_value = mock_resp_ok
        ok = spotify.upload_playlist_cover_image("token123", "pl_id", "data:image/jpeg;base64,/9j/4AAQSkZJRg==")
        self.assertTrue(ok)
        mock_put.assert_called_with(
            "https://api.spotify.com/v1/playlists/pl_id/images",
            headers={"Authorization": "Bearer token123", "Content-Type": "image/jpeg"},
            data="/9j/4AAQSkZJRg==",
            timeout=10
        )

        # Failure case (e.g. 403 lack of scope)
        mock_resp_err = MagicMock()
        mock_resp_err.status_code = 403
        mock_resp_err.text = "Insufficient client scope"
        mock_put.return_value = mock_resp_err
        fail = spotify.upload_playlist_cover_image("token123", "pl_id", "/9j/4AAQSkZJRg==")
        self.assertFalse(fail)


if __name__ == "__main__":
    unittest.main()
