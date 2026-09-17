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
