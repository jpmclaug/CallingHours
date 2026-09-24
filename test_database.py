import os
import tempfile
import unittest
import time
import database

class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_calling_hours.db")
        database.init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_init_db(self):
        # Database should be initialized and have searches table
        with database.get_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='searches'")
            self.assertIsNotNone(cursor.fetchone())

    def test_save_and_get_search(self):
        rec_id = database.save_search(
            artist="Adele",
            song="Hello",
            lyrics="Hello from the other side...",
            source="Genius",
            song_url="https://genius.com/adele-hello",
            db_path=self.db_path
        )
        self.assertGreater(rec_id, 0)

        # Retrieve by artist and song
        rec = database.get_search("adele", "hello", db_path=self.db_path)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["artist"], "Adele")
        self.assertEqual(rec["song"], "Hello")
        self.assertEqual(rec["source"], "Genius")
        self.assertEqual(rec["lyrics"], "Hello from the other side...")

        # Retrieve by id
        rec_by_id = database.get_search_by_id(rec_id, db_path=self.db_path)
        self.assertEqual(rec_by_id["id"], rec_id)
        self.assertEqual(rec_by_id["song"], "Hello")

    def test_save_search_upsert_preserves_analysis(self):
        # Insert initial search
        rec_id = database.save_search(
            artist="The Cure",
            song="Pictures of You",
            lyrics="I've been looking so long at these pictures of you",
            source="LRCLIB",
            db_path=self.db_path
        )

        # Save analysis
        database.save_analysis(
            artist="The Cure",
            song="Pictures of You",
            analysis="The song explores themes of grief, memory, and longing.",
            model_name="gemini-3.8-flash",
            prompt_name="Default Analysis",
            db_path=self.db_path
        )

        rec = database.get_search("The Cure", "Pictures of You", db_path=self.db_path)
        self.assertEqual(rec["analysis"], "The song explores themes of grief, memory, and longing.")
        self.assertEqual(rec["model_name"], "gemini-3.8-flash")

        # Re-search the same song with updated source or url
        database.save_search(
            artist="the cure ",
            song=" Pictures of You",
            lyrics="I've been looking so long at these pictures of you",
            source="Genius",
            song_url="https://genius.com/the-cure-pictures-of-you",
            db_path=self.db_path
        )

        rec2 = database.get_search("the cure", "pictures of you", db_path=self.db_path)
        self.assertEqual(rec2["id"], rec_id)
        self.assertEqual(rec2["source"], "Genius")
        self.assertEqual(rec2["song_url"], "https://genius.com/the-cure-pictures-of-you")
        # Analysis should still be preserved
        self.assertEqual(rec2["analysis"], "The song explores themes of grief, memory, and longing.")

    def test_distinct_bands(self):
        database.save_search("Radiohead", "Creep", lyrics="I'm a creep", db_path=self.db_path)
        database.save_search("Radiohead", "Karma Police", lyrics="Karma police", db_path=self.db_path)
        database.save_search("Deftones", "Change", lyrics="I watched a change in you", db_path=self.db_path)

        bands = database.get_distinct_bands(db_path=self.db_path)
        self.assertEqual(len(bands), 2)
        band_names = [b["artist"] for b in bands]
        self.assertIn("Radiohead", band_names)
        self.assertIn("Deftones", band_names)

        # Radiohead has 2 songs
        radiohead = next(b for b in bands if b["artist"] == "Radiohead")
        self.assertEqual(radiohead["song_count"], 2)

    def test_get_songs_by_band(self):
        database.save_search("Radiohead", "Creep", lyrics="I'm a creep", db_path=self.db_path)
        database.save_search("Radiohead", "Karma Police", lyrics="Karma police", db_path=self.db_path)

        songs = database.get_songs_by_band("radiohead", db_path=self.db_path)
        self.assertEqual(len(songs), 2)
        song_titles = [s["song"] for s in songs]
        self.assertIn("Creep", song_titles)
        self.assertIn("Karma Police", song_titles)
        self.assertTrue(songs[0]["has_lyrics"])
        self.assertFalse(songs[0]["has_analysis"])

    def test_get_recent_searches(self):
        database.save_search("Artist1", "Song1", db_path=self.db_path)
        database.save_search("Artist2", "Song2", db_path=self.db_path)
        recent = database.get_recent_searches(limit=10, db_path=self.db_path)
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[0]["artist"], "Artist2")

    def test_delete_search(self):
        rec_id = database.save_search("Foo", "Bar", db_path=self.db_path)
        self.assertIsNotNone(database.get_search_by_id(rec_id, db_path=self.db_path))
        deleted = database.delete_search(rec_id, db_path=self.db_path)
        self.assertTrue(deleted)
        self.assertIsNone(database.get_search_by_id(rec_id, db_path=self.db_path))

    def test_primary_admin_seeded_on_init(self):
        user = database.get_user("jpmclaug@gmail.com", db_path=self.db_path)
        self.assertIsNotNone(user)
        self.assertEqual(user["email"], "jpmclaug@gmail.com")
        self.assertTrue(user["is_admin"])
        self.assertTrue(user["is_active"])

    def test_user_crud_and_roles(self):
        # Insert new user
        uid = database.upsert_user("alice@example.com", name="Alice", is_admin=False, is_active=True, db_path=self.db_path)
        self.assertGreater(uid, 0)

        user = database.get_user("alice@example.com", db_path=self.db_path)
        self.assertIsNotNone(user)
        self.assertEqual(user["name"], "Alice")
        self.assertFalse(user["is_admin"])
        self.assertTrue(user["is_active"])

        # Promote to admin
        success = database.set_user_admin_role("alice@example.com", True, db_path=self.db_path)
        self.assertTrue(success)
        user = database.get_user("alice@example.com", db_path=self.db_path)
        self.assertTrue(user["is_admin"])

        # Deactivate
        success = database.set_user_active_status("alice@example.com", False, db_path=self.db_path)
        self.assertTrue(success)
        user = database.get_user("alice@example.com", db_path=self.db_path)
        self.assertFalse(user["is_active"])

        # Delete
        success = database.delete_user("alice@example.com", db_path=self.db_path)
        self.assertTrue(success)
        self.assertIsNone(database.get_user("alice@example.com", db_path=self.db_path))

    def test_primary_admin_safeguards(self):
        # Cannot deactivate primary admin
        self.assertFalse(database.set_user_active_status("jpmclaug@gmail.com", False, db_path=self.db_path))
        user = database.get_user("jpmclaug@gmail.com", db_path=self.db_path)
        self.assertTrue(user["is_active"])

        # Cannot demote primary admin
        self.assertFalse(database.set_user_admin_role("jpmclaug@gmail.com", False, db_path=self.db_path))
        user = database.get_user("jpmclaug@gmail.com", db_path=self.db_path)
        self.assertTrue(user["is_admin"])

        # Cannot delete primary admin
        self.assertFalse(database.delete_user("jpmclaug@gmail.com", db_path=self.db_path))
        user = database.get_user("jpmclaug@gmail.com", db_path=self.db_path)
        self.assertIsNotNone(user)

    def test_session_lifecycle(self):
        token = database.create_session("jpmclaug@gmail.com", duration_days=7, db_path=self.db_path)
        self.assertTrue(bool(token))

        # Retrieve user by session
        session_user = database.get_session_user(token, db_path=self.db_path)
        self.assertIsNotNone(session_user)
        self.assertEqual(session_user["email"], "jpmclaug@gmail.com")
        self.assertTrue(session_user["is_admin"])

        # Delete session
        database.delete_session(token, db_path=self.db_path)
        self.assertIsNone(database.get_session_user(token, db_path=self.db_path))

    def test_save_analysis_persists_and_updates_lyrics(self):
        # 1. Direct save analysis without prior search
        database.save_analysis(
            artist="Fugazi",
            song="Waiting Room",
            analysis="Themes of patience and anticipation.",
            model_name="gemini-3.8-flash",
            prompt_name="Default Analysis",
            lyrics="1 2 3, I am a patient boy...",
            db_path=self.db_path
        )
        rec = database.get_search("Fugazi", "Waiting Room", db_path=self.db_path)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["lyrics"], "1 2 3, I am a patient boy...")
        self.assertEqual(rec["source"], "Manual")
        self.assertEqual(rec["analysis"], "Themes of patience and anticipation.")

        # 2. Update existing search lyrics via save_analysis (user edited lyrics)
        database.save_analysis(
            artist="fugazi",
            song="waiting room",
            analysis="Updated analysis.",
            model_name="gemini-3.8-flash",
            prompt_name="Default Analysis",
            lyrics="1 2 3, I am a patient boy, I wait I wait I wait...",
            db_path=self.db_path
        )
        rec2 = database.get_search("Fugazi", "Waiting Room", db_path=self.db_path)
        self.assertEqual(rec2["lyrics"], "1 2 3, I am a patient boy, I wait I wait I wait...")
        self.assertEqual(rec2["analysis"], "Updated analysis.")

    def test_touch_search(self):
        rec_id1 = database.save_search("ArtistA", "SongA", lyrics="Lyrics A", db_path=self.db_path)
        rec_id2 = database.save_search("ArtistB", "SongB", lyrics="Lyrics B", db_path=self.db_path)

        # Initially ArtistB is top because it was saved second
        recent = database.get_recent_searches(limit=2, db_path=self.db_path)
        self.assertEqual(recent[0]["artist"], "ArtistB")

        # Sleep briefly so updated_at strictly increments on systems with lower clock granularity
        time.sleep(0.02)

        # Touch ArtistA to simulate re-searching or loading from cache
        database.touch_search("ArtistA", "SongA", db_path=self.db_path)

        # ArtistA should now be at the top of recent searches and distinct bands
        recent_after = database.get_recent_searches(limit=2, db_path=self.db_path)
        self.assertEqual(recent_after[0]["artist"], "ArtistA")

        bands = database.get_distinct_bands(db_path=self.db_path)
        self.assertEqual(bands[0]["artist"], "ArtistA")

    def test_distinct_bands_preserves_proper_casing(self):
        # Even if artist is entered, verify proper capitalization is maintained
        database.save_search("The National", "Fake Empire", lyrics="Stay down...", db_path=self.db_path)
        database.save_search("the national", "Bloodbuzz Ohio", lyrics="Stand up...", db_path=self.db_path)

        bands = database.get_distinct_bands(db_path=self.db_path)
        national_band = next(b for b in bands if b["artist"].lower() == "the national")
        self.assertEqual(national_band["song_count"], 2)

    def test_artist_metadata_crud(self):
        tags = [{"name": "post-punk", "count": 100, "url": "https://last.fm/tag/post-punk"}]
        tracks = [{"name": "Song 1", "playcount": 5000, "listeners": 1200, "rank": 1, "url": ""}]
        
        art_id = database.save_artist_metadata("Turnstile", tags=tags, top_tracks=tracks, db_path=self.db_path)
        self.assertGreater(art_id, 0)

        art = database.get_artist_metadata("turnstile", db_path=self.db_path)
        self.assertIsNotNone(art)
        self.assertEqual(art["artist"], "Turnstile")
        self.assertEqual(len(art["tags"]), 1)
        self.assertEqual(art["tags"][0]["name"], "post-punk")
        self.assertEqual(len(art["top_tracks"]), 1)
        self.assertEqual(art["top_tracks"][0]["name"], "Song 1")

        # Update artist with new tags
        new_tags = [{"name": "hardcore punk", "count": 120, "url": ""}]
        database.save_artist_metadata("Turnstile", tags=new_tags, db_path=self.db_path)

        art_updated = database.get_artist_metadata("turnstile", db_path=self.db_path)
        self.assertEqual(len(art_updated["tags"]), 1)
        self.assertEqual(art_updated["tags"][0]["name"], "hardcore punk")
        # Previous top tracks should be preserved
        self.assertEqual(len(art_updated["top_tracks"]), 1)

    def test_track_tags_persistence(self):
        track_tags = [
            {"name": "hardcore", "count": 90, "url": ""},
            {"name": "melodic", "count": 60, "url": ""}
        ]
        rec_id = database.save_search(
            artist="Calling Hours",
            song="Calling Hours",
            lyrics="Here in the calling hours...",
            track_tags=track_tags,
            db_path=self.db_path
        )
        rec = database.get_search_by_id(rec_id, db_path=self.db_path)
        self.assertEqual(len(rec["track_tags"]), 2)
        self.assertEqual(rec["track_tags"][0]["name"], "hardcore")

        # Update via save_track_tags
        updated_tags = [{"name": "punk", "count": 100, "url": ""}]
        database.save_track_tags("calling hours", "calling hours", updated_tags, db_path=self.db_path)

        rec_updated = database.get_search("Calling Hours", "Calling Hours", db_path=self.db_path)
        self.assertEqual(len(rec_updated["track_tags"]), 1)
        self.assertEqual(rec_updated["track_tags"][0]["name"], "punk")

    def test_theaudiodb_data_persistence(self):
        audiodb_data = {
            "track": "Yellow",
            "artist": "Coldplay",
            "album": "Parachutes",
            "tempo": 86,
            "key": "B",
            "mood": "Relaxed",
            "genre": "Pop-Rock",
            "energy": 66,
            "valence": 28,
            "danceability": 43,
            "description": "Yellow is a song by British alternative rock band Coldplay.",
            "music_vid_url": "https://www.youtube.com/watch?v=yKNxeF4KMsY"
        }

        # 1. Save search with theaudiodb_data
        rec_id = database.save_search(
            artist="Coldplay",
            song="Yellow",
            lyrics="Look at the stars, look how they shine for you",
            theaudiodb_data=audiodb_data,
            db_path=self.db_path
        )
        rec = database.get_search_by_id(rec_id, db_path=self.db_path)
        self.assertIsNotNone(rec["theaudiodb_data"])
        self.assertEqual(rec["theaudiodb_data"]["tempo"], 86)
        self.assertEqual(rec["theaudiodb_data"]["key"], "B")
        self.assertEqual(rec["theaudiodb_data"]["mood"], "Relaxed")

        # 2. get_theaudiodb_data helper
        direct_data = database.get_theaudiodb_data("coldplay", "yellow", db_path=self.db_path)
        self.assertIsNotNone(direct_data)
        self.assertEqual(direct_data["album"], "Parachutes")

        # 3. Update via save_theaudiodb_data
        updated_data = dict(audiodb_data)
        updated_data["tempo"] = 88
        database.save_theaudiodb_data("coldplay", "yellow", updated_data, db_path=self.db_path)

        rec_updated = database.get_search("Coldplay", "Yellow", db_path=self.db_path)
        self.assertEqual(rec_updated["theaudiodb_data"]["tempo"], 88)

        # 4. save_analysis preserves theaudiodb_data
        database.save_analysis(
            artist="Coldplay",
            song="Yellow",
            analysis="Deep themes of love and devotion.",
            db_path=self.db_path
        )
        rec_after_analysis = database.get_search("Coldplay", "Yellow", db_path=self.db_path)
        self.assertEqual(rec_after_analysis["analysis"], "Deep themes of love and devotion.")
        self.assertIsNotNone(rec_after_analysis["theaudiodb_data"])
        self.assertEqual(rec_after_analysis["theaudiodb_data"]["tempo"], 88)

        # 5. Standalone save_theaudiodb_data for new track (upsert)
        new_track_data = {"track": "Shiver", "artist": "Coldplay", "tempo": 125}
        database.save_theaudiodb_data("Coldplay", "Shiver", new_track_data, db_path=self.db_path)
        shiver_rec = database.get_search("Coldplay", "Shiver", db_path=self.db_path)
        self.assertIsNotNone(shiver_rec)
        self.assertEqual(shiver_rec["theaudiodb_data"]["tempo"], 125)

    def test_spotify_tokens_and_history(self):
        # 1. Save and retrieve Spotify token
        database.save_spotify_token(
            user_email="testuser@example.com",
            access_token="initial_access_token_123",
            refresh_token="initial_refresh_token_456",
            expires_at="2026-10-01 12:00:00",
            spotify_user_id="spot_user_1",
            spotify_display_name="Test Spotify User",
            spotify_profile_url="https://open.spotify.com/user/spot_user_1",
            spotify_image_url="https://img.spotify.com/avatar.jpg",
            db_path=self.db_path
        )
        tok = database.get_spotify_token("testuser@example.com", db_path=self.db_path)
        self.assertIsNotNone(tok)
        self.assertEqual(tok["access_token"], "initial_access_token_123")
        self.assertEqual(tok["refresh_token"], "initial_refresh_token_456")
        self.assertEqual(tok["spotify_user_id"], "spot_user_1")
        self.assertEqual(tok["spotify_display_name"], "Test Spotify User")

        # 2. Update access token without refresh token (retaining previous refresh_token)
        database.save_spotify_token(
            user_email="testuser@example.com",
            access_token="refreshed_access_token_789",
            refresh_token=None,
            expires_at="2026-10-02 12:00:00",
            db_path=self.db_path
        )
        tok2 = database.get_spotify_token("testuser@example.com", db_path=self.db_path)
        self.assertIsNotNone(tok2)
        self.assertEqual(tok2["access_token"], "refreshed_access_token_789")
        self.assertEqual(tok2["refresh_token"], "initial_refresh_token_456")
        self.assertEqual(tok2["spotify_display_name"], "Test Spotify User")

        # 3. Save Spotify history items
        items = [
            {
                "track_id": "trk_1",
                "played_at": "2026-09-24T10:00:00Z",
                "name": "Bleed American",
                "artist": "Jimmy Eat World",
                "album": "Bleed American",
                "album_image": "https://img.spotify.com/alb1.jpg",
                "duration_ms": 182000,
                "popularity": 75,
                "preview_url": "https://preview.spotify.com/1",
                "spotify_url": "https://open.spotify.com/track/trk_1",
                "release_date": "2001-07-24"
            },
            {
                "track_id": "trk_2",
                "played_at": "2026-09-24T09:30:00Z",
                "name": "Kisses",
                "artist": "Slowdive",
                "album": "everything is alive",
                "album_image": "https://img.spotify.com/alb2.jpg",
                "duration_ms": 236000,
                "popularity": 65,
                "preview_url": "https://preview.spotify.com/2",
                "spotify_url": "https://open.spotify.com/track/trk_2",
                "release_date": "2023-09-01"
            }
        ]
        inserted = database.save_spotify_history_items("testuser@example.com", items, db_path=self.db_path)
        self.assertEqual(inserted, 2)
        self.assertEqual(database.get_spotify_history_count("testuser@example.com", db_path=self.db_path), 2)

        # 4. Duplicate items ignored
        database.save_spotify_history_items("testuser@example.com", items, db_path=self.db_path)
        self.assertEqual(database.get_spotify_history_count("testuser@example.com", db_path=self.db_path), 2)

        # 5. Fetch history
        hist = database.get_spotify_history("testuser@example.com", limit=10, db_path=self.db_path)
        self.assertEqual(len(hist), 2)
        self.assertEqual(hist[0]["track_name"], "Bleed American")
        self.assertEqual(hist[1]["track_name"], "Kisses")

        # 6. Delete token (disconnect)
        database.delete_spotify_token("testuser@example.com", db_path=self.db_path)
        self.assertIsNone(database.get_spotify_token("testuser@example.com", db_path=self.db_path))

        # 7. Clear history
        database.clear_spotify_history("testuser@example.com", db_path=self.db_path)
        self.assertEqual(database.get_spotify_history_count("testuser@example.com", db_path=self.db_path), 0)


if __name__ == "__main__":
    unittest.main()


