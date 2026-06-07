import json
import sqlite3
from datetime import datetime, timezone

from . import config

_conn = None


def conn():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS posts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                criado_em   TEXT NOT NULL,
                publicar_em TEXT NOT NULL,      -- ISO 8601 em UTC
                caption     TEXT NOT NULL DEFAULT '',
                imagens     TEXT NOT NULL,      -- JSON: lista de nomes de arquivo
                status      TEXT NOT NULL DEFAULT 'agendado',
                ig_post_id  TEXT,
                erro        TEXT,
                tentativas  INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        _conn.commit()
    return _conn


def _row(r):
    d = dict(r)
    d["imagens"] = json.loads(d["imagens"])
    return d


def criar_post(publicar_em_utc: str, caption: str, imagens: list[str]) -> dict:
    c = conn()
    cur = c.execute(
        "INSERT INTO posts (criado_em, publicar_em, caption, imagens) VALUES (?, ?, ?, ?)",
        (
            datetime.now(timezone.utc).isoformat(),
            publicar_em_utc,
            caption,
            json.dumps(imagens),
        ),
    )
    c.commit()
    return get_post(cur.lastrowid)


def get_post(post_id: int) -> dict | None:
    r = conn().execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    return _row(r) if r else None


def listar(status: str | None = None) -> list[dict]:
    if status:
        rows = conn().execute(
            "SELECT * FROM posts WHERE status = ? ORDER BY publicar_em", (status,)
        ).fetchall()
    else:
        rows = conn().execute("SELECT * FROM posts ORDER BY publicar_em").fetchall()
    return [_row(r) for r in rows]


def pegar_vencidos(agora_utc: str) -> list[dict]:
    rows = conn().execute(
        "SELECT * FROM posts WHERE status = 'agendado' AND publicar_em <= ? ORDER BY publicar_em",
        (agora_utc,),
    ).fetchall()
    return [_row(r) for r in rows]


def marcar(post_id: int, status: str, ig_post_id: str | None = None, erro: str | None = None):
    c = conn()
    c.execute(
        "UPDATE posts SET status = ?, ig_post_id = COALESCE(?, ig_post_id), erro = ?, "
        "tentativas = tentativas + 1 WHERE id = ?",
        (status, ig_post_id, erro, post_id),
    )
    c.commit()


def cancelar(post_id: int) -> bool:
    c = conn()
    cur = c.execute(
        "UPDATE posts SET status = 'cancelado' WHERE id = ? AND status = 'agendado'",
        (post_id,),
    )
    c.commit()
    return cur.rowcount > 0
