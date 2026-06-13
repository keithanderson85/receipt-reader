"""
One-time Google Drive OAuth setup for the backup script.

Steps:
  1. Go to https://console.cloud.google.com/
  2. Create (or select) a project
  3. Enable the Google Drive API
  4. Go to APIs & Services > Credentials
  5. Create OAuth 2.0 Client ID — choose "Desktop app"
  6. Download the JSON and save it as:
       scripts/gdrive_credentials.json
  7. Run this script:
       python scripts/setup_gdrive.py
  8. A browser window will open — log in and allow access
  9. In Google Drive, create a folder for backups
 10. Open that folder; copy the ID from the URL:
       https://drive.google.com/drive/folders/<FOLDER_ID_HERE>
 11. Add to your .env.prod (and/or .env.dev):
       BACKUP_GDRIVE_FOLDER=<FOLDER_ID_HERE>
"""
from __future__ import annotations

import sys
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def main() -> None:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("ERROR: google-auth-oauthlib not installed.")
        print("Run: pip install google-api-python-client google-auth-oauthlib")
        sys.exit(1)

    root = Path(__file__).resolve().parent.parent
    creds_file = root / "scripts" / "gdrive_credentials.json"
    token_file = root / "scripts" / "gdrive_token.json"

    if not creds_file.exists():
        print(f"ERROR: credentials file not found at:\n  {creds_file}\n")
        print("To create it:")
        print("  1. Visit https://console.cloud.google.com/")
        print("  2. Create a project and enable the Google Drive API")
        print("  3. APIs & Services > Credentials > Create OAuth 2.0 Client ID")
        print("  4. Application type: Desktop app")
        print("  5. Download JSON and save as: scripts/gdrive_credentials.json")
        sys.exit(1)

    print("Opening browser for Google authorization...")
    flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), SCOPES)
    creds = flow.run_local_server(port=0)

    with open(token_file, "w") as fh:
        fh.write(creds.to_json())

    print(f"\nSuccess! Token saved to:\n  {token_file}")
    print("\nNext steps:")
    print("  1. Open Google Drive in your browser")
    print("  2. Create a folder for backups (e.g. 'receipt_reader_backups')")
    print("  3. Open that folder and copy the ID from the URL:")
    print("       https://drive.google.com/drive/folders/<FOLDER_ID>")
    print("  4. Add to your .env.prod / .env.dev:")
    print("       BACKUP_GDRIVE_FOLDER=<FOLDER_ID>")
    print("\nThen run a test:")
    print("  python scripts/backup.py --env prod --dry-run")


if __name__ == "__main__":
    main()
