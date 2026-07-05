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
                tentativas  INTEGER NOT NULL DEFAULT 0,
                youtube_title       TEXT NOT NULL DEFAULT '',
                youtube_description TEXT NOT NULL DEFAULT '',
                tiktok_caption      TEXT NOT NULL DEFAULT '',
                resultados          TEXT,     -- JSON: status por plataforma
                pular_instagram     INTEGER NOT NULL DEFAULT 0  -- 1 = só fan-out (reel já está no IG)
            )
            """
        )
        # Migração para bancos criados antes das colunas multi-plataforma
        cols = {r[1] for r in _conn.execute("PRAGMA table_info(posts)").fetchall()}
        for nova in ("youtube_title", "youtube_description", "tiktok_caption"):
            if nova not in cols:
                _conn.execute(f"ALTER TABLE posts ADD COLUMN {nova} TEXT NOT NULL DEFAULT ''")
        if "resultados" not in cols:
            _conn.execute("ALTER TABLE posts ADD COLUMN resultados TEXT")
        if "pular_instagram" not in cols:
            _conn.execute("ALTER TABLE posts ADD COLUMN pular_instagram INTEGER NOT NULL DEFAULT 0")
        # Controle do auto-repost: quais reels já foram processados (não repostar o mesmo)
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reels_vistos (
                media_id  TEXT PRIMARY KEY,
                visto_em  TEXT NOT NULL,
                post_id   INTEGER,
                motivo    TEXT          -- 'repostado' ou por que foi pulado
            )
            """
        )
        # Chave-valor genérico (ex.: marco inicial do auto-repost)
        _conn.execute(
            "CREATE TABLE IF NOT EXISTS meta (chave TEXT PRIMARY KEY, valor TEXT NOT NULL)"
        )
        _conn.commit()
    return _conn


def _row(r):
    d = dict(r)
    d["imagens"] = json.loads(d["imagens"])
    if d.get("resultados"):
        d["resultados"] = json.loads(d["resultados"])
    # bancos antigos ainda têm a coluna 'trial' (desenho descontinuado); não expor.
    d.pop("trial", None)
    d["pular_instagram"] = bool(d.get("pular_instagram"))
    return d


def criar_post(
    publicar_em_utc: str,
    caption: str,
    imagens: list[str],
    youtube_title: str = "",
    youtube_description: str = "",
    tiktok_caption: str = "",
    pular_instagram: bool = False,
) -> dict:
    c = conn()
    cur = c.execute(
        "INSERT INTO posts (criado_em, publicar_em, caption, imagens, "
        "youtube_title, youtube_description, tiktok_caption, pular_instagram) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            datetime.now(timezone.utc).isoformat(),
            publicar_em_utc,
            caption,
            json.dumps(imagens),
            youtube_title,
            youtube_description,
            tiktok_caption,
            1 if pular_instagram else 0,
        ),
    )
    c.commit()
    return get_post(cur.lastrowid)


def post_por_ig_id(ig_post_id: str) -> dict | None:
    """Post que ESTE servidor publicou com esse ig_post_id (pra saber se um reel é nosso)."""
    r = conn().execute("SELECT * FROM posts WHERE ig_post_id = ?", (ig_post_id,)).fetchone()
    return _row(r) if r else None


# ─── Auto-repost de trial reels ───

def reel_ja_visto(media_id: str) -> bool:
    return conn().execute(
        "SELECT 1 FROM reels_vistos WHERE media_id = ?", (media_id,)
    ).fetchone() is not None


def marcar_reel_visto(media_id: str, post_id: int | None = None, motivo: str = "repostado"):
    c = conn()
    c.execute(
        "INSERT OR IGNORE INTO reels_vistos (media_id, visto_em, post_id, motivo) VALUES (?, ?, ?, ?)",
        (media_id, datetime.now(timezone.utc).isoformat(), post_id, motivo),
    )
    c.commit()


def get_meta(chave: str) -> str | None:
    r = conn().execute("SELECT valor FROM meta WHERE chave = ?", (chave,)).fetchone()
    return r["valor"] if r else None


def set_meta(chave: str, valor: str):
    c = conn()
    c.execute(
        "INSERT INTO meta (chave, valor) VALUES (?, ?) "
        "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
        (chave, valor),
    )
    c.commit()


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


def marcar(post_id: int, status: str, ig_post_id: str | None = None, erro: str | None = None,
           resultados: dict | None = None):
    c = conn()
    c.execute(
        "UPDATE posts SET status = ?, ig_post_id = COALESCE(?, ig_post_id), erro = ?, "
        "resultados = COALESCE(?, resultados), tentativas = tentativas + 1 WHERE id = ?",
        (status, ig_post_id, erro, json.dumps(resultados) if resultados is not None else None, post_id),
    )
    c.commit()


def reabrir(post_id: int):
    """Reseta um post para 'agendado' agora, zerando tentativas e erro."""
    c = conn()
    c.execute(
        "UPDATE posts SET status = 'agendado', tentativas = 0, erro = NULL, publicar_em = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), post_id),
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


def remover(post_id: int) -> bool:
    """Apaga de vez um post — só se estiver em 'erro' ou 'cancelado' (nunca publicado)."""
    c = conn()
    cur = c.execute(
        "DELETE FROM posts WHERE id = ? AND status IN ('erro', 'cancelado')",
        (post_id,),
    )
    c.commit()
    return cur.rowcount > 0


def reagendar(post_id: int, novo_publicar_em_utc: str, caption: str | None = None) -> bool:
    """Troca o horário (e opcionalmente a legenda) de um post sem reenviar a mídia.
    Reabre posts 'agendado', 'cancelado' ou 'erro' para 'agendado'."""
    sets = "status = 'agendado', tentativas = 0, erro = NULL, publicar_em = ?"
    params: list = [novo_publicar_em_utc]
    if caption is not None:
        sets += ", caption = ?"
        params.append(caption)
    params.append(post_id)
    c = conn()
    cur = c.execute(
        f"UPDATE posts SET {sets} WHERE id = ? AND status IN ('agendado', 'cancelado', 'erro')",
        params,
    )
    c.commit()
    return cur.rowcount > 0
