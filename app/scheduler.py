"""Worker 24/7. A cada 30s checa a agenda e publica o que venceu — imediato."""
import os
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from . import config, db, publisher, token_store, youtube, tiktok, stories

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

    if post.get("tiktok_caption") and tiktok.disponivel():
        try:
            extra["tiktok"] = tiktok.publicar(caminho_video, post["tiktok_caption"])
        except Exception as e:  # noqa: BLE001
            extra["tiktok"] = {"status": "erro", "motivo": str(e)}
            print(f"[scheduler] Post {post['id']} TikTok falhou: {e}")

    return extra


def _fan_out_foto(post: dict, arquivos: list) -> dict:
    """Posta foto/carrossel no TikTok (Photo Mode). YouTube não faz foto."""
    extra = {}
    if post.get("tiktok_caption") and tiktok.disponivel():
        urls = [f"{config.PUBLIC_BASE_URL}/img/{n}" for n in arquivos]
        try:
            extra["tiktok"] = tiktok.publicar_foto(urls, post["tiktok_caption"])
        except Exception as e:  # noqa: BLE001
            extra["tiktok"] = {"status": "erro", "motivo": str(e)}
            print(f"[scheduler] Post {post['id']} TikTok (foto) falhou: {e}")
    return extra


def _processar():
    agora = datetime.now(timezone.utc).isoformat()
    for post in db.pegar_vencidos(agora):
        if post["tentativas"] >= _MAX_TENTATIVAS:
            db.marcar(post["id"], "erro", erro=post.get("erro") or "Máximo de tentativas atingido.")
            continue
        db.marcar(post["id"], "publicando")
        try:
            # 1. Instagram (obrigatório — define sucesso/retry do post)
            ig_id = publisher.publicar(post["imagens"], post["caption"], trial=post.get("trial", False))
            resultados = {"instagram": {"status": "ok", "post_id": ig_id}}

            # 2. Fan-out pras outras plataformas
            arquivos = post["imagens"]
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
            # falha no Instagram → volta para 'agendado' e tenta de novo no próximo ciclo
            db.marcar(post["id"], "agendado", erro=str(e))
            print(f"[scheduler] Post {post['id']} falhou (tentativa registrada): {e}")


def iniciar():
    _sched.add_job(_processar, "interval", seconds=30, id="publicar", max_instances=1, coalesce=True)
    _sched.add_job(token_store.renovar_se_necessario, "interval", hours=24, id="token")
    if config.STORIES_AUTO_REPOST:
        _sched.add_job(
            stories.rodar, "interval", minutes=config.STORIES_POLL_MINUTES,
            id="stories", max_instances=1, coalesce=True,
        )
        print(f"[scheduler] Auto-repost de stories ligado (a cada {config.STORIES_POLL_MINUTES} min).")
    _sched.start()
    token_store.renovar_se_necessario()
    print("[scheduler] Iniciado.")


def parar():
    _sched.shutdown(wait=False)
