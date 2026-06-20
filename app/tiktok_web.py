"""Interface web do TikTok (Login Kit + página de publicação conforme as diretrizes).

Serve dois propósitos:
  1. Fornecer URLs públicas de Política de Privacidade e Termos (exigidas no cadastro do app).
  2. Implementar a "Export page" exigida pela auditoria do Content Posting API: o usuário
     conecta a PRÓPRIA conta (OAuth), escolhe privacidade/interações/disclosure e só então
     consente o upload. Honra o creator_info conforme as regras de UX do TikTok.

Importante: o fluxo web usa tokens POR SESSÃO (cookie) — cada pessoa posta na própria conta.
Isso NÃO mexe no token global do servidor (/data/tiktok_token.json) usado pela automação.
"""
import os
import secrets
import urllib.parse

import requests
from fastapi import APIRouter, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from . import config, tiktok

router = APIRouter()

_WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
_TMP_DIR = os.path.join(config.DATA_DIR, "tiktok_web_tmp")
os.makedirs(_TMP_DIR, exist_ok=True)

SCOPES = "user.info.basic,video.publish,video.upload"

# Sessões em memória: session_id -> {access_token, refresh_token, open_id}
_SESSOES: dict[str, dict] = {}
# states OAuth pendentes (CSRF)
_STATES: set[str] = set()


def _redirect_uri() -> str:
    return f"{config.PUBLIC_BASE_URL}/tiktok/callback"


def _html(nome: str) -> str:
    with open(os.path.join(_WEB_DIR, nome), encoding="utf-8") as f:
        return f.read()


def _sessao(request: Request) -> dict | None:
    sid = request.cookies.get("tt_session")
    return _SESSOES.get(sid) if sid else None


# ─────────────────────────── Páginas estáticas ───────────────────────────

@router.get("/", response_class=HTMLResponse)
def home_page():
    # Homepage real (site externo completo) — exigência da auditoria do TikTok.
    return _html("home.html")


@router.get("/app", response_class=HTMLResponse)
@router.get("/tiktok", response_class=HTMLResponse)
def export_page():
    return _html("export.html")


@router.get("/privacy", response_class=HTMLResponse)
def privacy_page():
    return _html("privacy.html")


@router.get("/terms", response_class=HTMLResponse)
def terms_page():
    return _html("terms.html")


# ─────────────────────────── OAuth (Login Kit) ───────────────────────────

@router.get("/tiktok/login")
def login():
    if not config.TIKTOK_CLIENT_KEY:
        raise HTTPException(status_code=500, detail="TIKTOK_CLIENT_KEY não configurado.")
    state = secrets.token_urlsafe(16)
    _STATES.add(state)
    params = urllib.parse.urlencode({
        "client_key": config.TIKTOK_CLIENT_KEY,
        "response_type": "code",
        "scope": SCOPES,
        "redirect_uri": _redirect_uri(),
        "state": state,
    })
    return RedirectResponse(f"https://www.tiktok.com/v2/auth/authorize/?{params}")


@router.get("/tiktok/callback")
def callback(code: str = "", state: str = ""):
    if state not in _STATES:
        raise HTTPException(status_code=400, detail="state inválido.")
    _STATES.discard(state)
    if not code:
        raise HTTPException(status_code=400, detail="code ausente.")

    r = requests.post(
        f"{tiktok.API}/v2/oauth/token/",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": config.TIKTOK_CLIENT_KEY,
            "client_secret": config.TIKTOK_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": _redirect_uri(),
        },
        timeout=30,
    )
    data = r.json() if r.status_code == 200 else {}
    if "access_token" not in data:
        raise HTTPException(status_code=400, detail=f"Falha no OAuth: {r.text[:200]}")

    sid = secrets.token_urlsafe(24)
    _SESSOES[sid] = {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", ""),
        "open_id": data.get("open_id", ""),
    }
    resp = RedirectResponse("/tiktok")
    resp.set_cookie("tt_session", sid, httponly=True, secure=True, samesite="lax", max_age=86400)
    return resp


@router.post("/tiktok/logout")
def logout(request: Request):
    sid = request.cookies.get("tt_session")
    if sid:
        _SESSOES.pop(sid, None)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("tt_session")
    return resp


# ─────────────────────────── Dados do criador ───────────────────────────

@router.get("/tiktok/creator-info")
def creator_info(request: Request):
    """Nome da conta conectada (scope user.info.basic) — pra mostrar na página."""
    s = _sessao(request)
    if not s:
        return JSONResponse({"logged_in": False})
    try:
        r = requests.get(
            f"{tiktok.API}/v2/user/info/",
            params={"fields": "display_name"},
            headers={"Authorization": f"Bearer {s['access_token']}"},
            timeout=30,
        )
        user = r.json().get("data", {}).get("user", {})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"logged_in": True, "error": str(e)}, status_code=502)
    return {"logged_in": True, "display_name": user.get("display_name")}


# ─────────────────────────── Publicação ───────────────────────────

@router.post("/tiktok/publish")
async def publish(request: Request, video: UploadFile = File(...)):
    """Envia o vídeo para os rascunhos do TikTok (Upload-to-Inbox, scope video.upload).
    Sem privacidade/interações aqui — o criador finaliza no app do TikTok."""
    s = _sessao(request)
    if not s:
        raise HTTPException(status_code=401, detail="Conecte sua conta do TikTok primeiro.")
    token = s["access_token"]

    caminho = os.path.join(_TMP_DIR, f"{secrets.token_hex(8)}.mp4")
    with open(caminho, "wb") as f:
        while chunk := await video.read(1024 * 1024):
            f.write(chunk)

    try:
        file_size = os.path.getsize(caminho)
        init = requests.post(
            f"{tiktok.API}/v2/post/publish/inbox/video/init/",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"},
            json={
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": file_size,
                    "chunk_size": file_size,
                    "total_chunk_count": 1,
                },
            },
            timeout=60,
        )
        if init.status_code != 200:
            raise HTTPException(status_code=502, detail=f"init: {init.text[:200]}")
        d = init.json().get("data", {})
        upload_url, publish_id = d.get("upload_url"), d.get("publish_id")
        if not upload_url:
            raise HTTPException(status_code=502, detail=f"sem upload_url: {init.text[:200]}")
        tiktok._enviar(upload_url, caminho, file_size)
    finally:
        if os.path.exists(caminho):
            os.remove(caminho)

    return {"publish_id": publish_id}


@router.get("/tiktok/status/{publish_id}")
def status(publish_id: str, request: Request):
    s = _sessao(request)
    if not s:
        raise HTTPException(status_code=401, detail="Sessão expirada.")
    st = tiktok._poll(s["access_token"], publish_id, tentativas=1)
    return st
