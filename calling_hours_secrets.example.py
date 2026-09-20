# Genius API credentials
# Obtain these from https://genius.com/api-clients
GENIUS_CLIENT_ID = "your_genius_client_id_here"
GENIUS_CLIENT_SECRET = "your_genius_client_secret_here"

# If you already have an access token, you can set it here.
# Otherwise, the app will authenticate via OAuth and save it automatically.
GENIUS_ACCESS_TOKEN = ""

# Google Gemini API key
# Obtain a free key from https://aistudio.google.com/
GEMINI_API_KEY = "your_gemini_api_key_here"

# Google OAuth 2.0 credentials (for Google Sign-In authentication)
# Obtain these from https://console.cloud.google.com/apis/credentials
# Authorized redirect URI: http://127.0.0.1:8000/auth/google/callback (or your Cloud Run domain in production)
GOOGLE_CLIENT_ID = ""
GOOGLE_CLIENT_SECRET = ""
# Optional: explicitly set callback redirect URI (defaults to dynamic detection)
# GOOGLE_REDIRECT_URI = "http://127.0.0.1:8000/auth/google/callback"

# Neon PostgreSQL Database URL (optional, falls back to local SQLite if empty)
# Example: postgresql://neondb_owner:password@ep-xyz-pooler.us-east-1.aws.neon.tech/neondb?sslmode=require
NEON_DATABASE_URL = ""

# Last.fm API Key (for track top tags, artist tags, and top tracks)
# Obtain a free key from https://www.last.fm/api/account/create
LASTFM_API_KEY = ""

# TheAudioDB V1 API Key (for track insights, tempo/BPM, key, mood, audio features, story & media)
# The default free test key is "123" (public test key documented at https://www.theaudiodb.com/free_music_api)
# Can also be set to your premium API key if subscribed
THEAUDIODB_API_KEY = "123"

