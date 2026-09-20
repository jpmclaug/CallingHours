import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import theaudiodb
import database

class TestTheAudioDB(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_theaudiodb.db")
        database.init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_get_theaudiodb_api_key(self):
        # 1. Fallback default
        with patch.dict(os.environ, {}, clear=True):
            with patch("theaudiodb.calling_hours_secrets", create=True) as mock_sec:
                mock_sec.THEAUDIODB_API_KEY = ""
                key = theaudiodb.get_theaudiodb_api_key()
                self.assertEqual(key, "123")

        # 2. Environment variable precedence
        with patch.dict(os.environ, {"THEAUDIODB_API_KEY": "custom_env_key"}):
            key = theaudiodb.get_theaudiodb_api_key()
            self.assertEqual(key, "custom_env_key")

    def test_clean_track_data(self):
        raw_payload = {
            "idTrack": "32724184",
            "idAlbum": "2109615",
            "idArtist": "111239",
            "strTrack": "Yellow",
            "strAlbum": "Parachutes",
            "strArtist": "Coldplay",
            "strArtistAlternate": None,
            "intDuration": "269240",
            "strGenre": "Pop-Rock",
            "strMood": "Relaxed",
            "strStyle": "Rock/Pop",
            "strTheme": "In Love",
            "intTempo": "86",
            "strKey": "B",
            "strOpenKey": "6d",
            "strTimeSignature": "4/4",
            "intDanceability": "43",
            "intAcousticness": "0",
            "intValence": "28",
            "intEnergy": "66",
            "intLiveness": "23",
            "intInstrumentalness": "0",
            "intSpeechiness": "3",
            "strDescriptionEN": "Yellow is a song by Coldplay.",
            "strTrackThumb": "https://r2.theaudiodb.com/images/thumb.jpg",
            "strMusicVid": "https://www.youtube.com/watch?v=yKNxeF4KMsY",
            "strMusicVidDirector": "James & Alex",
            "intMusicVidViews": "409342773",
            "intTotalPlays": "39532808",
            "strSpotifyID": "3AJwUDP919kvQ9QcozQPxg",
            "strMusicBrainzID": "729cf505-94eb-4fbe-bc76-cbae44cff091",
            "strISRC": "GBAYE0000267"
        }

        cleaned = theaudiodb.clean_track_data(raw_payload)
        self.assertEqual(cleaned["track"], "Yellow")
        self.assertEqual(cleaned["album"], "Parachutes")
        self.assertEqual(cleaned["artist"], "Coldplay")
        self.assertEqual(cleaned["genre"], "Pop-Rock")
        self.assertEqual(cleaned["mood"], "Relaxed")
        self.assertEqual(cleaned["theme"], "In Love")
        self.assertEqual(cleaned["tempo"], 86)
        self.assertEqual(cleaned["key"], "B")
        self.assertEqual(cleaned["open_key"], "6d")
        self.assertEqual(cleaned["time_signature"], "4/4")
        self.assertEqual(cleaned["duration_ms"], 269240)
        self.assertEqual(cleaned["duration_formatted"], "4:29")
        self.assertEqual(cleaned["danceability"], 43)
        self.assertEqual(cleaned["energy"], 66)
        self.assertEqual(cleaned["valence"], 28)
        self.assertEqual(cleaned["description"], "Yellow is a song by Coldplay.")
        self.assertEqual(cleaned["music_vid_url"], "https://www.youtube.com/watch?v=yKNxeF4KMsY")
        self.assertEqual(cleaned["music_vid_views"], 409342773)
        self.assertEqual(cleaned["music_vid_views_formatted"], "409.3M")
        self.assertEqual(cleaned["spotify_id"], "3AJwUDP919kvQ9QcozQPxg")
        self.assertEqual(cleaned["isrc"], "GBAYE0000267")
        self.assertEqual(cleaned["raw"], raw_payload)

    def test_clean_track_data_empty_or_invalid(self):
        self.assertEqual(theaudiodb.clean_track_data({}), {})
        self.assertEqual(theaudiodb.clean_track_data(None), {})

    def test_clean_query_title(self):
        self.assertEqual(theaudiodb._clean_query_title("In the End (Remastered 2020)"), "In the End")
        self.assertEqual(theaudiodb._clean_query_title("Yellow [Official Video]"), "Yellow")
        self.assertEqual(theaudiodb._clean_query_title("Empire State of Mind feat. Alicia Keys"), "Empire State of Mind")
        self.assertEqual(theaudiodb._clean_query_title("Song Title (Live at Wembley)"), "Song Title")
        self.assertEqual(theaudiodb._clean_query_title("Normal Song"), "Normal Song")

    @patch("requests.get")
    def test_fetch_track_details_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "track": [
                {
                    "strTrack": "Karma Police",
                    "strArtist": "Radiohead",
                    "strAlbum": "OK Computer",
                    "intTempo": "75",
                    "strKey": "Am",
                    "strGenre": "Alternative Rock"
                }
            ]
        }
        mock_get.return_value = mock_resp

        result = theaudiodb.fetch_track_details("Radiohead", "Karma Police", api_key="123")
        self.assertIsNotNone(result)
        self.assertEqual(result["track"], "Karma Police")
        self.assertEqual(result["artist"], "Radiohead")
        self.assertEqual(result["tempo"], 75)
        self.assertEqual(result["key"], "Am")

    @patch("requests.get")
    def test_fetch_track_details_not_found(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"track": None}
        mock_get.return_value = mock_resp

        result = theaudiodb.fetch_track_details("Unknown", "Song", api_key="123")
        self.assertIsNone(result)

    @patch("requests.get")
    def test_fetch_track_details_fallback_strips_suffix(self, mock_get):
        # 1st call for "Karma Police (Remastered)" returns None
        resp_fail = MagicMock()
        resp_fail.status_code = 200
        resp_fail.json.return_value = {"track": None}

        # 2nd call for "Karma Police" returns the track
        resp_success = MagicMock()
        resp_success.status_code = 200
        resp_success.json.return_value = {
            "track": [
                {
                    "strTrack": "Karma Police",
                    "strArtist": "Radiohead",
                    "intTempo": "75"
                }
            ]
        }
        mock_get.side_effect = [resp_fail, resp_success]

        result = theaudiodb.fetch_track_details("Radiohead", "Karma Police (Remastered)", api_key="123")
        self.assertIsNotNone(result)
        self.assertEqual(result["track"], "Karma Police")
        self.assertEqual(mock_get.call_count, 2)

    @patch("requests.get")
    def test_fetch_track_details_network_error(self, mock_get):
        mock_get.side_effect = Exception("Connection timed out")
        result = theaudiodb.fetch_track_details("Artist", "Track")
        self.assertIsNone(result)

    @patch("theaudiodb.fetch_track_details")
    def test_get_or_fetch_track_metadata_caching(self, mock_fetch):
        mock_fetch.return_value = {
            "track": "Dammit",
            "artist": "Blink-182",
            "tempo": 110,
            "genre": "Pop Punk"
        }

        # First call: not cached, should call fetch and save into db
        data = theaudiodb.get_or_fetch_track_metadata("Blink-182", "Dammit", db_path=self.db_path)
        self.assertIsNotNone(data)
        self.assertEqual(data["track"], "Dammit")
        self.assertEqual(mock_fetch.call_count, 1)

        # Check that it's now in database
        cached = database.get_theaudiodb_data("Blink-182", "Dammit", db_path=self.db_path)
        self.assertIsNotNone(cached)
        self.assertEqual(cached["track"], "Dammit")
        self.assertEqual(cached["tempo"], 110)

        # Second call: should load from cache, not calling fetch again
        data2 = theaudiodb.get_or_fetch_track_metadata("Blink-182", "Dammit", db_path=self.db_path)
        self.assertEqual(mock_fetch.call_count, 1)
        self.assertEqual(data2["track"], "Dammit")

        # Third call with force_refresh=True: should call fetch again
        theaudiodb.get_or_fetch_track_metadata("Blink-182", "Dammit", force_refresh=True, db_path=self.db_path)
        self.assertEqual(mock_fetch.call_count, 2)


if __name__ == "__main__":
    unittest.main()
