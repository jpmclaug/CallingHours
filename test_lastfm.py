import os
import unittest
from unittest.mock import patch, MagicMock
import lastfm
import database
import tempfile

class TestLastFM(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_lastfm.db")
        database.init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_clean_tag_list(self):
        # Test list of dicts
        raw = [
            {"name": "post-hardcore", "count": 100, "url": "https://www.last.fm/tag/post-hardcore"},
            {"name": "punk", "count": "80"},
            {"name": "", "count": 50},  # should be skipped
            "invalid",  # should be skipped
        ]
        cleaned = lastfm._clean_tag_list(raw, limit=5)
        self.assertEqual(len(cleaned), 2)
        self.assertEqual(cleaned[0]["name"], "post-hardcore")
        self.assertEqual(cleaned[0]["count"], 100)
        self.assertEqual(cleaned[1]["name"], "punk")
        self.assertEqual(cleaned[1]["count"], 80)

        # Test single dict (Last.fm quirk when only 1 tag exists)
        single = {"name": "shoegaze", "count": 95}
        cleaned_single = lastfm._clean_tag_list(single)
        self.assertEqual(len(cleaned_single), 1)
        self.assertEqual(cleaned_single[0]["name"], "shoegaze")

        # Test empty/None
        self.assertEqual(lastfm._clean_tag_list([]), [])
        self.assertEqual(lastfm._clean_tag_list(None), [])

    def test_clean_track_list(self):
        raw = [
            {"name": "Calling Hours", "playcount": "15000", "listeners": "3200", "@attr": {"rank": "1"}, "url": "https://last.fm/track1"},
            {"name": "Curtains", "playcount": 12000, "listeners": "2800", "url": "https://last.fm/track2"},
        ]
        cleaned = lastfm._clean_track_list(raw)
        self.assertEqual(len(cleaned), 2)
        self.assertEqual(cleaned[0]["name"], "Calling Hours")
        self.assertEqual(cleaned[0]["playcount"], 15000)
        self.assertEqual(cleaned[0]["listeners"], 3200)
        self.assertEqual(cleaned[0]["rank"], 1)

        # Single dict
        single = {"name": "Solo Track", "playcount": "500", "listeners": "100"}
        cleaned_single = lastfm._clean_track_list(single)
        self.assertEqual(len(cleaned_single), 1)
        self.assertEqual(cleaned_single[0]["name"], "Solo Track")

    @patch("requests.get")
    def test_fetch_track_top_tags(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "toptags": {
                "tag": [
                    {"name": "alternative rock", "count": 100, "url": "https://last.fm/tag/alt"},
                    {"name": "indie", "count": 75, "url": "https://last.fm/tag/indie"}
                ]
            }
        }
        mock_get.return_value = mock_resp

        tags = lastfm.fetch_track_top_tags("Blink-182", "Dammit", api_key="dummy_key")
        self.assertEqual(len(tags), 2)
        self.assertEqual(tags[0]["name"], "alternative rock")
        self.assertEqual(tags[1]["name"], "indie")

    @patch("requests.get")
    def test_fetch_artist_top_tags(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "toptags": {
                "tag": [
                    {"name": "pop punk", "count": 100},
                    {"name": "punk rock", "count": 85}
                ]
            }
        }
        mock_get.return_value = mock_resp

        tags = lastfm.fetch_artist_top_tags("Blink-182", api_key="dummy_key")
        self.assertEqual(len(tags), 2)
        self.assertEqual(tags[0]["name"], "pop punk")

    @patch("requests.get")
    def test_fetch_artist_top_tracks(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "toptracks": {
                "track": [
                    {"name": "All the Small Things", "playcount": "100000", "listeners": "50000", "@attr": {"rank": "1"}},
                    {"name": "What's My Age Again?", "playcount": "90000", "listeners": "45000", "@attr": {"rank": "2"}}
                ]
            }
        }
        mock_get.return_value = mock_resp

        tracks = lastfm.fetch_artist_top_tracks("Blink-182", api_key="dummy_key")
        self.assertEqual(len(tracks), 2)
        self.assertEqual(tracks[0]["name"], "All the Small Things")
        self.assertEqual(tracks[1]["name"], "What's My Age Again?")

    @patch("lastfm.fetch_artist_top_tags")
    @patch("lastfm.fetch_artist_top_tracks")
    def test_get_or_fetch_artist_metadata_caching(self, mock_top_tracks, mock_top_tags):
        mock_top_tags.return_value = [{"name": "emo", "count": 90, "url": "https://last.fm/tag/emo"}]
        mock_top_tracks.return_value = [{"name": "Song 1", "playcount": 500, "listeners": 100, "rank": 1, "url": ""}]

        # First call: fetches from Last.fm and persists
        res1 = lastfm.get_or_fetch_artist_metadata("Sunny Day Real Estate", api_key="key", db_path=self.db_path)
        self.assertFalse(res1["cached"])
        self.assertEqual(len(res1["tags"]), 1)
        self.assertEqual(len(res1["top_tracks"]), 1)
        self.assertEqual(mock_top_tags.call_count, 1)

        # Second call: served from database cache, no network call
        res2 = lastfm.get_or_fetch_artist_metadata("sunny day real estate", api_key="key", db_path=self.db_path)
        self.assertTrue(res2["cached"])
        self.assertEqual(len(res2["tags"]), 1)
        self.assertEqual(res2["tags"][0]["name"], "emo")
        self.assertEqual(mock_top_tags.call_count, 1)  # not called again!

    @patch("lastfm.fetch_track_top_tags")
    def test_get_or_fetch_track_tags_caching(self, mock_fetch_track_tags):
        mock_fetch_track_tags.return_value = [{"name": "post-rock", "count": 99, "url": ""}]

        # Save an initial search
        database.save_search("Mogwai", "Mogwai Fear Satan", lyrics="instrumental", db_path=self.db_path)

        # First call fetches from API and saves to database
        tags1 = lastfm.get_or_fetch_track_tags("Mogwai", "Mogwai Fear Satan", api_key="key", db_path=self.db_path)
        self.assertEqual(len(tags1), 1)
        self.assertEqual(tags1[0]["name"], "post-rock")
        self.assertEqual(mock_fetch_track_tags.call_count, 1)

        # Second call loads from database
        tags2 = lastfm.get_or_fetch_track_tags("Mogwai", "Mogwai Fear Satan", api_key="key", db_path=self.db_path)
        self.assertEqual(len(tags2), 1)
        self.assertEqual(tags2[0]["name"], "post-rock")
        self.assertEqual(mock_fetch_track_tags.call_count, 1)  # cached!

if __name__ == "__main__":
    unittest.main()
