import json
import time
import unittest
from unittest.mock import patch, MagicMock

import calling_hours
import database


class TestDrivingMode(unittest.TestCase):
    def setUp(self):
        # Reset in-memory driving caches
        calling_hours._driving_analysis_in_flight.clear()
        calling_hours._driving_analysis_failure_cache.clear()
        calling_hours._spotify_driving_track_cache.clear()

    def test_ensure_song_lyrics_and_analysis_spawn_worker_flag(self):
        # When only_cached=True and spawn_worker=False, no background thread is spawned
        with patch('calling_hours.threading.Thread') as mock_thread:
            res = calling_hours.ensure_song_lyrics_and_analysis(
                'NonExistentArtist99', 'NonExistentSong99', only_cached=True, spawn_worker=False
            )
            mock_thread.assert_not_called()
            self.assertFalse(res.get('cached'))
            self.assertFalse(res.get('pending'))

    def test_ensure_song_lyrics_and_analysis_cached_hit(self):
        # Insert a mock analysis into database
        database.save_analysis(
            artist='TestDrivingArtist',
            song='TestDrivingSong',
            analysis='## Core Themes\nDriving at night in the neon rain.',
            model_name='gemini-2.5-flash',
            prompt_name='Default Analysis',
            prompt_text='Prompt...',
            lyrics='Test lyrics here'
        )

        res = calling_hours.ensure_song_lyrics_and_analysis(
            'TestDrivingArtist', 'TestDrivingSong', only_cached=True, spawn_worker=False
        )
        self.assertTrue(res.get('cached'))
        self.assertIn('Driving at night', res.get('analysis'))

    def test_driving_analyze_deduplication_and_cooldown(self):
        handler = calling_hours.CallingHoursRequestHandler.__new__(calling_hours.CallingHoursRequestHandler)
        handler.get_current_user = lambda: {'email': 'test@example.com', 'name': 'Tester'}

        sent_responses = []
        sent_headers = []
        written_data = []

        def mock_send_response(code):
            sent_responses.append(code)

        def mock_send_header(name, val):
            sent_headers.append((name, val))

        def mock_end_headers():
            pass

        class MockWfile:
            def write(self, b):
                written_data.append(b)

        handler.send_response = mock_send_response
        handler.send_header = mock_send_header
        handler.end_headers = mock_end_headers
        handler.wfile = MockWfile()

        # 1. Missing params -> 400
        handler.handle_api_spotify_driving_analyze('')
        self.assertEqual(sent_responses[-1], 400)
        data = json.loads(written_data[-1].decode('utf-8'))
        self.assertFalse(data['success'])

        # 2. Existing cached analysis -> returns ready immediately
        database.save_analysis(
            artist='The Cure',
            song='Pictures of You',
            analysis='## Core Meaning\nA nostalgic reverie on memories.',
            model_name='gemini-2.5-flash',
            prompt_name='Default',
            prompt_text='...',
            lyrics='I\'ve been looking so long at these pictures of you'
        )
        handler.handle_api_spotify_driving_analyze('artist=The%20Cure&song=Pictures%20of%20You')
        self.assertEqual(sent_responses[-1], 200)
        data = json.loads(written_data[-1].decode('utf-8'))
        self.assertTrue(data['success'])
        self.assertEqual(data['status'], 'ready')
        self.assertIn('nostalgic reverie', data['analysis'])
        self.assertTrue(data['cached'])

        # 3. Simulate failure and verify failure cache cooldown
        calling_hours._driving_analysis_failure_cache['failedartist:::failedsong'] = (
            time.time(), 'API Rate limit exceeded'
        )
        handler.handle_api_spotify_driving_analyze('artist=FailedArtist&song=FailedSong')
        self.assertEqual(sent_responses[-1], 200)
        data = json.loads(written_data[-1].decode('utf-8'))
        self.assertFalse(data['success'])
        self.assertEqual(data['status'], 'failed')
        self.assertTrue(data.get('cached_failure'))

        # 4. Force bypasses failure cache
        with patch('calling_hours.ensure_song_lyrics_and_analysis') as mock_ensure:
            mock_ensure.return_value = {
                'artist': 'FailedArtist',
                'song': 'FailedSong',
                'analysis': '## Recovered\nSuccessfully analyzed after retry.',
                'lyrics': 'Lyrics here'
            }
            handler.handle_api_spotify_driving_analyze('artist=FailedArtist&song=FailedSong&force=1')
            self.assertEqual(sent_responses[-1], 200)
            data = json.loads(written_data[-1].decode('utf-8'))
            self.assertTrue(data['success'])
            self.assertEqual(data['status'], 'ready')
            self.assertIn('Recovered', data['analysis'])

    def test_spotify_page_js_syntax_no_unescaped_newlines(self):
        """Ensure SPOTIFY_PAGE_HTML script block has no unescaped literal newlines inside regexes or single/double-quoted strings."""
        import re
        html = calling_hours.SPOTIFY_PAGE_HTML
        scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.S)
        self.assertTrue(len(scripts) >= 1)
        main_script = scripts[-1]
        # Verify regexes in parseDrivingMarkdown and parseDrivingMarkdownIntoSections are intact on single lines
        self.assertIn(r"let text = md.replace(/\r\n/g, '\n');", main_script)
        self.assertIn(r"const paras = text.split(/\n{2,}/);", main_script)
        self.assertIn(r"return `<p>${p.replace(/\n/g, '<br>')}</p>`;", main_script)
        self.assertIn(r"}).join('\n');", main_script)
        self.assertIn(r"let text = md.replace(/\r\n/g, '\n').trim();", main_script)
        self.assertIn(r"const headingRegex = /^##[ \t]+(.+)$/gm;", main_script)

    def test_generate_gemini_text_fallback_to_generate_content(self):
        """Ensure _generate_gemini_text falls back to client.models.generate_content if interactions.create fails."""
        mock_client = MagicMock()
        mock_client.interactions.create.side_effect = RuntimeError("Legacy Interactions API schema is no longer supported")
        mock_resp = MagicMock()
        mock_resp.text = "## Fallback Analysis\nGenerated via models.generate_content."
        mock_client.models.generate_content.return_value = mock_resp

        result = calling_hours._generate_gemini_text(mock_client, "gemini-2.5-flash", "Analyze this song")
        self.assertEqual(result, "## Fallback Analysis\nGenerated via models.generate_content.")
        mock_client.models.generate_content.assert_called_once_with(
            model="gemini-2.5-flash", contents="Analyze this song"
        )

    def test_driving_state_force_bypasses_cache(self):
        """Verify force=1 in /api/spotify/driving-state bypasses the 4-second player cache."""
        handler = calling_hours.CallingHoursRequestHandler.__new__(calling_hours.CallingHoursRequestHandler)
        handler.get_current_user = lambda: {'email': 'driver@example.com', 'name': 'Driver'}
        sent_responses = []
        written_data = []
        handler.send_response = lambda code: sent_responses.append(code)
        handler.send_header = lambda k, v: None
        handler.end_headers = lambda: None
        handler.wfile = MagicMock()
        handler.wfile.write = lambda b: written_data.append(b)

        calling_hours._spotify_driving_track_cache['driver@example.com'] = {
            'time': time.time(),
            'track': {'name': 'Cached Old Song', 'artist': 'Old Artist'},
            'is_playing': True,
            'is_connected': True
        }

        with patch('calling_hours.database.get_spotify_token', return_value={'access_token': 'tok'}), \
             patch('calling_hours.spotify.get_valid_access_token', return_value='tok'), \
             patch('calling_hours.spotify.fetch_currently_playing', return_value={'name': 'Fresh New Song', 'artist': 'New Artist'}), \
             patch('calling_hours.ensure_song_lyrics_and_analysis', return_value={'analysis': '## Fresh\nAnalysis'}):
            # Without force=1 -> returns cached
            handler.handle_api_spotify_driving_state('')
            data1 = json.loads(written_data[-1].decode('utf-8'))
            self.assertEqual(data1['track']['name'], 'Cached Old Song')

            # With force=1 -> bypasses cache and returns Fresh New Song
            handler.handle_api_spotify_driving_state('force=1')
            data2 = json.loads(written_data[-1].decode('utf-8'))
            self.assertEqual(data2['track']['name'], 'Fresh New Song')


if __name__ == '__main__':
    unittest.main()
