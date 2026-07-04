"""Auto-repost de Trial Reels: o reel de teste que o Pedro publica no app vira
TikTok + YouTube Shorts. NÃO republica no IG (o reel já está lá).

O job roda no scheduler a cada REPOST_POLL_MINUTES. Fluxo por reel novo:
  buscar (GET /media) → é nosso? (já foi pelo sistema, pula) → baixar → transcrever
  + gerar SEO do YouTube → enfileirar post "pular_instagram" → o scheduler faz o
  fan-out só pro TikTok e YouTube.

Como distinguir o trial (do app) de um reel que o próprio sistema publicou: os que o
sistema publicou têm o ig_post_id salvo no banco. Todo reel novo que NÃO está no banco
é um trial feito no app → repostar. (Confirmado em spike 2026-07-04: trial reel aparece
no GET /media como product=REELS, com media_url e caption.)
"""
import os
from datetime import datetime, timezone

import requests

from . import config, copy_ia, db
from .token_store import get_token

_MARCO = "repost_marco_inicial"


def _buscar_reels() -> list[dict]:
    r = requests.get(
        f"{config.GRAPH}/{config.INSTAGRAM_BUSINESS_ID}/media",
        params={
            "fields": "id,media_type,media_product_type,media_url,caption,timestamp",
            "limit": 10,
            "access_token": get_token(),
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("data", [])


def _parse_ts(ts: str) -> datetime:
    # IG devolve offset no formato "+0000"; normaliza pro fromisoformat.
    return datetime.fromisoformat(ts.replace("+0000", "+00:00"))


def _eh_reel(m: dict) -> bool:
    return m.get("media_type") == "VIDEO" and m.get("media_product_type") == "REELS"


def _baixar(media_url: str, media_id: str) -> str:
    """Baixa o vídeo do reel para o IMG_DIR e devolve o nome do arquivo."""
    nome = f"trial_{media_id}.mp4"
    r = requests.get(media_url, timeout=120)
    r.raise_for_status()
    with open(os.path.join(config.IMG_DIR, nome), "wb") as f:
        f.write(r.content)
    return nome


def _processar(reel: dict):
    media_id = reel["id"]
    legenda = (reel.get("caption") or "").strip()
    nome = _baixar(reel["media_url"], media_id)
    caminho = os.path.join(config.IMG_DIR, nome)
    try:
        seo = copy_ia.gerar_youtube_seo(copy_ia.transcrever(caminho), legenda)
    except Exception:
        try:
            os.remove(caminho)
        except OSError:
            pass
        raise

    post = db.criar_post(
        publicar_em_utc=datetime.now(timezone.utc).isoformat(),
        caption=legenda,                       # não vai pro IG (pular_instagram), fica de registro
        imagens=[nome],
        youtube_title=seo["youtube_title"],
        youtube_description=seo["youtube_description"],
        tiktok_caption=legenda,                # TikTok = legenda idêntica à do trial (regra de voz)
        pular_instagram=True,                  # o reel já está no IG; só fan-out
    )
    db.marcar_reel_visto(media_id, post_id=post["id"], motivo="repostado")
    print(f"[repost] Trial {media_id} enfileirado como post {post['id']} (só TikTok+Shorts): {legenda!r}")


def rodar():
    """Chamado pelo scheduler. Reposta os trial reels novos (publicados após o marco)."""
    if not config.REPOST_TRIALS:
        return
    if not copy_ia.disponivel():
        print("[repost] OPENAI_API_KEY ausente — auto-repost pausado.")
        return

    # Na primeira execução, fixa o marco e NÃO reposta o que já estava no ar.
    marco = db.get_meta(_MARCO)
    if marco is None:
        db.set_meta(_MARCO, datetime.now(timezone.utc).isoformat())
        print("[repost] Marco inicial definido. A partir de agora, só trial reels novos são repostados.")
        return
    marco_dt = _parse_ts(marco)

    try:
        reels = _buscar_reels()
    except Exception as e:  # noqa: BLE001
        print(f"[repost] Falha ao buscar reels: {e}")
        return

    for m in reels:
        mid = m.get("id")
        if not mid or db.reel_ja_visto(mid):
            continue
        if not _eh_reel(m):
            db.marcar_reel_visto(mid, motivo="pulado-nao-reel")
            continue
        if not m.get("timestamp") or _parse_ts(m["timestamp"]) <= marco_dt:
            db.marcar_reel_visto(mid, motivo="anterior-ao-marco")
            continue
        if db.post_por_ig_id(mid):
            # foi o próprio sistema que publicou → já foi pro TikTok/Shorts no fan-out
            db.marcar_reel_visto(mid, motivo="pulado-nosso")
            continue
        try:
            _processar(m)
        except Exception as e:  # noqa: BLE001
            # não marca visto: tenta de novo no próximo ciclo
            print(f"[repost] Falha ao processar trial {mid}: {e}")
