"""
Quick manual test for Dropbox OAuth2 credentials.

Usage:
    uv run python tools/test_dropbox_oauth.py

Steps:
  1. Ensure http://localhost:9876/api/user/dropbox/callback is in your Dropbox
     app's "Redirect URIs" at https://www.dropbox.com/developers/apps.
  2. Run the script — it opens your browser automatically.
  3. Authorise the app in the browser.
  4. The script catches the redirect, exchanges the code, and confirms it works
     by uploading a small test file and then deleting it.
"""

import os
import secrets
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import requests
from dotenv import load_dotenv

load_dotenv()

APP_KEY = os.environ["DROPBOX_APP_KEY"]
APP_SECRET = os.environ["DROPBOX_APP_SECRET_KEY"]

CALLBACK_PORT = 9876
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/api/user/dropbox/callback"
AUTH_URL = "https://www.dropbox.com/oauth2/authorize"
TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"
UPLOAD_URL = "https://content.dropboxapi.com/2/files/upload"
DELETE_URL = "https://api.dropboxapi.com/2/files/delete_v2"
ACCOUNT_URL = "https://api.dropboxapi.com/2/users/get_current_account"

SCOPES = "files.content.write files.metadata.read account_info.read"
TEST_FILE_PATH = "/mywbooks_oauth_test.txt"

_state = secrets.token_urlsafe(16)
_auth_code: list[str] = []


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        returned_state = params.get("state", [None])[0]
        if code and returned_state == _state:
            _auth_code.append(code)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h2>Authorised! You can close this tab.</h2>")
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Missing or mismatched state/code parameter.")

    def log_message(self, *args):
        pass


def _wait_for_code() -> str:
    server = HTTPServer(("localhost", CALLBACK_PORT), _CallbackHandler)
    server.handle_request()
    return _auth_code[0]


def build_auth_url() -> str:
    params = {
        "client_id": APP_KEY,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "token_access_type": "offline",
        "scope": SCOPES,
        "state": _state,
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(code: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        data={
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
        },
        auth=(APP_KEY, APP_SECRET),
    )
    resp.raise_for_status()
    return resp.json()


def get_account(access_token: str) -> dict:
    resp = requests.post(
        ACCOUNT_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
        },
    )
    resp.raise_for_status()
    return resp.json()


def upload_test_file(access_token: str) -> dict:
    resp = requests.post(
        UPLOAD_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Dropbox-API-Arg": '{"path": "'
            + TEST_FILE_PATH
            + '", "mode": "overwrite"}',
            "Content-Type": "application/octet-stream",
        },
        data=b"MyWBooks Dropbox OAuth2 test file. Safe to delete.",
    )
    resp.raise_for_status()
    return resp.json()


def delete_test_file(access_token: str) -> None:
    resp = requests.post(
        DELETE_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={"path": TEST_FILE_PATH},
    )
    resp.raise_for_status()


def main():
    print("\n=== Dropbox OAuth2 Test ===\n")
    print(f"Make sure {REDIRECT_URI} is listed in your Dropbox app's Redirect URIs.")
    print("https://www.dropbox.com/developers/apps\n")
    print("Opening browser for authorisation...")

    url = build_auth_url()
    webbrowser.open(url)

    print(f"Waiting for redirect on {REDIRECT_URI} ...")
    code = _wait_for_code()
    print("Code received.\n")

    print("Exchanging code for tokens...")
    tokens = exchange_code(code)

    access_token = tokens["access_token"]
    refresh_token = tokens.get("refresh_token", "(none)")
    expires_in = tokens.get("expires_in")

    print("✓ Token exchange successful!")
    print(f"  Access token  : {access_token[:40]}...")
    print(f"  Refresh token : {str(refresh_token)[:40]}...")
    print(f"  Expires in    : {expires_in} seconds")

    print("\nFetching account info...")
    account = get_account(access_token)
    print(
        f"  Account       : {account.get('name', {}).get('display_name')} ({account.get('email')})"
    )

    print(f"\nUploading test file to {TEST_FILE_PATH} ...")
    meta = upload_test_file(access_token)
    print(f"  Uploaded      : {meta.get('path_display')} ({meta.get('size')} bytes)")

    print("Deleting test file...")
    delete_test_file(access_token)
    print("  Deleted.")

    print("\n✓ All checks passed. Dropbox credentials are working.\n")


if __name__ == "__main__":
    main()
