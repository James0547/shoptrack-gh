"""
ShopTrack GH — Central configuration.
Uses local config.py; the app inserts this folder at the front of sys.path
in app.py to guarantee we don't collide with any installed 'config' package.
"""
import os
import sys

if getattr(sys, "frozen", False):
    APP_DIR    = os.path.dirname(sys.executable)
    BUNDLE_DIR = sys._MEIPASS
else:
    APP_DIR    = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = APP_DIR

DATA_DIR    = os.path.join(APP_DIR, "data")
BACKUP_DIR  = os.path.join(DATA_DIR, "backups")
LOG_DIR     = os.path.join(APP_DIR, "logs")

DB_PATH     = os.path.join(DATA_DIR, "shoptrack.db")
LOG_PATH    = os.path.join(LOG_DIR, "app.log")
MIG_LOG_PATH= os.path.join(LOG_DIR, "migration.log")

for _d in (DATA_DIR, BACKUP_DIR, LOG_DIR):
    os.makedirs(_d, exist_ok=True)

STATIC_DIR  = os.path.join(BUNDLE_DIR, "static")
LOGO_PATH   = os.path.join(STATIC_DIR, "img", "track.png")

APP_NAME      = "ShopTrack GH"
APP_VERSION   = "1.0.0"
SCHEMA_TARGET = 5

SECRET_KEY    = os.environ.get("SHOPTRACK_SECRET", "change-me-in-prod")

SUPABASE_URL          = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY          = os.environ.get("SUPABASE_KEY", "")
WHATSAPP_TOKEN        = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID     = os.environ.get("WHATSAPP_PHONE_ID", "")
WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_APP_SECRET   = os.environ.get("WHATSAPP_APP_SECRET", "")

DEFAULT_LOW_STOCK = 5
EXPIRY_WARN_DAYS  = 7
DAILY_REPORT_HOUR = 20