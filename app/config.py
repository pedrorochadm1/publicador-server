import os

INSTAGRAM_ACCESS_TOKEN = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
INSTAGRAM_BUSINESS_ID = os.environ.get("INSTAGRAM_BUSINESS_ID", "")
FACEBOOK_APP_ID = os.environ.get("FACEBOOK_APP_ID", "")
FACEBOOK_APP_SECRET = os.environ.get("FACEBOOK_APP_SECRET", "")
PUBLICADOR_API_KEY = os.environ.get("PUBLICADOR_API_KEY", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
DATA_DIR = os.environ.get("DATA_DIR", "/data")
TZ = os.environ.get("TZ", "America/Sao_Paulo")

GRAPH = "https://graph.facebook.com/v19.0"

IMG_DIR = os.path.join(DATA_DIR, "img")
DB_PATH = os.path.join(DATA_DIR, "agenda.db")
TOKEN_PATH = os.path.join(DATA_DIR, "token.json")

os.makedirs(IMG_DIR, exist_ok=True)
