"""
Backup Receipt Reader data to three destinations: local server path, Synology NAS
(SMB), and Google Drive.

Creates a zip archive of the database + uploads directory, ships it to each
configured destination, and enforces a rolling retention window so only
BACKUP_KEEP_COUNT backups are kept per destination (oldest deleted first).

Configuration via environment variables (add to .env.dev / .env.prod):

  BACKUP_KEEP_COUNT      Number of daily backups to retain (default: 7)

  # DB / uploads paths (auto-detected from --env if not set)
  BACKUP_DB_PATH         Explicit path to SQLite database file
  BACKUP_UPLOADS_DIR     Explicit path to uploads directory

  # Destination 1 — local server folder (set path to enable)
  BACKUP_LOCAL_PATH      Local directory to copy archives into (e.g. E:\\Backups\\receipt_reader)

  # Destination 2 — SMB / Synology NAS  (all four required to enable)
  BACKUP_SMB_SERVER      NAS hostname or IP  (e.g. SynologyNAS)
  BACKUP_SMB_SHARE       Share name          (e.g. homes)
  BACKUP_SMB_PATH        Sub-path in share   (e.g. anderson\\Backup\\receipt_reader)
  BACKUP_SMB_USERNAME    SMB username
  BACKUP_SMB_PASSWORD    SMB password

  # Destination 3 — Google Drive  (BACKUP_GDRIVE_FOLDER required to enable)
  BACKUP_GDRIVE_FOLDER   Drive folder ID (from folder URL)
  BACKUP_GDRIVE_CREDS    Path to OAuth client JSON  (default: scripts/gdrive_credentials.json)
  BACKUP_GDRIVE_TOKEN    Path to stored token JSON  (default: scripts/gdrive_token.json)

Usage:
  python scripts/backup.py [--env dev|prod] [--keep N] [--dry-run]

Schedule daily with Windows Task Scheduler (see scripts/backup_schedule.bat).
First-time Google Drive setup: python scripts/setup_gdrive.py
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

log = logging.getLogger("backup")

BACKUP_PREFIX = "receipt_reader_backup_"
BACKUP_DATE_FMT = "%Y-%m-%d"
GDRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _resolve_db(root: Path, env: Optional[str]) -> Path:
    if os.getenv("BACKUP_DB_PATH"):
        return Path(os.environ["BACKUP_DB_PATH"])
    if env == "dev":
        return root / "env" / "dev" / "receipts.dev.db"
    if env == "prod":
        return root / "env" / "prod" / "receipts.prod.db"
    candidate = root / "receipts.db"
    if candidate.exists():
        return candidate
    raise SystemExit(
        "Cannot locate database. Pass --env dev|prod or set BACKUP_DB_PATH."
    )


def _resolve_uploads(root: Path, env: Optional[str]) -> Path:
    if os.getenv("BACKUP_UPLOADS_DIR"):
        return Path(os.environ["BACKUP_UPLOADS_DIR"])
    if env in ("dev", "prod"):
        return root / "env" / env / "uploads"
    return root / "uploads"


def _local_backup_dir(root: Path, env: Optional[str]) -> Path:
    if env in ("dev", "prod"):
        return root / "env" / env / "backups"
    return root / "backups"


# ---------------------------------------------------------------------------
# Archive creation
# ---------------------------------------------------------------------------

def _backup_sqlite(db_path: Path, zf: zipfile.ZipFile) -> None:
    """Use SQLite's hot-backup API for a consistent snapshot with the app running."""
    import sqlite3
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        dst = sqlite3.connect(tmp_path)
        src.backup(dst)
        dst.close()
        src.close()
        zf.write(tmp_path, db_path.name)
        log.info("  + %s (%.1f KB, hot backup)", db_path.name, tmp_path.stat().st_size / 1024)
    finally:
        tmp_path.unlink(missing_ok=True)


def build_archive(db_path: Path, uploads_dir: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime(BACKUP_DATE_FMT)
    archive_path = out_dir / f"{BACKUP_PREFIX}{date_str}.zip"

    log.info("Building archive: %s", archive_path.name)
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        if db_path.exists():
            _backup_sqlite(db_path, zf)
        else:
            log.warning("Database not found at %s, skipping", db_path)

        if uploads_dir.exists():
            files = [f for f in uploads_dir.rglob("*") if f.is_file()]
            for f in sorted(files):
                zf.write(f, "uploads/" + str(f.relative_to(uploads_dir)).replace("\\", "/"))
            log.info("  + uploads/ (%d files)", len(files))
        else:
            log.info("Uploads directory not found at %s, skipping", uploads_dir)

    size_mb = archive_path.stat().st_size / 1_048_576
    log.info("Archive ready: %.2f MB", size_mb)
    return archive_path


# ---------------------------------------------------------------------------
# Local retention helper
# ---------------------------------------------------------------------------

def _prune_local(backup_dir: Path, keep: int, dry_run: bool) -> None:
    archives = sorted(backup_dir.glob(f"{BACKUP_PREFIX}*.zip"))
    while len(archives) >= keep:
        oldest = archives.pop(0)
        if dry_run:
            log.info("[DRY RUN] Would delete local: %s", oldest.name)
        else:
            oldest.unlink()
            log.info("Local: deleted %s", oldest.name)


# ---------------------------------------------------------------------------
# Local server path destination
# ---------------------------------------------------------------------------

def backup_local_path(archive_path: Path, keep: int, dry_run: bool) -> bool:
    """Copy archive to a secondary local directory. Returns True on success."""
    dest_dir_str = os.getenv("BACKUP_LOCAL_PATH", "").strip()
    if not dest_dir_str:
        log.info("Local path: not configured (BACKUP_LOCAL_PATH not set) — skipped")
        return False

    dest_dir = Path(dest_dir_str)

    try:
        if dry_run:
            log.info("[DRY RUN] Would copy to local path: %s\\%s", dest_dir, archive_path.name)
            return True

        dest_dir.mkdir(parents=True, exist_ok=True)

        # Prune oldest backups in this directory before adding new one
        existing = sorted(dest_dir.glob(f"{BACKUP_PREFIX}*.zip"))
        while len(existing) >= keep:
            oldest = existing.pop(0)
            oldest.unlink()
            log.info("Local path: deleted oldest backup %s", oldest.name)

        import shutil
        dest_file = dest_dir / archive_path.name
        shutil.copy2(archive_path, dest_file)
        log.info("Local path: copied to %s", dest_file)
        return True

    except Exception as exc:
        log.error("Local path: backup failed — %s", exc)
        return False


# ---------------------------------------------------------------------------
# SMB / NAS destination
# ---------------------------------------------------------------------------

def backup_smb(archive_path: Path, keep: int, dry_run: bool) -> bool:
    server = os.getenv("BACKUP_SMB_SERVER", "").strip()
    share = os.getenv("BACKUP_SMB_SHARE", "").strip()
    remote_subpath = os.getenv("BACKUP_SMB_PATH", "receipt_reader").strip()
    username = os.getenv("BACKUP_SMB_USERNAME", "").strip()
    password = os.getenv("BACKUP_SMB_PASSWORD", "").strip()

    if not all([server, share, username, password]):
        log.info("SMB: not configured (need SERVER, SHARE, USERNAME, PASSWORD) — skipped")
        return False

    try:
        import smbclient
    except ImportError:
        log.error("SMB: smbprotocol not installed — run: pip install smbprotocol")
        return False

    try:
        smbclient.register_session(server, username=username, password=password)
        remote_dir = f"\\\\{server}\\{share}\\{remote_subpath}"

        if dry_run:
            log.info("[DRY RUN] Would upload to SMB: %s\\%s", remote_dir, archive_path.name)
            return True

        smbclient.makedirs(remote_dir, exist_ok=True)

        # List existing backups and prune oldest
        try:
            entries = sorted(
                e.name for e in smbclient.scandir(remote_dir)
                if e.name.startswith(BACKUP_PREFIX) and e.name.endswith(".zip")
            )
        except Exception:
            entries = []

        while len(entries) >= keep:
            oldest = entries.pop(0)
            log.info("SMB: deleting oldest backup %s", oldest)
            smbclient.remove(f"{remote_dir}\\{oldest}")

        # Upload
        remote_file = f"{remote_dir}\\{archive_path.name}"
        log.info("SMB: uploading to %s", remote_file)
        with (
            open(archive_path, "rb") as src,
            smbclient.open_file(remote_file, mode="wb") as dst,
        ):
            while chunk := src.read(1024 * 1024):
                dst.write(chunk)

        log.info("SMB: upload complete")
        return True

    except Exception as exc:
        log.error("SMB: backup failed — %s", exc)
        return False


# ---------------------------------------------------------------------------
# Google Drive destination
# ---------------------------------------------------------------------------

def _gdrive_service(creds_file: str, token_file: str):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if Path(token_file).exists():
        creds = Credentials.from_authorized_user_file(token_file, GDRIVE_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(creds_file, GDRIVE_SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_file, "w") as fh:
            fh.write(creds.to_json())

    return build("drive", "v3", credentials=creds)


def backup_gdrive(archive_path: Path, keep: int, dry_run: bool) -> bool:
    folder_id = os.getenv("BACKUP_GDRIVE_FOLDER", "").strip()
    root = _project_root()
    creds_file = os.getenv(
        "BACKUP_GDRIVE_CREDS",
        str(root / "scripts" / "gdrive_credentials.json"),
    )
    token_file = os.getenv(
        "BACKUP_GDRIVE_TOKEN",
        str(root / "scripts" / "gdrive_token.json"),
    )

    if not folder_id:
        log.info("Google Drive: not configured (BACKUP_GDRIVE_FOLDER not set) — skipped")
        return False

    if not Path(creds_file).exists():
        log.error(
            "Google Drive: credentials file not found at %s\n"
            "  Run: python scripts/setup_gdrive.py",
            creds_file,
        )
        return False

    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        log.error(
            "Google Drive: client library not installed — run:\n"
            "  pip install google-api-python-client google-auth-oauthlib"
        )
        return False

    try:
        service = _gdrive_service(creds_file, token_file)

        if dry_run:
            log.info("[DRY RUN] Would upload to Google Drive folder: %s", folder_id)
            return True

        # List existing backups in the folder (oldest first)
        query = (
            f"'{folder_id}' in parents"
            f" and name contains '{BACKUP_PREFIX}'"
            f" and name contains '.zip'"
            f" and trashed = false"
        )
        result = service.files().list(
            q=query,
            fields="files(id, name, createdTime)",
            orderBy="name asc",
        ).execute()
        existing = result.get("files", [])

        # Delete oldest backups until we are under the limit
        while len(existing) >= keep:
            oldest = existing.pop(0)
            log.info("Google Drive: deleting oldest backup %s", oldest["name"])
            service.files().delete(fileId=oldest["id"]).execute()

        # Upload new archive with resumable upload (handles large files)
        media = MediaFileUpload(str(archive_path), mimetype="application/zip", resumable=True)
        file_meta = {"name": archive_path.name, "parents": [folder_id]}
        log.info("Google Drive: uploading %s", archive_path.name)

        request = service.files().create(body=file_meta, media_body=media, fields="id")
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                log.info("  Google Drive: %.0f%%", status.progress() * 100)

        log.info("Google Drive: upload complete (id: %s)", response.get("id"))
        return True

    except Exception as exc:
        log.error("Google Drive: backup failed — %s", exc)
        return False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )

    parser = argparse.ArgumentParser(
        description="Back up Receipt Reader database and uploads to local path, NAS, and Google Drive."
    )
    parser.add_argument(
        "--env",
        choices=["dev", "prod"],
        help="Which environment to back up (uses env-specific paths)",
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=None,
        help="Number of backups to retain per destination (overrides BACKUP_KEEP_COUNT)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without uploading or deleting anything",
    )
    args = parser.parse_args()

    root = _project_root()

    # Load env file (env-specific first, then fallback to .env)
    if args.env:
        load_dotenv(root / f".env.{args.env}")
    load_dotenv(root / ".env", override=False)

    keep = args.keep or int(os.getenv("BACKUP_KEEP_COUNT", "7"))
    db_path = _resolve_db(root, args.env)
    uploads_dir = _resolve_uploads(root, args.env)
    local_backup_dir = _local_backup_dir(root, args.env)

    log.info("=== Receipt Reader Backup%s ===", " (DRY RUN)" if args.dry_run else "")
    log.info("Environment : %s", args.env or "default")
    log.info("Database    : %s", db_path)
    log.info("Uploads     : %s", uploads_dir)
    log.info("Keep        : %d backups per destination", keep)

    if args.dry_run:
        log.info("Building archive (dry-run still creates local zip for size estimate)...")

    # Prune local before creating new one so we don't count the one we're about to add
    _prune_local(local_backup_dir, keep, dry_run=args.dry_run)

    archive = build_archive(db_path, uploads_dir, local_backup_dir)

    local_ok = backup_local_path(archive, keep, args.dry_run)
    smb_ok = backup_smb(archive, keep, args.dry_run)
    gdrive_ok = backup_gdrive(archive, keep, args.dry_run)

    any_remote = local_ok or smb_ok or gdrive_ok
    if not any_remote:
        if args.dry_run:
            log.info("No extra destinations configured — would keep local archive only")
        else:
            log.warning(
                "No extra destinations configured — archive kept locally only: %s",
                archive,
            )
    else:
        log.info(
            "Destinations: LocalPath=%s  SMB=%s  GDrive=%s",
            local_ok, smb_ok, gdrive_ok,
        )

    log.info("=== Done ===")


if __name__ == "__main__":
    main()
