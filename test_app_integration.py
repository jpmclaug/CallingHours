import os
import tempfile
import unittest
from unittest.mock import patch
import threading
import http.server
import urllib.request
import urllib.parse
import json

import database
import calling_hours
import lastfm

class TestAppIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.temp_dir.name, "test_integration.db")
        os.environ['DATABASE_PATH'] = cls.db_path
        database.init_db(cls.db_path)

        # Create session for the seeded superadmin
        cls.session_id = database.create_session("jpmclaug@gmail.com", db_path=cls.db_path)
        cls.auth_headers = {"Cookie": f"session_id={cls.session_id}"}

        # Start a test HTTP server on an ephemeral port (port 0)
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), calling_hours.CallingHoursRequestHandler)
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
        req = urllib.request.Request(f"{self.base_url}{path}", headers=self.auth_headers)
        return urllib.request.urlopen(req)

    def authed_post(self, path: str, data: dict):
        body = urllib.parse.urlencode(data).encode('utf-8')
        req = urllib.request.Request(f"{self.base_url}{path}", data=body, headers=self.auth_headers)
        return urllib.request.urlopen(req)

    def test_01_initial_home_page(self):
        with self.authed_get("/") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("Calling Hours", html)
            self.assertIn("Previous Bands", html)
            self.assertIn("Search History", html)
            # Initially, no bands are saved
            self.assertIn("0 saved", html)

    def test_02_save_search_and_verify_bands_in_page_and_api(self):
        # Insert a search record directly or via database
        rec_id = database.save_search(
            artist="Blink-182",
            song="Dammit",
            lyrics="It's alright, to tell me what you think about me",
            source="Genius",
            song_url="https://genius.com/blink-182-dammit",
            db_path=self.db_path
        )
        self.assertGreater(rec_id, 0)

        # 1. Verify GET / lists Blink-182 in the band selector and datalist
        with self.authed_get("/") as resp:
            html = resp.read().decode('utf-8')
            self.assertIn("Blink-182", html)
            self.assertIn("1 saved", html)
            self.assertIn('<option value="Blink-182"', html)

        # 2. Verify GET /api/bands returns JSON
        with self.authed_get("/api/bands") as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertIn("bands", data)
            self.assertEqual(len(data["bands"]), 1)
            self.assertEqual(data["bands"][0]["artist"], "Blink-182")
            self.assertEqual(data["bands"][0]["song_count"], 1)

        # 3. Verify GET /api/songs?artist=Blink-182
        url = "/api/songs?artist=" + urllib.parse.quote("Blink-182")
        with self.authed_get(url) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertIn("songs", data)
            self.assertEqual(len(data["songs"]), 1)
            self.assertEqual(data["songs"][0]["song"], "Dammit")
            self.assertTrue(data["songs"][0]["has_lyrics"])

        # 4. Verify GET /api/search?id=...
        with self.authed_get(f"/api/search?id={rec_id}") as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertIn("search", data)
            self.assertEqual(data["search"]["artist"], "Blink-182")
            self.assertEqual(data["search"]["song"], "Dammit")

    def test_03_load_saved_song_by_id(self):
        rec = database.get_search("Blink-182", "Dammit", db_path=self.db_path)
        self.assertIsNotNone(rec)
        rec_id = rec["id"]

        with self.authed_get(f"/?id={rec_id}") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("Loaded saved search for", html)
            self.assertIn("Blink-182", html)
            self.assertIn("Dammit", html)
            self.assertIn("tell me what you think about me", html)

    def test_04_history_page(self):
        with self.authed_get("/history") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("Search History", html)
            self.assertIn("Blink-182", html)
            self.assertIn("Dammit", html)
            self.assertIn("Load Song", html)
            self.assertIn("/history/delete?id=", html)

    def test_05_delete_from_history(self):
        rec = database.get_search("Blink-182", "Dammit", db_path=self.db_path)
        rec_id = rec["id"]

        # Call delete endpoint
        with self.authed_get(f"/history/delete?id={rec_id}") as resp:
            self.assertEqual(resp.status, 200)

        # Record should be gone from database
        self.assertIsNone(database.get_search_by_id(rec_id, db_path=self.db_path))

        # API should show 0 bands
        with self.authed_get("/api/bands") as resp:
            data = json.loads(resp.read().decode('utf-8'))
            self.assertEqual(len(data["bands"]), 0)

    def test_06_navigation_tabs_and_app_header(self):
        # 1. Verify Home page has global app-header, admin link, and workspace-tabs
        with self.authed_get("/") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn('class="app-header"', html)
            self.assertIn('id="nav-link-song"', html)
            self.assertIn('id="nav-link-history"', html)
            self.assertIn('id="nav-link-prompts"', html)
            self.assertIn('id="nav-link-admin"', html)
            self.assertIn('id="workspace-tabs"', html)
            self.assertIn('id="tab-btn-search"', html)
            self.assertIn('id="tab-btn-lyrics"', html)
            self.assertIn('id="tab-btn-analysis"', html)
            self.assertIn('switchWorkspaceTab', html)

        # 2. Verify History page has global app-header with history link active
        with self.authed_get("/history") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn('class="app-header"', html)
            self.assertIn('id="nav-link-history"', html)
            self.assertIn('class="app-nav-link active" id="nav-link-history"', html)

        # 3. Verify Prompts page has global app-header with prompts link active
        with self.authed_get("/prompts") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn('class="app-header"', html)
            self.assertIn('id="nav-link-prompts"', html)
            self.assertIn('class="app-nav-link active" id="nav-link-prompts"', html)

        # 4. Save a song with analysis and verify workspace tabs, badges, and flow buttons
        rec_id = database.save_search(
            artist="Paramore",
            song="Misery Business",
            lyrics="I'm in the business of misery, let's take it from the top",
            source="Genius",
            db_path=self.db_path
        )
        database.save_analysis(
            artist="Paramore",
            song="Misery Business",
            analysis="## Thematic Analysis\nPower and resentment.",
            db_path=self.db_path
        )
        with self.authed_get(f"/?id={rec_id}") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn('id="lyrics-tab-badge"', html)
            self.assertIn('id="analysis-tab-badge"', html)
            self.assertIn('⚡ Continue to Gemini Analysis', html)
            self.assertIn('View Lyrics', html)

    def test_07_html_escape(self):
        self.assertEqual(calling_hours.html_escape(None), "")
        self.assertEqual(calling_hours.html_escape("<script>"), "&lt;script&gt;")
        self.assertEqual(calling_hours.html_escape(123), "123")
        self.assertEqual(calling_hours.html_escape("test & fun"), "test &amp; fun")

    def test_08_healthz_endpoint(self):
        # Healthz does not require authentication
        with urllib.request.urlopen(f"{self.base_url}/healthz") as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read(), b'OK')

    def test_09_unauthenticated_access_redirects_to_login(self):
        # Unauthenticated request to / redirects to /login
        with urllib.request.urlopen(f"{self.base_url}/") as resp:
            self.assertEqual(resp.status, 200)
            self.assertTrue(resp.url.endswith("/login"))
            html = resp.read().decode('utf-8')
            self.assertIn("Sign In", html)
            self.assertIn("Calling Hours", html)

        # Unauthenticated request to /api/bands returns 401
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(f"{self.base_url}/api/bands")
        self.assertEqual(ctx.exception.code, 401)

    def test_10_login_and_unauthorized_pages(self):
        # Direct GET /login returns login page
        with urllib.request.urlopen(f"{self.base_url}/login") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("Sign In", html)

        # Direct GET /unauthorized returns 403 Access Restricted
        req = urllib.request.Request(f"{self.base_url}/unauthorized?email=" + urllib.parse.quote("intruder@example.com"))
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 403)
        body = ctx.exception.read().decode('utf-8')
        self.assertIn("Access Restricted", body)
        self.assertIn("intruder@example.com", body)

    def test_11_admin_page_authorization(self):
        # 1. Non-admin user gets 403 Forbidden on /admin
        database.upsert_user("regular@example.com", name="Regular User", is_admin=False, is_active=True, db_path=self.db_path)
        regular_token = database.create_session("regular@example.com", db_path=self.db_path)
        req = urllib.request.Request(f"{self.base_url}/admin", headers={"Cookie": f"session_id={regular_token}"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 403)

        # 2. Superadmin gets 200 on /admin
        with self.authed_get("/admin") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("User Access Management", html)
            self.assertIn("Authorized Users Directory", html)
            self.assertIn("jpmclaug@gmail.com", html)
            self.assertIn("Primary Superadmin", html)

    def test_12_admin_user_crud_flow(self):
        # 1. Add new user via admin form
        add_data = {
            "email": "colleague@gmail.com",
            "name": "Alex Colleague",
            "role": "user"
        }
        with self.authed_post("/admin/user/add", add_data) as resp:
            self.assertEqual(resp.status, 200)
            self.assertTrue(resp.url.endswith("/admin?msg=Access%20granted%20for%20colleague%40gmail.com."))

        new_user = database.get_user("colleague@gmail.com", db_path=self.db_path)
        self.assertIsNotNone(new_user)
        self.assertEqual(new_user["name"], "Alex Colleague")
        self.assertFalse(new_user["is_admin"])
        self.assertTrue(new_user["is_active"])

        # 2. Toggle role (promote to admin)
        with self.authed_post("/admin/user/toggle-role", {"email": "colleague@gmail.com"}) as resp:
            self.assertEqual(resp.status, 200)
        promoted = database.get_user("colleague@gmail.com", db_path=self.db_path)
        self.assertTrue(promoted["is_admin"])

        # 3. Toggle status (deactivate user)
        with self.authed_post("/admin/user/toggle-status", {"email": "colleague@gmail.com"}) as resp:
            self.assertEqual(resp.status, 200)
        deactivated = database.get_user("colleague@gmail.com", db_path=self.db_path)
        self.assertFalse(deactivated["is_active"])

        # 4. Delete user
        with self.authed_post("/admin/user/delete", {"email": "colleague@gmail.com"}) as resp:
            self.assertEqual(resp.status, 200)
        self.assertIsNone(database.get_user("colleague@gmail.com", db_path=self.db_path))

    def test_13_logout_flow(self):
        temp_token = database.create_session("jpmclaug@gmail.com", db_path=self.db_path)
        req = urllib.request.Request(f"{self.base_url}/logout", headers={"Cookie": f"session_id={temp_token}"})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            self.assertTrue(resp.url.endswith("/login"))
        # Verify session was deleted
        self.assertIsNone(database.get_session_user(temp_token, db_path=self.db_path))

    def test_14_analysis_loading_feedback_and_mobile_responsiveness(self):
        with self.authed_get("/") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')

            # 1. Loading overlay elements for non-dismissible analysis feedback
            self.assertIn('id="analysis-loading-overlay"', html)
            self.assertIn('class="analysis-loading-backdrop"', html)
            self.assertIn('class="analysis-loading-modal"', html)
            self.assertIn('class="analysis-cosmic-spinner"', html)
            self.assertIn('id="analysis-loading-title"', html)
            self.assertIn('id="analysis-loading-song"', html)
            self.assertIn('id="analysis-loading-status"', html)
            self.assertIn('Please keep this page open', html)

            # 2. Form submission hook and button ID
            self.assertIn('id="btn-perform-analysis"', html)
            self.assertIn('startAnalysisSubmit', html)
            self.assertIn('showAnalysisLoadingOverlay', html)

            # 3. Mobile responsive 2-row header elements and safeguards
            self.assertIn('class="app-header-top"', html)
            self.assertIn('class="btn-logout"', html)
            self.assertIn('class="app-user-bar"', html)
            self.assertIn('max-width: 100vw;', html)
            self.assertIn('overflow-x: hidden;', html)

    def test_15_history_artist_filtering_and_chips(self):
        database.save_search(
            artist="Soundgarden",
            song="Black Hole Sun",
            lyrics="In my eyes, indisposed...",
            source="Genius",
            db_path=self.db_path
        )
        database.save_search(
            artist="Pearl Jam",
            song="Alive",
            lyrics="Son, she said, have I got a little story for you...",
            source="Genius",
            db_path=self.db_path
        )

        # 1. Verify history page has artist filter chips and artist dropdown
        with self.authed_get("/history") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn('id="history-artist-filter"', html)
            self.assertIn('id="history-artist-chips"', html)
            self.assertIn('Previously Searched Artists', html)
            self.assertIn('class="artist-chip"', html)
            self.assertIn('data-artist="Soundgarden"', html)
            self.assertIn('data-artist="Pearl Jam"', html)
            self.assertIn('filterByArtist', html)

        # 2. Verify /history?artist=Soundgarden pre-selects artist
        with self.authed_get("/history?artist=Soundgarden") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("Soundgarden", html)
            self.assertIn("activeArtistFilter = 'Soundgarden'", html)

    def test_16_submit_cache_hit_touches_search_timestamp(self):
        # Insert initial search
        rec_id = database.save_search(
            artist="Nirvana",
            song="In Bloom",
            lyrics="He's the one who likes all our pretty songs...",
            source="Genius",
            db_path=self.db_path
        )
        rec_before = database.get_search("Nirvana", "In Bloom", db_path=self.db_path)
        self.assertIsNotNone(rec_before)

        # Re-submit the same song through POST /submit
        with self.authed_post("/submit", {"artist": "Nirvana", "song": "In Bloom"}) as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("Successfully found lyrics for", html)
            self.assertIn("Nirvana", html)
            self.assertIn("In Bloom", html)

        # In Bloom should now be at the top of recent searches
        recent = database.get_recent_searches(limit=1, db_path=self.db_path)
        self.assertEqual(recent[0]["artist"], "Nirvana")
        self.assertEqual(recent[0]["song"], "In Bloom")

    def test_17_search_form_artist_input_and_sync(self):
        with self.authed_get("/") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn('oninput="onArtistInput(this.value)"', html)
            self.assertIn('onchange="onArtistChange(this.value)"', html)
            self.assertIn('onBandSelected', html)
            self.assertIn('Previously Searched Artists &amp; Bands', html)

    @patch("lastfm.get_or_fetch_track_tags")
    def test_18_lastfm_track_tags_api(self, mock_track_tags):
        mock_track_tags.return_value = [
            {"name": "emo", "url": "https://www.last.fm/tag/emo", "count": 100},
            {"name": "alternative rock", "url": "https://www.last.fm/tag/alternative+rock", "count": 80}
        ]
        url = "/api/lastfm/track-tags?artist=" + urllib.parse.quote("Jimmy Eat World") + "&song=" + urllib.parse.quote("The Middle")
        with self.authed_get(url) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertIn("tags", data)
            self.assertEqual(len(data["tags"]), 2)
            self.assertEqual(data["tags"][0]["name"], "emo")

    @patch("lastfm.get_or_fetch_artist_metadata")
    def test_19_lastfm_artist_api(self, mock_artist_meta):
        mock_artist_meta.return_value = {
            "artist": "Jimmy Eat World",
            "tags": [{"name": "emo", "url": "https://www.last.fm/tag/emo"}],
            "top_tracks": [{"name": "The Middle", "rank": "1", "listeners": "1000000", "playcount": "5000000"}]
        }
        url = "/api/lastfm/artist?artist=" + urllib.parse.quote("Jimmy Eat World")
        with self.authed_get(url) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertEqual(data["artist"], "Jimmy Eat World")
            self.assertEqual(len(data["tags"]), 1)
            self.assertEqual(data["top_tracks"][0]["name"], "The Middle")

    def test_20_lastfm_ui_and_history_chips(self):
        # Save a search with track_tags
        tags = [
            {"name": "shoegaze", "url": "https://www.last.fm/tag/shoegaze"},
            {"name": "dream pop", "url": "https://www.last.fm/tag/dream+pop"}
        ]
        database.save_search(
            artist="Slowdive",
            song="Alison",
            lyrics="Listen close, and don't be slow...",
            source="Genius",
            track_tags=tags,
            db_path=self.db_path
        )

        # 1. Verify Home page contains Last.fm modal markup and artist profile button
        with self.authed_get("/") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn('id="artist-modal-overlay"', html)
            self.assertIn('id="btn-artist-profile"', html)
            self.assertIn('openArtistModal', html)

        # 2. Verify History page contains modal markup and track tag chips
        with self.authed_get("/history") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn('id="artist-modal-overlay"', html)
            self.assertIn('#shoegaze', html)
            self.assertIn('#dream pop', html)
            self.assertIn('openArtistModal(\'Slowdive\')', html)

        # 3. Verify loading song via artist & song query params (e.g. from quickLoadTrack)
        url = "/?artist=" + urllib.parse.quote("Slowdive") + "&song=" + urllib.parse.quote("Alison")
        with self.authed_get(url) as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("Loaded saved search for", html)
            self.assertIn("Slowdive", html)
            self.assertIn("Alison", html)
            self.assertIn("#shoegaze", html)

    def test_21_lastfm_track_name_layout_and_widget_rendering(self):
        # 1. Verify CSS styles prevent flex collapse of track names
        with self.authed_get("/") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn('.lastfm-track-name', html)
            self.assertIn('min-width: 0;', html)
            self.assertIn('.lastfm-quick-load-btn', html)
            self.assertIn('width: auto;', html)
            self.assertIn('flex-shrink: 0;', html)
            self.assertIn('class="artist-modal-close"', html)

        # 2. Verify build_lastfm_widget renders track names and attributes properly
        mock_artist_meta = {
            "tags": [{"name": "emo", "url": "https://last.fm/tag/emo"}],
            "top_tracks": [
                {"rank": 1, "name": "Your Deep Rest", "playcount": 25000, "listeners": 8200},
                {"rank": 2, "name": "An Introduction to the Album", "playcount": 20000, "listeners": 7100}
            ]
        }
        widget = calling_hours.build_lastfm_widget("The Hotelier", "Your Deep Rest", track_tags=[], artist_metadata=mock_artist_meta)
        self.assertIn("Your Deep Rest", widget)
        self.assertIn("An Introduction to the Album", widget)
        self.assertIn('class="lastfm-track-name" title="Your Deep Rest"', widget)
        self.assertIn('class="lastfm-quick-load-btn"', widget)
        self.assertIn("quickLoadTrack('The Hotelier', 'Your Deep Rest')", widget)

    def test_22_theaudiodb_api_and_widget_rendering(self):
        # 1. Verify /api/theaudiodb/track endpoint
        mock_track_data = {
            "track": "Karma Police",
            "artist": "Radiohead",
            "album": "OK Computer",
            "tempo": 75,
            "key": "Am",
            "genre": "Alternative Rock",
            "mood": "Melancholic",
            "energy": 60,
            "valence": 35,
            "danceability": 45,
            "description": "Karma Police is a song by the English alternative rock band Radiohead.",
            "music_vid_url": "https://www.youtube.com/watch?v=1uYWYWPc9HU",
            "music_vid_views_formatted": "150M",
            "spotify_id": "63OQupATfueENZJaC0fltO"
        }

        with patch("theaudiodb.get_or_fetch_track_metadata", return_value=mock_track_data):
            url = "/api/theaudiodb/track?artist=" + urllib.parse.quote("Radiohead") + "&song=" + urllib.parse.quote("Karma Police")
            with self.authed_get(url) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode('utf-8'))
                self.assertIn("theaudiodb_data", data)
                self.assertEqual(data["theaudiodb_data"]["tempo"], 75)
                self.assertEqual(data["theaudiodb_data"]["key"], "Am")

        # 2. Save search with theaudiodb_data and verify page renders the widget
        rec_id = database.save_search(
            artist="Radiohead",
            song="Karma Police",
            lyrics="Karma police, arrest this man",
            source="Genius",
            theaudiodb_data=mock_track_data,
            db_path=self.db_path
        )
        self.assertGreater(rec_id, 0)

        with self.authed_get(f"/load?id={rec_id}") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("TheAudioDB Track Insights", html)
            self.assertIn("75 BPM", html)
            self.assertIn("Key: Am", html)
            self.assertIn("Melancholic", html)
            self.assertIn("Karma Police is a song by the English alternative rock band Radiohead.", html)
            self.assertIn("Official Video", html)

        # 3. Test build_theaudiodb_widget directly
        widget = calling_hours.build_theaudiodb_widget("Radiohead", "Karma Police", mock_track_data)
        self.assertIn("TheAudioDB Track Insights", widget)
        self.assertIn("75 BPM", widget)
        self.assertIn("Key: Am", widget)
        self.assertIn("OK Computer", widget)
        self.assertIn("Energy", widget)
        self.assertIn("Danceability", widget)
        self.assertIn("Valence", widget)

    def test_23_artist_intelligence_page_and_directory(self):
        # 1. Verify App Header contains link to Artists
        with self.authed_get("/") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn('href="/artist"', html)
            self.assertIn('Artists', html)

        # 2. Verify Artist Directory page (when no artist specified)
        with self.authed_get("/artist") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("Artist Intelligence Directory", html)
            self.assertIn("Live touring, North Carolina concerts", html)
            self.assertIn('name="artist"', html)

        # 3. Verify Artist Intelligence Hub page for a specific artist
        mock_setlist_data = {
            "mbid": "test-mbid-jew",
            "name": "Jimmy Eat World",
            "url": "https://www.setlist.fm/setlists/jimmy-eat-world-4bd6838a.html",
            "last_nc_show": {
                "venue_name": "Red Hat Amphitheater",
                "city": "Raleigh",
                "state": "NC",
                "tour_name": "Amplify The Noise Tour",
                "date_formatted": "August 20, 2023",
                "url": "https://setlist.fm/show-nc-jew",
                "song_count": 18,
                "total_nc_shows": 12,
                "info": "Co-headlining with Manchester Orchestra"
            },
            "last_3_tours": [
                {
                    "tour_name": "Amplify The Noise Tour",
                    "sample_date_formatted": "August 20, 2023",
                    "venue_name": "Red Hat Amphitheater",
                    "location": "Raleigh, NC, United States",
                    "played_with": ["Manchester Orchestra", "Middle Kids"],
                    "setlist_url": "https://setlist.fm/show-nc-jew"
                },
                {
                    "tour_name": "Surviving The Truth Tour",
                    "sample_date_formatted": "March 15, 2022",
                    "venue_name": "The Orange Peel",
                    "location": "Asheville, NC, United States",
                    "played_with": ["Dashboard Confessional"],
                    "setlist_url": "https://setlist.fm/show-nc-jew-2"
                }
            ],
            "recent_setlists": [
                {
                    "venue_name": "Red Hat Amphitheater",
                    "city": "Raleigh",
                    "state_or_country": "NC",
                    "tour_name": "Amplify The Noise Tour",
                    "date_formatted": "August 20, 2023",
                    "url": "https://setlist.fm/show-nc-jew",
                    "song_count": 18,
                    "sample_songs": ["Bleed American", "Sweetness", "The Middle"]
                }
            ]
        }

        mock_adb_data = {
            "banner_url": "https://example.com/banner.jpg",
            "thumbnail_url": "https://example.com/thumb.jpg",
            "formed_year": "1993",
            "country": "Mesa, Arizona, United States",
            "biography": "Jimmy Eat World is an American rock band formed in 1993.",
        }

        with patch("setlistfm.get_or_fetch_artist_setlist_data", return_value=mock_setlist_data), \
             patch("theaudiodb.get_or_fetch_artist_details", return_value=mock_adb_data):
            url = "/artist?artist=" + urllib.parse.quote("Jimmy Eat World")
            with self.authed_get(url) as resp:
                self.assertEqual(resp.status, 200)
                html = resp.read().decode('utf-8')
                # Verify header and hero
                self.assertIn("Jimmy Eat World", html)
                self.assertIn("1993", html)
                self.assertIn("Mesa, Arizona, United States", html)
                # Verify NC Spotlight Card
                self.assertIn("North Carolina Show Spotlight", html)
                self.assertIn("Last Played in NC: August 20, 2023", html)
                self.assertIn("Red Hat Amphitheater", html)
                self.assertIn("12", html)  # Total NC shows
                # Verify Tours & Co-Performers
                self.assertIn("Last 3 Tours &amp; Who They Played With", html)
                self.assertIn("Amplify The Noise Tour", html)
                self.assertIn("Manchester Orchestra", html)
                self.assertIn("Middle Kids", html)
                self.assertIn("Surviving The Truth Tour", html)
                self.assertIn("Dashboard Confessional", html)
                # Verify Recent Setlists
                self.assertIn("Recent Concert Setlists", html)
                self.assertIn("Bleed American", html)
                self.assertIn("Sweetness", html)

    def test_24_unified_artist_api_and_auto_analyze_flow(self):
        # 1. Save a test song in database
        database.save_search(
            artist="Blink-182",
            song="All The Small Things",
            lyrics="Late night, come home, work sucks, I know",
            source="Genius",
            db_path=self.db_path
        )

        # 2. Test /api/artist endpoint
        mock_setlist_data = {
            "mbid": "blink-mbid",
            "name": "Blink-182",
            "last_nc_show": {"venue_name": "PNC Music Pavilion", "city": "Charlotte"},
            "tours": [{"tour_name": "One More Time Tour", "played_with": ["Pierce The Veil"]}],
            "recent_setlists": []
        }

        with patch("setlistfm.get_or_fetch_artist_setlist_data", return_value=mock_setlist_data):
            url = "/api/artist?artist=" + urllib.parse.quote("Blink-182")
            with self.authed_get(url) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode('utf-8'))
                self.assertEqual(data["artist"], "Blink-182")
                self.assertIn("setlistfm", data)
                self.assertEqual(data["setlistfm"]["last_nc_show"]["city"], "Charlotte")
                self.assertIn("songs", data)
                self.assertTrue(any(s["song"] == "All The Small Things" for s in data["songs"]))

        # 3. Test /api/setlistfm/artist endpoint
        with patch("setlistfm.get_or_fetch_artist_setlist_data", return_value=mock_setlist_data):
            url = "/api/setlistfm/artist?artist=" + urllib.parse.quote("Blink-182")
            with self.authed_get(url) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode('utf-8'))
                self.assertEqual(data["name"], "Blink-182")
                self.assertEqual(data["mbid"], "blink-mbid")

        # 4. Test loading song from artist page with auto_analyze=1
        url = "/?artist=" + urllib.parse.quote("Blink-182") + "&song=" + urllib.parse.quote("All The Small Things") + "&auto_analyze=1"
        with self.authed_get(url) as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("All The Small Things", html)
            self.assertIn("Late night, come home", html)


if __name__ == "__main__":
    unittest.main()




