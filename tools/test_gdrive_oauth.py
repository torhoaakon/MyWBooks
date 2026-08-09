"""
Quick manual test for Google Drive OAuth2 credentials.

Usage:
    uv run python tools/test_gdrive_oauth.py

Steps:
  1. Ensure http://localhost:8000/api/user/gdrive/callback is in your OAuth
     client's "Authorised redirect URIs" in Google Cloud Console.
  2. Run the script — it opens your browser automatically.
  3. Authorise the app in the browser.
  4. The script catches the redirect, exchanges the code, and confirms it works.
"""

import os
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]

CALLBACK_PORT = 9876
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/api/user/gdrive/callback"
SCOPE = "https://www.googleapis.com/auth/drive.file"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
FILES_URL = "https://www.googleapis.com/drive/v3/files"

_auth_code: list[str] = []


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        if code:
            _auth_code.append(code)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h2>Authorised! You can close this tab.</h2>")
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Missing code parameter.")

    def log_message(self, *args):
        pass  # suppress request logging


def _wait_for_code() -> str:
    server = HTTPServer(("localhost", CALLBACK_PORT), _CallbackHandler)
    server.handle_request()  # handle exactly one request then stop
    return _auth_code[0]


def build_auth_url() -> str:
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(code: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        },
    )
    resp.raise_for_status()
    return resp.json()


def list_files(access_token: str) -> list:
    resp = requests.get(
        FILES_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
        },
        params={"pageSize": 5, "fields": "files(id,name,mimeType)"},
    )
    resp.raise_for_status()
    return resp.json().get("files", [])


def main():
    print("\n=== Google Drive OAuth2 Test ===\n")
    print(
        "Make sure http://localhost:8000/api/user/gdrive/callback is in your OAuth client's"
    )
    print("Authorised redirect URIs in Google Cloud Console.\n")
    print("Opening browser for authorisation...")

    url = build_auth_url()
    webbrowser.open(url)

    print("Waiting for redirect on http://localhost:8000/api/user/gdrive/callback ...")
    code = _wait_for_code()
    print("Code received.\n")

    print("Exchanging code for tokens...")
    tokens = exchange_code(code)

    print("✓ Token exchange successful!")
    print(f"  Access token  : {tokens['access_token'][:40]}...")
    print(f"  Refresh token : {tokens.get('refresh_token', '(none)')[:40]}...")
    print(f"  Expires in    : {tokens.get('expires_in')} seconds")
    print(f"  Scope         : {tokens.get('scope')}")

    print("\nListing up to 5 Drive files accessible with this token...")
    files = list_files(tokens["access_token"])
    if files:
        for f in files:
            print(f"  {f['id']}  {f['mimeType']:<40}  {f['name']}")
    else:
        print(
            "  (no files visible yet — expected for a fresh app with drive.file scope)"
        )

    print("\n✓ All checks passed. Credentials are working.\n")


if __name__ == "__main__":
    main()
