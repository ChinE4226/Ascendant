"""Stable project paths and shared defaults."""

from pathlib import Path
import os


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OBJECTS_DIR = PROJECT_ROOT / "objects"
RUNTIME_DIR = Path(os.environ.get('BILIBILI_RUNTIME_DIR', str(PROJECT_ROOT / '.runtime'))).expanduser()
TEMPLATES_DIR = PROJECT_ROOT / "templates"
STATIC_DIR = PROJECT_ROOT / "static"

CREATORS_FILE = OBJECTS_DIR / "creators.json"

# Tracking history belongs to this checkout, independently of account runtime paths.
TRACKING_DB = Path(os.environ.get('BILIBILI_TRACKING_DB', str(PROJECT_ROOT / 'data' / 'tracking.sqlite3'))).expanduser()

QRCODE_FILE = RUNTIME_DIR / "bilibili_qrcode.png"
COOKIE_FILE = RUNTIME_DIR / "bilibili_cookie.txt"
LEGACY_CREDENTIAL_FILE = RUNTIME_DIR / "bilibili_credential.json"
ACCOUNTS_DIR = RUNTIME_DIR / "accounts"
ACTIVE_ACCOUNT_FILE = RUNTIME_DIR / "active_account.json"
PLOTS_DIR = RUNTIME_DIR / "plots"

DEFAULT_REQUEST_FREQUENCY = 4.0

VIDEO_STAT_FIELDS = [
    ("views", "Views", "view"),
    ("likes", "Likes", "like"),
    ("replies", "Replies", "reply"),
    ("favorites", "Favorites", "favorite"),
    ("coins", "Coins", "coin"),
    ("shares", "Shares", "share"),
]
