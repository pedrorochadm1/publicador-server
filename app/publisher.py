"""Publicação IMEDIATA no Instagram via Graph API.

Sem scheduled_publish_time: toda chamada aqui publica na hora. O agendamento
mora no scheduler deste servidor — o Instagram só recebe a ordem de publicar
quando chega o horário, e enxerga como publicação imediata.
"""
import time

import requests

from . import config
from .token_store import get_token


def _media_url(filename: str) -> str:
    if not config.PUBLIC_BASE_URL:
        raise RuntimeError("PUBLIC_BASE_URL não configurado — o Instagram não consegue baixar a mídia.")
    return f"{config.PUBLIC_BASE_URL}/img/{filename}"


def _eh_video(filename: str) -> bool:
    return filename.lower().endswith((".mp4", ".mov"))


def _criar_container(params: dict) -> str:
    params["access_token"] = get_token()
    r = requests.post(f"{config.GRAPH}/{config.INSTAGRAM_BUSINESS_ID}/media", params=params, timeout=60)
    r.raise_for_status()
    return r.json()["id"]


def _aguardar(container_id: str, tentativas: int = 20, intervalo: int = 5) -> bool:
    for _ in range(tentativas):
        r = requests.get(
            f"{config.GRAPH}/{container_id}",
            params={"fields": "status_code", "access_token": get_token()},
            timeout=30,
        )
        code = r.json().get("status_code")
        if code == "FINISHED":
            return True
        if code == "ERROR":
            return False
        time.sleep(intervalo)
    return False


def _publicar(container_id: str) -> str:
    r = requests.post(
        f"{config.GRAPH}/{config.INSTAGRAM_BUSINESS_ID}/media_publish",
        params={"creation_id": container_id, "access_token": get_token()},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["id"]


def publicar(arquivos: list[str], caption: str) -> str:
    """Publica reel (1 vídeo), foto única (1 imagem) ou carrossel (2-10 imagens)."""
    if not arquivos:
        raise ValueError("Nenhum arquivo.")

    # Reel: 1 vídeo
    if len(arquivos) == 1 and _eh_video(arquivos[0]):
        container = _criar_container(
            {"media_type": "REELS", "video_url": _media_url(arquivos[0]), "caption": caption}
        )
        # vídeo demora mais para processar
        if not _aguardar(container, tentativas=90, intervalo=5):
            raise RuntimeError("Instagram falhou ao processar o vídeo.")
        return _publicar(container)

    if any(_eh_video(a) for a in arquivos):
        raise ValueError("Vídeo só pode ser publicado sozinho (reel), não em carrossel.")

    # Foto única
    if len(arquivos) == 1:
        container = _criar_container({"image_url": _media_url(arquivos[0]), "caption": caption})
    # Carrossel
    else:
        if len(arquivos) > 10:
            raise ValueError("Carrossel suporta no máximo 10 imagens.")
        filhos = [
            _criar_container({"image_url": _media_url(n), "is_carousel_item": "true"})
            for n in arquivos
        ]
        container = _criar_container(
            {"media_type": "CAROUSEL", "children": ",".join(filhos), "caption": caption}
        )

    if not _aguardar(container):
        raise RuntimeError("Instagram falhou ao processar a mídia.")
    return _publicar(container)
