import os
import tempfile
import unittest
import threading
import http.server
import urllib.request
import urllib.parse
import json

import database
import calling_hours

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

if __name__ == "__main__":
    unittest.main()



