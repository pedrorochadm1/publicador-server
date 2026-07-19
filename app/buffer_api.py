"""Perna do TikTok via Buffer (GraphQL API).

O app do Buffer é auditado no TikTok, então o post sai PÚBLICO de verdade —
sem depender do audit do Direct Post do nosso app próprio (403 unaudited).
A mídia vai por URL pública (PUBLIC_BASE_URL); o Buffer ingere a mídia na
criação do post, e ainda assim a gente segura a limpeza até o status final
(poll de até ~2 min) por garantia.

Token: gerado por Pedro em https://publish.buffer.com/settings/api
(env BUFFER_ACCESS_TOKEN + BUFFER_TIKTOK_CHANNEL_ID).
"""
import time

import requests

from . import config

_URL = "https://api.buffer.com"

_MUTATION = """
mutation Criar($input: CreatePostInput!) {
  createPost(input: $input) {
    __typename
    ... on PostActionSuccess { post { id status } }
    ... on NotFoundError { message }
    ... on UnauthorizedError { message }
    ... on UnexpectedError { message }
    ... on RestProxyError { message }
    ... on LimitReachedError { message }
    ... on InvalidInputError { message }
  }
}
"""

_QUERY_STATUS = """
query Status($input: PostInput!) {
  post(input: $input) { id status }
}
"""


class BufferCreateError(RuntimeError):
    """Falha ANTES de criar o post no Buffer (nada saiu) — seguro cair no fallback."""


def disponivel() -> bool:
    return bool(config.BUFFER_ACCESS_TOKEN and config.BUFFER_TIKTOK_CHANNEL_ID)


def _gql(query: str, variables: dict) -> dict:
    r = requests.post(
        _URL,
        headers={"Authorization": f"Bearer {config.BUFFER_ACCESS_TOKEN}"},
        json={"query": query, "variables": variables},
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("errors"):
        raise RuntimeError(f"Buffer GraphQL: {data['errors']}")
    return data["data"]


def _criar(assets: list[dict], caption: str) -> dict:
    try:
        data = _gql(_MUTATION, {"input": {
            "channelId": config.BUFFER_TIKTOK_CHANNEL_ID,
            "text": caption,
            "assets": assets,
            "mode": "shareNow",
            "schedulingType": "automatic",
            "source": "publicador",
        }})
    except Exception as e:  # noqa: BLE001
        raise BufferCreateError(str(e)) from e

    res = data["createPost"]
    if res["__typename"] != "PostActionSuccess":
        raise BufferCreateError(f"{res['__typename']}: {res.get('message')}")
    return res["post"]


def _aguardar(post_id: str, tentativas: int = 12, intervalo: int = 10) -> str:
    """Poll até sent/error. Também garante que a mídia já foi ingerida pelo Buffer
    antes do scheduler apagar o arquivo local."""
    status = "sending"
    for _ in range(tentativas):
        try:
            p = _gql(_QUERY_STATUS, {"input": {"id": post_id}}).get("post") or {}
            status = p.get("status", status)
        except Exception:  # noqa: BLE001
            pass  # transitório: tenta de novo
        if status in ("sent", "error"):
            return status
        time.sleep(intervalo)
    return status


def _publicar(assets: list[dict], caption: str) -> dict:
    post = _criar(assets, caption)
    status = _aguardar(post["id"])
    ok = status == "sent"
    return {
        "status": "ok" if ok else ("erro" if status == "error" else "processando"),
        "modo": "buffer-publico",
        "buffer_post_id": post["id"],
        "buffer_status": status,
    }


def publicar_video(url: str, caption: str) -> dict:
    """Vídeo → TikTok público via Buffer. `url` deve ser pública (PUBLIC_BASE_URL)."""
    return _publicar([{"video": {"url": url}}], caption)


def publicar_fotos(urls: list[str], caption: str) -> dict:
    """Foto/carrossel (1-4 imagens) → TikTok via Buffer."""
    if len(urls) > 4:
        urls = urls[:4]  # limite de imagens por post no TikTok via Buffer
    return _publicar([{"image": {"url": u}} for u in urls], caption)
