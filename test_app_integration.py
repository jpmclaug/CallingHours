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

    def test_01_initial_home_page(self):
        with urllib.request.urlopen(f"{self.base_url}/") as resp:
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
        with urllib.request.urlopen(f"{self.base_url}/") as resp:
            html = resp.read().decode('utf-8')
            self.assertIn("Blink-182", html)
            self.assertIn("1 saved", html)
            self.assertIn('<option value="Blink-182"', html)

        # 2. Verify GET /api/bands returns JSON
        with urllib.request.urlopen(f"{self.base_url}/api/bands") as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertIn("bands", data)
            self.assertEqual(len(data["bands"]), 1)
            self.assertEqual(data["bands"][0]["artist"], "Blink-182")
            self.assertEqual(data["bands"][0]["song_count"], 1)

        # 3. Verify GET /api/songs?artist=Blink-182
        url = f"{self.base_url}/api/songs?artist=" + urllib.parse.quote("Blink-182")
        with urllib.request.urlopen(url) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertIn("songs", data)
            self.assertEqual(len(data["songs"]), 1)
            self.assertEqual(data["songs"][0]["song"], "Dammit")
            self.assertTrue(data["songs"][0]["has_lyrics"])

        # 4. Verify GET /api/search?id=...
        with urllib.request.urlopen(f"{self.base_url}/api/search?id={rec_id}") as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertIn("search", data)
            self.assertEqual(data["search"]["artist"], "Blink-182")
            self.assertEqual(data["search"]["song"], "Dammit")

    def test_03_load_saved_song_by_id(self):
        rec = database.get_search("Blink-182", "Dammit", db_path=self.db_path)
        self.assertIsNotNone(rec)
        rec_id = rec["id"]

        with urllib.request.urlopen(f"{self.base_url}/?id={rec_id}") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("Loaded saved search for", html)
            self.assertIn("Blink-182", html)
            self.assertIn("Dammit", html)
            self.assertIn("tell me what you think about me", html)

    def test_04_history_page(self):
        with urllib.request.urlopen(f"{self.base_url}/history") as resp:
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
        req = urllib.request.Request(f"{self.base_url}/history/delete?id={rec_id}")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)

        # Record should be gone from database
        self.assertIsNone(database.get_search_by_id(rec_id, db_path=self.db_path))

        # API should show 0 bands
        with urllib.request.urlopen(f"{self.base_url}/api/bands") as resp:
            data = json.loads(resp.read().decode('utf-8'))
            self.assertEqual(len(data["bands"]), 0)

    def test_06_navigation_tabs_and_app_header(self):
        # 1. Verify Home page has global app-header and workspace-tabs
        with urllib.request.urlopen(f"{self.base_url}/") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn('class="app-header"', html)
            self.assertIn('id="nav-link-song"', html)
            self.assertIn('id="nav-link-history"', html)
            self.assertIn('id="nav-link-prompts"', html)
            self.assertIn('id="workspace-tabs"', html)
            self.assertIn('id="tab-btn-search"', html)
            self.assertIn('id="tab-btn-lyrics"', html)
            self.assertIn('id="tab-btn-analysis"', html)
            self.assertIn('switchWorkspaceTab', html)

        # 2. Verify History page has global app-header with history link active
        with urllib.request.urlopen(f"{self.base_url}/history") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn('class="app-header"', html)
            self.assertIn('id="nav-link-history"', html)
            self.assertIn('class="app-nav-link active" id="nav-link-history"', html)

        # 3. Verify Prompts page has global app-header with prompts link active
        with urllib.request.urlopen(f"{self.base_url}/prompts") as resp:
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
        with urllib.request.urlopen(f"{self.base_url}/?id={rec_id}") as resp:
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
        with urllib.request.urlopen(f"{self.base_url}/healthz") as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read(), b'OK')

if __name__ == "__main__":
    unittest.main()


