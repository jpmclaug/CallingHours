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
            self.assertIn('id="nav-link-playlists"', html)
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
            self.assertIn('data-artist="Slowdive"', html)
            self.assertIn('openArtistModal(this.dataset.artist)', html)

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
        self.assertIn('data-artist="The Hotelier"', widget)
        self.assertIn('data-track="Your Deep Rest"', widget)
        self.assertIn("quickLoadTrack(this.dataset.artist, this.dataset.track)", widget)

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
            ],
            "most_played_with": [
                {
                    "rank": 1,
                    "band": "Manchester Orchestra",
                    "shows_shared": 28,
                    "tours_shared": 3,
                    "tours": ["Amplify The Noise Tour"],
                    "primary_role": "Co-Headliner",
                    "years_active": "2013 - 2023",
                    "latest_show": {
                        "date_formatted": "August 20, 2023",
                        "venue_name": "Red Hat Amphitheater",
                        "location": "Raleigh, NC",
                        "tour_name": "Amplify The Noise Tour",
                        "url": "https://setlist.fm/show-nc-jew"
                    }
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

        mock_spotify_data = {
            "is_configured": True,
            "found": True,
            "artist": "Jimmy Eat World",
            "artist_id": "jew_spotify_123",
            "popularity": 74,
            "popularity_tier": "Mainstream Heavyweight",
            "followers": 1400000,
            "followers_formatted": "1.4M",
            "genres": ["Emo", "Alternative Rock"],
            "avg_track_popularity": 71.5,
            "discography": {
                "albums_count": 10,
                "singles_count": 16,
                "total_releases": 26,
                "years_active_span": "1994 - 2024 (30 yrs)",
                "active_decades": ["1990s", "2000s", "2010s", "2020s"],
                "latest_release": {
                    "name": "Surviving",
                    "release_date": "2019-10-18",
                    "type": "Album",
                    "image_url": "https://example.com/surv.jpg",
                    "spotify_url": "https://open.spotify.com/album/surv"
                }
            },
            "top_tracks": [
                {
                    "id": "t_mid",
                    "name": "The Middle",
                    "duration_formatted": "2:46",
                    "popularity": 85,
                    "preview_url": "",
                    "spotify_url": "https://open.spotify.com/track/t_mid",
                    "album_name": "Bleed American",
                    "release_year": "2001"
                }
            ]
        }

        with patch("setlistfm.get_or_fetch_artist_setlist_data", return_value=mock_setlist_data), \
             patch("theaudiodb.get_or_fetch_artist_details", return_value=mock_adb_data), \
             patch("spotify.get_or_fetch_artist_spotify_data", return_value=mock_spotify_data):
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
                # Verify Spotify Analytics Card
                self.assertIn("spotify-analytics-card", html)
                self.assertIn("Mainstream Heavyweight", html)
                self.assertIn("1.4M", html)
                self.assertIn("The Middle", html)
                self.assertIn("1994 - 2024 (30 yrs)", html)
                # Verify Top 10 Bands Played With Card
                self.assertIn("top-bands-card", html)
                self.assertIn("Top 10 Bands Played With", html)
                self.assertIn("Manchester Orchestra", html)
                self.assertIn("28 shows", html)

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
            "recent_setlists": [],
            "most_played_with": [{"rank": 1, "band": "Green Day", "shows_shared": 45}]
        }
        mock_sp_data = {
            "found": True,
            "artist": "Blink-182",
            "popularity": 79,
            "followers_formatted": "4.2M"
        }

        with patch("setlistfm.get_or_fetch_artist_setlist_data", return_value=mock_setlist_data), \
             patch("spotify.get_or_fetch_artist_spotify_data", return_value=mock_sp_data):
            url = "/api/artist?artist=" + urllib.parse.quote("Blink-182")
            with self.authed_get(url) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode('utf-8'))
                self.assertEqual(data["artist"], "Blink-182")
                self.assertIn("setlistfm", data)
                self.assertEqual(data["setlistfm"]["last_nc_show"]["city"], "Charlotte")
                self.assertIn("most_played_with", data["setlistfm"])
                self.assertIn("spotify", data)
                self.assertEqual(data["spotify"]["popularity"], 79)
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


    def test_25_mobile_touch_fixes(self):
        # 1. Verify Home Page DOM structure and touch fixes
        with self.authed_get("/") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')

            # Verify pointer-events: none on stars
            self.assertIn("pointer-events: none;", html)

            # Verify PWA standalone navigation handler and --app-header-height in head
            self.assertIn("--app-header-height", html)
            self.assertIn("window.navigator.standalone", html)

            # Verify workspace-tabs sticky positioning and scroll-margin-top
            self.assertIn("scroll-margin-top: calc(var(--app-header-height", html)

            # Verify buttons are NOT inside <label for="artist"> or <label for="band_song_select">
            self.assertNotIn('<label for="artist"><button', html.replace(' ', '').replace('\n', ''))
            self.assertIn('<button type="button" id="btn-artist-profile"', html)
            self.assertIn('<button type="button" id="btn-quick-load"', html)

        # 2. Verify Artist Page container positioning and touch styling
        with self.authed_get("/artist?artist=Jimmy+Eat+World") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("position: relative;", html)
            self.assertIn("z-index: 2;", html)
            self.assertIn(".btn-analyze-song", html)
            self.assertIn("touch-action: manipulation;", html)
            self.assertIn("Average Setlist Playlists by Year", html)
            self.assertIn("/playlists?mode=setlist_fm", html)

        # 3. Verify Last.fm widget handles apostrophes in band and track names safely with dataset attributes
        mock_lastfm = {
            "tags": [{"name": "rock", "url": "https://www.last.fm/tag/rock"}],
            "top_tracks": [
                {"name": "What's My Age Again?", "rank": 1, "listeners": 500000, "playcount": 2000000}
            ]
        }
        with patch("lastfm.get_or_fetch_artist_metadata", return_value=mock_lastfm):
            widget_html = calling_hours.build_lastfm_widget("Jane's Addiction", "Jane Says", [{}], mock_lastfm, has_api_key=True)
            # Must NOT use unescaped raw quotes in onclick
            self.assertNotIn("quickLoadTrack('Jane&#x27;s Addiction'", widget_html)
            self.assertNotIn("quickLoadTrack('Jane's Addiction'", widget_html)
            # Must use data-artist and data-track with this.dataset
            self.assertIn('data-artist="Jane&#x27;s Addiction"', widget_html)
            self.assertIn('data-track="What&#x27;s My Age Again?"', widget_html)
            self.assertIn("quickLoadTrack(this.dataset.artist, this.dataset.track)", widget_html)
            self.assertIn("openArtistModal(this.dataset.artist)", widget_html)

        # 4. Verify History Page renders safe buttons with dataset attributes for bands with apostrophes
        database.save_search(
            artist="Guns N' Roses",
            song="Sweet Child O' Mine",
            lyrics="She's got a smile that it seems to me",
            source="Genius",
            db_path=self.db_path
        )
        with self.authed_get("/history") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn('data-artist="Guns N&#x27; Roses"', html)
            self.assertIn("filterByArtist(this.dataset.artist)", html)
            self.assertIn("openArtistModal(this.dataset.artist)", html)

    def test_24_mobile_redirects_for_post_endpoints(self):
        class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        opener = urllib.request.build_opener(NoRedirectHandler)

        # GET /submit should redirect 302 to /
        req = urllib.request.Request(f"{self.base_url}/submit", headers=self.auth_headers)
        try:
            resp = opener.open(req)
            self.assertEqual(resp.status, 302)
            self.assertEqual(resp.headers.get("Location"), "/")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 302)
            self.assertEqual(e.headers.get("Location"), "/")

        # GET /analyze should redirect 302 to /
        req = urllib.request.Request(f"{self.base_url}/analyze", headers=self.auth_headers)
        try:
            resp = opener.open(req)
            self.assertEqual(resp.status, 302)
            self.assertEqual(resp.headers.get("Location"), "/")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 302)
            self.assertEqual(e.headers.get("Location"), "/")

        # GET /prompts/save should redirect 302 to /prompts
        req = urllib.request.Request(f"{self.base_url}/prompts/save", headers=self.auth_headers)
        try:
            resp = opener.open(req)
            self.assertEqual(resp.status, 302)
            self.assertEqual(resp.headers.get("Location"), "/prompts")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 302)
            self.assertEqual(e.headers.get("Location"), "/prompts")

    def test_25_genius_403_graceful_handling_in_do_get(self):
        # Simulate Genius blocking requests with 403 Forbidden
        with patch("calling_hours.search_genius_song_details", return_value={"url": "https://genius.com/blocked-song", "artist": "BlockedBand", "song": "BlockedSong"}), \
             patch("calling_hours.fetch_genius_lyrics", side_effect=calling_hours.requests.exceptions.HTTPError("403 Client Error: Forbidden")), \
             patch("calling_hours.fetch_lrclib_lyrics", return_value="LRCLIB fallback lyrics content"):
            
            url = f"/?artist={urllib.parse.quote('BlockedBand')}&song={urllib.parse.quote('BlockedSong')}"
            with self.authed_get(url) as resp:
                self.assertEqual(resp.status, 200)
                html = resp.read().decode('utf-8')
                self.assertIn("LRCLIB fallback lyrics content", html)
                self.assertIn("BlockedBand", html)
                self.assertIn("BlockedSong", html)

    def test_26_history_page_syntax_and_mobile_styles(self):
        with self.authed_get("/history") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            # Verify missing }); is fixed (no unclosed DOMContentLoaded)
            self.assertIn("filterByArtist(activeArtistFilter);\n            }\n        });", html)
            # Verify mobile responsive style is present
            self.assertIn("@media (max-width: 768px)", html)
            self.assertIn(".history-card", html)
            # Verify rel="noopener noreferrer" is in modal links
            self.assertIn('rel="noopener noreferrer"', html)

    def test_27_artist_page_mobile_styles(self):
        with self.authed_get("/artist?artist=TestArtist") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("@media (max-width: 768px)", html)
            self.assertIn("@media (max-width: 480px)", html)
            self.assertIn(".artist-page-container", html)
            self.assertIn(".artist-hero-card", html)

    def test_28_render_page_tab_badges_and_prompts(self):
        with self.authed_get("/") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            # Invalid CSS style="inline-block;" must not be present
            self.assertNotIn('style="inline-block;"', html)
            # Prompt dropdown options must be populated even on empty search
            self.assertIn('<select name="prompt_idx" id="prompt_idx">', html)
            self.assertIn('Default Analysis', html)

    def test_29_mobile_bottom_navigation_bar_and_swipes(self):
        with self.authed_get("/") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            # Verify bottom navigation styling
            self.assertIn("position: fixed;", html)
            self.assertIn("bottom: 0;", html)
            self.assertIn("calc(76px + env(safe-area-inset-bottom", html)
            # Verify desktop-only-text for Search History
            self.assertIn('<span class="desktop-only-text">Search </span>History', html)
            # Verify swipe navigation script
            self.assertIn("initMobileTabSwipes", html)
            # Verify app-bottom-nav element exists and is strictly outside header (not trapped by backdrop-filter)
            self.assertIn('class="app-bottom-nav"', html)
            header_end = html.find('</header>')
            bottom_nav_pos = html.find('class="app-bottom-nav"')
            self.assertGreater(header_end, 0)
            self.assertGreater(bottom_nav_pos, header_end, "app-bottom-nav must be outside <header> to avoid containing block trap")
            # Verify mobile nav links and active state on /
            self.assertIn('id="mobile-nav-link-song"', html)
            self.assertIn('id="mobile-nav-link-artist"', html)
            self.assertIn('id="mobile-nav-link-playlists"', html)
            self.assertIn('id="mobile-nav-link-history"', html)
            self.assertIn('id="mobile-nav-link-prompts"', html)
            self.assertIn('class="app-bottom-nav-link active" id="mobile-nav-link-song"', html)

        # Verify bottom nav on /history
        with self.authed_get("/history") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn('class="app-bottom-nav"', html)
            self.assertIn('class="app-bottom-nav-link active" id="mobile-nav-link-history"', html)

        # Verify bottom nav on /prompts
        with self.authed_get("/prompts") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn('class="app-bottom-nav"', html)
            self.assertIn('class="app-bottom-nav-link active" id="mobile-nav-link-prompts"', html)

        # Verify bottom nav on /artist
        with self.authed_get("/artist") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn('class="app-bottom-nav"', html)
            self.assertIn('class="app-bottom-nav-link active" id="mobile-nav-link-artist"', html)

    def test_spotify_integration_and_navigation(self):
        # 1. Verify nav links on home page
        with self.authed_get("/") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn('href="/spotify"', html)
            self.assertIn('id="nav-link-spotify"', html)
            self.assertIn('id="mobile-nav-link-spotify"', html)

        # 2. Verify /spotify page loads and highlights active nav link
        with self.authed_get("/spotify") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn('Spotify Listening Intelligence', html)
            self.assertIn('id="nav-link-spotify"', html)
            self.assertIn('class="app-nav-link active" id="nav-link-spotify"', html)
            self.assertIn('class="app-bottom-nav-link active" id="mobile-nav-link-spotify"', html)

        # 3. Verify /spotify?demo=1 interactive demo preview
        with self.authed_get("/spotify?demo=1") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn('Interactive Demo Preview Mode', html)
            self.assertIn('Jimmy Eat World', html)
            self.assertIn('Slowdive', html)
            self.assertIn('Listening by Time of Day', html)
            self.assertIn('Activity by Day of Week', html)
            self.assertIn('Release Era Breakdown', html)
            self.assertIn('Listening History Stream', html)
            self.assertIn('✨ Analyze Lyrics', html)

        # 4. Verify API status endpoint
        with self.authed_get("/api/spotify/status") as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertIn('configured', data)
            self.assertIn('connected', data)

        # 5. Verify API history endpoint in demo mode
        with self.authed_get("/api/spotify/history?demo=1") as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertTrue(data.get('demo'))
            self.assertIn('tracks', data)
            self.assertIn('analytics', data)
            self.assertGreater(len(data['tracks']), 0)
            self.assertIn('persona', data['analytics'])

        # 6. Verify API now-playing endpoint
        with self.authed_get("/api/spotify/now-playing") as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertIn('playing', data)

    @patch("spotify.exchange_code_for_token")
    @patch("spotify.fetch_user_profile")
    @patch("spotify.fetch_recently_played")
    def test_spotify_callback_flow(self, mock_recent, mock_profile, mock_exchange):
        mock_exchange.return_value = {
            "access_token": "mock_access_tok_999",
            "refresh_token": "mock_refresh_tok_888",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        mock_profile.return_value = {
            "id": "spot_test_user",
            "display_name": "JP Test User",
            "email": "jp@example.com",
            "image_url": "https://img.spotify.com/u.jpg",
            "spotify_url": "https://open.spotify.com/user/spot_test_user",
        }
        mock_recent.return_value = []

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        headers = {
            'Cookie': f'session_id={self.session_id}; spotify_oauth_state=state_secret_123'
        }
        req = urllib.request.Request(
            f"{self.base_url}/auth/spotify/callback?code=code_abc&state=state_secret_123",
            headers=headers
        )
        opener = urllib.request.build_opener(NoRedirect)
        try:
            resp = opener.open(req)
            self.assertEqual(resp.status, 302)
            self.assertIn('/spotify?connected=1', resp.headers.get('Location', ''))
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 302)
            self.assertIn('/spotify?connected=1', e.headers.get('Location', ''))

        # Verify token was saved in database
        saved_tok = database.get_spotify_token("jpmclaug@gmail.com")
        self.assertIsNotNone(saved_tok)
        self.assertEqual(saved_tok["access_token"], "mock_access_tok_999")
        self.assertEqual(saved_tok["spotify_display_name"], "JP Test User")

        # Verify /spotify loads and displays connected user
        with self.authed_get("/spotify") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("JP Test User", html)
            self.assertIn("🟢 Connected", html)

    def test_46_playlist_generator_section_and_modes(self):
        # 1. Verify nav links on home and playlists
        with self.authed_get("/") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn('href="/playlists"', html)
            self.assertIn('id="nav-link-playlists"', html)
            self.assertIn('id="mobile-nav-link-playlists"', html)

        # 2. Verify /playlists page loads with Option 1 active as default
        with self.authed_get("/playlists") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn('Playlist Generator', html)
            self.assertIn('class="app-nav-link active" id="nav-link-playlists"', html)
            self.assertIn('class="app-bottom-nav-link active" id="mobile-nav-link-playlists"', html)
            # Option 1 is first and flagship
            self.assertIn('Option 1 • Flagship', html)
            self.assertIn('All Analyzed Songs', html)
            self.assertIn('id="option-card-all"', html)
            # Other options exist
            self.assertIn('Option 2 • Artist', html)
            self.assertIn('Option 3 • Last.fm', html)
            self.assertIn('Option 4 • Audio', html)
            self.assertIn('Option 5 • Streaming', html)
            self.assertIn('Option 6 • Setlist.fm', html)
            self.assertIn('Average Setlist by Year', html)
            self.assertIn('id="option-card-setlist"', html)
            # Export buttons
            self.assertIn('id="btn-export-spotify"', html)
            self.assertIn('href="/playlists/export/m3u', html)
            self.assertIn('href="/playlists/export/csv', html)

        # 3. Seed test songs with analysis
        s1 = database.save_search(
            artist="Jimmy Eat World",
            song="Sweetness",
            lyrics="Are you listening?",
            source="Genius",
            track_tags=[{"name": "emo"}],
            theaudiodb_data={"tempo": 135, "key": "D", "energy": 85},
            db_path=self.db_path
        )
        database.save_analysis(
            artist="Jimmy Eat World",
            song="Sweetness",
            analysis="## Meaning\nLyrical yearning.",
            model_name="gemini-3.8-flash",
            db_path=self.db_path
        )
        s2 = database.save_search(
            artist="Slowdive",
            song="When the Sun Hits",
            lyrics="It's so cold, it's so cold",
            source="Genius",
            track_tags=[{"name": "shoegaze"}],
            theaudiodb_data={"tempo": 120, "energy": 70},
            db_path=self.db_path
        )
        database.save_analysis(
            artist="Slowdive",
            song="When the Sun Hits",
            analysis="## Meaning\nShoegaze textures.",
            model_name="gemini-3.8-flash",
            db_path=self.db_path
        )

        # 4. Verify /playlists now lists both songs under Option 1 (All Analyzed Songs)
        with self.authed_get("/playlists?mode=all_analyzed") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("Sweetness", html)
            self.assertIn("When the Sun Hits", html)
            self.assertIn("Jimmy Eat World", html)
            self.assertIn("Slowdive", html)
            self.assertIn("✦ View Analysis", html)

        # 5. Verify /playlists mode filtering
        # By Artist
        with self.authed_get("/playlists?mode=artist&artist=Slowdive") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("When the Sun Hits", html)
            self.assertNotIn("Sweetness", html)

        # By Tag
        with self.authed_get("/playlists?mode=tag&tag=emo") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("Sweetness", html)
            self.assertNotIn("When the Sun Hits", html)

        # By Setlist.fm (Average Setlist by Year)
        with self.authed_get("/playlists?mode=setlist_fm&artist=Jimmy+Eat+World&year=2023") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("Jimmy Eat World: 2023 Average Setlist", html)
            self.assertIn("Option 6 • Setlist.fm", html)
            self.assertIn("Pain", html)
            self.assertIn("Sweetness", html)

        # 6. Verify Export Endpoints
        # M3U Export
        with self.authed_get("/playlists/export/m3u?mode=all_analyzed") as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("audio/x-mpegurl", resp.headers.get("Content-Type", ""))
            body = resp.read().decode('utf-8')
            self.assertIn("#EXTM3U", body)
            self.assertIn("Jimmy Eat World - Sweetness", body)
            self.assertIn("Slowdive - When the Sun Hits", body)

        # Setlist.fm M3U Export
        with self.authed_get("/playlists/export/m3u?mode=setlist_fm&artist=Jimmy+Eat+World&year=2023") as resp:
            self.assertEqual(resp.status, 200)
            body = resp.read().decode('utf-8')
            self.assertIn("#EXTM3U", body)
            self.assertIn("Jimmy Eat World", body)

        # CSV Export
        with self.authed_get("/playlists/export/csv?mode=all_analyzed") as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/csv", resp.headers.get("Content-Type", ""))
            body = resp.read().decode('utf-8')
            self.assertIn("Artist,Song,Gemini Model", body)
            self.assertIn("Jimmy Eat World,Sweetness", body)
            self.assertIn("Slowdive,When the Sun Hits", body)

        # Setlist.fm CSV Export
        with self.authed_get("/playlists/export/csv?mode=setlist_fm&artist=Jimmy+Eat+World&year=2023") as resp:
            self.assertEqual(resp.status, 200)
            body = resp.read().decode('utf-8')
            self.assertIn("Jimmy Eat World", body)

        # 7. Verify API Analyzed Songs
        with self.authed_get("/api/playlists/analyzed-songs") as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertGreaterEqual(data["count"], 2)
            song_titles = [s["song"] for s in data["songs"]]
            self.assertIn("Sweetness", song_titles)
            self.assertIn("When the Sun Hits", song_titles)

        # 8. Save playlist via API
        payload = json.dumps({
            "name": "Summer Jam Playlist",
            "description": "Awesome analyzed tracks",
            "generator_type": "all_analyzed",
            "items": [
                {"artist": "Jimmy Eat World", "song": "Sweetness", "id": s1}
            ]
        }).encode('utf-8')
        headers = dict(self.auth_headers)
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            f"{self.base_url}/api/playlists/save",
            data=payload,
            headers=headers
        )
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertTrue(data.get("success"))
            saved_pid = data.get("playlist_id")
            self.assertIsNotNone(saved_pid)

        # 9. Verify Saved Playlists View
        with self.authed_get(f"/playlists?tab=saved") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("Summer Jam Playlist", html)
            self.assertIn(f"/playlists?id={saved_pid}", html)

        # 10. Load Saved Playlist
        with self.authed_get(f"/playlists?id={saved_pid}") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("Summer Jam Playlist", html)
            self.assertIn("Sweetness", html)

        # 11. Delete Saved Playlist (follows 302 redirect to saved tab)
        with self.authed_get(f"/playlists/delete?id={saved_pid}") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("Playlist deleted successfully", html)
            self.assertNotIn(f"/playlists?id={saved_pid}", html)

        self.assertIsNone(database.get_saved_playlist(saved_pid, db_path=self.db_path))

        # 12. Verify /api/playlists/export-spotify 403 Forbidden handling
        database.save_spotify_token(
            user_email="jpmclaug@gmail.com",
            access_token="fake_token_no_scope",
            refresh_token="fake_refresh",
            expires_at="2099-01-01 00:00:00",
            db_path=self.db_path
        )
        with patch('spotify.export_songs_to_spotify_playlist') as mock_export:
            mock_export.side_effect = RuntimeError("Spotify API create playlist error (403): Forbidden. Your Spotify connection lacks playlist permissions.")
            exp_req = urllib.request.Request(
                f"{self.base_url}/api/playlists/export-spotify",
                data=json.dumps({"name": "Test"}).encode('utf-8'),
                headers=dict(self.auth_headers, **{"Content-Type": "application/json"})
            )
            with urllib.request.urlopen(exp_req) as resp:
                self.assertEqual(resp.status, 200)
                exp_data = json.loads(resp.read().decode('utf-8'))
                self.assertFalse(exp_data.get("success"))
                self.assertTrue(exp_data.get("needs_reauth"))
                self.assertTrue(exp_data.get("auth_url", "").startswith("/auth/spotify"))

        # 12b. Verify /api/playlists/export-spotify 401 Unauthorized handling triggers needs_reauth
        with patch('spotify.export_songs_to_spotify_playlist') as mock_export:
            mock_export.side_effect = RuntimeError('Spotify API create playlist error (401): {"error": {"status": 401, "message": "Missing/invalid/expired access token"}}')
            exp_req = urllib.request.Request(
                f"{self.base_url}/api/playlists/export-spotify",
                data=json.dumps({"name": "Test 401"}).encode('utf-8'),
                headers=dict(self.auth_headers, **{"Content-Type": "application/json"})
            )
            with urllib.request.urlopen(exp_req) as resp:
                self.assertEqual(resp.status, 200)
                exp_data_401 = json.loads(resp.read().decode('utf-8'))
                self.assertFalse(exp_data_401.get("success"))
                self.assertTrue(exp_data_401.get("needs_reauth"))
                self.assertTrue(exp_data_401.get("auth_url", "").startswith("/auth/spotify"))

        # 13. Verify /api/playlists/export-spotify success writes JSON response body
        with patch('spotify.export_songs_to_spotify_playlist') as mock_export:
            mock_export.return_value = {
                "success": True,
                "playlist_id": "sp_pl_123",
                "playlist_name": "Rock Anthem",
                "playlist_url": "https://open.spotify.com/playlist/sp_pl_123",
                "tracks_requested": 1,
                "tracks_matched": 1,
                "tracks_added": 1,
                "unmatched": []
            }
            exp_req = urllib.request.Request(
                f"{self.base_url}/api/playlists/export-spotify",
                data=json.dumps({"name": "Rock Anthem"}).encode('utf-8'),
                headers=dict(self.auth_headers, **{"Content-Type": "application/json"})
            )
            with urllib.request.urlopen(exp_req) as resp:
                self.assertEqual(resp.status, 200)
                raw_body = resp.read().decode('utf-8')
                self.assertTrue(len(raw_body) > 0)
                succ_data = json.loads(raw_body)
                self.assertTrue(succ_data.get("success"))
                self.assertEqual(succ_data.get("playlist_id"), "sp_pl_123")
                self.assertEqual(succ_data.get("tracks_added"), 1)

    def test_35_spotify_artist_api_endpoint(self):
        mock_spot_data = {
            "is_configured": True,
            "found": True,
            "artist": "Jimmy Eat World",
            "artist_id": "jew_spot_id",
            "popularity": 74,
            "popularity_tier": "Mainstream Heavyweight",
            "followers": 1400000,
            "followers_formatted": "1.4M",
            "genres": ["Emo", "Alternative Rock"],
            "avg_track_popularity": 71.5,
            "discography": {
                "albums_count": 10,
                "singles_count": 16,
                "total_releases": 26,
            },
            "top_tracks": [
                {
                    "id": "t1",
                    "name": "The Middle",
                    "popularity": 85,
                }
            ],
            "cached": False,
        }

        with patch("spotify.get_or_fetch_artist_spotify_data", return_value=mock_spot_data) as mock_fetch:
            url = "/api/spotify/artist?artist=" + urllib.parse.quote("Jimmy Eat World")
            with self.authed_get(url) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode('utf-8'))
                self.assertEqual(data["artist"], "Jimmy Eat World")
                self.assertEqual(data["popularity"], 74)
                self.assertEqual(data["popularity_tier"], "Mainstream Heavyweight")
                self.assertEqual(data["followers_formatted"], "1.4M")
                self.assertEqual(data["discography"]["albums_count"], 10)
                self.assertEqual(data["top_tracks"][0]["name"], "The Middle")
                mock_fetch.assert_called_once()

    def test_47_universal_action_loading_overlay_and_feedback_prevention(self):
        """Verify universal action loading screen is present on all app pages and prevents feedback until done."""
        pages_to_check = [
            "/",
            "/artist",
            "/spotify",
            "/playlists",
            "/history",
            "/prompts",
            "/admin",
        ]

        for path in pages_to_check:
            with self.subTest(page=path):
                with self.authed_get(path) as resp:
                    self.assertEqual(resp.status, 200, f"Expected 200 for {path}")
                    html = resp.read().decode('utf-8')

                    # 1. Overlay markup elements (shared and non-dismissible)
                    self.assertIn('id="analysis-loading-overlay"', html, f"Missing overlay on {path}")
                    self.assertIn('class="analysis-loading-backdrop"', html, f"Missing backdrop on {path}")
                    self.assertIn('class="analysis-loading-modal"', html, f"Missing modal on {path}")
                    self.assertIn('class="analysis-cosmic-spinner"', html, f"Missing spinner on {path}")
                    self.assertIn('id="analysis-loading-title"', html, f"Missing title element on {path}")
                    self.assertIn('id="analysis-loading-song"', html, f"Missing context element on {path}")
                    self.assertIn('id="analysis-loading-status"', html, f"Missing status element on {path}")
                    self.assertIn('id="analysis-loading-dismiss-btn"', html, f"Missing dismiss button on {path}")
                    self.assertIn('Please keep this page open', html, f"Missing lock notice on {path}")

                    # 2. Universal JavaScript API and anti-interruption handlers
                    self.assertIn('showActionLoadingOverlay', html, f"Missing showActionLoadingOverlay on {path}")
                    self.assertIn('hideActionLoadingOverlay', html, f"Missing hideActionLoadingOverlay on {path}")
                    self.assertIn('showAnalysisLoadingOverlay', html, f"Missing showAnalysisLoadingOverlay on {path}")
                    self.assertIn('handleActionLoadingPopState', html, f"Missing popstate handler on {path}")
                    self.assertIn('GLOBAL_ANALYSIS_STATUS_STEPS', html, f"Missing analysis steps on {path}")
                    self.assertIn('GLOBAL_DEFAULT_STATUS_STEPS', html, f"Missing default steps on {path}")

                    # 3. Anti-interruption feedback locking styles
                    self.assertIn('pointer-events: all;', html, f"Missing pointer-events all on {path}")
                    self.assertIn('backdrop-filter: blur(20px);', html, f"Missing backdrop-filter blur on {path}")

        # 4. Check specific page action hooks
        with self.authed_get("/") as resp:
            song_html = resp.read().decode('utf-8')
            self.assertIn('Finding Song Lyrics...', song_html)
            self.assertIn('Re-fetching Fresh Lyrics...', song_html)
            self.assertIn('Loading Saved Analysis...', song_html)

        with self.authed_get("/history") as resp:
            hist_html = resp.read().decode('utf-8')
            self.assertIn('Finding Song Lyrics...', hist_html)

        with self.authed_get("/playlists") as resp:
            pl_html = resp.read().decode('utf-8')
            self.assertIn('Saving Playlist...', pl_html)
            self.assertIn('Exporting to Spotify...', pl_html)


if __name__ == "__main__":
    unittest.main()





