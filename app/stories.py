"""Auto-repost de stories: todo story de VÍDEO novo vira Trial Reel + Shorts + TikTok.

O job roda no scheduler a cada STORIES_POLL_MINUTES. Fluxo por story novo:
  buscar (GET /stories) → baixar o vídeo → transcrever + gerar copy (copy_ia) →
  enfileirar um post trial pra publicar agora → o scheduler faz o fan-out.

Decisões (ver decisoes/registro.md do repo pedrorochadm1, 2026-07-03):
- Stories de "melhores amigos" NÃO aparecem nesta API — viram o filtro natural de
  "não republicar". Confirmado em spike.
- Não há filtro de música/link (a API não expõe). Pedro assume evitar story com link;
  música em Short <1min no máximo é mutada pelo Content ID, sem strike.
- IG entrega o story já em H.264, então não há transcode.
"""
import os
from datetime import datetime, timezone

import requests

from . import config, copy_ia, db
from .token_store import get_token

_MARCO = "stories_marco_inicial"


def _buscar_stories() -> list[dict]:
    r = requests.get(
        f"{config.GRAPH}/{config.INSTAGRAM_BUSINESS_ID}/stories",
        params={"fields": "id,media_type,media_url,timestamp", "access_token": get_token()},
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("data", [])


def _parse_ts(ts: str) -> datetime:
    # IG devolve offset no formato "+0000"; normaliza pro fromisoformat.
    return datetime.fromisoformat(ts.replace("+0000", "+00:00"))


def _baixar(media_url: str, story_id: str) -> str:
    """Baixa o vídeo do story para o IMG_DIR e devolve o nome do arquivo."""
    nome = f"story_{story_id}.mp4"
    destino = os.path.join(config.IMG_DIR, nome)
    r = requests.get(media_url, timeout=120)
    r.raise_for_status()
    with open(destino, "wb") as f:
        f.write(r.content)
    return nome


def _processar(story: dict):
    story_id = story["id"]
    nome = _baixar(story["media_url"], story_id)
    caminho = os.path.join(config.IMG_DIR, nome)
    try:
        copy = copy_ia.gerar_do_video(caminho)
    except Exception:
        # não deixa lixo se a geração falhar; o story fica pra reprocessar no próximo ciclo
        try:
            os.remove(caminho)
        except OSError:
            pass
        raise

    agora = datetime.now(timezone.utc).isoformat()
    post = db.criar_post(
        publicar_em_utc=agora,
        caption=copy["caption_ig"],
        imagens=[nome],
        youtube_title=copy["youtube_title"],
        youtube_description=copy["youtube_description"],
        tiktok_caption=copy["tiktok_caption"],
        trial=True,
    )
    db.marcar_story_visto(story_id, post_id=post["id"], motivo="republicado")
    print(f"[stories] Story {story_id} enfileirado como post trial {post['id']}: {copy['caption_ig']!r}")


def rodar():
    """Chamado pelo scheduler. Republica os stories de vídeo publicados após o marco inicial."""
    if not config.STORIES_AUTO_REPOST:
        return
    if not copy_ia.disponivel():
        print("[stories] OPENAI_API_KEY ausente — auto-repost pausado.")
        return

    # Na primeira execução, fixa o marco e NÃO republica o que já estava no ar.
    marco = db.get_meta(_MARCO)
    if marco is None:
        db.set_meta(_MARCO, datetime.now(timezone.utc).isoformat())
        print("[stories] Marco inicial definido. A partir de agora, só stories novos são republicados.")
        return
    marco_dt = _parse_ts(marco)

    try:
        stories = _buscar_stories()
    except Exception as e:  # noqa: BLE001
        print(f"[stories] Falha ao buscar stories: {e}")
        return

    for s in stories:
        sid = s.get("id")
        if not sid or db.story_ja_visto(sid):
            continue
        if s.get("media_type") != "VIDEO":
            db.marcar_story_visto(sid, motivo="pulado-nao-video")
            continue
        if not s.get("timestamp") or _parse_ts(s["timestamp"]) <= marco_dt:
            db.marcar_story_visto(sid, motivo="anterior-ao-marco")
            continue
        try:
            _processar(s)
        except Exception as e:  # noqa: BLE001
            # não marca visto: tenta de novo no próximo ciclo (story dura 24h)
            print(f"[stories] Falha ao processar story {sid}: {e}")
