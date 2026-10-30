import unittest
from unittest.mock import patch, MagicMock
import database
import calling_hours
import spotify

class TestSpotifyPerformance(unittest.TestCase):
    def test_ensure_lyrics_only_cached_does_not_block(self):
        # When lyrics/analysis are not in DB and only_cached=True is passed,
        # it should return pending immediately without blocking on external APIs.
        with patch('calling_hours.search_genius_song_details') as mock_genius, \
             patch('calling_hours.fetch_genius_lyrics') as mock_fetch:
            res = calling_hours.ensure_song_lyrics_and_analysis(
                "Nonexistent Artist 9999",
                "Nonexistent Song 9999",
                only_cached=True
            )
            self.assertTrue(res.get('pending'))
            self.assertFalse(res.get('cached'))
            mock_genius.assert_not_called()
            mock_fetch.assert_not_called()

    def test_enrichment_status_caching(self):
        # Ensure repeated calls to get_enrichment_status use the 60s in-memory cache
        s1 = database.get_enrichment_status()
        self.assertIn('coverage_pct', s1)
        s2 = database.get_enrichment_status()
        self.assertEqual(s1, s2)

    def test_lifetime_stats_caching(self):
        # Ensure stats cache returns cached data without re-querying
        stats = database.get_spotify_lifetime_stats("test_user@example.com")
        self.assertIsInstance(stats, dict)
        stats2 = database.get_spotify_lifetime_stats("test_user@example.com")
        self.assertEqual(stats, stats2)

if __name__ == '__main__':
    unittest.main()
