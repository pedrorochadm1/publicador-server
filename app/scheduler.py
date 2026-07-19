"""Worker 24/7. A cada 30s checa a agenda e publica o que venceu — imediato."""
import os
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from . import buffer_api, config, db, publisher, token_store, youtube, tiktok, repost

_sched = BackgroundScheduler(timezone="UTC")
_MAX_TENTATIVAS = 3


def _limpar_arquivos(arquivos):
    """Apaga a mídia do servidor após publicar — hospedagem é só temporária."""
    for nome in arquivos:
        try:
            os.remove(os.path.join(config.IMG_DIR, nome))
        except OSError:
            pass


def _eh_video(nome: str) -> bool:
    return nome.lower().endswith((".mp4", ".mov"))


def _fan_out_video(post: dict, caminho_video: str) -> dict:
    """Posta o vídeo no YouTube Shorts e no TikTok. Erro aqui não derruba o post (IG já saiu)."""
    extra = {}

    if post.get("youtube_title") and youtube.disponivel():
        try:
            vid = youtube.publicar_short(caminho_video, post["youtube_title"], post.get("youtube_description", ""))
            extra["youtube"] = {"status": "ok", "video_id": vid, "url": f"https://youtube.com/shorts/{vid}"}
        except Exception as e:  # noqa: BLE001
            extra["youtube"] = {"status": "erro", "motivo": str(e)}
            print(f"[scheduler] Post {post['id']} YouTube falhou: {e}")

    if post.get("tiktok_caption"):
        nome = os.path.basename(caminho_video)
        extra["tiktok"] = _tiktok_leg(
            post,
            via_buffer=lambda: buffer_api.publicar_video(
                f"{config.PUBLIC_BASE_URL}/img/{nome}", post["tiktok_caption"]
            ),
            via_inbox=lambda: tiktok.publicar(caminho_video, post["tiktok_caption"]),
        )

    return extra


def _tiktok_leg(post: dict, via_buffer, via_inbox) -> dict:
    """Perna do TikTok: Buffer (público) primeiro; inbox próprio só como fallback
    quando o Buffer nem chegou a criar o post (BufferCreateError = nada saiu)."""
    if buffer_api.disponivel():
        try:
            return via_buffer()
        except buffer_api.BufferCreateError as e:
            print(f"[scheduler] Post {post['id']} Buffer não criou o post ({e}); caindo pro inbox.")
        except Exception as e:  # noqa: BLE001
            # post pode ter sido criado no Buffer: NÃO cai no fallback (evita duplicar)
            print(f"[scheduler] Post {post['id']} TikTok via Buffer falhou: {e}")
            return {"status": "erro", "modo": "buffer-publico", "motivo": str(e)}
    if tiktok.disponivel():
        try:
            return via_inbox()
        except Exception as e:  # noqa: BLE001
            print(f"[scheduler] Post {post['id']} TikTok (inbox) falhou: {e}")
            return {"status": "erro", "modo": "rascunho", "motivo": str(e)}
    return {"status": "indisponivel", "motivo": "nem Buffer nem TikTok próprio configurados"}


def _fan_out_foto(post: dict, arquivos: list) -> dict:
    """Posta foto/carrossel no TikTok (Photo Mode). YouTube não faz foto."""
    extra = {}
    if post.get("tiktok_caption"):
        urls = [f"{config.PUBLIC_BASE_URL}/img/{n}" for n in arquivos]
        extra["tiktok"] = _tiktok_leg(
            post,
            via_buffer=lambda: buffer_api.publicar_fotos(urls, post["tiktok_caption"]),
            via_inbox=lambda: tiktok.publicar_foto(urls, post["tiktok_caption"]),
        )
    return extra


def _processar():
    agora = datetime.now(timezone.utc).isoformat()
    for post in db.pegar_vencidos(agora):
        if post["tentativas"] >= _MAX_TENTATIVAS:
            db.marcar(post["id"], "erro", erro=post.get("erro") or "Máximo de tentativas atingido.")
            continue
        db.marcar(post["id"], "publicando")
        try:
            arquivos = post["imagens"]

            # 1. Instagram — obrigatório, MENOS quando o reel já está no IG (repost de trial).
            if post.get("pular_instagram"):
                ig_id = None
                resultados = {"instagram": {"status": "pulado", "motivo": "reel já publicado (trial)"}}
            else:
                ig_id = publisher.publicar(arquivos, post["caption"], ig_trial=post.get("ig_trial", False))
                # marca já visto ANTES do fan-out: o ig_post_id só chega ao banco no fim,
                # e o auto-repost pode rodar nesse intervalo e duplicar o reel como "trial do app"
                db.marcar_reel_visto(ig_id, post_id=post["id"], motivo="pulado-nosso")
                resultados = {"instagram": {"status": "ok", "post_id": ig_id}}
                if post.get("ig_trial"):
                    resultados["instagram"]["modo"] = "trial"

            # 2. Fan-out pras outras plataformas
            if len(arquivos) == 1 and _eh_video(arquivos[0]):
                # vídeo → YouTube Shorts + TikTok
                caminho = os.path.join(config.IMG_DIR, arquivos[0])
                resultados.update(_fan_out_video(post, caminho))
            else:
                # foto/carrossel → TikTok Photo Mode (YouTube não faz foto)
                resultados.update(_fan_out_foto(post, arquivos))

            db.marcar(post["id"], "publicado", ig_post_id=ig_id, resultados=resultados)
            _limpar_arquivos(post["imagens"])
            print(f"[scheduler] Post {post['id']} publicado: {ig_id} | {resultados}")
        except Exception as e:  # noqa: BLE001
            # falha → volta para 'agendado' e tenta de novo no próximo ciclo
            db.marcar(post["id"], "agendado", erro=str(e))
            print(f"[scheduler] Post {post['id']} falhou (tentativa registrada): {e}")


def iniciar():
    _sched.add_job(_processar, "interval", seconds=30, id="publicar", max_instances=1, coalesce=True)
    _sched.add_job(token_store.renovar_se_necessario, "interval", hours=24, id="token")
    if config.REPOST_TRIALS:
        _sched.add_job(
            repost.rodar, "interval", minutes=config.REPOST_POLL_MINUTES,
            id="repost", max_instances=1, coalesce=True,
        )
        print(f"[scheduler] Auto-repost de trial reels ligado (a cada {config.REPOST_POLL_MINUTES} min).")
    _sched.start()
    token_store.renovar_se_necessario()
    print("[scheduler] Iniciado.")


def parar():
    _sched.shutdown(wait=False)
