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
    def test_fetch_last_show_in_location_california(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "total": 35,
            "setlist": [
                {
                    "id": "show-ca-1",
                    "eventDate": "12-10-2023",
                    "venue": {
                        "name": "The Roxy",
                        "city": {"name": "Los Angeles", "state": "California", "stateCode": "CA", "country": {"name": "United States"}}
                    },
                    "tour": {"name": "California Tour"},
                    "url": "https://setlist.fm/show-ca",
                    "info": "Sold out",
                    "sets": {
                        "set": [
                            {"song": [{"name": "Song 1"}, {"name": "Song 2"}, {"name": "Song 3"}]}
                        ]
                    }
                }
            ]
        }
        mock_get.return_value = mock_resp

        show = setlistfm.fetch_last_show_in_location("artist-mbid-ca", state_code="CA", api_key="dummy_key")
        self.assertIsNotNone(show)
        self.assertEqual(show["venue_name"], "The Roxy")
        self.assertEqual(show["city"], "Los Angeles")
        self.assertEqual(show["state"], "CA")
        self.assertEqual(show["total_location_shows"], 35)
        self.assertEqual(show["song_count"], 3)
        self.assertEqual(show["requested_state"], "CA")

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
        self.assertIn("most_played_with", refreshed)
        self.assertIsInstance(refreshed["most_played_with"], list)

    def test_extract_coperformers_from_tour_name(self):
        # 1. Ampersand / co-headliner
        c1 = setlistfm._extract_coperformers_from_tour_name(
            "Jimmy Eat World & Manchester Orchestra: The Amplified Echoes Tour",
            "Jimmy Eat World"
        )
        self.assertIn("Manchester Orchestra", c1)

        # 2. Slash split
        c2 = setlistfm._extract_coperformers_from_tour_name(
            "Blink-182 / Green Day: Pop Disaster Tour",
            "Blink-182"
        )
        self.assertIn("Green Day", c2)

        # 3. 'with' keyword
        c3 = setlistfm._extract_coperformers_from_tour_name(
            "Brand New with Modern Baseball Tour 2016",
            "Brand New"
        )
        self.assertIn("Modern Baseball", c3)

        # 4. Solitary artist or unrelated
        c4 = setlistfm._extract_coperformers_from_tour_name(
            "Bleed American 20th Anniversary Tour",
            "Jimmy Eat World"
        )
        self.assertEqual(c4, [])

    def test_get_demo_top_coperformers(self):
        jew_bands = setlistfm.get_demo_top_coperformers("Jimmy Eat World")
        self.assertEqual(len(jew_bands), 10)
        self.assertEqual(jew_bands[0]["rank"], 1)
        self.assertEqual(jew_bands[0]["band"], "Manchester Orchestra")
        self.assertGreater(jew_bands[0]["shows_shared"], 0)
        self.assertIsNotNone(jew_bands[0]["latest_show"])
        self.assertIn("date_formatted", jew_bands[0]["latest_show"])

        # Generic artist fallback
        generic_bands = setlistfm.get_demo_top_coperformers("The Foo Fighters")
        self.assertEqual(len(generic_bands), 10)
        self.assertEqual(generic_bands[0]["rank"], 1)

    @patch("setlistfm.fetch_co_performers_for_show")
    @patch("setlistfm.requests.get")
    def test_fetch_top_coperformers(self, mock_get, mock_venue_bands):
        mock_venue_bands.return_value = []
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "setlist": [
                {
                    "eventDate": "12-08-2023",
                    "venue": {"id": "v1", "name": "Ascend Amphitheater", "city": {"name": "Nashville", "stateCode": "TN"}},
                    "tour": {"name": "The Amplified Echoes Tour"},
                    "info": "Co-headlining with Manchester Orchestra. Middle Kids opened.",
                    "url": "https://setlist.fm/show1",
                    "sets": {
                        "set": [
                            {"song": [{"name": "Sweetness", "with": {"name": "Andy Hull"}}]}
                        ]
                    }
                },
                {
                    "eventDate": "14-08-2023",
                    "venue": {"id": "v2", "name": "Red Hat Amphitheater", "city": {"name": "Raleigh", "stateCode": "NC"}},
                    "tour": {"name": "The Amplified Echoes Tour"},
                    "info": "With Manchester Orchestra and Middle Kids",
                    "url": "https://setlist.fm/show2",
                    "sets": {"set": []}
                }
            ]
        }
        mock_get.return_value = mock_resp

        top_bands = setlistfm.fetch_top_coperformers(
            "mbid_123",
            "Jimmy Eat World",
            api_key="test_key",
            max_pages=1
        )
        self.assertGreater(len(top_bands), 0)
        band_names = [b["band"] for b in top_bands]
        self.assertIn("Manchester Orchestra", band_names)
        self.assertIn("Middle Kids", band_names)
        self.assertIn("Andy Hull", band_names)

        # Manchester Orchestra was in both shows -> rank 1
        mo = next(b for b in top_bands if b["band"] == "Manchester Orchestra")
        self.assertEqual(mo["shows_shared"], 2)
        self.assertEqual(mo["rank"], 1)

    def test_get_demo_average_setlist(self):
        demo = setlistfm.get_demo_average_setlist("Jimmy Eat World", "2023")
        self.assertEqual(demo["artist"], "Jimmy Eat World")
        self.assertEqual(demo["year"], "2023")
        self.assertTrue(demo["is_demo"])
        self.assertGreater(len(demo["tracks"]), 0)
        self.assertEqual(demo["tracks"][0]["song"], "Pain")
        self.assertEqual(demo["tracks"][0]["position"], 1)

    def test_get_artist_touring_years_defaults(self):
        years = setlistfm.get_artist_touring_years("Unknown Band", api_key=None)
        self.assertIsInstance(years, list)
        self.assertGreaterEqual(len(years), 5)
        # Should be sorted descending
        self.assertEqual(years, sorted(years, reverse=True))

    @patch("database.save_cached_average_setlist")
    @patch("database.get_cached_average_setlist", return_value=None)
    @patch("database.get_artist_metadata", return_value=None)
    @patch("setlistfm.search_artist")
    @patch("setlistfm.requests.get")
    def test_fetch_average_setlist_web_parsing(self, mock_get, mock_search, mock_meta, mock_cached, mock_save):
        mock_search.return_value = {
            "mbid": "test-mbid-123",
            "name": "Jimmy Eat World",
            "url": "https://www.setlist.fm/setlists/jimmy-eat-world-5bd69b28.html"
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """
        <html>
            <p>Note: only considered 20 of 22 setlists</p>
            <ul class="setlistList">
                <li><a class="songLabel">Sweetness</a></li>
                <li><a class="songLabel">Bleed American</a></li>
                <li><a class="songLabel">The Middle</a></li>
            </ul>
        </html>
        """
        mock_get.return_value = mock_resp

        res = setlistfm.fetch_average_setlist_by_year(
            artist_name="Jimmy Eat World",
            year="2023",
            api_key="test_key",
            force_refresh=True
        )
        self.assertEqual(res["artist"], "Jimmy Eat World")
        self.assertEqual(res["year"], "2023")
        self.assertEqual(res["method"], "web")
        self.assertEqual(res["considered_concerts"], 20)
        self.assertEqual(res["total_concerts"], 22)
        self.assertEqual(len(res["tracks"]), 3)
        self.assertEqual(res["tracks"][0]["song"], "Sweetness")
        self.assertEqual(res["tracks"][2]["song"], "The Middle")

    @patch("database.save_cached_average_setlist")
    @patch("database.get_cached_average_setlist", return_value=None)
    @patch("database.get_artist_metadata", return_value=None)
    @patch("setlistfm.search_artist")
    @patch("setlistfm.requests.get")
    def test_fetch_average_setlist_api_fallback(self, mock_get, mock_search, mock_meta, mock_cached, mock_save):
        mock_search.return_value = {
            "mbid": "test-mbid-456",
            "name": "Turnstile",
            "url": "https://www.setlist.fm/setlists/turnstile-123.html"
        }

        web_resp = MagicMock()
        web_resp.status_code = 202
        web_resp.text = ""

        api_resp = MagicMock()
        api_resp.status_code = 200
        api_resp.json.return_value = {
            "total": 3,
            "setlist": [
                {
                    "eventDate": "10-05-2023",
                    "sets": {
                        "set": [
                            {"song": [{"name": "MYSTERY"}, {"name": "BLACKOUT"}, {"name": "HOLIDAY"}, {"name": "FLY AGAIN"}, {"name": "T.L.C."}]}
                        ]
                    }
                },
                {
                    "eventDate": "12-05-2023",
                    "sets": {
                        "set": [
                            {"song": [{"name": "MYSTERY"}, {"name": "BLACKOUT"}, {"name": "HOLIDAY"}, {"name": "FLY AGAIN"}, {"name": "T.L.C."}]}
                        ]
                    }
                },
                {
                    "eventDate": "15-05-2023",
                    "sets": {
                        "set": [
                            {"song": [{"name": "MYSTERY"}, {"name": "BLACKOUT"}, {"name": "HOLIDAY"}, {"name": "FLY AGAIN"}, {"name": "T.L.C."}]}
                        ]
                    }
                }
            ]
        }

        mock_get.side_effect = [web_resp, api_resp]

        res = setlistfm.fetch_average_setlist_by_year(
            artist_name="Turnstile",
            year="2023",
            api_key="test_key",
            force_refresh=True
        )
        self.assertEqual(res["artist"], "Turnstile")
        self.assertEqual(res["year"], "2023")
        self.assertEqual(res["method"], "api")
        self.assertEqual(len(res["tracks"]), 5)
        self.assertEqual(res["tracks"][0]["song"], "MYSTERY")
        self.assertEqual(res["tracks"][0]["position"], 1)
        self.assertEqual(res["tracks"][4]["song"], "T.L.C.")

    @patch("database.get_cached_average_setlist")
    def test_fetch_average_setlist_from_cache(self, mock_get_cached):
        mock_get_cached.return_value = {
            "artist": "Jimmy Eat World",
            "year": "2023",
            "tracks": [{"song": "Pain", "position": 1}],
            "total_concerts": 47,
        }
        res = setlistfm.fetch_average_setlist_by_year("Jimmy Eat World", "2023", force_refresh=False)
        self.assertTrue(res.get("cached"))
        self.assertEqual(len(res["tracks"]), 1)
        self.assertEqual(res["tracks"][0]["song"], "Pain")

    @patch("database.save_cached_latest_setlist")
    @patch("database.get_cached_latest_setlist")
    @patch("database.get_artist_metadata")
    @patch("setlistfm.requests.get")
    def test_fetch_latest_setlist_with_min_tracks_multipage(
        self, mock_get, mock_get_meta, mock_get_cached, mock_save_cached
    ):
        mock_get_cached.return_value = None
        mock_get_meta.return_value = {"setlistfm_data": {"mbid": "bmth-mbid-123", "artist": "Bring Me The Horizon"}}

        # Page 1: setlist with only 5 songs (< 10)
        # Page 2: setlist with 12 songs (>= 10)
        page1_resp = MagicMock()
        page1_resp.status_code = 200
        page1_resp.json.return_value = {
            "setlist": [
                {
                    "eventDate": "01-08-2026",
                    "venue": {"name": "Festival Stage", "city": {"name": "Reading", "country": {"name": "UK"}}},
                    "sets": {"set": [{"song": [{"name": f"Song {i}"} for i in range(1, 6)]}]},
                }
            ]
        }

        page2_resp = MagicMock()
        page2_resp.status_code = 200
        page2_resp.json.return_value = {
            "setlist": [
                {
                    "eventDate": "15-07-2026",
                    "venue": {"name": "Headline Arena", "city": {"name": "London", "country": {"name": "UK"}}},
                    "tour": {"name": "NeX GEn Tour"},
                    "url": "https://setlist.fm/bmth-headline",
                    "sets": {"set": [{"song": [{"name": f"Headline Song {i}"} for i in range(1, 13)]}]},
                }
            ]
        }

        mock_get.side_effect = [page1_resp, page2_resp]

        res = setlistfm.fetch_latest_setlist_with_min_tracks(
            "Bring Me The Horizon",
            min_tracks=10,
            api_key="mock_key",
            force_refresh=True
        )

        self.assertIsNotNone(res)
        self.assertEqual(res["artist"], "Bring Me The Horizon")
        self.assertEqual(res["venue_name"], "Headline Arena")
        self.assertEqual(res["tour_name"], "NeX GEn Tour")
        self.assertEqual(len(res["tracks"]), 12)
        self.assertEqual(res["tracks"][0]["song"], "Headline Song 1")
        self.assertEqual(res["tracks"][0]["position"], 1)
        self.assertEqual(res["tracks"][11]["song"], "Headline Song 12")
        self.assertEqual(res["tracks"][11]["position"], 12)
        mock_save_cached.assert_called()

    @patch("database.get_cached_latest_setlist")
    def test_fetch_latest_setlist_cached(self, mock_get_cached):
        mock_get_cached.return_value = {
            "artist": "Underoath",
            "mbid": "underoath-mbid",
            "venue_name": "Fillmore",
            "tracks": [{"position": i, "song": f"Track {i}", "title": f"Track {i}"} for i in range(1, 12)],
            "song_count": 11,
        }

        res = setlistfm.fetch_latest_setlist_with_min_tracks("Underoath", min_tracks=10, force_refresh=False)
        self.assertIsNotNone(res)
        self.assertTrue(res.get("cached"))
        self.assertEqual(res["venue_name"], "Fillmore")
        self.assertEqual(len(res["tracks"]), 11)

    @patch("database.get_cached_latest_setlist")
    @patch("database.get_artist_metadata")
    @patch("setlistfm.requests.get")
    def test_fetch_latest_setlist_none_found(self, mock_get, mock_get_meta, mock_get_cached):
        mock_get_cached.return_value = None
        mock_get_meta.return_value = {"setlistfm_data": {"mbid": "short-mbid", "artist": "Short Band"}}

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "setlist": [
                {
                    "eventDate": "01-01-2026",
                    "sets": {"set": [{"song": [{"name": "Only One"}, {"name": "Only Two"}]}]}
                }
            ]
        }
        mock_get.return_value = resp

        res = setlistfm.fetch_latest_setlist_with_min_tracks("Short Band", min_tracks=10, api_key="mock_key", max_pages=1)
        self.assertIsNone(res)


if __name__ == "__main__":
    unittest.main()

