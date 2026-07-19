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

# ─── OpenAI (transcrição Whisper + geração de copy) ───
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# ─── Auto-repost de Trial Reels (Pedro publica o trial no app → repost em Shorts + TikTok) ───
# O reel de teste que o Pedro publica no app é detectado e repostado no TikTok e no
# YouTube Shorts (não republica no IG — já está lá). Desligado por padrão.
REPOST_TRIALS = os.environ.get("REPOST_TRIALS", "").lower() in ("1", "true", "sim", "yes")
REPOST_POLL_MINUTES = int(os.environ.get("REPOST_POLL_MINUTES", "12"))
# Perna TikTok do auto-repost. Desligada por decisão de 2026-07-19: o repost usa a
# cópia comprimida do IG, e Pedro prefere postar no TikTok à mão com o arquivo
# original do Edits (qualidade máxima). O fluxo de publicação direta (via /agendar)
# não é afetado — lá o TikTok continua saindo via Buffer.
REPOST_TIKTOK = os.environ.get("REPOST_TIKTOK", "").lower() in ("1", "true", "sim", "yes")

# ─── Buffer (perna do TikTok via app auditado do Buffer → post público) ───
# Token pessoal: https://publish.buffer.com/settings/api
BUFFER_ACCESS_TOKEN = os.environ.get("BUFFER_ACCESS_TOKEN", "")
BUFFER_TIKTOK_CHANNEL_ID = os.environ.get("BUFFER_TIKTOK_CHANNEL_ID", "")

# ─── TikTok Content Posting API ───
TIKTOK_CLIENT_KEY = os.environ.get("TIKTOK_CLIENT_KEY", "")
TIKTOK_CLIENT_SECRET = os.environ.get("TIKTOK_CLIENT_SECRET", "")
TIKTOK_ACCESS_TOKEN = os.environ.get("TIKTOK_ACCESS_TOKEN", "")
TIKTOK_REFRESH_TOKEN = os.environ.get("TIKTOK_REFRESH_TOKEN", "")
# Redirect OAuth registrado no app. O TikTok não aceita a marca "tiktok" na URL,
# então usamos um domínio/path limpo. Se vazio, cai em PUBLIC_BASE_URL/connect/callback.
TIKTOK_REDIRECT_URI = os.environ.get("TIKTOK_REDIRECT_URI", "")
# Só vira True depois da auditoria do TikTok. Antes disso, todo post sai privado.
TIKTOK_AUDITADO = os.environ.get("TIKTOK_AUDITADO", "").lower() in ("1", "true", "sim", "yes")
# open_id da conta DONA (Pedro). Só ela atualiza o token global da automação;
# visitantes que conectam pelo site ficam só na própria sessão.
TIKTOK_OWNER_OPEN_ID = os.environ.get("TIKTOK_OWNER_OPEN_ID", "")

GRAPH_BASE = "https://graph.facebook.com"
# v22+ é necessário pro trial_params (Trial Reels); v19 é anterior ao recurso.
GRAPH = f"{GRAPH_BASE}/v25.0"

IMG_DIR = os.path.join(DATA_DIR, "img")
DB_PATH = os.path.join(DATA_DIR, "agenda.db")
TOKEN_PATH = os.path.join(DATA_DIR, "token.json")
TIKTOK_TOKEN_PATH = os.path.join(DATA_DIR, "tiktok_token.json")

os.makedirs(IMG_DIR, exist_ok=True)
