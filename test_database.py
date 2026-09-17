import os
import tempfile
import unittest
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

if __name__ == "__main__":
    unittest.main()
