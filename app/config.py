import os

INSTAGRAM_ACCESS_TOKEN = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
INSTAGRAM_BUSINESS_ID = os.environ.get("INSTAGRAM_BUSINESS_ID", "")
FACEBOOK_APP_ID = os.environ.get("FACEBOOK_APP_ID", "")
FACEBOOK_APP_SECRET = os.environ.get("FACEBOOK_APP_SECRET", "")
PUBLICADOR_API_KEY = os.environ.get("PUBLICADOR_API_KEY", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
DATA_DIR = os.environ.get("DATA_DIR", "/data")
TZ = os.environ.get("TZ", "America/Sao_Paulo")

# ─── YouTube Data API (Shorts) ───
YOUTUBE_CLIENT_ID = os.environ.get("YOUTUBE_CLIENT_ID", "")
YOUTUBE_CLIENT_SECRET = os.environ.get("YOUTUBE_CLIENT_SECRET", "")
YOUTUBE_REFRESH_TOKEN = os.environ.get("YOUTUBE_REFRESH_TOKEN", "")

# ─── TikTok Content Posting API ───
TIKTOK_CLIENT_KEY = os.environ.get("TIKTOK_CLIENT_KEY", "")
TIKTOK_CLIENT_SECRET = os.environ.get("TIKTOK_CLIENT_SECRET", "")
TIKTOK_ACCESS_TOKEN = os.environ.get("TIKTOK_ACCESS_TOKEN", "")
TIKTOK_REFRESH_TOKEN = os.environ.get("TIKTOK_REFRESH_TOKEN", "")
# Só vira True depois da auditoria do TikTok. Antes disso, todo post sai privado.
TIKTOK_AUDITADO = os.environ.get("TIKTOK_AUDITADO", "").lower() in ("1", "true", "sim", "yes")

GRAPH = "https://graph.facebook.com/v19.0"

IMG_DIR = os.path.join(DATA_DIR, "img")
DB_PATH = os.path.join(DATA_DIR, "agenda.db")
TOKEN_PATH = os.path.join(DATA_DIR, "token.json")
TIKTOK_TOKEN_PATH = os.path.join(DATA_DIR, "tiktok_token.json")

os.makedirs(IMG_DIR, exist_ok=True)
