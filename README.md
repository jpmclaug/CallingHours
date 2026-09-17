# Calling Hours

Calling Hours is a web application that fetches song lyrics via the Genius API and provides deep thematic and poetic analysis using Google Gemini models.

## Features

- **Genius Lyric Search & Scraping**: Search for songs by artist and title, and fetch lyrics with automatic Genius OAuth flow.
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

### 1. Direct Source Deploy with gcloud

You can build and deploy the application in a single command using Google Cloud Build (no local Docker daemon required):

```bash
gcloud run deploy callinghours \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars GEMINI_API_KEY="your_gemini_api_key",GENIUS_CLIENT_ID="your_genius_client_id",GENIUS_CLIENT_SECRET="your_genius_client_secret"
```

### 2. Environment Variables

| Variable | Description | Default |
|---|---|---|
| `PORT` | Container listen port (injected automatically by Cloud Run) | `8080` (or `8000` locally) |
| `HOST` | Bind address | `0.0.0.0` in Cloud Run (`127.0.0.1` locally) |
| `GEMINI_API_KEY` | Google Gemini API Key | Falling back to `calling_hours_secrets.py` |
| `GENIUS_CLIENT_ID` | Genius Application Client ID | Falling back to `calling_hours_secrets.py` |
| `GENIUS_CLIENT_SECRET` | Genius Application Client Secret | Falling back to `calling_hours_secrets.py` |
| `GENIUS_ACCESS_TOKEN` | Pre-generated Genius Bearer Token (optional) | Falling back to `calling_hours_secrets.py` |
| `GENIUS_REDIRECT_URI` | Explicit Genius OAuth callback URL (e.g. `https://your-service.run.app/callback`) | Dynamically resolved from host header |
| `NO_BROWSER` | Set to `1` to disable browser auto-launch | `1` on Cloud Run (`0` locally) |
| `PROMPTS_FILE_PATH` | Path to prompt templates JSON | `/app/prompts.json` |

### 3. Configuring Genius OAuth on Cloud Run

When your service is deployed, Google Cloud Run will assign a URL like `https://callinghours-<hash>-uc.a.run.app`.
1. Go to your [Genius API Clients](https://genius.com/api-clients) dashboard.
2. Add `https://<your-cloud-run-url>/callback` to the **Redirect URI** field.
3. Save the changes. You can now authenticate Genius directly from your Cloud Run deployment.

