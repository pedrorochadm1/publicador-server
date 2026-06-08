"""Worker 24/7. A cada 30s checa a agenda e publica o que venceu — imediato."""
import os
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from . import config, db, publisher, token_store

_sched = BackgroundScheduler(timezone="UTC")
_MAX_TENTATIVAS = 3


def _limpar_arquivos(arquivos):
    """Apaga a mídia do servidor após publicar — hospedagem é só temporária."""
    for nome in arquivos:
        try:
            os.remove(os.path.join(config.IMG_DIR, nome))
        except OSError:
            pass


def _processar():
    agora = datetime.now(timezone.utc).isoformat()
    for post in db.pegar_vencidos(agora):
        if post["tentativas"] >= _MAX_TENTATIVAS:
            db.marcar(post["id"], "erro", erro=post.get("erro") or "Máximo de tentativas atingido.")
            continue
        db.marcar(post["id"], "publicando")
        try:
            ig_id = publisher.publicar(post["imagens"], post["caption"])
            db.marcar(post["id"], "publicado", ig_post_id=ig_id)
            _limpar_arquivos(post["imagens"])
            print(f"[scheduler] Post {post['id']} publicado: {ig_id}")
        except Exception as e:  # noqa: BLE001
            # volta para 'agendado' para tentar de novo no próximo ciclo
            db.marcar(post["id"], "agendado", erro=str(e))
            print(f"[scheduler] Post {post['id']} falhou (tentativa registrada): {e}")


def iniciar():
    _sched.add_job(_processar, "interval", seconds=30, id="publicar", max_instances=1, coalesce=True)
    _sched.add_job(token_store.renovar_se_necessario, "interval", hours=24, id="token")
    _sched.start()
    token_store.renovar_se_necessario()
    print("[scheduler] Iniciado.")


def parar():
    _sched.shutdown(wait=False)
