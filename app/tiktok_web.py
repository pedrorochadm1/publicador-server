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
import time
import urllib.parse

import requests
from fastapi import APIRouter, BackgroundTasks, Form, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from . import config, tiktok

router = APIRouter()

_WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
_TMP_DIR = os.path.join(config.DATA_DIR, "tiktok_web_tmp")
os.makedirs(_TMP_DIR, exist_ok=True)

# video.publish = Direct Post; video.upload = etapa de envio do arquivo (vem junto no produto).
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
    # Salva também como token global (pra automação e diagnóstico)
    try:
        tiktok._save_tokens(data["access_token"], data.get("refresh_token", ""))
    except Exception:  # noqa: BLE001
        pass
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
    s = _sessao(request)
    if not s:
        return JSONResponse({"logged_in": False})
    try:
        info = tiktok._creator_info(s["access_token"])
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"logged_in": True, "error": str(e)}, status_code=502)
    return {
        "logged_in": True,
        "creator_nickname": info.get("creator_nickname"),
        "privacy_level_options": info.get("privacy_level_options", []),
        "comment_disabled": info.get("comment_disabled", False),
        "duet_disabled": info.get("duet_disabled", False),
        "stitch_disabled": info.get("stitch_disabled", False),
        "max_video_post_duration_sec": info.get("max_video_post_duration_sec"),
    }


# ─────────────────────────── Publicação ───────────────────────────

def _upload_bg(upload_url: str, caminho: str, file_size: int):
    """Envia o arquivo ao TikTok em segundo plano e limpa o temporário.
    Roda fora da requisição pra o navegador não esperar (evita 502 do proxy)."""
    try:
        with open(caminho, "rb") as vf:
            requests.put(
                upload_url,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(file_size),
                    "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
                },
                data=vf,
                timeout=600,
            )
    except Exception as e:  # noqa: BLE001
        print(f"[tiktok_web] upload bg falhou: {e}")
    finally:
        if os.path.exists(caminho):
            os.remove(caminho)


@router.post("/tiktok/publish")
async def publish(
    request: Request,
    background: BackgroundTasks,
    video: UploadFile = File(...),
    caption: str = Form(""),
    privacy_level: str = Form(...),
    allow_comment: str = Form("false"),
    allow_duet: str = Form("false"),
    allow_stitch: str = Form("false"),
    commercial: str = Form("false"),
    your_brand: str = Form("false"),
    branded_content: str = Form("false"),
):
    s = _sessao(request)
    if not s:
        raise HTTPException(status_code=401, detail="Conecte sua conta do TikTok primeiro.")

    def _b(v):
        return str(v).lower() in ("1", "true", "on", "yes")

    branded = _b(branded_content) and _b(commercial)
    # Regra do TikTok: conteúdo de marca (branded) não pode ser privado.
    if branded and privacy_level == "SELF_ONLY":
        raise HTTPException(status_code=400, detail="Conteúdo de marca não pode ser privado.")

    token = s["access_token"]

    # 1. valida privacidade contra a conta
    try:
        info = tiktok._creator_info(token)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"creator_info falhou: {e}")
    if privacy_level not in info.get("privacy_level_options", []):
        raise HTTPException(status_code=400, detail="privacy_level inválido para esta conta.")

    # 2. salva o upload num arquivo temporário
    caminho = os.path.join(_TMP_DIR, f"{secrets.token_hex(8)}.mp4")
    with open(caminho, "wb") as f:
        while chunk := await video.read(1024 * 1024):
            f.write(chunk)
    file_size = os.path.getsize(caminho)

    # 3. init Direct Post (rápido) — pega publish_id + upload_url
    try:
        init = requests.post(
            f"{tiktok.API}/v2/post/publish/video/init/",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"},
            json={
                "post_info": {
                    "title": caption[:2200],
                    "privacy_level": privacy_level,
                    "disable_comment": not _b(allow_comment),
                    "disable_duet": not _b(allow_duet),
                    "disable_stitch": not _b(allow_stitch),
                    "brand_content_toggle": branded,
                    "brand_organic_toggle": _b(your_brand) and _b(commercial),
                },
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": file_size,
                    "chunk_size": file_size,
                    "total_chunk_count": 1,
                },
            },
            timeout=30,
        )
    except Exception as e:  # noqa: BLE001
        if os.path.exists(caminho):
            os.remove(caminho)
        raise HTTPException(status_code=504, detail=f"init sem resposta (timeout?): {e}")
    if init.status_code != 200:
        if os.path.exists(caminho):
            os.remove(caminho)
        raise HTTPException(status_code=502, detail=f"init {init.status_code}: {init.text[:300]}")
    d = init.json().get("data", {})
    upload_url, publish_id = d.get("upload_url"), d.get("publish_id")
    if not upload_url:
        if os.path.exists(caminho):
            os.remove(caminho)
        raise HTTPException(status_code=502, detail=f"sem upload_url: {init.text[:300]}")

    # 4. envia o arquivo em segundo plano — a requisição volta já, sem 502 do proxy
    background.add_task(_upload_bg, upload_url, caminho, file_size)
    return {"publish_id": publish_id}


@router.get("/tiktok/debug")
def debug(key: str = ""):
    """Diagnóstico (protegido por key): roda creator_info + init e devolve tempos/erros crus."""
    if key != config.PUBLICADOR_API_KEY:
        raise HTTPException(status_code=401, detail="key inválida")
    out = {}
    token = tiktok._renovar()
    out["tem_token"] = bool(token)

    t0 = time.time()
    try:
        info = tiktok._creator_info(token)
        out["creator_info"] = {"ok": True, "seg": round(time.time() - t0, 1),
                               "privacy": info.get("privacy_level_options"),
                               "nickname": info.get("creator_nickname")}
        priv = (info.get("privacy_level_options") or ["SELF_ONLY"])[0]
    except Exception as e:  # noqa: BLE001
        out["creator_info"] = {"ok": False, "seg": round(time.time() - t0, 1), "erro": str(e)}
        return out

    t1 = time.time()
    try:
        r = requests.post(
            f"{tiktok.API}/v2/post/publish/video/init/",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"},
            json={
                "post_info": {"title": "teste", "privacy_level": priv,
                              "disable_comment": False, "disable_duet": False, "disable_stitch": False},
                "source_info": {"source": "FILE_UPLOAD", "video_size": 1000000,
                                "chunk_size": 1000000, "total_chunk_count": 1},
            },
            timeout=30,
        )
        out["init"] = {"status": r.status_code, "seg": round(time.time() - t1, 1), "resp": r.text[:400]}
    except Exception as e:  # noqa: BLE001
        out["init"] = {"erro": str(e), "seg": round(time.time() - t1, 1)}
    return out


@router.get("/tiktok/status/{publish_id}")
def status(publish_id: str, request: Request):
    s = _sessao(request)
    if not s:
        raise HTTPException(status_code=401, detail="Sessão expirada.")
    st = tiktok._poll(s["access_token"], publish_id, tentativas=1)
    return st
