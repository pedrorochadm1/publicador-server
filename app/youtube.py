"""Publicação de YouTube Short pelo servidor.

O refresh token do Google não rotaciona, então basta trocá-lo por um access token
fresco a cada publicação — sem persistência necessária.
"""
import os

import requests

from . import config


def disponivel() -> bool:
    return bool(config.YOUTUBE_CLIENT_ID and config.YOUTUBE_CLIENT_SECRET and config.YOUTUBE_REFRESH_TOKEN)


def _access_token() -> str:
    r = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": config.YOUTUBE_CLIENT_ID,
            "client_secret": config.YOUTUBE_CLIENT_SECRET,
            "refresh_token": config.YOUTUBE_REFRESH_TOKEN,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if r.status_code != 200 or "access_token" not in r.json():
        raise RuntimeError(f"Falha ao renovar token YouTube: {r.text[:200]}")
    return r.json()["access_token"]


def publicar_short(video_path: str, titulo: str, descricao: str) -> str:
    """Sobe o vídeo como YouTube Short (público) e devolve o video_id."""
    token = _access_token()

    init = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos",
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json={
            "snippet": {"title": titulo, "description": descricao, "categoryId": "27"},
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
        },
        timeout=60,
    )
    if init.status_code != 200:
        raise RuntimeError(f"Falha ao iniciar upload YouTube: {init.status_code} — {init.text[:200]}")
    upload_url = init.headers.get("Location")
    if not upload_url:
        raise RuntimeError("YouTube não retornou URL de upload resumable.")

    file_size = os.path.getsize(video_path)
    with open(video_path, "rb") as f:
        up = requests.put(
            upload_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "video/mp4",
                "Content-Length": str(file_size),
            },
            data=f,
            timeout=600,
        )
    if up.status_code not in (200, 201):
        raise RuntimeError(f"Falha no upload YouTube: {up.status_code} — {up.text[:200]}")
    return up.json().get("id")
