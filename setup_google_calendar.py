#!/usr/bin/env python3
"""
One-time setup script to authenticate with Google Calendar.

Run this locally to generate the token.json file, then store
the contents as a GitHub secret (GOOGLE_CALENDAR_TOKEN).

Prerequisites:
1. Go to https://console.cloud.google.com/
2. Create a project (or use existing)
3. Enable the Google Calendar API
4. Create OAuth 2.0 credentials (Desktop app type)
5. Download the credentials JSON file
6. Run this script: python setup_google_calendar.py path/to/credentials.json
"""

import json
import sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def main():
    if len(sys.argv) < 2:
        print("Usage: python setup_google_calendar.py <path-to-credentials.json>")
        print("\nGet credentials from: https://console.cloud.google.com/apis/credentials")
        sys.exit(1)

    creds_file = sys.argv[1]

    flow = InstalledAppFlow.from_client_secrets_file(creds_file, SCOPES)
    creds = flow.run_local_server(port=0)

    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes),
    }

    with open("token.json", "w") as f:
        json.dump(token_data, f, indent=2)

    print("\nToken saved to token.json")
    print("\nNow add it as a GitHub secret:")
    print("  1. Go to your repo Settings > Secrets and variables > Actions")
    print("  2. Add a new secret named GOOGLE_CALENDAR_TOKEN")
    print("  3. Paste the entire contents of token.json as the value")
    print(f"\nToken contents:\n{json.dumps(token_data, indent=2)}")


if __name__ == "__main__":
    main()
