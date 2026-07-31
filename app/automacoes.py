"""Automações de comentário → resposta pública + DM (estilo ManyChat).

Fluxo de uma automação:
  alguém comenta a palavra-chave no post alvo
    → o servidor responde o comentário em público (uma das respostas, sorteada)
    → e manda a DM (private reply) com texto + botão de link

Duas formas de detectar o comentário novo:
  1. WEBHOOK (instantâneo) — Meta chama POST /webhook/instagram. Precisa do callback
     configurado no app do Facebook; ver insta_web.py.
  2. POLLING (garantido) — job no scheduler varre os comentários do post alvo a cada
     AUTOMACOES_POLL_SEGUNDOS. Funciona sem nenhuma configuração extra.
Os dois caminhos caem no mesmo tratador, e o comment_id é gravado antes de responder,
então nunca respondem duas vezes o mesmo comentário.

Escopos:
  proximo — engata na PRÓXIMA mídia publicada depois que a automação foi criada e
            passa a valer só pra ela (é o "meu próximo post" do Pedro).
  midia   — um post específico (media_id fixo).
  todos   — qualquer post; só comentários posteriores à criação da automação.

Limites da API do Instagram que o código respeita:
  - private reply: 1 por comentário, dentro de 7 dias do comentário.
  - só comentários de terceiros (nunca responde a si mesmo).
"""
import json
import random
import sqlite3
import time
import unicodedata
from datetime import datetime, timedelta, timezone

import requests

from . import config, db
from .token_store import get_token

# Comentário mais velho que isso não recebe DM (janela de private reply da Meta)
JANELA_DM_DIAS = 7
# Teto por rodada, por automação. Existe pra não estourar limite da Meta numa tempestade
# de comentário; o que passar disso fica pra rodada seguinte, nada se perde.
MAX_POR_RODADA = 50


# ─────────────────────────── Banco ───────────────────────────

def _init():
    c = db.conn()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS automacoes (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            nome               TEXT NOT NULL DEFAULT '',
            ativa              INTEGER NOT NULL DEFAULT 1,
            palavras           TEXT NOT NULL DEFAULT '[]',   -- JSON: lista de palavras-chave
            modo               TEXT NOT NULL DEFAULT 'contem',  -- 'contem' | 'exata'
            escopo             TEXT NOT NULL DEFAULT 'proximo', -- 'proximo' | 'midia' | 'todos'
            midia_id           TEXT,                          -- post alvo (engatado ou fixo)
            respostas          TEXT NOT NULL DEFAULT '[]',    -- JSON: respostas públicas (sorteia 1)
            respostas_sem_dm   TEXT NOT NULL DEFAULT '[]',    -- JSON: respostas quando o direct NÃO sai
            dm_texto           TEXT NOT NULL DEFAULT '',
            botao_texto        TEXT NOT NULL DEFAULT '',
            botao_url          TEXT NOT NULL DEFAULT '',
            responder_publico  INTEGER NOT NULL DEFAULT 1,
            enviar_dm          INTEGER NOT NULL DEFAULT 1,
            uma_vez_por_pessoa INTEGER NOT NULL DEFAULT 1,
            criada_em          TEXT NOT NULL,
            engatada_em        TEXT
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS automacao_eventos (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            automacao_id INTEGER NOT NULL,
            comment_id   TEXT NOT NULL UNIQUE,
            midia_id     TEXT,
            usuario      TEXT,
            texto        TEXT,
            resposta     TEXT,
            dm_status    TEXT,
            erro         TEXT,
            quando       TEXT NOT NULL
        )
        """
    )
    # marca-passo do direct: um registro por envio, pra segurar o ritmo mesmo após reinício
    c.execute("CREATE TABLE IF NOT EXISTS dm_envios (id INTEGER PRIMARY KEY AUTOINCREMENT, quando TEXT NOT NULL)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_dm_envios_quando ON dm_envios (quando)")
    cols = {r[1] for r in c.execute("PRAGMA table_info(automacoes)").fetchall()}
    if "respostas_sem_dm" not in cols:
        c.execute("ALTER TABLE automacoes ADD COLUMN respostas_sem_dm TEXT NOT NULL DEFAULT '[]'")
    c.commit()


def _row(r) -> dict:
    d = dict(r)
    d["palavras"] = json.loads(d["palavras"])
    d["respostas"] = json.loads(d["respostas"])
    d["respostas_sem_dm"] = json.loads(d.get("respostas_sem_dm") or "[]")
    for b in ("ativa", "responder_publico", "enviar_dm", "uma_vez_por_pessoa"):
        d[b] = bool(d[b])
    return d


def listar(so_ativas: bool = False) -> list[dict]:
    _init()
    sql = "SELECT * FROM automacoes"
    if so_ativas:
        sql += " WHERE ativa = 1"
    sql += " ORDER BY id DESC"
    return [_row(r) for r in db.conn().execute(sql).fetchall()]


def get(aid: int) -> dict | None:
    _init()
    r = db.conn().execute("SELECT * FROM automacoes WHERE id = ?", (aid,)).fetchone()
    return _row(r) if r else None


_CAMPOS = (
    "nome", "ativa", "palavras", "modo", "escopo", "midia_id", "respostas", "respostas_sem_dm",
    "dm_texto", "botao_texto", "botao_url", "responder_publico", "enviar_dm", "uma_vez_por_pessoa",
)


def _serializar(dados: dict) -> dict:
    v = {k: dados[k] for k in _CAMPOS if k in dados}
    for lista in ("palavras", "respostas", "respostas_sem_dm"):
        if lista in v:
            itens = v[lista]
            if isinstance(itens, str):
                itens = [x.strip() for x in itens.splitlines()]
            v[lista] = json.dumps([x for x in itens if str(x).strip()])
    for b in ("ativa", "responder_publico", "enviar_dm", "uma_vez_por_pessoa"):
        if b in v:
            v[b] = 1 if v[b] else 0
    if v.get("escopo") != "midia" and "escopo" in v:
        v["midia_id"] = v.get("midia_id") or None
    return v


def criar(dados: dict) -> dict:
    _init()
    v = _serializar(dados)
    v.setdefault("criada_em", datetime.now(timezone.utc).isoformat())
    colunas = ", ".join(v)
    marcas = ", ".join("?" for _ in v)
    c = db.conn()
    cur = c.execute(f"INSERT INTO automacoes ({colunas}) VALUES ({marcas})", list(v.values()))
    c.commit()
    return get(cur.lastrowid)


def atualizar(aid: int, dados: dict) -> dict | None:
    if not get(aid):
        return None
    v = _serializar(dados)
    if v:
        sets = ", ".join(f"{k} = ?" for k in v)
        c = db.conn()
        c.execute(f"UPDATE automacoes SET {sets} WHERE id = ?", [*v.values(), aid])
        c.commit()
    return get(aid)


def remover(aid: int) -> bool:
    _init()
    c = db.conn()
    cur = c.execute("DELETE FROM automacoes WHERE id = ?", (aid,))
    c.commit()
    return cur.rowcount > 0


def eventos(automacao_id: int | None = None, limite: int = 100) -> list[dict]:
    _init()
    sql = "SELECT * FROM automacao_eventos"
    params: list = []
    if automacao_id:
        sql += " WHERE automacao_id = ?"
        params.append(automacao_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limite)
    return [dict(r) for r in db.conn().execute(sql, params).fetchall()]


def contadores(automacao_id: int) -> dict:
    _init()
    r = db.conn().execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN resposta IS NOT NULL AND resposta != '' THEN 1 ELSE 0 END) AS respondidos, "
        "SUM(CASE WHEN dm_status = 'ok' THEN 1 ELSE 0 END) AS dms, "
        "MAX(quando) AS ultimo "
        "FROM automacao_eventos WHERE automacao_id = ?",
        (automacao_id,),
    ).fetchone()
    return {
        "acionamentos": r["total"] or 0,
        "respostas": r["respondidos"] or 0,
        "dms": r["dms"] or 0,
        "ultimo": r["ultimo"],
    }


def _reservar_comentario(automacao_id: int, comentario: dict, midia_id: str) -> bool:
    """Grava o comentário ANTES de responder. False = já foi tratado (não repetir)."""
    _init()
    c = db.conn()
    try:
        c.execute(
            "INSERT INTO automacao_eventos (automacao_id, comment_id, midia_id, usuario, texto, quando) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                automacao_id, comentario["id"], midia_id, comentario.get("username", ""),
                comentario.get("text", ""), datetime.now(timezone.utc).isoformat(),
            ),
        )
        c.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def _fechar_evento(comment_id: str, resposta: str = "", dm_status: str = "", erro: str = ""):
    c = db.conn()
    c.execute(
        "UPDATE automacao_eventos SET resposta = ?, dm_status = ?, erro = ? WHERE comment_id = ?",
        (resposta, dm_status, erro or None, comment_id),
    )
    c.commit()


def _atualizar_dm(comment_id: str, dm_status: str, erro: str = ""):
    c = db.conn()
    c.execute(
        "UPDATE automacao_eventos SET dm_status = ?, erro = ? WHERE comment_id = ?",
        (dm_status, erro or None, comment_id),
    )
    c.commit()


def _ja_atendeu(automacao_id: int, usuario: str) -> bool:
    if not usuario:
        return False
    return db.conn().execute(
        "SELECT 1 FROM automacao_eventos WHERE automacao_id = ? AND usuario = ? AND dm_status = 'ok'",
        (automacao_id, usuario),
    ).fetchone() is not None


# ─────────────────────────── Casamento de palavra-chave ───────────────────────────

def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFD", texto or "")
    sem_acento = "".join(ch for ch in sem_acento if unicodedata.category(ch) != "Mn")
    return sem_acento.lower().strip()


def _so_letras(texto: str) -> list[str]:
    limpo = "".join(ch if ch.isalnum() else " " for ch in texto)
    return limpo.split()


def casa(texto: str, palavras: list[str], modo: str = "contem") -> bool:
    alvo = _normalizar(texto)
    if not alvo:
        return False
    tokens = _so_letras(alvo)
    for p in palavras:
        chave = _normalizar(p)
        if not chave:
            continue
        if modo == "exata":
            if alvo.strip() == chave or tokens == _so_letras(chave):
                return True
        elif chave in alvo or chave in tokens:
            return True
    return False


# ─────────────────────────── Instagram ───────────────────────────

def _usuario_proprio() -> str:
    global _CACHE_USERNAME
    if _CACHE_USERNAME is None:
        try:
            r = requests.get(
                f"{config.GRAPH}/{config.INSTAGRAM_BUSINESS_ID}",
                params={"fields": "username", "access_token": get_token()}, timeout=20,
            )
            _CACHE_USERNAME = _normalizar(r.json().get("username", ""))
        except Exception:  # noqa: BLE001
            _CACHE_USERNAME = ""
    return _CACHE_USERNAME


_CACHE_USERNAME: str | None = None


def _erro_graph(r: requests.Response) -> str:
    """Erro da Graph com code/subcode juntos — sem eles não dá pra saber por que falhou."""
    try:
        e = r.json()["error"]
    except Exception:  # noqa: BLE001
        return f"HTTP {r.status_code}: {r.text[:200]}"
    partes = [e.get("message") or "", e.get("error_user_msg") or ""]
    if e.get("code") is not None:
        sub = e.get("error_subcode")
        partes.append(f"code {e['code']}" + (f"/{sub}" if sub else ""))
    return " | ".join(p for p in partes if p)


def responder_comentario(comment_id: str, mensagem: str):
    r = requests.post(
        f"{config.GRAPH}/{comment_id}/replies",
        params={"message": mensagem, "access_token": get_token()}, timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"resposta pública falhou: {_erro_graph(r)}")


# Limites do template de botão da Meta
LIMITE_TEXTO_BOTAO = 640
LIMITE_TITULO_BOTAO = 20

# Ritmo do direct. A Meta aceita 750 private replies por hora; mandamos 1 a cada 6s, que
# dá 600/h com folga e, principalmente, sem rajada — rajada é o que dispara o (#613).
# O teto por hora fica como segunda trava, caso o intervalo escorregue.
INTERVALO_DM_S = 6
LIMITE_DM_HORA = 600
DESCANSO_APOS_613 = timedelta(minutes=3)
_DESCANSO_ATE: dict = {"quando": None}


def _registrar_envio():
    """Grava o envio em disco: se o container reiniciar, o ritmo continua de onde parou."""
    c = db.conn()
    c.execute("INSERT INTO dm_envios (quando) VALUES (?)", (datetime.now(timezone.utc).isoformat(),))
    c.execute("DELETE FROM dm_envios WHERE quando < ?",
              ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),))
    c.commit()


def _pode_enviar_dm() -> tuple[bool, str]:
    """(pode, motivo). A resposta pública NÃO passa por aqui: ela tem limite próprio e
    bem mais folgado, e é ela que faz quem comentou ser atendido na hora."""
    agora = datetime.now(timezone.utc)
    if _DESCANSO_ATE["quando"] and agora < _DESCANSO_ATE["quando"]:
        return False, "descansando"
    _init()
    c = db.conn()
    ultimo = c.execute("SELECT MAX(quando) FROM dm_envios").fetchone()[0]
    if ultimo:
        faltam = INTERVALO_DM_S - (agora - datetime.fromisoformat(ultimo)).total_seconds()
        if faltam > 0:
            return False, "intervalo"
    corte = (agora - timedelta(hours=1)).isoformat()
    na_hora = c.execute("SELECT COUNT(*) FROM dm_envios WHERE quando >= ?", (corte,)).fetchone()[0]
    if na_hora >= LIMITE_DM_HORA:
        return False, "teto-da-hora"
    return True, ""


def enviados_na_hora() -> int:
    _init()
    corte = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    return db.conn().execute("SELECT COUNT(*) FROM dm_envios WHERE quando >= ?", (corte,)).fetchone()[0]


def _tomar_folego():
    _DESCANSO_ATE["quando"] = datetime.now(timezone.utc) + DESCANSO_APOS_613
    print(f"[automacoes] (#613) limite da Meta. Direct pausado até {_DESCANSO_ATE['quando']:%H:%M:%S}.")

_VERSAO = config.GRAPH.rstrip("/").rsplit("/", 1)[-1]
# Hosts de envio, na ordem em que valem a pena tentar.
#   graph.instagram.com — Instagram API with Instagram Login. É o caso desta conta: o
#     token é Instagram-scoped e /me/accounts volta vazio (não existe Página do Facebook).
#     É também o host que o OpenReply usa em produção pra private reply.
#   graph.facebook.com  — só serve quando existe Página ligada. Fica de segundo caminho
#     pra não quebrar se a conta virar Business com Página depois.
HOSTS_DM = (f"https://graph.instagram.com/{_VERSAO}", config.GRAPH)
# Host que funcionou, pra não pagar a tentativa perdida em todo envio
_HOST_BOM: dict = {"url": ""}

# Página ligada à conta, resolvida uma vez e reaproveitada
_PAGINA: dict = {"id": "", "token": "", "quando": None}
_PAGINA_VALIDA_H = 6


def _pagina() -> tuple[str, str]:
    """(page_id, page_token) da Página ligada ao Instagram, ou ("", "") se não houver.

    É o caminho preferencial do direct: no graph.facebook.com mensagem é Messenger
    Platform, que fala com o ID da PÁGINA usando TOKEN DA PÁGINA. Enquanto o token
    de usuário não trouxer Página (granular_scopes sem target_ids, /me/accounts
    vazio), isso devolve vazio e o envio cai nos hosts de HOSTS_DM.
    """
    agora = datetime.now(timezone.utc)
    if _PAGINA["quando"] and agora - _PAGINA["quando"] < timedelta(hours=_PAGINA_VALIDA_H):
        return _PAGINA["id"], _PAGINA["token"]
    _PAGINA["quando"] = agora
    try:
        r = requests.get(
            f"{config.GRAPH}/me/accounts",
            params={"fields": "id,name,access_token,instagram_business_account",
                    "access_token": get_token()},
            timeout=30,
        )
        paginas = r.json().get("data", []) if r.status_code < 400 else []
    except Exception as e:  # noqa: BLE001
        print(f"[automacoes] não consegui listar Páginas: {e}")
        paginas = []
    escolhida = next(
        (p for p in paginas
         if (p.get("instagram_business_account") or {}).get("id") == config.INSTAGRAM_BUSINESS_ID
         and p.get("access_token")),
        None,
    )
    _PAGINA["id"] = escolhida["id"] if escolhida else ""
    _PAGINA["token"] = escolhida["access_token"] if escolhida else ""
    if escolhida:
        print(f"[automacoes] direct vai pela Página {escolhida.get('name')} ({escolhida['id']}).")
    return _PAGINA["id"], _PAGINA["token"]


def enviar_dm(comment_id: str, texto: str, botao_texto: str = "", botao_url: str = ""):
    """Private reply: DM pra quem comentou. Com botão quando há link.

    Duas regras da Meta mandam no formato deste envio:

    1. A private reply é UMA mensagem por comentário, pra sempre. Então nunca fazer
       duas chamadas no caminho feliz: a primeira gasta a cota e a segunda morre.
    2. O formato com botão é o template de BOTÃO (`template_type: "button"`), não o
       genérico. O genérico é card de carrossel e a private reply recusa — era por
       isso que o botão nunca chegava e o link caía como texto puro.

    O template de botão aceita 640 chars de texto (contra 80 do título do genérico),
    então some também a gambiarra de partir a mensagem em duas quando passava de 80.

    E o envio vai pro host certo (ver HOSTS_DM): nesta conta é o graph.instagram.com.
    Pelo graph.facebook.com a resposta é "(#3) Application does not have the capability",
    porque lá mensagem é Messenger Platform e exige Página, que esta conta não tem.
    """
    erros: list[str] = []

    page_id, page_token = _pagina()

    def _tentativas() -> list[tuple[str, str, str]]:
        """(rótulo, url, token). Com Página resolvida é só ela: os outros hosts já são
        conhecidamente recusados nesta conta, e insistir neles só gasta cota de API e
        polui o erro gravado."""
        if page_id:
            return [("pagina", f"{config.GRAPH}/{page_id}/messages", page_token)]
        t = [(h.split("//")[1].split("/")[0],
              f"{h}/{config.INSTAGRAM_BUSINESS_ID}/messages", get_token()) for h in HOSTS_DM]
        if _HOST_BOM["url"]:
            t.sort(key=lambda x: x[1] != _HOST_BOM["url"])
        return t

    def _enviar(payload: dict) -> requests.Response | None:
        """Primeira resposta boa. Devolve None quando nenhum caminho aceitou."""
        for rotulo, url, token in _tentativas():
            r = requests.post(
                url, params={"access_token": token},
                json={"recipient": {"comment_id": comment_id}, "message": payload},
                timeout=30,
            )
            if r.status_code < 400:
                if _HOST_BOM["url"] != url:
                    _HOST_BOM["url"] = url
                    print(f"[automacoes] direct sai por {rotulo} ({url}).")
                _registrar_envio()
                return r
            motivo = _erro_graph(r)
            if "613" in motivo:
                _tomar_folego()
                erros.append(f"{rotulo}: {motivo}")
                return None          # bateu no teto: parar aqui, não queimar mais chamada
            erros.append(f"{rotulo}: {motivo}")
        return None

    if botao_url and botao_texto:
        if _enviar({
            "attachment": {
                "type": "template",
                "payload": {
                    "template_type": "button",
                    "text": texto[:LIMITE_TEXTO_BOTAO],
                    "buttons": [{
                        "type": "web_url",
                        "url": botao_url,
                        "title": botao_texto[:LIMITE_TITULO_BOTAO],
                    }],
                },
            }
        }):
            return "ok"
        # último recurso: link no texto. Só chega aqui se o template foi recusado,
        # e recusa é validação — não gasta a private reply.
        if _enviar({"text": f"{texto}\n{botao_url}"}):
            return "ok-sem-botao"
        raise RuntimeError("DM falhou: " + " | ".join(erros))

    if _enviar({"text": f"{texto}\n{botao_url}".strip()}):
        return "ok"
    raise RuntimeError("DM falhou: " + " | ".join(erros))


def diagnostico_dm() -> dict:
    """Por que o direct sai ou não sai. Roda com o token vivo do servidor.

    Existe porque a mensagem de erro da Meta ("(#3) capability") não distingue token
    errado de Página ausente de permissão faltando. Aqui dá pra ver os três.
    """
    token = get_token()
    out: dict = {}

    r = requests.get(
        f"{config.GRAPH_BASE}/debug_token",
        params={"input_token": token, "access_token": f"{config.FACEBOOK_APP_ID}|{config.FACEBOOK_APP_SECRET}"},
        timeout=30,
    )
    d = r.json().get("data", {}) if r.status_code < 400 else {}
    out["token"] = {
        "tipo": d.get("type"), "valido": d.get("is_valid"), "app": d.get("application"),
        "expira_em": d.get("expires_at"),
        "tem_manage_messages": "instagram_manage_messages" in (d.get("scopes") or []),
        "granular": {g.get("scope"): g.get("target_ids") for g in d.get("granular_scopes") or []},
        "erro": None if r.status_code < 400 else _erro_graph(r),
    }

    r = requests.get(
        f"{config.GRAPH}/me/accounts",
        params={"fields": "id,name,access_token,instagram_business_account", "access_token": token},
        timeout=30,
    )
    if r.status_code >= 400:
        out["paginas"] = {"erro": _erro_graph(r)}
    else:
        out["paginas"] = [{
            "id": p.get("id"), "nome": p.get("name"),
            "tem_token": bool(p.get("access_token")),
            "ig": (p.get("instagram_business_account") or {}).get("id"),
        } for p in r.json().get("data", [])]

    r = requests.get(
        f"{config.GRAPH}/{config.INSTAGRAM_BUSINESS_ID}",
        params={"fields": "id,username", "access_token": token}, timeout=30,
    )
    out["conta_ig"] = r.json() if r.status_code < 400 else {"erro": _erro_graph(r)}

    # o host do Instagram Login só aceita token Instagram-scoped; serve pra saber qual é o caso
    r = requests.get(
        f"https://graph.instagram.com/{_VERSAO}/me",
        params={"fields": "id,username", "access_token": token}, timeout=30,
    )
    out["graph_instagram"] = r.json() if r.status_code < 400 else {"erro": _erro_graph(r)}

    return out


def buscar_midias(limite: int = 10) -> list[dict]:
    r = requests.get(
        f"{config.GRAPH}/{config.INSTAGRAM_BUSINESS_ID}/media",
        params={
            "fields": "id,caption,media_type,media_product_type,permalink,timestamp,thumbnail_url,media_url",
            "limit": limite, "access_token": get_token(),
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("data", [])


MAX_PAGINAS = 30  # 30 x 50 = 1500 comentários por rodada, teto de segurança


def _buscar_comentarios(midia_id: str, desde: datetime | None = None) -> list[dict]:
    """Comentários do post, seguindo a paginação da Graph.

    Sem paginar, uma enxurrada empurra os comentários pra fora da primeira página e eles
    nunca chegam a ser atendidos. Para de paginar quando a página inteira já é anterior a
    `desde`, que é de onde a automação vale.
    """
    url = f"{config.GRAPH}/{midia_id}/comments"
    params: dict | None = {
        "fields": "id,text,username,timestamp,replies{id,text,username,timestamp}",
        "limit": 50, "access_token": get_token(),
    }
    achatado: list[dict] = []
    for _ in range(MAX_PAGINAS):
        r = requests.get(url, params=params, timeout=30)
        if r.status_code >= 400:
            raise RuntimeError(_erro_graph(r))
        corpo = r.json()
        pagina = corpo.get("data", [])
        for c in pagina:
            achatado.append(c)
            achatado.extend(c.get("replies", {}).get("data", []))
        proxima = (corpo.get("paging") or {}).get("next")
        if not pagina or not proxima:
            break
        if desde and all((_parse_ts(c.get("timestamp")) or desde) < desde for c in pagina):
            break
        url, params = proxima, None  # o next já vem com token e cursor embutidos
    return achatado


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("+0000", "+00:00"))
    except ValueError:
        return None


# ─────────────────────────── Motor ───────────────────────────

def _desde(a: dict) -> datetime:
    """Só trata comentário posterior a isto (não mexe em comentário velho)."""
    marco = _parse_ts(a.get("engatada_em")) or _parse_ts(a.get("criada_em"))
    return marco or datetime.now(timezone.utc)


def engatar_proximo(a: dict) -> dict:
    """Escopo 'proximo': fixa a automação na primeira mídia publicada após a criação."""
    if a["escopo"] != "proximo" or a.get("midia_id"):
        return a
    criada = _parse_ts(a.get("criada_em")) or datetime.now(timezone.utc)
    candidatas = [m for m in buscar_midias() if (_parse_ts(m.get("timestamp")) or criada) > criada]
    if not candidatas:
        return a
    nova = min(candidatas, key=lambda m: _parse_ts(m["timestamp"]))
    print(f"[automacoes] #{a['id']} engatou no post {nova['id']} ({nova.get('permalink')})")
    return atualizar(a["id"], {
        "midia_id": nova["id"],
        "engatada_em": _parse_ts(nova["timestamp"]).isoformat(),
    }) or a


def _midias_alvo(a: dict) -> list[str]:
    if a["escopo"] == "todos":
        return [m["id"] for m in buscar_midias(limite=5)]
    return [a["midia_id"]] if a.get("midia_id") else []


def tratar_comentario(a: dict, midia_id: str, comentario: dict) -> bool:
    """Responde um comentário. Devolve True se acionou a automação."""
    texto = comentario.get("text", "")
    usuario = comentario.get("username", "")
    if not casa(texto, a["palavras"], a["modo"]):
        return False
    if usuario and _normalizar(usuario) == _usuario_proprio():
        return False
    if a["uma_vez_por_pessoa"] and _ja_atendeu(a["id"], usuario):
        return False
    if not _reservar_comentario(a["id"], comentario, midia_id):
        return False

    dm_status = ""
    erros = []

    # O direct NÃO sai daqui: entra na fila e o marca-passo (enviar_fila) manda no ritmo.
    # Assim a resposta pública sai na hora pra quem acabou de comentar, que é o que a
    # pessoa vê, e o direct nunca vira rajada em cima da Meta.
    if a["enviar_dm"] and a["dm_texto"]:
        quando = _parse_ts(comentario.get("timestamp"))
        dm_status = ("fora-da-janela"
                     if quando and datetime.now(timezone.utc) - quando > timedelta(days=JANELA_DM_DIAS)
                     else "na-fila")

    # 2º a resposta pública, que sai na hora. O direct vai pela fila (enviar_fila),
    # então a resposta é sempre a mesma: não existe versão "sem direct".
    resposta = ""
    if a["responder_publico"] and a["respostas"]:
        resposta = random.choice(a["respostas"])
        try:
            responder_comentario(comentario["id"], resposta)
        except Exception as e:  # noqa: BLE001
            erros.append(str(e))
            resposta = ""

    _fechar_evento(comentario["id"], resposta, dm_status, " | ".join(erros))
    print(f"[automacoes] #{a['id']} @{usuario}: resposta={bool(resposta)} dm={dm_status} {erros or ''}")
    return True


def rodar():
    """Varre os comentários dos posts alvo das automações ativas (job do scheduler)."""
    for a in listar(so_ativas=True):
        try:
            if a["escopo"] == "proximo" and not a.get("midia_id"):
                a = engatar_proximo(a)
            desde = _desde(a)
            for midia_id in _midias_alvo(a):
                tratados = 0
                for c in _buscar_comentarios(midia_id, desde):
                    quando = _parse_ts(c.get("timestamp"))
                    if quando and quando < desde:
                        continue
                    if tratar_comentario(a, midia_id, c):
                        tratados += 1
                        if tratados >= MAX_POR_RODADA:
                            print(f"[automacoes] #{a['id']} atingiu o teto da rodada ({MAX_POR_RODADA}).")
                            break
        except Exception as e:  # noqa: BLE001
            print(f"[automacoes] #{a['id']} falhou na rodada: {e}")


def _definitivo(erro: str) -> str:
    """Status final quando o direct nunca mais vai sair pra aquele comentário.

    Sem isso a fila fica batendo pra sempre num comentário que a Meta já fechou. O 2534023
    é "esse comentário já recebeu resposta privada" (a cota é de uma por comentário)."""
    e = erro.lower()
    if "2534023" in e or "já tem uma resposta" in e:
        return "ja-respondido"
    if "2534001" in e or "arquivou ou excluiu esta conversa" in e:
        return "conversa-indisponivel"
    if "inválido" in e and "comment_id" in e:
        return "comentario-sumiu"
    return ""


# Estados que ainda esperam direct. 'na-fila' é o normal; os outros são tentativa que
# não vingou e continua valendo enquanto estiver dentro dos 7 dias.
PENDENTES = ("na-fila", "erro", "aguardando-limite")


def enviar_fila() -> int:
    """Marca-passo do direct: manda UM por vez, respeitando o intervalo.

    Ordem de prioridade, que é a que o Pedro pediu:
      1. quem acabou de comentar (evento mais recente primeiro)
      2. o que sobrou de trás, conforme a fila da frente esvazia
    Como roda a cada poucos segundos, a fila anda sozinha sem nunca fazer rajada.
    """
    pode, motivo = _pode_enviar_dm()
    if not pode:
        return 0
    _init()
    corte = (datetime.now(timezone.utc) - timedelta(days=JANELA_DM_DIAS)).isoformat()
    marcas = ", ".join("?" for _ in PENDENTES)
    e = db.conn().execute(
        f"""SELECT * FROM automacao_eventos
            WHERE (dm_status IN ({marcas}) OR dm_status IS NULL OR dm_status = '')
              AND quando >= ?
            ORDER BY id DESC LIMIT 1""",
        (*PENDENTES, corte),
    ).fetchone()
    if not e:
        return 0

    a = get(e["automacao_id"])
    if not a or not a["enviar_dm"] or not a["dm_texto"]:
        _atualizar_dm(e["comment_id"], "sem-direct")
        return 0
    try:
        status = enviar_dm(e["comment_id"], a["dm_texto"], a["botao_texto"], a["botao_url"])
    except Exception as err:  # noqa: BLE001
        _atualizar_dm(e["comment_id"], _definitivo(str(err)) or "erro", str(err))
        return 0
    _atualizar_dm(e["comment_id"], status)
    print(f"[automacoes] direct pra @{e['usuario']} ({status}).")
    return 1


def drenar_fila(segundos: int = 55) -> int:
    """Fica mandando direct no ritmo por ~1 minuto, um a cada INTERVALO_DM_S.

    É um laço próprio em vez de um job de 6 em 6 segundos porque tick curto no
    APScheduler é descartado quando o worker está ocupado com a varredura de
    comentários, e aí a fila simplesmente não anda.
    """
    fim = time.monotonic() + segundos
    enviados = 0
    while time.monotonic() < fim:
        pode, motivo = _pode_enviar_dm()
        if not pode:
            if motivo in ("teto-da-hora", "descansando"):
                return enviados          # não adianta girar em falso até a próxima rodada
            time.sleep(1)                # só o intervalo entre envios
            continue
        if not enviar_fila():
            return enviados              # fila vazia ou item que não dá pra mandar agora
        enviados += 1
    return enviados


def marcar_fora_da_janela() -> int:
    """Passou dos 7 dias, a Meta não aceita mais. Sai da fila pra não bater à toa."""
    _init()
    corte = (datetime.now(timezone.utc) - timedelta(days=JANELA_DM_DIAS)).isoformat()
    marcas = ", ".join("?" for _ in PENDENTES)
    c = db.conn()
    cur = c.execute(
        f"""UPDATE automacao_eventos SET dm_status = 'fora-da-janela'
            WHERE (dm_status IN ({marcas}) OR dm_status IS NULL OR dm_status = '') AND quando < ?""",
        (*PENDENTES, corte),
    )
    c.commit()
    return cur.rowcount


def pendentes_na_fila() -> int:
    _init()
    marcas = ", ".join("?" for _ in PENDENTES)
    corte = (datetime.now(timezone.utc) - timedelta(days=JANELA_DM_DIAS)).isoformat()
    return db.conn().execute(
        f"""SELECT COUNT(*) FROM automacao_eventos
            WHERE (dm_status IN ({marcas}) OR dm_status IS NULL OR dm_status = '') AND quando >= ?""",
        (*PENDENTES, corte),
    ).fetchone()[0]


def tratar_webhook(payload: dict) -> int:
    """Comentário chegando pelo webhook da Meta (caminho instantâneo)."""
    tratados = 0
    for entry in payload.get("entry", []):
        for ch in entry.get("changes", []):
            if ch.get("field") != "comments":
                continue
            v = ch.get("value", {})
            comentario = {
                "id": v.get("id"),
                "text": v.get("text", ""),
                "username": (v.get("from") or {}).get("username", ""),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            midia_id = (v.get("media") or {}).get("id", "")
            if not comentario["id"]:
                continue
            for a in listar(so_ativas=True):
                if a["escopo"] == "proximo" and not a.get("midia_id"):
                    a = engatar_proximo(a)
                alvos = _midias_alvo(a)
                if a["escopo"] != "todos" and midia_id not in alvos:
                    continue
                if tratar_comentario(a, midia_id, comentario):
                    tratados += 1
                    break
    return tratados


# ─────────────────────────── Semente ───────────────────────────

SEMENTE_SENSOR = {
    "nome": "Consulta pública do sensor",
    "palavras": ["sensor"],
    "modo": "contem",
    "escopo": "proximo",
    "respostas": [
        "Te enviei o link para participar no direct!",
        "Não deixa de participar, te enviei o link no privado!",
        "Vamos lá, te enviei o link para participar no direct!",
        "Isso aí, vamos fazer a nossa parte! Te enviei o link no direct.",
    ],
    # usadas quando o direct não sai (não prometer o que não foi entregue)
    "respostas_sem_dm": [
        "O link da consulta está aqui: https://brasilparticipativo.presidencia.gov.br/processes/consultas-publicas-conitec/f/5171/",
        "Participa aqui: https://brasilparticipativo.presidencia.gov.br/processes/consultas-publicas-conitec/f/5171/",
    ],
    "dm_texto": "Consulta pública para incorporação do sensor no sus.",
    "botao_texto": "Participar",
    "botao_url": "https://brasilparticipativo.presidencia.gov.br/processes/consultas-publicas-conitec/f/5171/",
}


def semear():
    """Cria a automação inicial do sensor na primeira subida (só se não houver nenhuma)."""
    _init()
    if db.conn().execute("SELECT COUNT(*) FROM automacoes").fetchone()[0]:
        return
    criar(SEMENTE_SENSOR)
    print("[automacoes] Automação inicial 'sensor' criada (aguardando a próxima publicação).")
