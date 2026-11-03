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


if __name__ == '__main__':
    unittest.main()
