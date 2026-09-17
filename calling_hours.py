import http.server
import socketserver
import os
import sys
import urllib.parse
import webbrowser
import threading
import re
import html
import json

import requests
from google import genai

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

PROMPTS_FILE = os.environ.get('PROMPTS_FILE_PATH') or os.path.join(SCRIPT_DIR, 'prompts.json')

def load_prompts():
    if os.path.exists(PROMPTS_FILE):
        try:
            with open(PROMPTS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return [{"name": "Default Analysis", "text": "Analyze the following lyrics for the song '{song}' by '{artist}'.\n\nLyrics:\n{lyrics_text}\n\nProvide an in-depth analysis of the themes, meaning, and poetic devices. Format your response with clear Markdown headings (e.g. ## Core Themes, ## Meaning & Interpretation, ## Poetic Devices) and bullet points so it is structured and easy to read."}]

def save_prompt(name, text):
    prompts = load_prompts()
    prompts.append({"name": name, "text": text})
    try:
        with open(PROMPTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(prompts, f, indent=4)
    except (OSError, IOError) as e:
        print(f"Warning: Could not save prompt to file ({e}).")

AVAILABLE_GEMINI_MODELS = [
    {
        "id": "gemini-3.8-flash",
        "name": "Gemini 3.8 Flash (Recommended - Most Intelligent)",
    },
    {
        "id": "gemini-3.5-flash-lite",
        "name": "Gemini 3.5 Flash-Lite (Fastest)",
    }
]
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"

# Load local secrets file if present (for local development)
_local_secrets = {}
try:
    import calling_hours_secrets
    for attr in ('GENIUS_CLIENT_ID', 'GENIUS_CLIENT_SECRET', 'GENIUS_ACCESS_TOKEN', 'GEMINI_API_KEY'):
        if hasattr(calling_hours_secrets, attr):
            _local_secrets[attr] = getattr(calling_hours_secrets, attr)
except ImportError:
    pass

# Environment variables take precedence (standard for Cloud Run/Docker), falling back to local secrets
GENIUS_CLIENT_ID = os.environ.get('GENIUS_CLIENT_ID') or _local_secrets.get('GENIUS_CLIENT_ID')
GENIUS_CLIENT_SECRET = os.environ.get('GENIUS_CLIENT_SECRET') or _local_secrets.get('GENIUS_CLIENT_SECRET')
GENIUS_ACCESS_TOKEN = os.environ.get('GENIUS_ACCESS_TOKEN') or _local_secrets.get('GENIUS_ACCESS_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY') or _local_secrets.get('GEMINI_API_KEY')

ACCESS_TOKEN = GENIUS_ACCESS_TOKEN
SERVER_PORT = None

GENIUS_AUTH_URL = 'https://api.genius.com/oauth/authorize'
GENIUS_TOKEN_URL = 'https://api.genius.com/oauth/token'
GENIUS_API_SEARCH_URL = 'https://api.genius.com/search'
GENIUS_WEB_SEARCH_URL = 'https://genius.com/api/search/multi'

# Cloud Run injects K_SERVICE and PORT (default 8080)
IS_CLOUD_RUN = bool(os.environ.get('K_SERVICE'))
PORT = int(os.environ.get('PORT', 8080 if IS_CLOUD_RUN else 8000))
HOST = os.environ.get('HOST', '0.0.0.0' if IS_CLOUD_RUN else '127.0.0.1')
OAUTH_PORT = PORT

def get_genius_missing_message(action='authorize') -> str:
    if IS_CLOUD_RUN or not os.path.exists(os.path.join(SCRIPT_DIR, 'calling_hours_secrets.py')):
        return (
            '<div class="message">Genius credentials not found. '
            'In production, configure <code>GENIUS_ACCESS_TOKEN</code> (recommended) '
            'or <code>GENIUS_CLIENT_ID</code> and <code>GENIUS_CLIENT_SECRET</code> as environment variables.</div>'
        )
    verb = 'before authorizing' if action == 'authorizing' else 'and reload to authorize Genius'
    return f'<div class="message">Set your Genius credentials in calling_hours_secrets.py {verb}.</div>'

def get_gemini_missing_message() -> str:
    if IS_CLOUD_RUN or not os.path.exists(os.path.join(SCRIPT_DIR, 'calling_hours_secrets.py')):
        return (
            '<div class="message">Gemini API key not found. '
            'In production, configure <code>GEMINI_API_KEY</code> as an environment variable.</div>'
        )
    return '<div class="message">Set your GEMINI_API_KEY in calling_hours_secrets.py and reload to use analysis.</div>'

PAGE_HTML = r'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Calling Hours</title>
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@700;800;900&family=Roboto+Condensed:wght@300;400;700&display=swap');

        body {
            margin: 0;
            min-height: 100vh;
            display: flex;
            align-items: flex-start;
            justify-content: center;
            font-family: 'Roboto Condensed', sans-serif;
            background: linear-gradient(to bottom, #050A14 0%, #0B1E3F 100%);
            color: #E1E8F0;
            position: relative;
            overflow-x: hidden;
            padding: 40px 0;
            box-sizing: border-box;
        }

        /* Starfield background */
        .stars {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background-image: 
                radial-gradient(1px 1px at 20px 30px, #ffffff, rgba(0,0,0,0)),
                radial-gradient(1px 1px at 40px 70px, #ffffff, rgba(0,0,0,0)),
                radial-gradient(1px 1px at 50px 160px, #ffffff, rgba(0,0,0,0)),
                radial-gradient(1.5px 1.5px at 90px 40px, #ffffff, rgba(0,0,0,0)),
                radial-gradient(2px 2px at 130px 80px, #A5C8FF, rgba(0,0,0,0)),
                radial-gradient(1px 1px at 160px 120px, #ffffff, rgba(0,0,0,0)),
                radial-gradient(1.5px 1.5px at 250px 50px, #A5C8FF, rgba(0,0,0,0)),
                radial-gradient(1px 1px at 300px 90px, #ffffff, rgba(0,0,0,0));
            background-repeat: repeat;
            background-size: 350px 250px;
            opacity: 0.65;
            z-index: 0;
        }

        /* Suburban horizon silhouette */
        .horizon {
            position: fixed;
            bottom: 0;
            left: 0;
            width: 100%;
            height: 20vh;
            background: url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 100" preserveAspectRatio="none"><path d="M0,100 L0,70 L20,70 L30,40 L60,40 L70,70 L100,70 L110,50 L140,50 L150,70 L180,70 L180,60 L200,60 L200,70 L250,70 L280,30 L320,30 L350,70 L400,70 L400,50 L450,50 L450,70 L500,70 L520,40 L560,40 L580,70 L650,70 L650,20 L680,20 L680,70 L750,70 L770,50 L800,50 L820,70 L880,70 L900,30 L940,30 L960,70 L1000,70 L1000,100 Z" fill="%2302040A"/></svg>') repeat-x bottom;
            background-size: auto 100%;
            z-index: 1;
            pointer-events: none;
        }

        .container {
            position: relative;
            z-index: 2;
            width: min(1560px, 96vw);
            display: flex;
            flex-direction: row;
            gap: 32px;
            padding: 36px;
            border-radius: 16px;
            background: rgba(5, 10, 20, 0.5);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid rgba(165, 200, 255, 0.12);
            box-shadow: 0 30px 60px rgba(0, 0, 0, 0.7), inset 0 1px 0 rgba(255, 255, 255, 0.05);
        }

        .form-section {
            flex: 0.9;
            min-width: 250px;
        }

        .lyrics-section {
            flex: 1.4;
            display: flex;
            flex-direction: column;
            min-width: 320px;
        }
        
        .analysis-section {
            flex: 1.7;
            display: flex;
            flex-direction: column;
            min-width: 340px;
        }

        h1 {
            margin-top: 0;
            font-family: 'Montserrat', sans-serif;
            font-size: 2.3rem;
            text-align: center;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            font-weight: 800;
            text-shadow: 0 4px 15px rgba(0,0,0,0.8);
            color: #E1E8F0;
        }

        p {
            line-height: 1.6;
            color: #A5C8FF;
            text-align: center;
            font-size: 1.05rem;
            font-weight: 300;
            margin-bottom: 24px;
        }

        label {
            display: block;
            margin: 20px 0 8px;
            font-weight: 400;
            color: #A5C8FF;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            font-size: 0.85rem;
        }

        input[type="text"], textarea, select {
            width: 100%;
            padding: 14px 16px;
            border: 1px solid rgba(165, 200, 255, 0.2);
            border-radius: 8px;
            background: rgba(11, 30, 63, 0.5);
            color: #E1E8F0;
            font-size: 1.05rem;
            font-family: inherit;
            box-sizing: border-box;
            transition: all 0.3s ease;
        }

        input[type="text"]:focus, textarea:focus, select:focus {
            outline: none;
            border-color: #A5C8FF;
            background: rgba(11, 30, 63, 0.8);
            box-shadow: 0 0 15px rgba(165, 200, 255, 0.25);
        }

        input[type="text"]::placeholder, textarea::placeholder {
            color: rgba(225, 232, 240, 0.3);
        }
        
        select {
            appearance: none;
            cursor: pointer;
        }
        
        select option {
            background: #0B1E3F;
            color: #E1E8F0;
        }

        button {
            width: 100%;
            margin-top: 32px;
            padding: 16px;
            border: none;
            border-radius: 8px;
            background: #A5C8FF;
            color: #050A14;
            font-family: 'Montserrat', sans-serif;
            font-size: 1.05rem;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            cursor: pointer;
            transition: all 0.3s ease;
            box-shadow: 0 0 25px rgba(165, 200, 255, 0.35);
        }

        button:hover {
            background: #C4DFFF;
            transform: translateY(-2px);
            box-shadow: 0 0 35px rgba(165, 200, 255, 0.5);
        }

        .message {
            margin-top: 24px;
            padding: 16px;
            border-radius: 8px;
            background: rgba(165, 200, 255, 0.08);
            border: 1px solid rgba(165, 200, 255, 0.25);
            color: #E1E8F0;
            white-space: pre-wrap;
            font-weight: 300;
            text-align: center;
        }

        .message a {
            color: #A5C8FF;
            font-weight: 400;
            text-decoration: none;
            border-bottom: 1px dotted #A5C8FF;
            transition: color 0.3s;
        }

        .message a:hover {
            color: #ffffff;
            border-bottom-color: #ffffff;
        }

        /* Section Header Bars & Controls */
        .section-header-bar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin: 20px 0 10px;
            min-height: 36px;
            gap: 8px;
            flex-wrap: wrap;
        }

        .section-title {
            font-weight: 700;
            color: #A5C8FF;
            letter-spacing: 0.06em;
            text-transform: uppercase;
            font-size: 0.85rem;
            margin: 0;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .view-controls {
            display: flex;
            align-items: center;
            gap: 6px;
            flex-wrap: wrap;
        }

        .pill-btn {
            background: rgba(165, 200, 255, 0.08);
            color: #A5C8FF;
            border: 1px solid rgba(165, 200, 255, 0.22);
            padding: 6px 14px;
            border-radius: 20px;
            font-size: 0.78rem;
            font-family: inherit;
            cursor: pointer;
            transition: all 0.2s ease;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            font-weight: 700;
            margin: 0;
            box-shadow: none;
            width: auto;
            line-height: 1.2;
        }

        .pill-btn:hover {
            background: rgba(165, 200, 255, 0.22);
            color: #FFFFFF;
            transform: none;
            box-shadow: 0 0 10px rgba(165, 200, 255, 0.2);
        }

        .pill-btn.active {
            background: #A5C8FF;
            color: #050A14;
            border-color: #A5C8FF;
            box-shadow: 0 0 12px rgba(165, 200, 255, 0.35);
        }

        .pill-btn.secondary {
            font-weight: 400;
            opacity: 0.85;
        }

        .pill-btn.secondary:hover {
            opacity: 1;
        }

        /* Lyrics Styling */
        .lyrics-box {
            flex-grow: 1;
            width: 100%;
            min-height: 480px;
            max-height: 72vh;
            overflow-y: auto;
            padding: 22px;
            border-radius: 8px;
            border: 1px solid rgba(165, 200, 255, 0.2);
            background: rgba(5, 10, 20, 0.65);
            color: #E1E8F0;
            font-family: inherit;
            font-size: 1.02rem;
            font-weight: 300;
            line-height: 1.7;
            white-space: pre-wrap;
            box-sizing: border-box;
            resize: vertical;
        }

        .lyrics-box:focus {
            outline: none;
            border-color: #A5C8FF;
            box-shadow: 0 0 12px rgba(165, 200, 255, 0.25);
        }

        .lyrics-reader-box {
            flex-grow: 1;
            width: 100%;
            min-height: 480px;
            max-height: 72vh;
            overflow-y: auto;
            padding: 22px;
            border-radius: 8px;
            border: 1px solid rgba(165, 200, 255, 0.2);
            background: rgba(5, 10, 20, 0.65);
            box-sizing: border-box;
            display: flex;
            flex-direction: column;
            gap: 14px;
        }

        .lyric-stanza {
            background: rgba(11, 30, 63, 0.35);
            border: 1px solid rgba(165, 200, 255, 0.09);
            border-radius: 8px;
            padding: 14px 18px;
            transition: all 0.2s ease;
        }

        .lyric-stanza:hover {
            background: rgba(11, 30, 63, 0.6);
            border-color: rgba(165, 200, 255, 0.25);
        }

        .lyric-line {
            font-size: 1.05rem;
            line-height: 1.75;
            color: #E1E8F0;
            font-weight: 300;
            letter-spacing: 0.01em;
        }

        .lyric-section-badge {
            display: inline-flex;
            align-items: center;
            align-self: flex-start;
            padding: 4px 12px;
            margin: 10px 0 2px 0;
            border-radius: 14px;
            background: rgba(165, 200, 255, 0.14);
            border: 1px solid rgba(165, 200, 255, 0.35);
            color: #C4DFFF;
            font-family: 'Montserrat', sans-serif;
            font-size: 0.74rem;
            font-weight: 800;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            box-shadow: 0 0 10px rgba(165, 200, 255, 0.12);
        }

        .backing-vocal {
            color: #A5C8FF;
            font-style: italic;
            opacity: 0.85;
        }

        /* Analysis Box & Cards */
        #analysis-result-wrapper {
            flex-grow: 1;
            min-height: 480px;
            max-height: 72vh;
            overflow-y: auto;
            padding-right: 4px;
        }

        .analysis-container {
            display: flex;
            flex-direction: column;
            gap: 14px;
        }

        .analysis-card {
            background: rgba(11, 30, 63, 0.42);
            border: 1px solid rgba(165, 200, 255, 0.18);
            border-radius: 10px;
            padding: 18px 22px;
            box-sizing: border-box;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.35);
            transition: all 0.25s ease;
        }

        .analysis-card:hover {
            border-color: rgba(165, 200, 255, 0.35);
            box-shadow: 0 6px 24px rgba(0, 0, 0, 0.5), 0 0 16px rgba(165, 200, 255, 0.08);
        }

        .analysis-card h1, .analysis-card h2, .analysis-card h3, .analysis-card h4 {
            font-family: 'Montserrat', sans-serif;
            margin-top: 0;
            margin-bottom: 12px;
            color: #FFFFFF;
            font-weight: 800;
            letter-spacing: 0.03em;
            text-align: left;
            text-transform: none;
            text-shadow: none;
        }

        .analysis-card h1 {
            font-size: 1.35rem;
            color: #A5C8FF;
            border-bottom: 1px solid rgba(165, 200, 255, 0.2);
            padding-bottom: 8px;
        }

        .analysis-card h2 {
            font-size: 1.18rem;
            color: #C4DFFF;
            border-bottom: 1px solid rgba(165, 200, 255, 0.16);
            padding-bottom: 6px;
        }

        .analysis-card h3 {
            font-size: 1.05rem;
            color: #A5C8FF;
        }

        .analysis-card p {
            font-size: 1.02rem;
            line-height: 1.7;
            color: #E1E8F0;
            font-weight: 300;
            text-align: left;
            margin: 0 0 10px 0;
        }

        .analysis-card p:last-child {
            margin-bottom: 0;
        }

        .analysis-card strong, .analysis-card b {
            color: #FFFFFF;
            font-weight: 700;
        }

        .analysis-card ul, .analysis-card ol {
            margin: 8px 0 12px 20px;
            padding: 0;
            color: #E1E8F0;
            font-size: 1rem;
            line-height: 1.65;
            font-weight: 300;
        }

        .analysis-card li {
            margin-bottom: 6px;
        }

        .analysis-card li::marker {
            color: #A5C8FF;
        }

        .analysis-card blockquote {
            margin: 10px 0;
            padding: 10px 16px;
            border-left: 3px solid #A5C8FF;
            background: rgba(165, 200, 255, 0.06);
            border-radius: 0 8px 8px 0;
            font-style: italic;
            color: #C4DFFF;
        }

        .analysis-card code {
            background: rgba(165, 200, 255, 0.12);
            padding: 2px 6px;
            border-radius: 4px;
            font-family: monospace;
            font-size: 0.9em;
            color: #A5C8FF;
        }

        .analysis-raw-box {
            white-space: pre-wrap;
            font-family: inherit;
            font-size: 0.95rem;
            line-height: 1.65;
            background: rgba(5, 10, 20, 0.65);
            border: 1px solid rgba(165, 200, 255, 0.2);
            border-radius: 8px;
            padding: 20px;
            color: #E1E8F0;
            max-height: 72vh;
            overflow-y: auto;
            box-sizing: border-box;
            margin: 0;
        }

        .analysis-form {
            margin-top: 16px;
        }

        /* Scrollbars */
        .lyrics-box::-webkit-scrollbar, .lyrics-reader-box::-webkit-scrollbar, #analysis-result-wrapper::-webkit-scrollbar, .analysis-raw-box::-webkit-scrollbar {
            width: 8px;
        }
        
        .lyrics-box::-webkit-scrollbar-track, .lyrics-reader-box::-webkit-scrollbar-track, #analysis-result-wrapper::-webkit-scrollbar-track, .analysis-raw-box::-webkit-scrollbar-track {
            background: rgba(11, 30, 63, 0.5);
            border-radius: 8px;
        }
        
        .lyrics-box::-webkit-scrollbar-thumb, .lyrics-reader-box::-webkit-scrollbar-thumb, #analysis-result-wrapper::-webkit-scrollbar-thumb, .analysis-raw-box::-webkit-scrollbar-thumb {
            background: rgba(165, 200, 255, 0.3);
            border-radius: 8px;
        }
        
        .lyrics-box::-webkit-scrollbar-thumb:hover, .lyrics-reader-box::-webkit-scrollbar-thumb:hover, #analysis-result-wrapper::-webkit-scrollbar-thumb:hover, .analysis-raw-box::-webkit-scrollbar-thumb:hover {
            background: rgba(165, 200, 255, 0.5);
        }

        /* Responsive & Mobile Enhancements */
        @media (max-width: 1080px) {
            .container {
                flex-direction: column;
                gap: 28px;
                width: min(1560px, 94vw);
            }

            .form-section, .lyrics-section, .analysis-section {
                min-width: 0;
                width: 100%;
            }
        }

        @media (max-width: 768px) {
            body {
                padding: 12px 8px 36px;
            }

            .container {
                width: 100%;
                padding: 20px 14px;
                gap: 20px;
                border-radius: 12px;
            }

            .horizon {
                height: 10vh;
            }

            h1 {
                font-size: 1.7rem;
                letter-spacing: 0.08em;
                margin-bottom: 4px;
            }

            p {
                font-size: 0.95rem;
                margin-bottom: 18px;
            }

            label {
                margin: 14px 0 6px;
                font-size: 0.8rem;
            }

            input[type="text"], textarea, select {
                font-size: 16px; /* Prevents automatic iOS Safari zoom */
                padding: 12px 14px;
                border-radius: 6px;
            }

            button {
                margin-top: 20px;
                padding: 14px;
                font-size: 0.95rem;
                touch-action: manipulation;
            }

            .section-header-bar {
                margin: 14px 0 8px;
                gap: 8px;
            }

            .section-title {
                font-size: 0.82rem;
            }

            .view-controls {
                gap: 6px;
                width: 100%;
                justify-content: flex-start;
            }

            .pill-btn {
                padding: 7px 12px;
                font-size: 0.74rem;
                min-height: 36px;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                white-space: nowrap;
                touch-action: manipulation;
            }

            .lyrics-box, .lyrics-reader-box {
                min-height: 280px;
                max-height: 52vh;
                padding: 16px;
                -webkit-overflow-scrolling: touch;
            }

            .lyric-stanza {
                padding: 12px 14px;
                margin-bottom: 10px;
            }

            .lyric-line {
                font-size: 0.98rem;
                line-height: 1.65;
            }

            .lyric-section-badge {
                font-size: 0.7rem;
                padding: 3px 10px;
                margin: 8px 0 2px 0;
            }

            #analysis-result-wrapper {
                min-height: 280px;
                max-height: 55vh;
                -webkit-overflow-scrolling: touch;
            }

            .analysis-card {
                padding: 14px 16px;
                border-radius: 8px;
                margin-bottom: 10px;
            }

            .analysis-card h1 {
                font-size: 1.18rem;
                padding-bottom: 6px;
            }

            .analysis-card h2 {
                font-size: 1.08rem;
                padding-bottom: 4px;
            }

            .analysis-card h3 {
                font-size: 0.98rem;
            }

            .analysis-card p {
                font-size: 0.95rem;
                line-height: 1.6;
            }

            .analysis-card ul, .analysis-card ol {
                margin: 6px 0 10px 16px;
                font-size: 0.94rem;
            }

            .analysis-card li {
                margin-bottom: 5px;
            }

            .analysis-raw-box {
                padding: 14px;
                font-size: 0.88rem;
                max-height: 52vh;
                -webkit-overflow-scrolling: touch;
            }

            .message {
                margin-top: 16px;
                padding: 12px;
                font-size: 0.92rem;
                word-break: break-word;
            }
        }

        @media (max-width: 480px) {
            body {
                padding: 8px 4px 28px;
            }

            .container {
                padding: 16px 12px;
                border-radius: 10px;
            }

            h1 {
                font-size: 1.5rem;
                letter-spacing: 0.05em;
            }

            .pill-btn {
                padding: 6px 10px;
                font-size: 0.72rem;
            }
        }
    </style>
</head>
<body>
    <div class="stars"></div>
    <div class="horizon"></div>
    <div class="container">
        <div class="form-section">
            <h1>Calling Hours</h1>
            <p>Enter an artist and a song title, then submit to post the details.</p>

            <form method="post" action="/submit">
                <label for="artist">Artist Name</label>
                <input type="text" id="artist" name="artist" placeholder="e.g. Adele" value="{artist_value}" required>

                <label for="song">Song Title</label>
                <input type="text" id="song" name="song" placeholder="e.g. Hello" value="{song_value}" required>

                <button type="submit">Post Song</button>
            </form>

            {message_block}
            
            <div style="text-align: center; margin-top: 30px;">
                <a href="/prompts" style="color: #A5C8FF; text-decoration: none; border-bottom: 1px dotted #A5C8FF;">Manage Prompts</a>
            </div>
        </div>

        <div class="lyrics-section" style="{lyrics_display}">
            <div class="section-header-bar">
                <span class="section-title">Lyrics</span>
                <div class="view-controls">
                    <button type="button" id="btn-lyrics-reader" class="pill-btn active" onclick="setLyricsMode('reader')">Reader View</button>
                    <button type="button" id="btn-lyrics-edit" class="pill-btn" onclick="setLyricsMode('edit')">Edit Lyrics</button>
                    <button type="button" id="btn-lyrics-format" class="pill-btn secondary" title="Add spacing between stanzas" onclick="autoFormatStanzas()">Auto-Space</button>
                </div>
            </div>
            <div id="lyrics-reader" class="lyrics-reader-box"></div>
            <textarea id="lyrics" class="lyrics-box" placeholder="Paste or edit lyrics here..." style="display: none;" oninput="updateLyricsReader()">{lyrics_text}</textarea>
        </div>
        
        <div class="analysis-section" style="{analysis_display}">
            <div class="section-header-bar">
                <span class="section-title">Analysis</span>
                <div class="view-controls" id="analysis-controls" style="{analysis_controls_display}">
                    <button type="button" id="btn-copy-analysis" class="pill-btn secondary" onclick="copyAnalysis()">📋 Copy</button>
                    <button type="button" id="btn-raw-analysis" class="pill-btn secondary" onclick="toggleRawAnalysis()">Raw View</button>
                    <button type="button" class="pill-btn secondary" onclick="showAnalysisForm()">Re-Analyze</button>
                </div>
            </div>
            
            <div class="analysis-form" style="{analysis_form_display}">
                <form id="analyze-form" method="post" action="/analyze" onsubmit="document.getElementById('form_lyrics').value = document.getElementById('lyrics').value;">
                    <input type="hidden" name="artist" value="{artist_value}">
                    <input type="hidden" name="song" value="{song_value}">
                    <input type="hidden" id="form_lyrics" name="lyrics" value="{lyrics_text_attr}">
                    
                    <label for="model_name">Select Gemini Model</label>
                    <select name="model_name" id="model_name">
                        {model_options}
                    </select>

                    <label for="prompt_idx">Select Prompt</label>
                    <select name="prompt_idx" id="prompt_idx">
                        {prompt_options}
                    </select>
                    
                    <button type="submit" style="margin-top: 16px;">Perform Analysis</button>
                </form>
            </div>
            
            <div id="analysis-result-wrapper" style="{analysis_result_display}">
                <div id="analysis-formatted" class="analysis-container"></div>
                <pre id="analysis-raw" class="analysis-raw-box" style="display: none;">{analysis_result}</pre>
                
                <div style="margin-top: 20px; text-align: center;">
                    <button type="button" onclick="showAnalysisForm()" style="background: transparent; border: 1px solid rgba(165, 200, 255, 0.4); color: #A5C8FF; padding: 10px 18px; font-size: 0.9rem; cursor: pointer; border-radius: 6px; font-family: inherit; width: auto;">Perform Another Analysis</button>
                </div>
            </div>
        </div>
    </div>

    <script>
        function escapeHtml(str) {
            return String(str)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#039;');
        }

        // Lyrics Reader Logic
        function renderLyricsHtml(text) {
            if (!text || !text.trim()) {
                return '<div style="color: rgba(225,232,240,0.5); font-style: italic; text-align: center; padding: 40px;">No lyrics available. Paste or edit lyrics in Edit mode.</div>';
            }
            const rawLines = text.replace(/\r\n/g, '\n').split('\n');
            const hasBlankLines = rawLines.some(l => l.trim() === '');
            const hasSectionTags = rawLines.some(l => /^\[.*?\]$/.test(l.trim()));
            
            let html = '';
            let currentStanzaLines = [];
            let linesInStanzaCount = 0;
            
            function flushStanza() {
                if (currentStanzaLines.length > 0) {
                    html += '<div class="lyric-stanza">';
                    for (const line of currentStanzaLines) {
                        html += `<div class="lyric-line">${line}</div>`;
                    }
                    html += '</div>';
                    currentStanzaLines = [];
                    linesInStanzaCount = 0;
                }
            }
            
            for (let i = 0; i < rawLines.length; i++) {
                const trimmed = rawLines[i].trim();
                
                // Section tags like [Verse 1], [Chorus]
                const sectionMatch = trimmed.match(/^\[(.*?)\]$/);
                if (sectionMatch) {
                    flushStanza();
                    const tag = escapeHtml(sectionMatch[1]);
                    html += `<div class="lyric-section-badge"><span>${tag}</span></div>`;
                    continue;
                }
                
                if (trimmed === '') {
                    flushStanza();
                    continue;
                }
                
                // If lyrics lack both blank lines and section tags, auto-group into 4 lines for readability
                if (!hasBlankLines && !hasSectionTags && linesInStanzaCount >= 4) {
                    flushStanza();
                }
                
                let formattedLine = escapeHtml(trimmed);
                // Backing vocals / parenthetical styling: (Yeah, yeah)
                formattedLine = formattedLine.replace(/\(([^)]+)\)/g, '<span class="backing-vocal">($1)</span>');
                currentStanzaLines.push(formattedLine);
                linesInStanzaCount++;
            }
            flushStanza();
            return html;
        }

        function updateLyricsReader() {
            const textarea = document.getElementById('lyrics');
            const reader = document.getElementById('lyrics-reader');
            if (textarea && reader) {
                reader.innerHTML = renderLyricsHtml(textarea.value);
            }
        }

        function setLyricsMode(mode) {
            const reader = document.getElementById('lyrics-reader');
            const textarea = document.getElementById('lyrics');
            const btnReader = document.getElementById('btn-lyrics-reader');
            const btnEdit = document.getElementById('btn-lyrics-edit');
            
            if (mode === 'reader') {
                updateLyricsReader();
                if (reader) reader.style.display = 'flex';
                if (textarea) textarea.style.display = 'none';
                if (btnReader) btnReader.classList.add('active');
                if (btnEdit) btnEdit.classList.remove('active');
            } else {
                if (reader) reader.style.display = 'none';
                if (textarea) {
                    textarea.style.display = 'block';
                    textarea.focus();
                }
                if (btnReader) btnReader.classList.remove('active');
                if (btnEdit) btnEdit.classList.add('active');
            }
        }

        function autoFormatStanzas() {
            const textarea = document.getElementById('lyrics');
            if (!textarea) return;
            const text = textarea.value.replace(/\r\n/g, '\n');
            if (!text.trim()) return;
            
            const lines = text.split('\n');
            const newLines = [];
            let count = 0;
            
            for (let i = 0; i < lines.length; i++) {
                const trimmed = lines[i].trim();
                if (/^\[.*?\]$/.test(trimmed)) {
                    if (newLines.length > 0 && newLines[newLines.length - 1] !== '') {
                        newLines.push('');
                    }
                    newLines.push(trimmed);
                    count = 0;
                } else if (trimmed === '') {
                    if (newLines.length > 0 && newLines[newLines.length - 1] !== '') {
                        newLines.push('');
                    }
                    count = 0;
                } else {
                    newLines.push(trimmed);
                    count++;
                    if (count >= 4 && i < lines.length - 1 && lines[i+1].trim() !== '') {
                        newLines.push('');
                        count = 0;
                    }
                }
            }
            textarea.value = newLines.join('\n');
            updateLyricsReader();
            setLyricsMode('reader');
        }

        // Analysis Formatting Logic
        function fallbackMarkdown(md) {
            if (!md) return '';
            let text = md.replace(/\r\n/g, '\n');
            text = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            
            text = text.replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>');
            text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
            
            text = text.replace(/^#### (.*$)/gim, '<h4>$1</h4>');
            text = text.replace(/^### (.*$)/gim, '<h3>$1</h3>');
            text = text.replace(/^## (.*$)/gim, '<h2>$1</h2>');
            text = text.replace(/^# (.*$)/gim, '<h1>$1</h1>');
            
            text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
            text = text.replace(/\*([^*]+)\*/g, '<em>$1</em>');
            text = text.replace(/^> (.*$)/gim, '<blockquote>$1</blockquote>');
            
            text = text.replace(/^\s*[-*]\s+(.*$)/gim, '<li>$1</li>');
            text = text.replace(/(<li>[\s\S]*?<\/li>)/g, '<ul>$1</ul>');
            text = text.replace(/<\/ul>\s*<ul>/g, '');
            
            const paras = text.split(/\n{2,}/);
            return paras.map(p => {
                p = p.trim();
                if (/^<(h[1-6]|ul|ol|blockquote|pre)/.test(p)) return p;
                return `<p>${p.replace(/\n/g, '<br>')}</p>`;
            }).join('\n');
        }

        function parseMarkdown(md) {
            if (window.marked && typeof window.marked.parse === 'function') {
                try {
                    return window.marked.parse(md);
                } catch (e) {
                    console.warn('Marked parse error, using fallback:', e);
                }
            }
            return fallbackMarkdown(md);
        }

        function renderAnalysisCards() {
            const rawEl = document.getElementById('analysis-raw');
            const formattedContainer = document.getElementById('analysis-formatted');
            if (!rawEl || !formattedContainer) return;
            const rawText = rawEl.textContent || '';
            if (!rawText.trim()) return;
            
            const parsedHtml = parseMarkdown(rawText);
            const temp = document.createElement('div');
            temp.innerHTML = parsedHtml;
            
            const headings = temp.querySelectorAll('h1, h2, h3, h4');
            if (headings.length === 0) {
                formattedContainer.innerHTML = `<div class="analysis-card">${parsedHtml}</div>`;
                return;
            }
            
            formattedContainer.innerHTML = '';
            let currentCard = null;
            
            Array.from(temp.childNodes).forEach(node => {
                if (node.nodeType === Node.TEXT_NODE && !node.textContent.trim()) {
                    return;
                }
                const isHeading = node.nodeType === Node.ELEMENT_NODE && ['H1', 'H2', 'H3', 'H4'].includes(node.tagName);
                if (isHeading || !currentCard) {
                    currentCard = document.createElement('div');
                    currentCard.className = 'analysis-card';
                    formattedContainer.appendChild(currentCard);
                }
                currentCard.appendChild(node.cloneNode(true));
            });
        }

        function copyAnalysis() {
            const rawEl = document.getElementById('analysis-raw');
            const btn = document.getElementById('btn-copy-analysis');
            if (!rawEl) return;
            const text = rawEl.textContent || '';

            function showSuccess() {
                if (btn) {
                    const original = btn.innerHTML;
                    btn.innerHTML = '✅ Copied!';
                    setTimeout(() => { btn.innerHTML = original; }, 2000);
                }
            }

            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(showSuccess).catch(() => {
                    fallbackCopyText(text);
                    showSuccess();
                });
            } else {
                fallbackCopyText(text);
                showSuccess();
            }
        }

        function fallbackCopyText(text) {
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.focus();
            ta.select();
            try {
                document.execCommand('copy');
            } catch (e) {
                console.warn('Fallback copy failed:', e);
            }
            document.body.removeChild(ta);
        }

        let isRawView = false;
        function toggleRawAnalysis() {
            const formatted = document.getElementById('analysis-formatted');
            const raw = document.getElementById('analysis-raw');
            const btn = document.getElementById('btn-raw-analysis');
            isRawView = !isRawView;
            if (isRawView) {
                if (formatted) formatted.style.display = 'none';
                if (raw) raw.style.display = 'block';
                if (btn) {
                    btn.classList.add('active');
                    btn.textContent = 'Formatted';
                }
            } else {
                if (formatted) formatted.style.display = 'flex';
                if (raw) raw.style.display = 'none';
                if (btn) {
                    btn.classList.remove('active');
                    btn.textContent = 'Raw View';
                }
            }
        }

        function showAnalysisForm() {
            const form = document.querySelector('.analysis-form');
            const resultWrapper = document.getElementById('analysis-result-wrapper');
            const controls = document.getElementById('analysis-controls');
            if (form) form.style.display = 'block';
            if (resultWrapper) resultWrapper.style.display = 'none';
            if (controls) controls.style.display = 'none';
        }

        // Initialize on page load
        document.addEventListener('DOMContentLoaded', () => {
            const textarea = document.getElementById('lyrics');
            if (textarea && textarea.value.trim()) {
                updateLyricsReader();
                setLyricsMode('reader');
            } else {
                setLyricsMode('edit');
            }
            renderAnalysisCards();

            // Smooth scroll on mobile to active section
            if (window.innerWidth <= 1080) {
                const analysisWrapper = document.getElementById('analysis-result-wrapper');
                const hasAnalysis = analysisWrapper && analysisWrapper.style.display !== 'none';
                if (hasAnalysis) {
                    const analysisSection = document.querySelector('.analysis-section');
                    if (analysisSection) {
                        setTimeout(() => {
                            analysisSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
                        }, 250);
                    }
                } else if (textarea && textarea.value.trim()) {
                    const lyricsSection = document.querySelector('.lyrics-section');
                    if (lyricsSection && lyricsSection.style.display !== 'none') {
                        setTimeout(() => {
                            lyricsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
                        }, 250);
                    }
                }
            }
        });
    </script>
</body>
</html>'''

PROMPTS_PAGE_HTML = PAGE_HTML.split('<body>')[0] + '''<body>
    <div class="stars"></div>
    <div class="horizon"></div>
    <div class="container" style="flex-direction: column; width: min(800px, 94vw);">
        <h1 style="font-size: 2rem;">Manage Prompts</h1>
        <a href="/" style="color:#A5C8FF; text-decoration:none; margin-bottom:20px; display:inline-block;">&larr; Back to App</a>
        
        <div style="{message_display}">
            {message_block}
        </div>

        <form method="post" action="/prompts/save">
            <label for="prompt_name">Prompt Name</label>
            <input type="text" id="prompt_name" name="prompt_name" placeholder="e.g. Poetic Devices" required>
            
            <label for="prompt_text">Prompt Text (Use {song}, {artist}, {lyrics_text} as placeholders)</label>
            <textarea id="prompt_text" name="prompt_text" rows="5" required style="resize: vertical;"></textarea>
            
            <button type="submit" style="margin-top: 16px;">Save Prompt</button>
        </form>

        <h2 style="margin-top: 40px; color:#E1E8F0; text-align: center; font-weight: 300;">Saved Prompts</h2>
        <div style="display:flex; flex-direction:column; gap:16px;">
            {prompts_list}
        </div>
    </div>
</body>
</html>'''

class CallingHoursRequestHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/healthz':
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(b'OK')
            return

        if parsed.path == '/authorize':
            self.handle_authorize()
            return

        if parsed.path == '/callback':
            self.handle_callback(parsed.query)
            return

        if parsed.path == '/prompts':
            self.render_prompts_page()
            return

        if parsed.path != '/':
            self.send_error(404, 'Not Found')
            return

        if not ACCESS_TOKEN:
            if GENIUS_CLIENT_ID and GENIUS_CLIENT_SECRET:
                message = (
                    '<div class="message">Genius is not authorized yet. '
                    '<a href="/authorize" style="color:#A8D2FF; text-decoration:underline;">Authorize Genius</a>'
                    '</div>'
                )
            else:
                message = get_genius_missing_message()
        else:
            message = ''

        self.render_page(message=message, lyrics_text='')

    def do_POST(self):
        if self.path not in ('/submit', '/analyze', '/prompts/save'):
            self.send_error(404, 'Not Found')
            return

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')
        data = urllib.parse.parse_qs(body)
        
        if self.path == '/prompts/save':
            name = data.get('prompt_name', [''])[0].strip()
            text = data.get('prompt_text', [''])[0].strip()
            if name and text:
                save_prompt(name, text)
                message = f'<div class="message">Prompt "{html_escape(name)}" saved successfully.</div>'
            else:
                message = '<div class="message">Name and text are required.</div>'
            self.render_prompts_page(message=message)
            return

        if self.path == '/submit':
            artist = data.get('artist', [''])[0].strip()
            song = data.get('song', [''])[0].strip()
            lyrics_text = ''
            message = ''
            show_editor = False

            if not artist or not song:
                message = '<div class="message">Please enter both artist name and song title.</div>'
            else:
                song_url = None
                genius_error = None

                # 1. Attempt Genius song search if credentials or access token are present
                if ACCESS_TOKEN:
                    try:
                        song_url = search_genius_song(artist, song)
                    except Exception as e:
                        print(f"Genius search error: {e}")
                        genius_error = str(e)
                elif GENIUS_CLIENT_ID and GENIUS_CLIENT_SECRET:
                    try:
                        song_url = search_genius_song(artist, song)
                    except Exception as e:
                        print(f"Genius web search error: {e}")
                        genius_error = str(e)

                lyrics = None
                source = ""

                # 2. Try scraping Genius if song URL was resolved
                if song_url:
                    try:
                        lyrics = fetch_genius_lyrics(song_url)
                        if lyrics:
                            source = "Genius"
                    except Exception as e:
                        print(f"Genius scraping failed ({e}), attempting LRCLIB fallback...")
                        genius_error = str(e)

                # 3. Fallback to LRCLIB open database if Genius didn't provide lyrics
                if not lyrics:
                    try:
                        lyrics = fetch_lrclib_lyrics(artist, song)
                        if lyrics:
                            source = "LRCLIB"
                    except Exception as e:
                        print(f"LRCLIB fallback error: {e}")

                if lyrics:
                    lyrics_text = lyrics
                    show_editor = True
                    genius_link = f' <a href="{html_escape(song_url)}" target="_blank" style="color:#A8D2FF; text-decoration:underline;">View on Genius</a>' if song_url else ''
                    note = ' (via LRCLIB fallback - Genius web access blocked)' if (source == 'LRCLIB' and song_url) else (f' (via {source})' if source == 'LRCLIB' else '')
                    message = (
                        '<div class="message">'
                        f'Successfully found lyrics for <strong>{html_escape(artist)}</strong> - <strong>{html_escape(song)}</strong>{note}.'
                        f'{genius_link}'
                        '</div>'
                    )
                else:
                    show_editor = True
                    reason_msg = f" (Genius returned: {html_escape(genius_error)})" if genius_error else ""
                    genius_link = f' <a href="{html_escape(song_url)}" target="_blank" style="color:#A8D2FF; text-decoration:underline;">View on Genius</a>' if song_url else ''
                    message = (
                        '<div class="message">'
                        f'Could not automatically retrieve lyrics for <strong>{html_escape(artist)}</strong> - <strong>{html_escape(song)}</strong>{reason_msg}.'
                        f'{genius_link}<br>'
                        'You can paste or edit the lyrics in the box below to run Gemini analysis.'
                        '</div>'
                    )

            self.render_page(
                message=message,
                lyrics_text=lyrics_text,
                artist_value=html_escape(artist),
                song_value=html_escape(song),
                show_editor=show_editor
            )
            
        elif self.path == '/analyze':
            artist = data.get('artist', [''])[0].strip()
            song = data.get('song', [''])[0].strip()
            lyrics_text = data.get('lyrics', [''])[0].strip()
            prompt_idx_str = data.get('prompt_idx', ['0'])[0].strip()
            model_name = data.get('model_name', [DEFAULT_GEMINI_MODEL])[0].strip()
            if model_name not in [m['id'] for m in AVAILABLE_GEMINI_MODELS]:
                model_name = DEFAULT_GEMINI_MODEL
            analysis_result = ''
            
            prompt_idx = 0
            try:
                prompt_idx = int(prompt_idx_str)
                prompts = load_prompts()
                if 0 <= prompt_idx < len(prompts):
                    prompt_template = prompts[prompt_idx]['text']
                else:
                    prompt_template = prompts[0]['text']
                    prompt_idx = 0
            except (ValueError, IndexError):
                prompts = load_prompts()
                prompt_template = prompts[0]['text']
                prompt_idx = 0
            
            if not GEMINI_API_KEY:
                message = get_gemini_missing_message()
            elif not lyrics_text:
                message = '<div class="message">Please paste or enter lyrics in the lyrics box before running analysis.</div>'
            else:
                try:
                    client = genai.Client(api_key=GEMINI_API_KEY)
                    prompt = prompt_template.replace('{song}', song).replace('{artist}', artist).replace('{lyrics_text}', html.unescape(lyrics_text))
                    interaction = client.interactions.create(
                        model=model_name,
                        input=prompt,
                    )
                    analysis_result = interaction.output_text or ''
                    model_display_name = next((m['name'] for m in AVAILABLE_GEMINI_MODELS if m['id'] == model_name), model_name)
                    message = f'<div class="message">Analysis complete using {html_escape(model_display_name)}.</div>'
                except Exception as e:
                    message = f'<div class="message">Analysis failed: {html_escape(str(e))}</div>'
                    
            self.render_page(
                message=message,
                lyrics_text=lyrics_text,
                artist_value=html_escape(artist),
                song_value=html_escape(song),
                analysis_result=analysis_result,
                selected_model=model_name,
                selected_prompt=prompt_idx,
                show_editor=True
            )

    def handle_authorize(self):
        if not GENIUS_CLIENT_ID or not GENIUS_CLIENT_SECRET:
            self.render_page(
                message=get_genius_missing_message(action='authorizing'),
                lyrics_text=''
            )
            return

        redirect_uri = get_redirect_uri(self.headers.get('Host'))
        auth_url = (
            f'{GENIUS_AUTH_URL}?client_id={urllib.parse.quote(GENIUS_CLIENT_ID)}'
            f'&redirect_uri={urllib.parse.quote(redirect_uri)}&scope=me&response_type=code'
        )

        self.send_response(302)
        self.send_header('Location', auth_url)
        self.end_headers()

    def handle_callback(self, query):
        params = urllib.parse.parse_qs(query)
        code = params.get('code', [''])[0]
        error = params.get('error', [''])[0]

        if error or not code:
            self.render_page(
                message='<div class="message">Authorization failed or was cancelled.</div>',
                lyrics_text=''
            )
            return

        try:
            redirect_uri = get_redirect_uri(self.headers.get('Host'))
            token = exchange_genius_code(code, redirect_uri)
            if token:
                global ACCESS_TOKEN
                ACCESS_TOKEN = token
                save_access_token(token)
                message = '<div class="message">Genius authorization completed. You can now search for lyrics.</div>'
            else:
                message = '<div class="message">Authorization succeeded but no access token was returned.</div>'
        except Exception as e:
            message = '<div class="message">Genius authorization failed: {}</div>'.format(html_escape(str(e)))

        self.render_page(message=message, lyrics_text='')

    def render_page(self, message: str, lyrics_text: str, artist_value: str = '', song_value: str = '', analysis_result: str = '', selected_model: str = DEFAULT_GEMINI_MODEL, selected_prompt: int = 0, show_editor: bool = False):
        show_sections = bool(lyrics_text or show_editor)
        lyrics_display = '' if show_sections else 'display: none;'
        analysis_display = '' if show_sections else 'display: none;'
        analysis_form_display = 'display: none;' if analysis_result else ''
        analysis_result_display = 'display: none;' if not analysis_result else ''
        analysis_controls_display = 'display: flex;' if analysis_result else 'display: none;'
        
        model_options = ''
        for m in AVAILABLE_GEMINI_MODELS:
            selected_attr = ' selected' if m['id'] == selected_model else ''
            model_options += f'<option value="{html_escape(m["id"])}"{selected_attr}>{html_escape(m["name"])}</option>\n'

        prompt_options = ''
        if show_sections:
            prompts = load_prompts()
            for idx, p in enumerate(prompts):
                selected_attr = ' selected' if idx == selected_prompt else ''
                prompt_options += f'<option value="{idx}"{selected_attr}>{html_escape(p["name"])}</option>\n'

        lyrics_text_attr = html.escape(lyrics_text, quote=True)
        content = PAGE_HTML.replace('{message_block}', message)\
                           .replace('{lyrics_text}', html_escape(lyrics_text))\
                           .replace('{lyrics_text_attr}', lyrics_text_attr)\
                           .replace('{artist_value}', artist_value)\
                           .replace('{song_value}', song_value)\
                           .replace('{lyrics_display}', lyrics_display)\
                           .replace('{analysis_display}', analysis_display)\
                           .replace('{analysis_form_display}', analysis_form_display)\
                           .replace('{analysis_result_display}', analysis_result_display)\
                           .replace('{analysis_controls_display}', analysis_controls_display)\
                           .replace('{analysis_result}', html_escape(analysis_result))\
                           .replace('{model_options}', model_options)\
                           .replace('{prompt_options}', prompt_options)
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(content.encode('utf-8'))))
        self.end_headers()
        self.wfile.write(content.encode('utf-8'))

    def render_prompts_page(self, message=''):
        prompts = load_prompts()
        prompts_list_html = ''
        if not prompts:
            prompts_list_html = '<p style="text-align: center; color: #A5C8FF;">No prompts found.</p>'
        else:
            for p in prompts:
                prompts_list_html += f'''
                <div style="background: rgba(165, 200, 255, 0.08); padding: 16px; border-radius: 8px; border: 1px solid rgba(165, 200, 255, 0.2);">
                    <h3 style="margin-top: 0; color: #A5C8FF;">{html_escape(p['name'])}</h3>
                    <pre style="white-space: pre-wrap; margin-bottom: 0; font-family: inherit; font-size: 0.9rem; color: #E1E8F0;">{html_escape(p['text'])}</pre>
                </div>
                '''
                
        message_display = 'display: none;' if not message else 'display: block;'

        content = PROMPTS_PAGE_HTML.replace('{message_block}', message)\
                                   .replace('{message_display}', message_display)\
                                   .replace('{prompts_list}', prompts_list_html)
                                   
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(content.encode('utf-8'))))
        self.end_headers()
        self.wfile.write(content.encode('utf-8'))

    def log_message(self, format, *args):
        return

def html_escape(text: str) -> str:
    return html.escape(text)

def build_genius_api_headers():
    headers = {
        'User-Agent': 'CallingHours/1.0',
        'Accept': 'application/json',
    }
    if ACCESS_TOKEN:
        headers['Authorization'] = f'Bearer {ACCESS_TOKEN}'
    elif GENIUS_CLIENT_ID:
        headers['X-Genius-Client-Id'] = GENIUS_CLIENT_ID
    return headers

def build_genius_headers():
    return build_genius_api_headers()

def build_browser_headers():
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Sec-Ch-Ua': '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Upgrade-Insecure-Requests': '1',
    }

def fetch_lrclib_lyrics(artist: str, song: str) -> str | None:
    headers = {
        'User-Agent': 'CallingHours/1.0 (https://github.com/callinghours)'
    }
    # 1. Exact match endpoint
    try:
        resp = requests.get(
            'https://lrclib.net/api/get',
            params={'artist_name': artist, 'track_name': song},
            headers=headers,
            timeout=8
        )
        if resp.status_code == 200:
            data = resp.json()
            lyrics = data.get('plainLyrics')
            if lyrics and lyrics.strip():
                return lyrics.strip()
    except Exception as e:
        print(f"LRCLIB direct fetch failed: {e}")

    # 2. Search endpoint fallback
    try:
        resp = requests.get(
            'https://lrclib.net/api/search',
            params={'q': f"{artist} {song}"},
            headers=headers,
            timeout=8
        )
        if resp.status_code == 200:
            results = resp.json()
            if isinstance(results, list):
                for item in results:
                    lyrics = item.get('plainLyrics')
                    if lyrics and lyrics.strip():
                        return lyrics.strip()
    except Exception as e:
        print(f"LRCLIB search fallback failed: {e}")

    return None

def get_redirect_uri(host_header: str | None = None) -> str:
    # Explicit override via environment variable
    if os.environ.get('GENIUS_REDIRECT_URI'):
        return os.environ['GENIUS_REDIRECT_URI']

    if host_header:
        scheme = 'https' if (IS_CLOUD_RUN or 'run.app' in host_header) else 'http'
        return f'{scheme}://{host_header}/callback'

    display_host = '127.0.0.1' if HOST == '0.0.0.0' else HOST
    return f'http://{display_host}:{SERVER_PORT or PORT}/callback'

def exchange_genius_code(code: str, redirect_uri: str) -> str | None:
    if not GENIUS_CLIENT_ID or not GENIUS_CLIENT_SECRET:
        raise RuntimeError('Missing Genius client credentials.')

    payload = {
        'client_id': GENIUS_CLIENT_ID,
        'client_secret': GENIUS_CLIENT_SECRET,
        'code': code,
        'grant_type': 'authorization_code',
        'redirect_uri': redirect_uri,
    }
    response = requests.post(GENIUS_TOKEN_URL, data=payload, headers={'Accept': 'application/json'}, timeout=10)
    response.raise_for_status()
    data = response.json()
    return data.get('access_token')

def save_access_token(token: str):
    secrets_path = os.path.join(SCRIPT_DIR, 'calling_hours_secrets.py')
    if not os.path.exists(secrets_path):
        return

    try:
        with open(secrets_path, 'r', encoding='utf-8') as file:
            content = file.read()

        if 'GENIUS_ACCESS_TOKEN' in content:
            content = re.sub(r'GENIUS_ACCESS_TOKEN\s*=\s*.*', f'GENIUS_ACCESS_TOKEN = "{token}"', content, count=1)
        else:
            content += f'\nGENIUS_ACCESS_TOKEN = "{token}"\n'

        with open(secrets_path, 'w', encoding='utf-8') as file:
            file.write(content)
    except (OSError, IOError) as e:
        print(f"Warning: Could not write access token to file ({e}). Token is stored in-memory.")

def search_genius_song(artist: str, song: str) -> str | None:
    query = f"{artist} {song}".strip()

    if ACCESS_TOKEN:
        response = requests.get(GENIUS_API_SEARCH_URL, params={'q': query}, headers=build_genius_headers(), timeout=10)
        response.raise_for_status()
        data = response.json()

        for hit in data.get('response', {}).get('hits', []):
            result = hit.get('result')
            if result and (hit.get('type') == 'song' or result.get('_type') == 'song' or result.get('type') == 'song'):
                return result.get('url')
    else:
        response = requests.get(GENIUS_WEB_SEARCH_URL, params={'q': query}, headers=build_genius_headers(), timeout=10)
        response.raise_for_status()
        data = response.json()

        for section in data.get('response', {}).get('sections', []):
            for hit in section.get('hits', []):
                result = hit.get('result') or hit.get('hit', {}).get('result')
                if result and (hit.get('type') == 'song' or result.get('_type') == 'song' or result.get('type') == 'song'):
                    return result.get('url')

    return None

def fetch_genius_lyrics(song_url: str) -> str | None:
    response = requests.get(song_url, headers=build_browser_headers(), timeout=10)
    response.raise_for_status()
    html_text = response.text

    lyrics = []
    start_matches = list(re.finditer(r'<div[^>]*data-lyrics-container="true"[^>]*>', html_text))
    
    if start_matches:
        for match in start_matches:
            start_idx = match.end()
            div_count = 1
            pos = start_idx
            end_idx = start_idx
            while div_count > 0 and pos < len(html_text):
                next_open = html_text.find('<div', pos)
                next_close = html_text.find('</div>', pos)
                if next_close == -1:
                    break
                if next_open != -1 and next_open < next_close:
                    div_count += 1
                    pos = next_open + 4
                else:
                    div_count -= 1
                    if div_count == 0:
                        end_idx = next_close
                    pos = next_close + 6
            
            block = html_text[start_idx:end_idx]
            block = re.sub(r'<br\s*/?>', '\n', block)
            block = re.sub(r'<.*?>', '', block)
            block = html.unescape(block)
            lyrics.append(block.strip())
            
        full_lyrics = '\n\n'.join(line for line in lyrics if line)
        full_lyrics = re.sub(r'^\d*\s*Contributors.*?Lyrics\n?', '', full_lyrics, flags=re.IGNORECASE)
        return full_lyrics.strip()

    match = re.search(r'<div class="lyrics">.*?<p>(.*?)</p>.*?</div>', html_text, flags=re.S)
    if match:
        text = match.group(1)
        text = re.sub(r'<br\s*/?>', '\n', text)
        text = re.sub(r'<.*?>', '', text)
        text = html.unescape(text)
        text = re.sub(r'^\d*\s*Contributors.*?Lyrics\n?', '', text, flags=re.IGNORECASE)
        return text.strip()

    return None

def run_server():
    global SERVER_PORT
    port = PORT
    try:
        server_address = (HOST, port)
        with http.server.ThreadingHTTPServer(server_address, CallingHoursRequestHandler) as httpd:
            SERVER_PORT = port
            display_host = '127.0.0.1' if HOST == '0.0.0.0' else HOST
            print(f'Calling Hours app running at http://{display_host}:{port} (listening on {HOST}:{port})')
            
            # Open browser automatically only if running locally (not in Cloud Run/Docker/headless)
            no_browser = os.environ.get('NO_BROWSER', '').lower() in ('1', 'true', 'yes')
            if not IS_CLOUD_RUN and not no_browser:
                threading.Timer(0.5, lambda: webbrowser.open(f'http://{display_host}:{port}')).start()

            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print('\nServer stopped')
    except OSError as e:
        print(f'Unable to bind to {HOST}:{port}: {e}')
        return

if __name__ == '__main__':
    run_server()