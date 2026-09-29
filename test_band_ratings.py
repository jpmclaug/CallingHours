import json
import os
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from unittest.mock import patch

import database
import calling_hours
import band_recommender


class TestBandRatingsUIAndRecommender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.temp_dir.name, "test_band_ratings.db")
        os.environ['DATABASE_PATH'] = cls.db_path
        database.DATABASE_PATH = cls.db_path
        calling_hours.DATABASE_PATH = cls.db_path
        database.init_db(cls.db_path)

        # Create session for the test user
        cls.test_email = "jpmclaug@gmail.com"
        cls.session_id = database.create_session(cls.test_email, db_path=cls.db_path)
        cls.auth_headers = {
            "Cookie": f"session_id={cls.session_id}",
            "Content-Type": "application/json",
        }

        # Start ephemeral test HTTP server
        cls.server = calling_hours.http.server.ThreadingHTTPServer(('127.0.0.1', 0), calling_hours.CallingHoursRequestHandler)
        cls.port = cls.server.server_port
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp_dir.cleanup()

    def authed_get(self, path: str):
        req = urllib.request.Request(f"{self.base_url}{path}", headers={"Cookie": f"session_id={self.session_id}"})
        return urllib.request.urlopen(req)

    def authed_post_json(self, path: str, data: dict):
        body = json.dumps(data).encode('utf-8')
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=self.auth_headers,
            method="POST"
        )
        return urllib.request.urlopen(req)

    # ---------------------------------------------------------
    # 1. Widget Unit Tests
    # ---------------------------------------------------------
    def test_widget_rendering(self):
        # Empty artist returns empty string
        self.assertEqual(calling_hours.build_band_rating_widget(""), "")

        # Default widget for an unrated band
        widget = calling_hours.build_band_rating_widget(
            artist="Bane",
            current_rating=None,
            compact=False,
            show_header=True
        )
        self.assertIn("data-artist=\"Bane\"", widget)
        self.assertIn("Not yet rated", widget)
        self.assertIn("data-rating=\"0\"", widget)
        self.assertIn("data-rating=\"1\"", widget)
        self.assertIn("data-rating=\"2\"", widget)
        self.assertIn("data-rating=\"3\"", widget)
        self.assertIn("data-rating=\"4\"", widget)
        self.assertIn("data-rating=\"5\"", widget)
        self.assertIn("🏆", widget)
        self.assertIn("5: Absolute favorite", widget)

        # Compact widget for rating 5
        compact_widget = calling_hours.build_band_rating_widget(
            artist="Have Heart",
            current_rating=5,
            compact=True,
            show_header=False
        )
        self.assertIn("compact-widget", compact_widget)
        self.assertIn("rating-5", compact_widget)
        self.assertIn("🏆 5★ Absolute favorite", compact_widget)

    # ---------------------------------------------------------
    # 2. Recommender Engine Logic Unit Tests
    # ---------------------------------------------------------
    def test_recommender_weights_and_suppression(self):
        rec_email = "recommender_spec@test.local"
        # Rate several bands
        database.save_band_rating("Turnstile", 5, user_email=rec_email, db_path=self.db_path)
        database.save_band_rating("Touche Amore", 4, user_email=rec_email, db_path=self.db_path)
        database.save_band_rating("Bad Luck", 1, user_email=rec_email, db_path=self.db_path) # Disliked

        # Seed mock similar artists in database metadata cache (at least 5 to satisfy cache check)
        database.save_artist_metadata(
            artist="Turnstile",
            similar_artists=[
                {"name": "Gouge Away", "match": 0.9},
                {"name": "Bad Luck", "match": 0.8}, # Must be suppressed because rating is 1
                {"name": "Militarie Gun", "match": 0.85},
                {"name": "Angel Du$t", "match": 0.82},
                {"name": "Drug Church", "match": 0.80},
            ],
            db_path=self.db_path
        )
        database.save_artist_metadata(
            artist="Touche Amore",
            similar_artists=[
                {"name": "Militarie Gun", "match": 0.95}, # Synergy: appears in both Turnstile and Touche Amore!
                {"name": "La Dispute", "match": 0.88},
                {"name": "Defeater", "match": 0.82},
                {"name": "Pianos Become the Teeth", "match": 0.80},
                {"name": "Modern Life Is War", "match": 0.78},
            ],
            db_path=self.db_path
        )

        res = band_recommender.get_related_band_recommendations(
            user_email=rec_email,
            limit=10,
            db_path=self.db_path
        )
        recs = res.get("recommendations", [])
        rec_names = [r["artist"] for r in recs]

        # 1s must be suppressed
        self.assertNotIn("Bad Luck", rec_names)
        self.assertNotIn("Turnstile", rec_names) # Seed itself suppressed

        # Recommendations present
        self.assertIn("Militarie Gun", rec_names)
        self.assertIn("Gouge Away", rec_names)
        self.assertIn("La Dispute", rec_names)

        # Militarie Gun should have highest score due to multi-seed synergy boost (Turnstile 5 + Touche Amore 4)
        militarie = next(r for r in recs if r["artist"] == "Militarie Gun")
        self.assertIn("Turnstile", militarie["reason"])
        self.assertIn("Touche Amore", militarie["reason"])
        self.assertGreater(militarie["score"], 5.0)

    def test_single_artist_recommendations(self):
        database.save_artist_metadata(
            artist="Deftones",
            similar_artists=[
                {"name": "Chevelle", "match": 0.85},
                {"name": "Korn", "match": 0.75},
            ],
            db_path=self.db_path
        )
        res = band_recommender.get_recommendations_for_single_artist(
            artist="Deftones",
            user_email=self.test_email,
            db_path=self.db_path
        )
        recs = res.get("recommendations", [])
        rec_names = [r["artist"] for r in recs]
        self.assertIn("Chevelle", rec_names)
        self.assertIn("Korn", rec_names)

    # ---------------------------------------------------------
    # 3. HTTP API Endpoints Tests
    # ---------------------------------------------------------
    def test_api_save_and_get_band_rating(self):
        # 1. Save rating 4 for Converge
        post_data = {"artist": "Converge", "rating": 4, "notes": "Mathcore legends"}
        with self.authed_post_json("/api/band-rating", post_data) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("rating"), 4)

        # 2. Retrieve via GET /api/band-rating?artist=Converge
        with self.authed_get("/api/band-rating?artist=Converge") as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertEqual(data.get("rating"), 4)
            self.assertEqual(data["record"]["notes"], "Mathcore legends")

        # 3. Update rating to 5 (Favorite)
        post_data2 = {"artist": "Converge", "rating": 5}
        with self.authed_post_json("/api/band-rating", post_data2) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("rating"), 5)

        # Verify update
        with self.authed_get("/api/band-rating?artist=Converge") as resp:
            data = json.loads(resp.read().decode('utf-8'))
            self.assertEqual(data.get("rating"), 5)

    def test_api_validation(self):
        # Rating out of bounds (> 5)
        try:
            self.authed_post_json("/api/band-rating", {"artist": "Converge", "rating": 6})
            self.fail("Expected HTTPError 400 for rating > 5")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)

        # Rating out of bounds (< 0)
        try:
            self.authed_post_json("/api/band-rating", {"artist": "Converge", "rating": -1})
            self.fail("Expected HTTPError 400 for rating < 0")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)

        # Missing artist
        try:
            self.authed_post_json("/api/band-rating", {"artist": "", "rating": 3})
            self.fail("Expected HTTPError 400 for missing artist")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)

    def test_api_band_ratings_list_and_stats(self):
        database.save_band_rating("Title Fight", 5, user_email=self.test_email, db_path=self.db_path)
        with self.authed_get("/api/band-ratings") as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertIn("ratings", data)
            self.assertIn("stats", data)
            self.assertGreaterEqual(data["count"], 1)

    def test_api_band_recommendations(self):
        with self.authed_get("/api/band-recommendations") as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertIn("recommendations", data)
            self.assertIn("seed_artists", data)

    def test_api_delete_band_rating(self):
        # Save a rating to delete
        database.save_band_rating("Temporary Band", 2, user_email=self.test_email, db_path=self.db_path)
        
        with self.authed_post_json("/api/band-rating/delete", {"artist": "Temporary Band"}) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertTrue(data.get("success"))

        # Verify deletion
        rec = database.get_band_rating("Temporary Band", user_email=self.test_email, db_path=self.db_path)
        self.assertIsNone(rec)

    # ---------------------------------------------------------
    # 4. HTML Page Rendering Tests
    # ---------------------------------------------------------
    def test_render_ratings_pages(self):
        # Main Discover Tab before rating (empty state / invitation)
        with self.authed_get("/ratings") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("Band Rankings &amp; Discovery", html)
            self.assertIn("Quick Rate Any Band", html)
            self.assertIn("Rate Analyzed Bands", html)

        # Rate a band so seeds become active
        database.save_band_rating("Turnstile", 5, user_email=self.test_email, db_path=self.db_path)
        with self.authed_get("/ratings") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("Active Recommendation Seeds", html)

        # Analyzed Bands Tab
        # First save an analyzed song so analyzed list has items
        database.save_analysis(
            artist="Fiddlehead",
            song="Million Miles",
            analysis="Some AI analysis",
            lyrics="Some lyrics",
            db_path=self.db_path
        )
        with self.authed_get("/ratings?tab=analyzed") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("Rate Analyzed Bands", html)
            self.assertIn("Fiddlehead", html)
            self.assertIn("data-artist=\"Fiddlehead\"", html)

        # My Ratings Tab
        with self.authed_get("/ratings?tab=my_ratings") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("Turnstile", html)
            self.assertIn("Converge", html)
            self.assertIn("deleteRating", html)


if __name__ == '__main__':
    unittest.main()
