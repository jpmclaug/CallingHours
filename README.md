# Calling Hours

Calling Hours is a web application that fetches song lyrics via the Genius API and provides deep thematic and poetic analysis using Google Gemini models.

## Features

- **Genius & Open Lyrics Search**: Search for songs by artist and title via Genius with seamless automatic fallback to the **LRCLIB** open database (resilient to Cloudflare 403 blocks on cloud hosts like Cloud Run).
- **Neon Serverless PostgreSQL Persistence**: Automatically saves searches, retrieved lyrics, and Gemini analyses in **Neon PostgreSQL** so search history persists permanently across devices, sessions, and cloud restarts (with graceful fallback to local SQLite when offline). Select from previously searched bands via dropdown or autocomplete, browse past tracks for any band, and instantly load saved results without network latency.
- **Search History Dashboard**: Dedicated `/history` view to browse, filter, quick-load, and manage previously searched songs and analyses.
- **Editable & Custom Lyrics**: An editable lyrics workspace allowing users to review, edit, or paste lyrics manually if needed.
- **Gemini-Powered Analysis**: Deep lyric analysis analyzing themes, narrative, emotional tone, and poetic devices.
- **Newest Gemini Free-Tier Models**:
  - `gemini-3.8-flash`: Default flagship Flash model for high-intelligence, multimodal analysis on the free tier.
  - `gemini-3.5-flash-lite`: Ultra-fast, lightweight model optimized for speed.
- **Custom Prompts**: Manage and customize your own lyric analysis prompt templates.

## Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/jpmclaug/CallingHours.git
   cd CallingHours
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure API Keys**:
   Copy `calling_hours_secrets.example.py` to `calling_hours_secrets.py`:
   ```bash
   cp calling_hours_secrets.example.py calling_hours_secrets.py
   ```
   Add your Genius client ID & secret (from [Genius API Clients](https://genius.com/api-clients)) and your Gemini API key (from [Google AI Studio](https://aistudio.google.com/)).

4. **Run the application**:
   ```bash
   python calling_hours.py
   ```
   The browser will open automatically at `http://127.0.0.1:8000`.

## Deploying to Google Cloud Run

Calling Hours includes a production-ready `Dockerfile` and `.dockerignore` for deploying directly to Google Cloud Run.

> **Note**: `calling_hours_secrets.py` is intentionally excluded by `.dockerignore` to prevent leaking API keys into container images. In production, credentials must be supplied via **Environment Variables** (or Secret Manager).

### 1. Direct Source Deploy with gcloud (Recommended)

The simplest and most reliable deployment uses your **Genius Client Access Token** (`GENIUS_ACCESS_TOKEN`), which allows Genius searches immediately without needing OAuth redirect setup:

```bash
gcloud run deploy callinghours \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars GEMINI_API_KEY="your_gemini_api_key",GENIUS_ACCESS_TOKEN="your_genius_access_token"
```

> **Where to get `GENIUS_ACCESS_TOKEN`**: On your [Genius API Clients](https://genius.com/api-clients) dashboard, click **"Generate Access Token"**.

### 2. Updating an Existing Deployment

If your service is already deployed on Cloud Run, you can set the environment variables directly without rebuilding:

```bash
gcloud run services update callinghours \
  --region us-central1 \
  --set-env-vars GEMINI_API_KEY="your_gemini_api_key",GENIUS_ACCESS_TOKEN="your_genius_access_token"
```

Or via Google Cloud Console:
1. Go to **Cloud Run** &rarr; click your **`callinghours`** service.
2. Click **Edit & Deploy New Revision**.
3. Under the **Variables & Secrets** tab, add `GEMINI_API_KEY` and `GENIUS_ACCESS_TOKEN`.
4. Click **Deploy**.

### 3. Environment Variables

| Variable | Description | Recommended for Prod |
|---|---|---|
| `GENIUS_ACCESS_TOKEN` | Genius Client Access Token (from Genius dashboard) | **Yes** (avoids OAuth flow) |
| `GEMINI_API_KEY` | Google Gemini API Key (from AI Studio) | **Yes** |
| `GENIUS_CLIENT_ID` | Genius Client ID (only needed if using OAuth) | Optional |
| `GENIUS_CLIENT_SECRET` | Genius Client Secret (only needed if using OAuth) | Optional |
| `PORT` | Container listen port (injected by Cloud Run) | `8080` |
| `HOST` | Bind address | `0.0.0.0` in Cloud Run |
| `GENIUS_REDIRECT_URI` | Explicit Genius OAuth callback URL (e.g. `https://your-service.run.app/callback`) | Dynamically resolved |
| `NO_BROWSER` | Set to `1` to disable browser auto-launch | Automatically `1` on Cloud Run |
| `DATABASE_URL` | Neon PostgreSQL connection URI (`postgresql://neondb_owner:pass@ep-xyz.../neondb?sslmode=require`) | **Yes** (enables cloud persistence) |
| `NEON_DATABASE_URL` | Alias for `DATABASE_URL` | Optional |
| `PROMPTS_FILE_PATH` | Path to prompt templates JSON | `/app/prompts.json` |
| `DATABASE_PATH` | Path to fallback SQLite database file | `/app/calling_hours.db` |


### 4. Alternative: Using Genius OAuth Flow on Cloud Run

If you prefer using the full OAuth authorization redirect flow instead of a static access token:
1. Deploy with `GENIUS_CLIENT_ID` and `GENIUS_CLIENT_SECRET`:
   ```bash
   gcloud run deploy callinghours \
     --source . \
     --region us-central1 \
     --allow-unauthenticated \
     --set-env-vars GEMINI_API_KEY="your_gemini_api_key",GENIUS_CLIENT_ID="your_genius_client_id",GENIUS_CLIENT_SECRET="your_genius_client_secret"
   ```
2. Go to your [Genius API Clients](https://genius.com/api-clients) dashboard.
3. Add `https://<your-cloud-run-url>/callback` to the **Redirect URI** field and save.
4. Open the service URL and click **Authorize Genius**.

