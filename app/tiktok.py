"""Publicação no TikTok pelo servidor (Content Posting API / Direct Post), conforme.

Este servidor é o ÚNICO dono dos tokens do TikTok. O refresh token do TikTok é
rotativo (uso único): centralizar aqui evita o conflito de ter dois clientes
renovando o mesmo token. Os tokens vivos ficam persistidos em /data.

Enquanto TIKTOK_AUDITADO=false, todo post sai privado (SELF_ONLY) — limite de app
não auditado pelo TikTok.
"""
import json
import math
import os
import time

import requests

from . import config

API = "https://open.tiktokapis.com"
CHUNK_LIMITE = 64 * 1024 * 1024
CHUNK_SIZE = 10 * 1024 * 1024


def disponivel() -> bool:
    return bool(config.TIKTOK_CLIENT_KEY and config.TIKTOK_CLIENT_SECRET)


def _load_tokens() -> dict:
    if os.path.exists(config.TIKTOK_TOKEN_PATH):
        with open(config.TIKTOK_TOKEN_PATH) as f:
            return json.load(f)
    return {"access_token": config.TIKTOK_ACCESS_TOKEN, "refresh_token": config.TIKTOK_REFRESH_TOKEN}


def _save_tokens(access: str, refresh: str):
    with open(config.TIKTOK_TOKEN_PATH, "w") as f:
        json.dump({"access_token": access, "refresh_token": refresh}, f)


def _renovar() -> str:
    """Renova o access token (rotaciona o refresh token) e persiste em /data."""
    t = _load_tokens()
    if not t.get("refresh_token"):
        return t.get("access_token", "")
    r = requests.post(
        f"{API}/v2/oauth/token/",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": config.TIKTOK_CLIENT_KEY,
            "client_secret": config.TIKTOK_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": t["refresh_token"],
        },
        timeout=30,
    )
    data = r.json() if r.status_code == 200 else {}
    if "access_token" not in data:
        # mantém o que tinha; pode ainda estar válido
        return t.get("access_token", "")
    access = data["access_token"]
    refresh = data.get("refresh_token", t["refresh_token"])
    _save_tokens(access, refresh)
    return access


def _creator_info(token: str) -> dict:
    r = requests.post(
        f"{API}/v2/post/publish/creator_info/query/",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"creator_info falhou: {r.status_code} — {r.text[:200]}")
    data = r.json().get("data", {})
    if not data.get("privacy_level_options"):
        raise RuntimeError(f"creator_info sem privacy_level_options: {r.text[:200]}")
    return data


def _privacidade(opcoes: list) -> str:
    desejada = "PUBLIC_TO_EVERYONE" if config.TIKTOK_AUDITADO else "SELF_ONLY"
    if desejada in opcoes:
        return desejada
    return "SELF_ONLY" if "SELF_ONLY" in opcoes else opcoes[0]


def _enviar(upload_url: str, video_path: str, file_size: int):
    if file_size <= CHUNK_LIMITE:
        with open(video_path, "rb") as f:
            r = requests.put(
                upload_url,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(file_size),
                    "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
                },
                data=f,
                timeout=600,
            )
        if r.status_code not in (200, 201):
            raise RuntimeError(f"upload falhou: {r.status_code} — {r.text[:200]}")
        return
    total = math.floor(file_size / CHUNK_SIZE)
    with open(video_path, "rb") as f:
        for i in range(total):
            inicio = i * CHUNK_SIZE
            tam = CHUNK_SIZE if i < total - 1 else file_size - inicio
            fim = inicio + tam - 1
            f.seek(inicio)
            corpo = f.read(tam)
            r = requests.put(
                upload_url,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(tam),
                    "Content-Range": f"bytes {inicio}-{fim}/{file_size}",
                },
                data=corpo,
                timeout=600,
            )
            if r.status_code not in (200, 201, 206):
                raise RuntimeError(f"upload chunk {i} falhou: {r.status_code} — {r.text[:200]}")


def _poll(token: str, publish_id: str, tentativas: int = 30) -> dict:
    for _ in range(tentativas):
        r = requests.post(
            f"{API}/v2/post/publish/status/fetch/",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"},
            json={"publish_id": publish_id},
            timeout=30,
        )
        if r.status_code == 200:
            data = r.json().get("data", {})
            if data.get("status") in ("PUBLISH_COMPLETE", "FAILED", "SEND_TO_USER_INBOX"):
                return data
        time.sleep(3)
    return {"status": "PROCESSING_TIMEOUT"}


def publicar(video_path: str, caption: str) -> dict:
    """Publica um vídeo no TikTok. Devolve dict com status/publish_id/privacy."""
    token = _renovar()
    if not token:
        return {"status": "indisponivel", "motivo": "sem token TikTok"}

    info = _creator_info(token)
    privacy = _privacidade(info["privacy_level_options"])
    file_size = os.path.getsize(video_path)

    init = requests.post(
        f"{API}/v2/post/publish/video/init/",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"},
        json={
            "post_info": {
                "title": caption[:2200],
                "privacy_level": privacy,
                "disable_comment": bool(info.get("comment_disabled", False)),
                "disable_duet": bool(info.get("duet_disabled", False)),
                "disable_stitch": bool(info.get("stitch_disabled", False)),
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": file_size,
                "chunk_size": file_size if file_size <= CHUNK_LIMITE else CHUNK_SIZE,
                "total_chunk_count": 1 if file_size <= CHUNK_LIMITE else math.floor(file_size / CHUNK_SIZE),
            },
        },
        timeout=60,
    )
    if init.status_code != 200:
        return {"status": "erro", "motivo": f"init {init.status_code}: {init.text[:200]}"}
    d = init.json().get("data", {})
    upload_url, publish_id = d.get("upload_url"), d.get("publish_id")
    if not upload_url:
        return {"status": "erro", "motivo": f"sem upload_url: {init.text[:200]}"}

    _enviar(upload_url, video_path, file_size)
    st = _poll(token, publish_id)
    status = st.get("status")
    if status == "FAILED":
        return {"status": "erro", "publish_id": publish_id, "motivo": st.get("fail_reason", "?")}
    return {"status": "ok", "publish_id": publish_id, "privacy": privacy, "tiktok_status": status}


def publicar_foto(image_urls: list[str], caption: str) -> dict:
    """Publica foto/carrossel no TikTok (Photo Mode), puxando as imagens das URLs públicas.

    As imagens são baixadas pelo TikTok via PULL_FROM_URL — o domínio do PUBLIC_BASE_URL
    precisa estar verificado no app do TikTok (URL prefix property). Por isso o poll do
    status roda ANTES de o servidor apagar os arquivos.
    """
    token = _renovar()
    if not token:
        return {"status": "indisponivel", "motivo": "sem token TikTok"}
    if not image_urls:
        return {"status": "erro", "motivo": "sem imagens"}

    info = _creator_info(token)
    privacy = _privacidade(info["privacy_level_options"])

    init = requests.post(
        f"{API}/v2/post/publish/content/init/",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"},
        json={
            "post_info": {
                "title": caption[:90],
                "description": caption[:4000],
                "privacy_level": privacy,
                "disable_comment": bool(info.get("comment_disabled", False)),
                "auto_add_music": True,
            },
            "source_info": {
                "source": "PULL_FROM_URL",
                "photo_cover_index": 0,
                "photo_images": image_urls,
            },
            "post_mode": "DIRECT_POST",
            "media_type": "PHOTO",
        },
        timeout=60,
    )
    if init.status_code != 200:
        return {"status": "erro", "motivo": f"init {init.status_code}: {init.text[:200]}"}
    d = init.json().get("data", {})
    publish_id = d.get("publish_id")
    if not publish_id:
        return {"status": "erro", "motivo": f"sem publish_id: {init.text[:200]}"}

    st = _poll(token, publish_id)
    status = st.get("status")
    if status == "FAILED":
        return {"status": "erro", "publish_id": publish_id, "motivo": st.get("fail_reason", "?")}
    return {"status": "ok", "publish_id": publish_id, "privacy": privacy, "tiktok_status": status}
