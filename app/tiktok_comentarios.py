"""Perna do TikTok das automações de comentário (via Zernio, plano gratuito).

O TikTok não deixa mandar direct a partir de comentário no Brasil, então aqui a
automação só RESPONDE EM PÚBLICO, mandando a pessoa tocar no link da bio. Nunca
vai link na resposta (regra do Pedro, ver feedback_nunca_link_no_comentario).

Por que Zernio: o app deles é o app Business do TikTok, já auditado, com leitura e
resposta de comentário liberadas. O nosso app próprio só tem Content Posting. O
Zernio não cobra nada pelas 2 primeiras contas, e ler e responder comentário também
é de graça. A lógica toda roda aqui no servidor: as "automações" do Zernio, que têm
cota, nem são usadas.

Duas formas de pegar o comentário novo (as duas caem em tratar()):
  1. WEBHOOK comment.received → POST /webhook/zernio (assinado com HMAC).
  2. POLLING a cada TIKTOK_POLL_SEGUNDOS nos vídeos mais recentes (rede de segurança).

Tudo vive numa tabela própria (tiktok_eventos). A fila do Instagram (automacao_eventos)
nunca vê comentário do TikTok, senão o marca-passo tentaria mandar direct do IG.
"""
import hashlib
import hmac
import json
import random
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import requests

from . import automacoes, config, db

BASE = "https://zernio.com/api/v1"

CHAVE_API = "zernio_api_key"
CHAVE_PERFIL = "zernio_profile_id"
CHAVE_SEGREDO = "zernio_webhook_secret"
CHAVE_WEBHOOK = "zernio_webhook_id"

# Quantos vídeos recentes a varredura olha e até onde volta no tempo
VIDEOS_POR_RODADA = 12
PAGINAS_POR_VIDEO = 4
JANELA_DIAS = 3
# Ritmo da resposta pública: muita resposta igual em rajada é o que o TikTok lê como spam
ESPACO_MIN_S = 25
TETO_HORA = 80
MAX_TENTATIVAS = 5

# As 10 variações aprovadas na conversa de 2026-11-19. Valem quando a automação não
# tem respostas próprias do TikTok.
RESPOSTAS_PADRAO = [
    "Não consigo te mandar no privado por aqui. O link tá na minha bio, consegue tocar lá?",
    "O TikTok não me deixa mandar link no direct. Tá na bio, toca lá.",
    "Por aqui não dá pra mandar no privado. Deixei o link na bio pra você.",
    "Não consigo enviar no direct, mas é só tocar no link da bio.",
    "O link tá na bio. Se não abrir, me fala aqui.",
    "Tá na bio. Entra no meu perfil e toca no link.",
    "O TikTok não deixa eu mandar link no privado. Corre na bio que tá lá.",
    "Link na bio. Consegue abrir por lá?",
    "Toca no link da minha bio, tá tudo lá.",
    "Não tenho como te mandar no privado aqui. O caminho é o link da bio.",
]

_conta_cache: dict = {}
_local = threading.local()


def _c() -> sqlite3.Connection:
    """Conexão PRÓPRIA por thread. A conexão compartilhada do db.py, usada ao mesmo
    tempo pelos jobs daqui, pelo marca-passo e pelas rotas, deu 'bad parameter or
    other API misuse' e travou o servidor inteiro no primeiro deploy (2026-11-19)."""
    c = getattr(_local, "c", None)
    if c is None:
        c = sqlite3.connect(config.DB_PATH, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=10000")
        _local.c = c
    return c


def _meta(chave: str) -> str:
    r = _c().execute("SELECT valor FROM meta WHERE chave = ?", (chave,)).fetchone()
    return (r["valor"] if r else "") or ""


def _set_meta(chave: str, valor: str):
    c = _c()
    c.execute("INSERT INTO meta (chave, valor) VALUES (?, ?) "
              "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor", (chave, valor))
    c.commit()
_ultimo_erro: dict = {"rodada": "", "envio": ""}


# ─────────────────────────── Banco ───────────────────────────

_iniciado = False


def _init():
    global _iniciado
    if _iniciado:
        return
    c = _c()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS tiktok_eventos (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            automacao_id INTEGER NOT NULL,
            comment_id   TEXT NOT NULL UNIQUE,
            video_id     TEXT NOT NULL,
            autor_id     TEXT,
            usuario      TEXT,
            texto        TEXT,
            resposta     TEXT,
            status       TEXT NOT NULL DEFAULT 'na-fila',  -- na-fila | ok | erro | desistiu | velho
            erro         TEXT,
            tentativas   INTEGER NOT NULL DEFAULT 0,
            quando       TEXT NOT NULL,     -- hora do comentário
            enviado_em   TEXT
        )
        """
    )
    c.execute("CREATE INDEX IF NOT EXISTS ix_tt_status ON tiktok_eventos (status)")
    c.commit()
    _iniciado = True


def chave_api() -> str:
    return getattr(config, "ZERNIO_API_KEY", "") or _meta(CHAVE_API) or ""


def gravar_chave(chave: str):
    chave = (chave or "").strip()
    if not chave.startswith("sk_"):
        raise ValueError("A chave do Zernio começa com sk_.")
    _conta_cache.clear()
    antes = _meta(CHAVE_API)
    _set_meta(CHAVE_API, chave)
    try:
        _get("/profiles")
    except Exception:
        _set_meta(CHAVE_API, antes or "")
        raise
    if antes != chave:          # chave nova = conta Zernio nova: perfil e webhook são outros
        _set_meta(CHAVE_PERFIL, "")
        _set_meta(CHAVE_WEBHOOK, "")


# ─────────────────────────── Cliente Zernio ───────────────────────────

def _h() -> dict:
    return {"Authorization": f"Bearer {chave_api()}", "Content-Type": "application/json"}


def _checar(r: requests.Response) -> dict:
    try:
        corpo = r.json()
    except ValueError:
        corpo = {"bruto": r.text[:300]}
    if r.status_code >= 400:
        raise RuntimeError(f"Zernio {r.status_code}: {json.dumps(corpo, ensure_ascii=False)[:400]}")
    return corpo


def _get(rota: str, **params) -> dict:
    return _checar(requests.get(BASE + rota, headers=_h(), params=params, timeout=30))


def _post(rota: str, corpo: dict, idem: str = "") -> dict:
    h = _h()
    if idem:
        h["Idempotency-Key"] = idem
    return _checar(requests.post(BASE + rota, headers=h, json=corpo, timeout=30))


def perfil_id() -> str:
    pid = _meta(CHAVE_PERFIL)
    if pid:
        return pid
    perfis = _get("/profiles").get("profiles") or []
    pid = (perfis[0] if perfis else _post("/profiles", {"name": "Pedro Rocha"})["profile"])["_id"]
    _set_meta(CHAVE_PERFIL, pid)
    return pid


def conta() -> dict | None:
    """A conta do TikTok conectada no Zernio, ou None se ainda não conectou."""
    if _conta_cache.get("expira", datetime.min.replace(tzinfo=timezone.utc)) > datetime.now(timezone.utc):
        return _conta_cache.get("conta")
    contas = _get("/accounts", profileId=perfil_id(), platform="tiktok").get("accounts") or []
    contas = [a for a in contas if (a.get("status") or "connected") != "disconnected"] or contas
    c = contas[0] if contas else None
    if c:
        c = {"id": c.get("_id") or c.get("id"),
             "usuario": c.get("username") or c.get("displayName") or c.get("name") or ""}
    _conta_cache.update(conta=c, expira=datetime.now(timezone.utc) + timedelta(minutes=10 if c else 1))
    return c


def url_conectar(voltar_para: str) -> str:
    r = _get("/connect/tiktok", profileId=perfil_id(), redirect_url=voltar_para)
    _conta_cache.clear()
    return r.get("authUrl") or r.get("url") or ""


def garantir_webhook() -> dict:
    """Cadastra o webhook comment.received apontando pra cá (uma vez só)."""
    if _meta(CHAVE_WEBHOOK):
        return {"ok": True, "id": _meta(CHAVE_WEBHOOK)}
    base = config.PUBLIC_BASE_URL or "https://insta.pedrorochadm1.com"
    url = f"{base}/webhook/zernio"
    segredo = _meta(CHAVE_SEGREDO) or secrets.token_hex(24)
    _set_meta(CHAVE_SEGREDO, segredo)
    try:
        existentes = _get("/webhooks/settings")
        lista = existentes.get("webhooks") or existentes.get("data") or []
        for w in lista if isinstance(lista, list) else []:
            if w.get("url") == url:
                # já existe, mas o segredo pode ser outro: some e recria abaixo não dá sem
                # o endpoint de update confirmado, então só registra o id e segue
                _set_meta(CHAVE_WEBHOOK, w.get("_id", "existente"))
                return {"ok": True, "id": w.get("_id"), "aviso": "webhook já existia"}
    except Exception:  # noqa: BLE001 — listar é só pra não duplicar
        pass
    r = _post("/webhooks/settings", {
        "name": "publicador comentarios tiktok", "url": url, "secret": segredo,
        "events": ["comment.received"], "profileIds": [perfil_id()],
    })
    wid = (r.get("webhook") or {}).get("_id", "criado")
    _set_meta(CHAVE_WEBHOOK, wid)
    return {"ok": True, "id": wid}


def assinatura_ok(corpo: bytes, assinatura: str) -> bool:
    segredo = _meta(CHAVE_SEGREDO) or ""
    if not segredo or not assinatura:
        return False
    esperado = hmac.new(segredo.encode(), corpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, assinatura.strip().lower())


# ─────────────────────────── Automações que valem no TikTok ───────────────────────────

def _auto(aid: int) -> dict | None:
    r = _c().execute("SELECT * FROM automacoes WHERE id = ?", (aid,)).fetchone()
    return automacoes._row(r) if r else None


def _ativas() -> list[dict]:
    try:
        rows = _c().execute("SELECT * FROM automacoes WHERE ativa = 1 AND tiktok = 1 ORDER BY id DESC").fetchall()
    except sqlite3.OperationalError:
        return []           # coluna tiktok ainda não migrada (automacoes._init roda na subida)
    return [automacoes._row(r) for r in rows]


def _respostas(a: dict) -> list[str]:
    proprias = [r for r in (a.get("respostas_tiktok") or []) if r.strip()]
    # cinto e suspensório: resposta com link nunca sai, mesmo que entre pelo banco
    return [r for r in (proprias or RESPOSTAS_PADRAO) if "http" not in r.lower() and "www." not in r.lower()]


def _parse(ts) -> datetime | None:
    if not ts:
        return None
    try:
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts / (1000 if ts > 1e12 else 1), tz=timezone.utc)
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def tratar(video_id: str, c: dict, conta_tt: dict) -> int:
    """Um comentário normalizado: {id, texto, autor_id, usuario, quando, eh_resposta, meu}.
    Enfileira em toda automação que casar. Devolve quantas acionou."""
    if c["eh_resposta"] or c["meu"] or not c["id"]:
        return 0
    if conta_tt.get("usuario") and c["usuario"] and c["usuario"].lower() == conta_tt["usuario"].lower():
        return 0
    quando = c["quando"] or datetime.now(timezone.utc)
    if datetime.now(timezone.utc) - quando > timedelta(days=JANELA_DIAS):
        return 0
    _init()
    acionou = 0
    for a in _ativas():
        desde = _parse(a.get("tiktok_desde"))
        if desde and quando < desde:
            continue
        if not automacoes.casa(c["texto"], a["palavras"], a["modo"]):
            continue
        if c["texto"].strip() in {r.strip() for r in _respostas(a)}:
            continue              # a nossa própria resposta voltando como comentário
        pessoa = c["autor_id"] or c["usuario"]
        if a["uma_vez_por_pessoa"] and pessoa and _c().execute(
            "SELECT 1 FROM tiktok_eventos WHERE automacao_id = ? AND (autor_id = ? OR usuario = ?) "
            "AND status IN ('na-fila', 'ok')", (a["id"], pessoa, pessoa),
        ).fetchone():
            continue
        try:
            conn = _c()
            conn.execute(
                "INSERT INTO tiktok_eventos (automacao_id, comment_id, video_id, autor_id, usuario, texto, quando) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (a["id"], c["id"], video_id, c["autor_id"], c["usuario"], c["texto"], quando.isoformat()),
            )
            conn.commit()
            acionou += 1
            break                 # um comentário, uma resposta: a primeira automação que casa leva
        except sqlite3.IntegrityError:
            break                 # já está na fila (webhook e varredura chegaram juntos)
    return acionou


def _normalizar_rest(c: dict) -> dict:
    autor = c.get("from") or {}
    return {
        "id": str(c.get("id") or ""),
        "texto": c.get("message") or c.get("text") or "",
        "autor_id": str(autor.get("id") or ""),
        "usuario": autor.get("username") or autor.get("name") or "",
        "quando": _parse(c.get("createdTime") or c.get("createdAt")),
        "eh_resposta": bool(c.get("parentId")),
        "meu": bool(autor.get("isOwner")),
    }


def _normalizar_webhook(c: dict) -> dict:
    autor = c.get("author") or {}
    return {
        "id": str(c.get("id") or ""),
        "texto": c.get("text") or c.get("message") or "",
        "autor_id": str(autor.get("id") or ""),
        "usuario": autor.get("username") or "",
        "quando": _parse(c.get("createdAt")),
        "eh_resposta": bool(c.get("isReply") or c.get("parentCommentId")),
        "meu": bool(autor.get("isOwnAccount")),
    }


def tratar_webhook(payload: dict) -> int:
    if payload.get("event") != "comment.received" or payload.get("test"):
        return 0
    c = payload.get("comment") or {}
    if (c.get("platform") or (payload.get("account") or {}).get("platform")) != "tiktok":
        return 0
    if not _ativas():
        return 0
    conta_tt = conta() or {}
    video = str(c.get("platformPostId") or (payload.get("post") or {}).get("platformPostId") or "")
    return tratar(video, _normalizar_webhook(c), conta_tt) if video else 0


def rodar() -> int:
    """Varredura: comentários dos vídeos mais recentes (job do scheduler)."""
    if not chave_api() or not _ativas():
        return 0
    try:
        conta_tt = conta()
        if not conta_tt:
            _ultimo_erro["rodada"] = "TikTok ainda não conectado no Zernio."
            return 0
        posts = _get("/inbox/comments", profileId=perfil_id(), accountId=conta_tt["id"],
                     sortBy="date", sortOrder="desc", limit=VIDEOS_POR_RODADA).get("data") or []
        total = 0
        for p in posts[:VIDEOS_POR_RODADA]:
            vid = str(p.get("id") or "")
            cursor = None
            for _ in range(PAGINAS_POR_VIDEO):
                params = {"accountId": conta_tt["id"], "limit": 50}
                if cursor:
                    params["cursor"] = cursor
                r = _get(f"/inbox/comments/{vid}", **params)
                for c in r.get("comments") or []:
                    total += tratar(vid, _normalizar_rest(c), conta_tt)
                pag = r.get("pagination") or {}
                cursor = pag.get("cursor") or pag.get("nextCursor")
                if not pag.get("hasMore") or not cursor:
                    break
        _ultimo_erro["rodada"] = ""
        return total
    except Exception as e:  # noqa: BLE001
        _ultimo_erro["rodada"] = f"{type(e).__name__}: {e}"
        print(f"[tiktok-comentarios] varredura falhou: {e}")
        return 0


# ─────────────────────────── Envio (um por vez, com ritmo) ───────────────────────────

def _enviados_desde(delta: timedelta) -> int:
    corte = (datetime.now(timezone.utc) - delta).isoformat()
    return _c().execute(
        "SELECT COUNT(*) FROM tiktok_eventos WHERE enviado_em >= ?", (corte,)).fetchone()[0]


def enviar_fila() -> int:
    if not chave_api():
        return 0
    _init()
    if _enviados_desde(timedelta(seconds=ESPACO_MIN_S)) or _enviados_desde(timedelta(hours=1)) >= TETO_HORA:
        return 0
    conn = _c()
    corte = (datetime.now(timezone.utc) - timedelta(days=JANELA_DIAS)).isoformat()
    conn.execute("UPDATE tiktok_eventos SET status = 'velho' WHERE status = 'na-fila' AND quando < ?", (corte,))
    conn.commit()
    e = conn.execute(
        "SELECT * FROM tiktok_eventos WHERE status IN ('na-fila', 'erro') AND tentativas < ? "
        "ORDER BY quando DESC LIMIT 1", (MAX_TENTATIVAS,)).fetchone()
    if not e:
        return 0
    a = _auto(e["automacao_id"])
    if not a or not a["ativa"] or not a.get("tiktok"):
        conn.execute("UPDATE tiktok_eventos SET status = 'desistiu', erro = 'automação desligada' WHERE id = ?",
                     (e["id"],))
        conn.commit()
        return 0
    conta_tt = conta()
    if not conta_tt:
        return 0
    opcoes = _respostas(a)
    # não repete a última variação usada, pra não sair a mesma frase em sequência
    ultima = conn.execute("SELECT resposta FROM tiktok_eventos WHERE resposta IS NOT NULL "
                          "ORDER BY enviado_em DESC LIMIT 1").fetchone()
    if ultima and len(opcoes) > 1:
        opcoes = [o for o in opcoes if o != ultima[0]] or opcoes
    resposta = random.choice(opcoes)
    agora = datetime.now(timezone.utc).isoformat()
    try:
        _post(f"/inbox/comments/{e['video_id']}",
              {"accountId": conta_tt["id"], "commentId": e["comment_id"], "message": resposta},
              idem=f"tt-{e['comment_id']}")
        conn.execute("UPDATE tiktok_eventos SET status = 'ok', resposta = ?, enviado_em = ?, erro = NULL, "
                     "tentativas = tentativas + 1 WHERE id = ?", (resposta, agora, e["id"]))
        conn.commit()
        _ultimo_erro["envio"] = ""
        print(f"[tiktok-comentarios] respondeu @{e['usuario'] or e['autor_id']}: {resposta}")
        return 1
    except Exception as err:  # noqa: BLE001
        msg = str(err)
        # 4xx (fora 429) é recusa do TikTok pra esse comentário: não adianta insistir
        definitivo = any(f"Zernio {c}" in msg for c in ("400", "403", "404", "409", "422"))
        conn.execute("UPDATE tiktok_eventos SET status = ?, erro = ?, enviado_em = ?, "
                     "tentativas = tentativas + 1 WHERE id = ?",
                     ("desistiu" if definitivo else "erro", msg[:500], agora, e["id"]))
        conn.commit()
        _ultimo_erro["envio"] = msg[:300]
        print(f"[tiktok-comentarios] resposta falhou: {msg}")
        return 0


# ─────────────────────────── Painel ───────────────────────────

def eventos(automacao_id: int | None = None, limite: int = 100) -> list[dict]:
    _init()
    sql = "SELECT * FROM tiktok_eventos"
    params: list = []
    if automacao_id:
        sql += " WHERE automacao_id = ?"
        params.append(automacao_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limite)
    return [dict(r) for r in _c().execute(sql, params).fetchall()]


def status() -> dict:
    _init()
    st = {"chave": bool(chave_api()), "conectado": False, "usuario": "", "webhook": bool(_meta(CHAVE_WEBHOOK)),
          "automacoes": len(_ativas()), "na_fila": _c().execute(
              "SELECT COUNT(*) FROM tiktok_eventos WHERE status IN ('na-fila', 'erro') AND tentativas < ?",
              (MAX_TENTATIVAS,)).fetchone()[0],
          "respondidos_hora": _enviados_desde(timedelta(hours=1)),
          "erro": _ultimo_erro["rodada"] or _ultimo_erro["envio"], "respostas_padrao": RESPOSTAS_PADRAO}
    if st["chave"]:
        try:
            c = conta()
            st["conectado"] = bool(c)
            st["usuario"] = (c or {}).get("usuario", "")
        except Exception as e:  # noqa: BLE001
            st["erro"] = str(e)[:300]
    return st
