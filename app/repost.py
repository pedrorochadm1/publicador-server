"""Auto-repost do que Pedro publica pelo app do IG (decisões 2026-07-04 e 2026-07-19):
  - reel (trial ou normal)  → TikTok + YouTube Shorts
  - foto / carrossel        → TikTok Photo Mode (YouTube não faz foto)
NÃO republica no IG (o post já está lá).

O job roda no scheduler a cada REPOST_POLL_MINUTES. Fluxo por mídia nova:
  buscar (GET /media) → é nossa? (já foi pelo sistema, pula) → baixar →
  (reel: transcrever + gerar SEO do YouTube) → enfileirar post "pular_instagram" →
  o scheduler faz o fan-out pras outras plataformas.

Como distinguir o post do app de um que o próprio sistema publicou: os do sistema
têm o ig_post_id salvo no banco. Toda mídia nova que NÃO está no banco veio do app
→ repostar. (Confirmado em spike 2026-07-04 pra reels; GET /media também lista
IMAGE e CAROUSEL_ALBUM com media_url/children.)

Regras de pulo: carrossel com vídeo no meio não vai (TikTok Photo Mode é só imagem);
foto/carrossel sem legenda não vai (a legenda do TikTok é a mesma do IG).
"""
import os
from datetime import datetime, timezone

import requests

from . import config, copy_ia, db
from .token_store import get_token

_MARCO = "repost_marco_inicial"


def _buscar_midias() -> list[dict]:
    r = requests.get(
        f"{config.GRAPH}/{config.INSTAGRAM_BUSINESS_ID}/media",
        params={
            "fields": "id,media_type,media_product_type,media_url,caption,timestamp,"
                      "children{media_url,media_type}",
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


def _eh_foto(m: dict) -> bool:
    return m.get("media_type") == "IMAGE"


def _eh_carrossel(m: dict) -> bool:
    return m.get("media_type") == "CAROUSEL_ALBUM"


def _baixar(media_url: str, nome: str) -> str:
    """Baixa a mídia para o IMG_DIR e devolve o nome do arquivo."""
    r = requests.get(media_url, timeout=120)
    r.raise_for_status()
    with open(os.path.join(config.IMG_DIR, nome), "wb") as f:
        f.write(r.content)
    return nome


def _processar_reel(reel: dict):
    media_id = reel["id"]
    legenda = (reel.get("caption") or "").strip()
    nome = _baixar(reel["media_url"], f"trial_{media_id}.mp4")
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
        # TikTok do repost é opt-in: Pedro posta à mão com o original do Edits
        tiktok_caption=legenda if config.REPOST_TIKTOK else "",
        pular_instagram=True,                  # o reel já está no IG; só fan-out
    )
    db.marcar_reel_visto(media_id, post_id=post["id"], motivo="repostado")
    destino = "TikTok+Shorts" if config.REPOST_TIKTOK else "só Shorts"
    print(f"[repost] Reel {media_id} enfileirado como post {post['id']} ({destino}): {legenda!r}")


def _urls_de_imagem(m: dict) -> list[str] | None:
    """URLs das imagens da foto/carrossel; None se o carrossel tiver vídeo no meio."""
    if _eh_foto(m):
        return [m["media_url"]] if m.get("media_url") else None
    filhos = (m.get("children") or {}).get("data", [])
    if not filhos or any(f.get("media_type") != "IMAGE" for f in filhos):
        return None
    return [f["media_url"] for f in filhos if f.get("media_url")]


def _processar_foto(m: dict):
    media_id = m["id"]
    legenda = (m.get("caption") or "").strip()
    if not legenda:
        db.marcar_reel_visto(media_id, motivo="pulado-sem-legenda")
        return
    urls = _urls_de_imagem(m)
    if not urls:
        db.marcar_reel_visto(media_id, motivo="pulado-carrossel-com-video")
        return

    nomes = [
        _baixar(u, f"repost_{media_id}_{i}.jpg")
        for i, u in enumerate(urls)
    ]
    post = db.criar_post(
        publicar_em_utc=datetime.now(timezone.utc).isoformat(),
        caption=legenda,                       # registro; não vai pro IG (pular_instagram)
        imagens=nomes,
        tiktok_caption=legenda,                # TikTok = legenda idêntica à do IG (regra de voz)
        pular_instagram=True,
    )
    db.marcar_reel_visto(media_id, post_id=post["id"], motivo="repostado")
    print(f"[repost] Foto/carrossel {media_id} enfileirado como post {post['id']} (só TikTok): {legenda!r}")


def rodar():
    """Chamado pelo scheduler. Reposta as mídias novas do app (publicadas após o marco)."""
    if not config.REPOST_TRIALS:
        return

    # Na primeira execução, fixa o marco e NÃO reposta o que já estava no ar.
    marco = db.get_meta(_MARCO)
    if marco is None:
        db.set_meta(_MARCO, datetime.now(timezone.utc).isoformat())
        print("[repost] Marco inicial definido. A partir de agora, só mídias novas são repostadas.")
        return
    marco_dt = _parse_ts(marco)

    try:
        midias = _buscar_midias()
    except Exception as e:  # noqa: BLE001
        print(f"[repost] Falha ao buscar mídias: {e}")
        return

    for m in midias:
        mid = m.get("id")
        if not mid or db.reel_ja_visto(mid):
            continue
        if not (_eh_reel(m) or _eh_foto(m) or _eh_carrossel(m)):
            db.marcar_reel_visto(mid, motivo="pulado-nao-repostavel")
            continue
        if not m.get("timestamp") or _parse_ts(m["timestamp"]) <= marco_dt:
            db.marcar_reel_visto(mid, motivo="anterior-ao-marco")
            continue
        if db.post_por_ig_id(mid):
            # foi o próprio sistema que publicou → já foi pro fan-out na publicação
            db.marcar_reel_visto(mid, motivo="pulado-nosso")
            continue
        try:
            if _eh_reel(m):
                if not copy_ia.disponivel():
                    # sem OPENAI_API_KEY não dá pra gerar o SEO do YouTube;
                    # não marca visto: tenta de novo quando a key voltar
                    print(f"[repost] OPENAI_API_KEY ausente — reel {mid} aguardando.")
                    continue
                _processar_reel(m)
            elif not config.REPOST_TIKTOK:
                # foto/carrossel só teria o TikTok como destino (YouTube não faz foto)
                db.marcar_reel_visto(mid, motivo="pulado-tiktok-manual")
            else:
                _processar_foto(m)
        except Exception as e:  # noqa: BLE001
            # não marca visto: tenta de novo no próximo ciclo
            print(f"[repost] Falha ao processar mídia {mid}: {e}")
