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

        # Update artist with new tags and spotify_data
        new_tags = [{"name": "hardcore punk", "count": 120, "url": ""}]
        spot_data = {"popularity": 75, "followers": 500000, "genres": ["hardcore", "punk"]}
        database.save_artist_metadata("Turnstile", tags=new_tags, spotify_data=spot_data, db_path=self.db_path)

        art_updated = database.get_artist_metadata("turnstile", db_path=self.db_path)
        self.assertEqual(len(art_updated["tags"]), 1)
        self.assertEqual(art_updated["tags"][0]["name"], "hardcore punk")
        # Previous top tracks should be preserved
        self.assertEqual(len(art_updated["top_tracks"]), 1)
        # Spotify data should be persisted and parsed as dict
        self.assertIsNotNone(art_updated.get("spotify_data"))
        self.assertEqual(art_updated["spotify_data"]["popularity"], 75)
        self.assertEqual(art_updated["spotify_data"]["followers"], 500000)

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

        # 5. Fetch history and verify normalized aliases
        hist = database.get_spotify_history("testuser@example.com", limit=10, db_path=self.db_path)
        self.assertEqual(len(hist), 2)
        self.assertEqual(hist[0]["track_name"], "Bleed American")
        self.assertEqual(hist[0]["name"], "Bleed American")
        self.assertEqual(hist[0]["artist"], "Jimmy Eat World")
        self.assertEqual(hist[0]["track_id"], "trk_1")
        self.assertEqual(hist[1]["track_name"], "Kisses")
        self.assertEqual(hist[1]["name"], "Kisses")
        self.assertEqual(hist[1]["track_id"], "trk_2")

        # 5b. Save items using Spotify Extended Streaming History export format
        ext_items = [
            {
                "ts": "2024-05-01T12:00:00Z",
                "master_metadata_track_name": "Modern Color",
                "master_metadata_album_artist_name": "One Step Closer",
                "master_metadata_album_album_name": "Songs for the Finished",
                "spotify_track_uri": "spotify:track:ext_trk_999",
                "ms_played": 185000,
            }
        ]
        ext_inserted = database.save_spotify_history_items("testuser@example.com", ext_items, db_path=self.db_path)
        self.assertEqual(ext_inserted, 1)
        self.assertEqual(database.get_spotify_history_count("testuser@example.com", db_path=self.db_path), 3)

        # Re-saving identical item is ignored (no double counting)
        ext_dup = database.save_spotify_history_items("testuser@example.com", ext_items, db_path=self.db_path)
        self.assertEqual(ext_dup, 0)
        self.assertEqual(database.get_spotify_history_count("testuser@example.com", db_path=self.db_path), 3)

        # 6. Delete token (disconnect)
        database.delete_spotify_token("testuser@example.com", db_path=self.db_path)
        self.assertIsNone(database.get_spotify_token("testuser@example.com", db_path=self.db_path))

        # 7. Clear history
        database.clear_spotify_history("testuser@example.com", db_path=self.db_path)
        self.assertEqual(database.get_spotify_history_count("testuser@example.com", db_path=self.db_path), 0)

    def test_spotify_listening_timeline_rollups_phases_and_genres(self):
        email = "timeline@example.com"
        plays = [
            ("2024-01", "Signal Fire", 2),
            ("2024-02", "Signal Fire", 10),
            ("2024-03", "Signal Fire", 12),
            ("2024-04", "Signal Fire", 2),
            ("2024-01", "Unenriched Band", 1),
            ("2024-01", "Ritual", 1),
            ("2024-02", "Ritual", 1),
            ("2024-03", "Ritual", 1),
            ("2024-04", "Ritual", 4),
            ("2024-01", "Perennial", 9),
            ("2024-02", "Perennial", 9),
            ("2024-03", "Perennial", 9),
        ]
        history = []
        for month, artist, count in plays:
            for play_index in range(count):
                history.append({
                    "track_id": f"{artist}-{month}-{play_index}",
                    "played_at": f"{month}-05T{play_index % 24:02d}:00:00Z",
                    "name": f"Track {play_index}",
                    "artist": artist,
                    "album": "Archive Album",
                    "duration_ms": 180000,
                })
        database.save_spotify_history_items(email, history, db_path=self.db_path)
        database.upsert_artist_enrichment(
            "Signal Fire",
            {
                "genre": "emo",
                "tags": [{"name": "indie rock"}, {"name": "emo"}],
            },
            db_path=self.db_path,
        )

        monthly = database.get_spotify_listening_timeline(
            email, granularity="month", artist_limit=1, db_path=self.db_path
        )
        self.assertEqual([bucket["key"] for bucket in monthly["buckets"]], [
            "2024-01", "2024-02", "2024-03", "2024-04"
        ])
        february = monthly["buckets"][1]
        self.assertEqual(february["artists"][0]["artist"], "Signal Fire")
        self.assertEqual(february["artists"][0]["genres"], ["emo", "indie rock"])
        self.assertLess(monthly["genre_coverage_percent"], 100)
        sustained = [
            phase for phase in monthly["phases"]
            if phase["artist"] == "Signal Fire" and phase["type"] == "sustained"
        ]
        self.assertEqual(len(sustained), 1)
        self.assertEqual((sustained[0]["start"], sustained[0]["end"]), ("2024-02", "2024-03"))
        self.assertEqual(sustained[0]["play_count"], 22)
        spike = next(phase for phase in monthly["phases"] if phase["artist"] == "Ritual")
        self.assertEqual((spike["type"], spike["start"], spike["end"]), ("spike", "2024-04", "2024-04"))

        quarterly = database.get_spotify_listening_timeline(
            email, granularity="quarter", artist_limit=1, db_path=self.db_path
        )
        self.assertEqual([bucket["key"] for bucket in quarterly["buckets"]], ["2024-Q1", "2024-Q2"])
        self.assertEqual(quarterly["buckets"][0]["artists"][0]["artist"], "Perennial")
        self.assertEqual(quarterly["buckets"][0]["artists"][0]["count"], 27)
        yearly = database.get_spotify_listening_timeline(
            email, granularity="year", db_path=self.db_path
        )
        self.assertEqual(yearly["buckets"][0]["key"], "2024")
        self.assertEqual(yearly["total_plays"], len(history))

    def test_spotify_listening_timeline_rejects_unknown_granularity(self):
        with self.assertRaises(ValueError):
            database.get_spotify_listening_timeline(
                "timeline@example.com", granularity="week", db_path=self.db_path
            )

    def test_playlist_generation_and_storage(self):
        # 1. Initially no analyzed songs
        self.assertEqual(database.get_analyzed_songs_count(db_path=self.db_path), 0)
        self.assertEqual(len(database.get_analyzed_songs(db_path=self.db_path)), 0)
        self.assertEqual(len(database.get_analyzed_artists(db_path=self.db_path)), 0)

        # 2. Add songs: one with analysis, one without
        s1_id = database.save_search(
            artist="Jimmy Eat World",
            song="Sweetness",
            lyrics="Are you listening?...",
            track_tags=[{"name": "emo"}, {"name": "alternative rock"}],
            theaudiodb_data={"tempo": 135, "key": "D", "energy": 85},
            db_path=self.db_path
        )
        s2_id = database.save_search(
            artist="Jimmy Eat World",
            song="The Middle",
            lyrics="Hey, don't write yourself off yet...",
            db_path=self.db_path
        )
        s3_id = database.save_search(
            artist="Slowdive",
            song="Alison",
            lyrics="Listen close and don't be slow...",
            track_tags=[{"name": "shoegaze"}, {"name": "dream pop"}],
            db_path=self.db_path
        )

        # Save analysis for Sweetness and Alison
        database.save_analysis(
            artist="Jimmy Eat World",
            song="Sweetness",
            analysis="## Themes\nDesire for connection.",
            db_path=self.db_path
        )
        database.save_analysis(
            artist="Slowdive",
            song="Alison",
            analysis="## Themes\nNostalgia and melancholy.",
            db_path=self.db_path
        )

        # Verify analyzed songs count
        self.assertEqual(database.get_analyzed_songs_count(db_path=self.db_path), 2)
        analyzed_songs = database.get_analyzed_songs(db_path=self.db_path)
        self.assertEqual(len(analyzed_songs), 2)

        # Filter by artist
        jew_songs = database.get_analyzed_songs(artist="Jimmy Eat World", db_path=self.db_path)
        self.assertEqual(len(jew_songs), 1)
        self.assertEqual(jew_songs[0]["song"], "Sweetness")

        # Filter by tag
        shoegaze_songs = database.get_analyzed_songs(tag="shoegaze", db_path=self.db_path)
        self.assertEqual(len(shoegaze_songs), 1)
        self.assertEqual(shoegaze_songs[0]["song"], "Alison")

        # Verify analyzed artists
        artists = database.get_analyzed_artists(db_path=self.db_path)
        self.assertEqual(len(artists), 2)
        artist_names = [a["artist"] for a in artists]
        self.assertIn("Jimmy Eat World", artist_names)
        self.assertIn("Slowdive", artist_names)

        # Verify analyzed tags
        tags = database.get_analyzed_tags(db_path=self.db_path)
        tag_names = [t["tag"] for t in tags]
        self.assertIn("Emo", tag_names)
        self.assertIn("Shoegaze", tag_names)

        # 3. Save a playlist
        playlist_items = [
            {"id": s1_id, "artist": "Jimmy Eat World", "song": "Sweetness", "model_name": "gemini-3.8-flash"},
            {"id": s3_id, "artist": "Slowdive", "song": "Alison", "model_name": "gemini-3.8-flash"}
        ]
        pid = database.save_playlist(
            name="My Analyzed Gems",
            generator_type="all_analyzed",
            items=playlist_items,
            description="The best analyzed tracks",
            criteria={"order": "updated_at DESC"},
            db_path=self.db_path
        )
        self.assertGreater(pid, 0)

        # 4. Fetch saved playlist
        saved_p = database.get_saved_playlist(pid, db_path=self.db_path)
        self.assertIsNotNone(saved_p)
        self.assertEqual(saved_p["name"], "My Analyzed Gems")
        self.assertEqual(saved_p["track_count"], 2)
        self.assertEqual(len(saved_p["items"]), 2)
        self.assertEqual(saved_p["items"][0]["song"], "Sweetness")
        self.assertEqual(saved_p["items"][1]["song"], "Alison")

        # 5. Update Spotify info
        database.update_playlist_spotify_info(pid, "spotify_pl_123", "https://open.spotify.com/playlist/spotify_pl_123", db_path=self.db_path)
        updated_p = database.get_saved_playlist(pid, db_path=self.db_path)
        self.assertEqual(updated_p["spotify_playlist_id"], "spotify_pl_123")
        self.assertEqual(updated_p["spotify_playlist_url"], "https://open.spotify.com/playlist/spotify_pl_123")

        # 6. List saved playlists
        all_playlists = database.get_saved_playlists(db_path=self.db_path)
        self.assertEqual(len(all_playlists), 1)

        # 7. Delete saved playlist
        deleted = database.delete_saved_playlist(pid, db_path=self.db_path)
        self.assertTrue(deleted)
        self.assertIsNone(database.get_saved_playlist(pid, db_path=self.db_path))
        self.assertEqual(len(database.get_saved_playlists(db_path=self.db_path)), 0)

    def test_band_ratings_crud_and_stats(self):
        # 1. Save ratings (0 to 5)
        # 5: Absolute Favorite
        r5 = database.save_band_rating("Jimmy Eat World", 5, user_email="test@example.com", notes="All time favorite", db_path=self.db_path)
        self.assertIsNotNone(r5)
        self.assertEqual(r5["artist"], "Jimmy Eat World")
        self.assertEqual(r5["rating"], 5)
        self.assertEqual(r5["notes"], "All time favorite")

        # 4: Really Enjoy
        r4 = database.save_band_rating("The Starting Line", 4, user_email="test@example.com", db_path=self.db_path)
        self.assertEqual(r4["rating"], 4)

        # 3: Likes
        r3 = database.save_band_rating("Taking Back Sunday", 3, user_email="test@example.com", db_path=self.db_path)
        self.assertEqual(r3["rating"], 3)

        # 2: Is ok
        r2 = database.save_band_rating("Good Charlotte", 2, user_email="test@example.com", db_path=self.db_path)
        self.assertEqual(r2["rating"], 2)

        # 1: Dislike
        r1 = database.save_band_rating("Nickelback", 1, user_email="test@example.com", db_path=self.db_path)
        self.assertEqual(r1["rating"], 1)

        # 0: Know nothing about them
        r0 = database.save_band_rating("Unknown Indie Band", 0, user_email="test@example.com", db_path=self.db_path)
        self.assertEqual(r0["rating"], 0)

        # Rating clamp testing
        r_clamped_high = database.save_band_rating("Overflow Band", 99, user_email="test@example.com", db_path=self.db_path)
        self.assertEqual(r_clamped_high["rating"], 5)
        r_clamped_low = database.save_band_rating("Underflow Band", -10, user_email="test@example.com", db_path=self.db_path)
        self.assertEqual(r_clamped_low["rating"], 0)

        # 2. Get single rating
        retrieved = database.get_band_rating("jimmy eat world", user_email="test@example.com", db_path=self.db_path)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["rating"], 5)
        self.assertEqual(retrieved["artist"], "Jimmy Eat World")

        # 3. Update existing rating (UPSERT)
        updated = database.save_band_rating("Good Charlotte", 3, user_email="test@example.com", notes="Growing on me", db_path=self.db_path)
        self.assertEqual(updated["rating"], 3)
        self.assertEqual(updated["notes"], "Growing on me")
        check_updated = database.get_band_rating("Good Charlotte", user_email="test@example.com", db_path=self.db_path)
        self.assertEqual(check_updated["rating"], 3)

        # 4. Filter ratings
        fives = database.get_band_ratings(user_email="test@example.com", rating_filter=5, db_path=self.db_path)
        self.assertEqual(len(fives), 2)  # Jimmy Eat World + Overflow Band clamped to 5

        min_fours = database.get_band_ratings(user_email="test@example.com", min_rating=4, db_path=self.db_path)
        self.assertEqual(len(min_fours), 3)  # 2 fives + 1 four

        # 5. Rated map
        rated_map = database.get_rated_artists_map(user_email="test@example.com", db_path=self.db_path)
        self.assertIn("jimmy eat world", rated_map)
        self.assertEqual(rated_map["jimmy eat world"], 5)
        self.assertEqual(rated_map["nickelback"], 1)

        # 6. Analyzed artists with ratings
        # Seed an analyzed song in searches
        database.save_search(
            artist="Jimmy Eat World",
            song="Sweetness",
            lyrics="Are you listening?",
            source="Genius",
            db_path=self.db_path
        )
        database.save_analysis(
            artist="Jimmy Eat World",
            song="Sweetness",
            analysis="Deep analysis...",
            db_path=self.db_path
        )
        database.save_search(
            artist="Unrated Band",
            song="Sample Song",
            lyrics="Sample lyrics",
            source="LRCLIB",
            db_path=self.db_path
        )
        database.save_analysis(
            artist="Unrated Band",
            song="Sample Song",
            analysis="Another analysis...",
            db_path=self.db_path
        )

        analyzed_with_ratings = database.get_analyzed_artists_with_ratings(user_email="test@example.com", db_path=self.db_path)
        jew = next((a for a in analyzed_with_ratings if a["artist"] == "Jimmy Eat World"), None)
        unrated = next((a for a in analyzed_with_ratings if a["artist"] == "Unrated Band"), None)

        self.assertIsNotNone(jew)
        self.assertEqual(jew["rating"], 5)
        self.assertTrue(jew["has_rating"])

        self.assertIsNotNone(unrated)
        self.assertIsNone(unrated["rating"])
        self.assertFalse(unrated["has_rating"])

        # 7. Rating statistics
        stats = database.get_band_rating_stats(user_email="test@example.com", db_path=self.db_path)
        self.assertGreater(stats["total_rated"], 0)
        self.assertGreater(stats["total_ranked"], 0)
        self.assertEqual(stats["by_rating"][5], 2)
        self.assertEqual(stats["by_rating"][1], 1)
        self.assertGreater(stats["average_ranked_rating"], 0.0)

        # 8. Delete rating
        deleted = database.delete_band_rating("Nickelback", user_email="test@example.com", db_path=self.db_path)
        self.assertTrue(deleted)
        self.assertIsNone(database.get_band_rating("Nickelback", user_email="test@example.com", db_path=self.db_path))

    def test_prompt_analysis_storage_and_caching(self):
        # 1. Save initial analysis using "Default Analysis"
        database.save_analysis(
            artist="Deftones",
            song="Digital Bath",
            analysis="Default analysis of Digital Bath.",
            model_name="gemini-3.8-flash",
            prompt_name="Default Analysis",
            prompt_text="Analyze lyrics:\n{lyrics_text}",
            lyrics="You move like I want to...",
            db_path=self.db_path
        )

        # Retrieve by exact prompt_name
        cached_default = database.get_analysis("Deftones", "Digital Bath", prompt_name="Default Analysis", db_path=self.db_path)
        self.assertIsNotNone(cached_default)
        self.assertEqual(cached_default["prompt_name"], "Default Analysis")
        self.assertEqual(cached_default["analysis"], "Default analysis of Digital Bath.")
        self.assertEqual(cached_default["model_name"], "gemini-3.8-flash")

        # 2. Save a SECOND analysis using a different prompt "Top 5 Breakdown"
        database.save_analysis(
            artist="Deftones",
            song="Digital Bath",
            analysis="Top 5 Breakdown analysis of Digital Bath.",
            model_name="gemini-3.5-flash-lite",
            prompt_name="Top 5 Breakdown",
            prompt_text="5 characteristic breakdown:\n{lyrics_text}",
            lyrics="You move like I want to...",
            db_path=self.db_path
        )

        # Retrieve "Top 5 Breakdown"
        cached_top5 = database.get_analysis("Deftones", "Digital Bath", prompt_name="Top 5 Breakdown", db_path=self.db_path)
        self.assertIsNotNone(cached_top5)
        self.assertEqual(cached_top5["prompt_name"], "Top 5 Breakdown")
        self.assertEqual(cached_top5["analysis"], "Top 5 Breakdown analysis of Digital Bath.")
        self.assertEqual(cached_top5["model_name"], "gemini-3.5-flash-lite")

        # CRUCIAL: "Default Analysis" MUST STILL BE PRESERVED and not overwritten!
        cached_default_again = database.get_analysis("Deftones", "Digital Bath", prompt_name="Default Analysis", db_path=self.db_path)
        self.assertIsNotNone(cached_default_again)
        self.assertEqual(cached_default_again["analysis"], "Default analysis of Digital Bath.")

        # 3. get_song_analyses retrieves all saved prompt results for this song
        all_analyses = database.get_song_analyses("Deftones", "Digital Bath", db_path=self.db_path)
        self.assertEqual(len(all_analyses), 2)
        prompt_names = {a["prompt_name"] for a in all_analyses}
        self.assertIn("Default Analysis", prompt_names)
        self.assertIn("Top 5 Breakdown", prompt_names)

        # 4. Unknown prompt returns None
        self.assertIsNone(database.get_analysis("Deftones", "Digital Bath", prompt_name="Nonexistent Prompt", db_path=self.db_path))

        # 5. Overwriting/re-analyzing the SAME prompt updates that prompt's results
        database.save_analysis(
            artist="Deftones",
            song="Digital Bath",
            analysis="Updated Default analysis of Digital Bath.",
            model_name="gemini-3.8-flash",
            prompt_name="Default Analysis",
            db_path=self.db_path
        )
        updated_default = database.get_analysis("Deftones", "Digital Bath", prompt_name="Default Analysis", db_path=self.db_path)
        self.assertEqual(updated_default["analysis"], "Updated Default analysis of Digital Bath.")
        # "Top 5 Breakdown" is still intact
        cached_top5_after = database.get_analysis("Deftones", "Digital Bath", prompt_name="Top 5 Breakdown", db_path=self.db_path)
        self.assertEqual(cached_top5_after["analysis"], "Top 5 Breakdown analysis of Digital Bath.")

        # 6. Searches record still reflects active/latest analysis
        search_rec = database.get_search("Deftones", "Digital Bath", db_path=self.db_path)
        self.assertIsNotNone(search_rec)
        self.assertTrue(search_rec["has_analysis"])

        # 7. delete_search removes both search and song_analyses
        database.delete_search(search_rec["id"], db_path=self.db_path)
        self.assertIsNone(database.get_analysis("Deftones", "Digital Bath", prompt_name="Default Analysis", db_path=self.db_path))
        self.assertEqual(len(database.get_song_analyses("Deftones", "Digital Bath", db_path=self.db_path)), 0)


if __name__ == "__main__":
    unittest.main()
