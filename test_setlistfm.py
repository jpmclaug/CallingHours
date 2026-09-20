"""Unit tests for setlistfm.py module."""
import os
import unittest
from unittest.mock import MagicMock, patch

import setlistfm


class TestSetlistFm(unittest.TestCase):

    def setUp(self):
        # Save original env
        self.orig_key = os.environ.get("SETLIST_FM_API_KEY")

    def tearDown(self):
        if self.orig_key is not None:
            os.environ["SETLIST_FM_API_KEY"] = self.orig_key
        else:
            os.environ.pop("SETLIST_FM_API_KEY", None)

    def test_get_setlistfm_api_key_env(self):
        os.environ["SETLIST_FM_API_KEY"] = "mock_env_key_123"
        key = setlistfm.get_setlistfm_api_key()
        self.assertEqual(key, "mock_env_key_123")

    def test_get_setlistfm_api_key_fallback(self):
        os.environ.pop("SETLIST_FM_API_KEY", None)
        # Should fall back to calling_hours_secrets or return string/None
        key = setlistfm.get_setlistfm_api_key()
        self.assertTrue(key is None or isinstance(key, str))

    @patch("setlistfm.requests.get")
    def test_search_artist_exact_match(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "artist": [
                {"mbid": "split-id", "name": "Jimmy Eat World / Jebediah", "sortName": "Jimmy Eat World / Jebediah"},
                {"mbid": "real-mbid-456", "name": "Jimmy Eat World", "sortName": "Jimmy Eat World", "url": "https://setlist.fm/jimmy"},
            ]
        }
        mock_get.return_value = mock_resp

        result = setlistfm.search_artist("Jimmy Eat World", api_key="dummy_key")
        self.assertIsNotNone(result)
        self.assertEqual(result["mbid"], "real-mbid-456")
        self.assertEqual(result["name"], "Jimmy Eat World")
        self.assertEqual(result["url"], "https://setlist.fm/jimmy")

    @patch("setlistfm.requests.get")
    def test_search_artist_not_found(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp

        result = setlistfm.search_artist("Nonexistent Artist 9999", api_key="dummy_key")
        self.assertIsNone(result)

    def test_search_artist_no_key(self):
        with patch.object(setlistfm, "get_setlistfm_api_key", return_value=None):
            result = setlistfm.search_artist("Blink-182", api_key=None)
            self.assertIsNone(result)

    @patch("setlistfm.requests.get")
    def test_fetch_last_nc_show(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "total": 14,
            "setlist": [
                {
                    "id": "nc-show-1",
                    "eventDate": "15-11-2023",
                    "venue": {
                        "name": "PNC Arena",
                        "city": {"name": "Raleigh", "state": "North Carolina", "stateCode": "NC", "country": {"name": "United States"}}
                    },
                    "tour": {"name": "One More Time Tour"},
                    "url": "https://setlist.fm/show1",
                    "info": "Supported by Turnstile",
                    "sets": {
                        "set": [
                            {"song": [{"name": "Anthem Part Two"}, {"name": "The Rock Show"}]}
                        ]
                    }
                }
            ]
        }
        mock_get.return_value = mock_resp

        show = setlistfm.fetch_last_nc_show("artist-mbid-1", api_key="dummy_key")
        self.assertIsNotNone(show)
        self.assertEqual(show["venue_name"], "PNC Arena")
        self.assertEqual(show["city"], "Raleigh")
        self.assertEqual(show["tour_name"], "One More Time Tour")
        self.assertEqual(show["total_nc_shows"], 14)
        self.assertEqual(show["song_count"], 2)
        self.assertEqual(show["date_formatted"], "November 15, 2023")
        self.assertEqual(show["info"], "Supported by Turnstile")

    @patch("setlistfm.requests.get")
    def test_fetch_last_tours_and_co_performers(self, mock_get):
        # 1. Main artist setlists (2 shows from 2 different tours)
        artist_setlists = {
            "setlist": [
                {
                    "eventDate": "20-07-2024",
                    "tour": {"name": "One More Time Tour"},
                    "venue": {"id": "venue-1", "name": "Kia Center", "city": {"name": "Orlando"}},
                    "info": "With Pierce The Veil and Hot Milk",
                    "sets": {"set": [{"song": [{"name": "Feeling This"}]}]}
                },
                {
                    "eventDate": "10-05-2023",
                    "tour": {"name": "World Tour 2023"},
                    "venue": {"id": "venue-2", "name": "United Center", "city": {"name": "Chicago"}},
                    "info": "",
                    "sets": {"set": [{"song": [{"name": "Dammit"}]}]}
                }
            ]
        }

        # 2. Bill setlist for venue-2 date 10-05-2023 (shows Turnstile played same venue)
        venue_bill_2 = {
            "setlist": [
                {"artist": {"name": "Blink-182"}},
                {"artist": {"name": "Turnstile"}},
                {"artist": {"name": "Beauty School Dropout"}}
            ]
        }
        venue_bill_1 = {
            "setlist": [
                {"artist": {"name": "Blink-182"}}
            ]
        }

        def side_effect(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            params = kwargs.get("params", {})
            v_id = params.get("venueId")
            if v_id == "venue-2":
                resp.json.return_value = venue_bill_2
            elif v_id == "venue-1":
                resp.json.return_value = venue_bill_1
            else:
                resp.json.return_value = artist_setlists
            return resp

        mock_get.side_effect = side_effect

        tours = setlistfm.fetch_last_tours("blink-mbid", "Blink-182", api_key="dummy_key", max_tours=2)
        self.assertEqual(len(tours), 2)
        
        # Tour 1: from info note "With Pierce The Veil and Hot Milk"
        self.assertEqual(tours[0]["tour_name"], "One More Time Tour")
        self.assertIn("Pierce The Veil", tours[0]["played_with"])
        self.assertIn("Hot Milk", tours[0]["played_with"])

        # Tour 2: from venue bill "Turnstile", "Beauty School Dropout"
        self.assertEqual(tours[1]["tour_name"], "World Tour 2023")
        self.assertIn("Turnstile", tours[1]["played_with"])
        self.assertIn("Beauty School Dropout", tours[1]["played_with"])

    @patch("setlistfm.requests.get")
    def test_fetch_recent_setlists(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "setlist": [
                {
                    "id": "show-abc",
                    "eventDate": "04-09-2024",
                    "venue": {"name": "Hydro", "city": {"name": "Glasgow", "country": {"name": "United Kingdom"}}},
                    "tour": {"name": "One More Time Part 2"},
                    "url": "https://setlist.fm/show-abc",
                    "sets": {
                        "set": [
                            {"song": [{"name": "Stay Together for the Kids"}, {"name": "Down"}]}
                        ]
                    }
                }
            ]
        }
        mock_get.return_value = mock_resp

        recents = setlistfm.fetch_recent_setlists("blink-mbid", api_key="dummy_key", limit=1)
        self.assertEqual(len(recents), 1)
        self.assertEqual(recents[0]["venue_name"], "Hydro")
        self.assertEqual(recents[0]["city"], "Glasgow")
        self.assertEqual(recents[0]["song_count"], 2)
        self.assertIn("Stay Together for the Kids", recents[0]["sample_songs"])

    @patch("setlistfm.fetch_recent_setlists")
    @patch("setlistfm.fetch_last_tours")
    @patch("setlistfm.fetch_last_nc_show")
    @patch("setlistfm.search_artist")
    @patch("database.save_artist_metadata")
    @patch("database.get_artist_metadata")
    def test_get_or_fetch_artist_setlist_data_caching(
        self, mock_get_meta, mock_save_meta, mock_search, mock_nc, mock_tours, mock_recents
    ):
        # 1. Cached in DB
        mock_get_meta.return_value = {
            "artist": "Paramore",
            "setlistfm_data": {
                "mbid": "paramore-mbid",
                "last_nc_show": {"venue_name": "Greensboro Coliseum"},
                "tours": [{"tour_name": "This Is Why Tour"}],
                "recent_setlists": []
            }
        }

        result = setlistfm.get_or_fetch_artist_setlist_data("Paramore", force_refresh=False)
        self.assertIsNotNone(result)
        self.assertEqual(result["mbid"], "paramore-mbid")
        self.assertEqual(result["last_nc_show"]["venue_name"], "Greensboro Coliseum")
        mock_search.assert_not_called()

        # 2. Force refresh or not cached
        mock_get_meta.return_value = None
        mock_search.return_value = {"mbid": "paramore-mbid", "name": "Paramore", "url": "https://setlist.fm/paramore"}
        mock_nc.return_value = {"venue_name": "Greensboro Coliseum", "city": "Greensboro"}
        mock_tours.return_value = [{"tour_name": "This Is Why Tour", "played_with": ["Foals"]}]
        mock_recents.return_value = [{"venue_name": "Greensboro Coliseum"}]

        refreshed = setlistfm.get_or_fetch_artist_setlist_data("Paramore", api_key="test_key", force_refresh=True)
        self.assertIsNotNone(refreshed)
        mock_search.assert_called_once_with("Paramore", api_key="test_key")
        mock_nc.assert_called_once_with("paramore-mbid", api_key="test_key")
        mock_tours.assert_called_once_with("paramore-mbid", "Paramore", api_key="test_key", max_tours=3)
        mock_save_meta.assert_called_once()


if __name__ == "__main__":
    unittest.main()
