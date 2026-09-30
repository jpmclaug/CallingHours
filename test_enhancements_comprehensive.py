import unittest
import urllib.request
import urllib.parse
import json
import gzip
import os
import threading
import http.server

import calling_hours
import database
import playlist_curator

class TestEnhancementsComprehensive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Create session for superadmin
        cls.user_email = "jpmclaug@gmail.com"
        cls.session_id = database.create_session(cls.user_email)
        cls.auth_headers = {"Cookie": f"session_id={cls.session_id}"}

        # Start test HTTP server
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), calling_hours.CallingHoursRequestHandler)
        cls.port = cls.server.server_port
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def authed_get(self, path: str, headers: dict = None):
        h = dict(self.auth_headers)
        if headers:
            h.update(headers)
        req = urllib.request.Request(f"{self.base_url}{path}", headers=h)
        return urllib.request.urlopen(req)

    def test_01_global_search_api(self):
        """Verify /api/search?q= returns matching artists and songs as JSON."""
        with self.authed_get("/api/search?q=hotelier") as resp:
            self.assertEqual(resp.status, 200)
            self.assertTrue(resp.headers.get('Content-Type', '').startswith('application/json'))
            data = json.loads(resp.read().decode('utf-8'))
            self.assertIn("artists", data)
            self.assertIn("songs", data)
            self.assertTrue(any("Hotelier" in a["artist"] for a in data["artists"]))

    def test_02_similar_songs_api(self):
        """Verify /api/similar-songs returns library & Last.fm song recommendations."""
        with self.authed_get("/api/similar-songs?artist=The%20Hotelier&song=Dendron") as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertIn("similar", data)
            self.assertIn("target", data)
            self.assertEqual(data["target"]["artist"], "The Hotelier")
            self.assertEqual(data["target"]["song"], "Dendron")

    def test_03_apple_music_export(self):
        """Verify /playlists/export/apple returns extended M3U file with #EXTINF headers."""
        with self.authed_get("/playlists/export/apple?mode=all_analyzed&limit=5") as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("audio/x-mpegurl", resp.headers.get('Content-Type'))
            disp = resp.headers.get('Content-Disposition')
            self.assertIn("apple_music.m3u", disp)
            body = resp.read().decode('utf-8')
            self.assertTrue(body.startswith("#EXTM3U"))
            self.assertIn("#EXTINF:", body)

    def test_04_gzip_compression(self):
        """Verify gzip compression is applied when client sends Accept-Encoding: gzip."""
        headers = {"Accept-Encoding": "gzip"}
        with self.authed_get("/playlists", headers=headers) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get('Content-Encoding'), 'gzip')
            raw = resp.read()
            decompressed = gzip.decompress(raw).decode('utf-8')
            self.assertIn("Generator Studio", decompressed)

    def test_05_playlist_narrative_arc_and_deep_cuts(self):
        """Verify narrative arc sequencing and deep cuts filter in playlist_curator."""
        sample_tracks = [
            {"artist": "Artist 1", "song": "Big Hit", "popularity": 95, "theaudiodb_data": {"energy": 90, "tempo": 140, "valence": 70}},
            {"artist": "Artist 1", "song": "Medium Track", "popularity": 50, "theaudiodb_data": {"energy": 65, "tempo": 120, "valence": 50}},
            {"artist": "Artist 1", "song": "Slow Opener", "popularity": 30, "theaudiodb_data": {"energy": 30, "tempo": 85, "valence": 35}},
            {"artist": "Artist 1", "song": "B-Side Rare", "popularity": 15, "theaudiodb_data": {"energy": 45, "tempo": 110, "valence": 40}},
            {"artist": "Artist 1", "song": "Deep Acoustic", "popularity": 10, "theaudiodb_data": {"energy": 20, "tempo": 90, "valence": 30}},
        ]
        # Narrative Arc
        sequenced = playlist_curator.sequence_narrative_arc(sample_tracks)
        self.assertEqual(len(sequenced), len(sample_tracks))
        # Deep cuts should exclude the highest popularity track
        deep = playlist_curator.filter_deep_cuts(sample_tracks)
        self.assertTrue(all(t["song"] != "Big Hit" for t in deep))

    def test_06_ai_prompt_playlist_curation(self):
        """Verify AI Prompt playlist curator produces ordered tracks and thematic notes."""
        sample_tracks = [
            {"artist": "Bane", "song": "Ali v. Frazier", "theaudiodb_data": {"energy": 85, "mood": "aggressive, fast"}},
            {"artist": "The Hotelier", "song": "Your Deep Rest", "theaudiodb_data": {"energy": 40, "valence": 30, "mood": "sad, melancholic"}},
        ]
        curated = playlist_curator.mix_ai_prompt(
            prompt="intense emotional catharsis and fast driving tempo",
            tracks=sample_tracks,
            gemini_api_key=None,  # Tests deterministic heuristic fallback
            limit=5
        )
        self.assertIn("tracks", curated)
        self.assertIn("playlist_title", curated)
        self.assertIn("curator_notes", curated)
        self.assertGreater(len(curated["tracks"]), 0)

    def test_07_artist_page_thematic_dna_and_recommendations(self):
        """Verify /artist page renders Thematic DNA for analyzed artist and directory has recommendations."""
        # 1. Directory page
        with self.authed_get("/artist") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("Artist Intelligence Directory", html)
            self.assertIn("Artists You Might Like", html)

        # 2. Artist with analyzed songs (The Hotelier)
        with self.authed_get("/artist?artist=The%20Hotelier") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("Artist Thematic DNA &amp; Signature Motifs", html)
            self.assertIn("Curate Narrative Arc Playlist", html)

    def test_08_thematic_curation_caching_and_refresh(self):
        """Verify thematic and AI mood curations are cached in the database and re-used on reload/export."""
        # 1. Database layer verification
        test_key = "test:unit_cache_verification"
        test_payload = {"tracks": [{"artist": "Test Band", "song": "Song A"}], "themes": [{"name": "Resilience"}]}
        self.assertTrue(database.save_thematic_curation(test_key, "thematic_blend", test_payload))
        cached = database.get_thematic_curation(test_key)
        self.assertIsNotNone(cached)
        self.assertTrue(cached.get('_cached'))
        self.assertEqual(cached['themes'][0]['name'], "Resilience")
        database.clear_thematic_curation_cache(test_key)

        # 2. playlist_curator layer verification
        catalog = {
            "Artist Alpha": [{"artist": "Artist Alpha", "song": "Alpha Song 1", "track_tags": ["punk"]}],
            "Artist Beta": [{"artist": "Artist Beta", "song": "Beta Song 1", "track_tags": ["hardcore"]}],
        }
        res1 = playlist_curator.mix_thematic(catalog, force_refresh=True)
        self.assertFalse(res1.get('_cached', False))
        res2 = playlist_curator.mix_thematic(catalog, force_refresh=False)
        self.assertTrue(res2.get('_cached', False))

        # 3. HTTP endpoint caching & badge verification
        url_path = "/playlists?mode=multi_artist&artists=Artist%20Alpha,Artist%20Beta&mix_mode=thematic"
        with self.authed_get(url_path) as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("Cached Curation", html)
            self.assertIn("Re-analyze with Gemini", html)
            self.assertIn("refresh=1", html)

        # 4. Export verification re-using cache without error
        export_path = "/playlists/export/m3u?mode=multi_artist&artists=Artist%20Alpha,Artist%20Beta&mix_mode=thematic"
        with self.authed_get(export_path) as resp:
            self.assertEqual(resp.status, 200)
            body = resp.read().decode('utf-8')
            self.assertTrue(body.startswith("#EXTM3U"))
            self.assertIn("Artist Alpha", body)

    def test_09_playlists_mobile_responsiveness(self):
        """Verify /playlists includes responsive mobile styles for headers, workbench, and inputs."""
        with self.authed_get("/playlists") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            # Check responsive container and grid rules
            self.assertIn(".playlist-page-container", html)
            self.assertIn("@media (max-width: 1080px)", html)
            self.assertIn("grid-template-columns: minmax(0, 1fr) !important;", html)
            self.assertIn("@media (max-width: 768px)", html)
            self.assertIn(".playlist-form-row", html)
            self.assertIn(".playlist-quick-load-wrap", html)
            self.assertIn(".playlist-quick-load-select", html)
            self.assertIn(".playlist-title-input-row", html)
            self.assertIn("word-break: break-word;", html)

    def test_10_playlists_no_prepopulation(self):
        """Verify generator modes do NOT prepopulate default bands when query params are absent."""
        # 1. Mode = artist without param
        with self.authed_get("/playlists?mode=artist") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("-- Select an Artist / Band --", html)
            self.assertIn("Please select or enter an artist/band above to generate this playlist.", html)

        # 2. Mode = setlist_fm without param
        with self.authed_get("/playlists?mode=setlist_fm") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("-- Select an Artist / Band --", html)
            self.assertIn("Please select or enter an artist/band above to generate this playlist.", html)
            # Default title should be generic, not prepopulated band
            self.assertIn("Average Setlist by Year", html)

        # 3. Mode = multi_artist without param
        with self.authed_get("/playlists?mode=multi_artist") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn('id="input-multi-artists" value=""', html)
            self.assertIn("Please enter or pick 2 or more artists above to generate a blend.", html)

        # 4. Mode = artist WITH param should load the requested artist
        with self.authed_get("/playlists?mode=artist&artist=Slowdive") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn("Calling Hours: Slowdive (Analyzed)", html)

if __name__ == "__main__":
    unittest.main()


