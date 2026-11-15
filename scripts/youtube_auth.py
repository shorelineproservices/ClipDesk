"""One-time YouTube login for Pia (the publisher). Run it on the laptop:  python scripts\\youtube_auth.py
Needs data/client_secrets.json (Google Cloud console -> enable YouTube Data API v3 -> OAuth client, type Desktop).
A browser opens, you approve upload access for the channel, and data/youtube_token.json is saved (it stays on this machine)."""
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

ROOT = Path(__file__).resolve().parent.parent
secrets, token = ROOT / "data" / "client_secrets.json", ROOT / "data" / "youtube_token.json"
if not secrets.is_file():
    raise SystemExit(f"Put your OAuth client file at {secrets} first (see the docstring).")
flow = InstalledAppFlow.from_client_secrets_file(str(secrets), ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"])
creds = flow.run_local_server(port=0)
token.write_text(creds.to_json(), encoding="utf-8")
print(f"Saved {token}. Pia can post now.")
