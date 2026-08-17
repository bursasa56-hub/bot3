import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)

_volume = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
DATA_DIR = Path(_volume) if _volume else BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "5"))
TELEGRAM_PROXY = os.getenv("TELEGRAM_PROXY", "").strip()
DB_PATH = os.getenv("DB_PATH", str(DATA_DIR / "bot.db"))
