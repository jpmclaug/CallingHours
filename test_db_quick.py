"""Quick smoke test for database layer."""
import database
import tempfile
import os

db = tempfile.mktemp(suffix='.db')
try:
    database.init_db(db)
    database.save_search('Test Artist', 'Test Song', lyrics='Hello world', db_path=db)
    result = database.get_search('Test Artist', 'Test Song', db_path=db)
    assert result is not None, 'get_search failed'
    assert result['artist'] == 'Test Artist', 'Artist mismatch'
    print('PASS: save_search / get_search')

    database.save_search('Artist A', 'Song 1', db_path=db)
    database.save_search('Artist B', 'Song 2', db_path=db)
    bands = database.get_distinct_bands(db_path=db)
    assert len(bands) >= 2, 'Expected at least 2 bands'
    print(f'PASS: get_distinct_bands -> {bands}')

    songs = database.get_songs_by_band('Test Artist', db_path=db)
    song_names = [s['song'] for s in songs]
    print(f'PASS: get_songs_by_band -> {song_names}')

    # Test analysis save
    database.save_analysis('Test Artist', 'Test Song', 'Mock analysis text', 'gemini-3.8-flash', 'Default', db_path=db)
    updated = database.get_search_by_id(result['id'], db_path=db)
    assert updated['analysis'] == 'Mock analysis text', 'Analysis not saved'
    print('PASS: save_analysis / get_search_by_id')

    analyzed = database.get_analyzed_songs(db_path=db)
    assert any(s['artist'] == 'Test Artist' for s in analyzed), 'Analyzed songs missing'
    print('PASS: get_analyzed_songs')

    print('\nAll database smoke tests PASSED.')
except Exception as e:
    import traceback
    print(f'FAIL: {e}')
    traceback.print_exc()
finally:
    if os.path.exists(db):
        os.unlink(db)
