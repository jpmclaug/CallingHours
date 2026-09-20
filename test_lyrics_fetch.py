import unittest
from unittest.mock import patch, MagicMock
import requests

import calling_hours

class TestLyricsFetch(unittest.TestCase):
    def test_search_genius_song_details(self):
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            'response': {
                'hits': [
                    {
                        'type': 'song',
                        'result': {
                            'title': 'Your Deep Rest',
                            'url': 'https://genius.com/The-hotelier-your-deep-rest-lyrics',
                            'primary_artist': {'name': 'The Hotelier'},
                            'artist_names': 'The Hotelier'
                        }
                    }
                ]
            }
        }
        with patch.object(requests, 'get', return_value=fake_response):
            details = calling_hours.search_genius_song_details('the hoteliers', 'your deep rest')
            self.assertIsNotNone(details)
            self.assertEqual(details['url'], 'https://genius.com/The-hotelier-your-deep-rest-lyrics')
            self.assertEqual(details['artist'], 'The Hotelier')
            self.assertEqual(details['song'], 'Your Deep Rest')

            # Backward compatibility check for search_genius_song
            url_only = calling_hours.search_genius_song('the hoteliers', 'your deep rest')
            self.assertEqual(url_only, 'https://genius.com/The-hotelier-your-deep-rest-lyrics')

    def test_fetch_genius_lyrics_cleans_html(self):
        sample_html = """
        <html>
            <body>
                <div data-lyrics-container="true" class="Lyrics__Container-sc-123">
                    <div class="LyricsHeader__Container-sc-abc">
                        <span>42 Contributors</span>
                        <span>Track Description and info</span>
                    </div>
                    [Verse 1]<br>
                    Line one of the song<br>
                    Line two of the song<br>
                </div>
                <div data-lyrics-container="true" class="Lyrics__Container-sc-123">
                    [Chorus]<br>
                    Chorus line one<br>
                </div>
            </body>
        </html>
        """
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.text = sample_html

        patch_target = 'calling_hours.c_requests.get' if calling_hours.HAS_CURL_CFFI else 'requests.get'
        with patch(patch_target, return_value=fake_resp):
            lyrics = calling_hours.fetch_genius_lyrics('https://genius.com/sample-song-lyrics')
            self.assertIsNotNone(lyrics)
            self.assertNotIn("42 Contributors", lyrics)
            self.assertNotIn("Track Description", lyrics)
            self.assertIn("[Verse 1]\nLine one of the song\nLine two of the song", lyrics)
            self.assertIn("[Chorus]\nChorus line one", lyrics)

    def test_fetch_lrclib_lyrics_with_canonical_names(self):
        def fake_get(url, params=None, headers=None, timeout=None):
            resp = MagicMock()
            if 'api/get' in url and params.get('artist_name') == 'The Hotelier':
                resp.status_code = 200
                resp.json.return_value = {'plainLyrics': 'I called in sick from your funeral'}
            else:
                resp.status_code = 404
                resp.json.return_value = {}
            return resp

        with patch.object(requests, 'get', side_effect=fake_get):
            lyrics = calling_hours.fetch_lrclib_lyrics(
                artist='the hoteliers',
                song='your deep rest',
                canonical_artist='The Hotelier',
                canonical_song='Your Deep Rest'
            )
            self.assertEqual(lyrics, 'I called in sick from your funeral')

    def test_fetch_lrclib_lyrics_fuzzy_artist_match(self):
        def fake_get(url, params=None, headers=None, timeout=None):
            resp = MagicMock()
            if 'api/get' in url:
                resp.status_code = 404
                resp.json.return_value = {}
            elif 'api/search' in url:
                if 'the hoteliers' in params.get('q', ''):
                    resp.status_code = 200
                    resp.json.return_value = []
                else:
                    resp.status_code = 200
                    resp.json.return_value = [
                        {
                            'artistName': 'The Hotelier',
                            'trackName': 'Your Deep Rest',
                            'plainLyrics': 'Fuzzy matched lyrics from LRCLIB'
                        }
                    ]
            return resp

        with patch.object(requests, 'get', side_effect=fake_get):
            lyrics = calling_hours.fetch_lrclib_lyrics(
                artist='the hoteliers',
                song='your deep rest'
            )
            self.assertEqual(lyrics, 'Fuzzy matched lyrics from LRCLIB')

    def test_genius_403_recovers_via_lrclib(self):
        # Simulate Genius search succeeding with canonical names, but scraping raising 403 Forbidden
        genius_info = {
            'url': 'https://genius.com/The-hotelier-your-deep-rest-lyrics',
            'artist': 'The Hotelier',
            'song': 'Your Deep Rest'
        }
        with patch('calling_hours.search_genius_song_details', return_value=genius_info), \
             patch('calling_hours.fetch_genius_lyrics', side_effect=requests.exceptions.HTTPError('403 Client Error: Forbidden for url: https://genius.com/The-hotelier-your-deep-rest-lyrics')), \
             patch('calling_hours.fetch_lrclib_lyrics', return_value='LRCLIB plain lyrics text') as mock_lrclib:

            # Execute the logic from the route
            artist = 'the hoteliers'
            song = 'your deep rest'
            song_url = None
            genius_error = None
            canonical_artist = None
            canonical_song = None
            lyrics = None
            source = ""

            info = calling_hours.search_genius_song_details(artist, song)
            if info:
                song_url = info.get('url')
                canonical_artist = info.get('artist')
                canonical_song = info.get('song')

            if song_url:
                try:
                    lyrics = calling_hours.fetch_genius_lyrics(song_url)
                    if lyrics:
                        source = "Genius"
                except Exception as e:
                    genius_error = str(e)

            if not lyrics:
                lyrics = calling_hours.fetch_lrclib_lyrics(
                    artist=artist,
                    song=song,
                    canonical_artist=canonical_artist,
                    canonical_song=canonical_song
                )
                if lyrics:
                    source = "LRCLIB"

            self.assertEqual(lyrics, 'LRCLIB plain lyrics text')
            self.assertEqual(source, 'LRCLIB')
            self.assertEqual(canonical_artist, 'The Hotelier')
            self.assertEqual(canonical_song, 'Your Deep Rest')
            mock_lrclib.assert_called_once_with(
                artist='the hoteliers',
                song='your deep rest',
                canonical_artist='The Hotelier',
                canonical_song='Your Deep Rest'
            )

if __name__ == '__main__':
    unittest.main()

