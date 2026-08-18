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
import threading
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
            quando       TEXT NOT NULL,
            plataforma   TEXT NOT NULL DEFAULT 'ig'   -- 'ig' | 'fb'
        )
        """
    )
    # marca-passo do direct: um registro por envio, pra segurar o ritmo mesmo após reinício
    c.execute("CREATE TABLE IF NOT EXISTS dm_envios (id INTEGER PRIMARY KEY AUTOINCREMENT, quando TEXT NOT NULL)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_dm_envios_quando ON dm_envios (quando)")
    cols = {r[1] for r in c.execute("PRAGMA table_info(automacoes)").fetchall()}
    if "respostas_sem_dm" not in cols:
        c.execute("ALTER TABLE automacoes ADD COLUMN respostas_sem_dm TEXT NOT NULL DEFAULT '[]'")
    if "facebook" not in cols:
        c.execute("ALTER TABLE automacoes ADD COLUMN facebook INTEGER NOT NULL DEFAULT 1")
    if "esperando_desde" not in cols:
        c.execute("ALTER TABLE automacoes ADD COLUMN esperando_desde TEXT")
    ev = {r[1] for r in c.execute("PRAGMA table_info(automacao_eventos)").fetchall()}
    if "plataforma" not in ev:
        c.execute("ALTER TABLE automacao_eventos ADD COLUMN plataforma TEXT NOT NULL DEFAULT 'ig'")
    c.commit()


def _row(r) -> dict:
    d = dict(r)
    d["palavras"] = json.loads(d["palavras"])
    d["respostas"] = json.loads(d["respostas"])
    d["respostas_sem_dm"] = json.loads(d.get("respostas_sem_dm") or "[]")
    for b in ("ativa", "responder_publico", "enviar_dm", "uma_vez_por_pessoa", "facebook"):
        d[b] = bool(d.get(b, 1))
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
    "facebook", "engatada_em", "esperando_desde",
)


def _serializar(dados: dict) -> dict:
    v = {k: dados[k] for k in _CAMPOS if k in dados}
    for lista in ("palavras", "respostas", "respostas_sem_dm"):
        if lista in v:
            itens = v[lista]
            if isinstance(itens, str):
                itens = [x.strip() for x in itens.splitlines()]
            v[lista] = json.dumps([x for x in itens if str(x).strip()])
    for b in ("ativa", "responder_publico", "enviar_dm", "uma_vez_por_pessoa", "facebook"):
        if b in v:
            v[b] = 1 if v[b] else 0
    if v.get("escopo") != "midia" and "escopo" in v:
        v["midia_id"] = v.get("midia_id") or None
    return v


def criar(dados: dict) -> dict:
    _init()
    v = _serializar(dados)
    v.setdefault("criada_em", datetime.now(timezone.utc).isoformat())
    if v.get("escopo") == "proximo":
        v.setdefault("esperando_desde", v["criada_em"])
    colunas = ", ".join(v)
    marcas = ", ".join("?" for _ in v)
    c = db.conn()
    cur = c.execute(f"INSERT INTO automacoes ({colunas}) VALUES ({marcas})", list(v.values()))
    c.commit()
    return get(cur.lastrowid)


# Quem escolhe o post alvo e os marcos é o servidor, nunca o painel: a tela pode estar
# com uma cópia velha e desfazer o engate que acabou de acontecer.
CAMPOS_DO_SERVIDOR = ("engatada_em", "esperando_desde")


def atualizar(aid: int, dados: dict, interno: bool = False) -> dict | None:
    antes = get(aid)
    if not antes:
        return None
    if not interno:
        dados = {k: x for k, x in dados.items() if k not in CAMPOS_DO_SERVIDOR}
    v = _serializar(dados)
    # Voltar pra "próxima publicação" REARMA: solta o post em que estava e passa a esperar
    # a próxima de verdade. Sem isso, reselecionar reengatava no último post publicado,
    # que é justamente o que não se quer quando já saiu publicação no meio.
    if v.get("escopo") == "proximo" and (antes["escopo"] != "proximo" or antes.get("midia_id")):
        v["midia_id"] = None
        v["engatada_em"] = None
        v["esperando_desde"] = datetime.now(timezone.utc).isoformat()
        print(f"[automacoes] #{aid} rearmada: esperando a próxima publicação.")
    if v:
        sets = ", ".join(f"{k} = ?" for k in v)
        c = db.conn()
        c.execute(f"UPDATE automacoes SET {sets} WHERE id = ?", [*v.values(), aid])
        c.commit()
    return get(aid)


def duplicar(aid: int) -> dict | None:
    """Cópia de uma automação, pra usar de base.

    Copia o conteúdo (palavras, respostas, direct, botão, chaves) e deixa de fora tudo
    que é estado de execução: post alvo, marcos e histórico. Nasce DESLIGADA e esperando
    a próxima publicação de propósito — duas automações ativas com a mesma palavra no
    mesmo post brigam entre si, e só uma entrega.
    """
    base = get(aid)
    if not base:
        return None
    copia = {k: base[k] for k in _CAMPOS if k not in ("engatada_em", "esperando_desde")}
    copia["nome"] = f"{base['nome']} (cópia)".strip()
    copia["ativa"] = False
    copia["escopo"] = "proximo"
    copia["midia_id"] = None
    return criar(copia)


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


def _reservar_comentario(automacao_id: int, comentario: dict, midia_id: str,
                         plataforma: str = "ig") -> bool:
    """Grava o comentário ANTES de responder. False = já foi tratado (não repetir)."""
    _init()
    c = db.conn()
    try:
        c.execute(
            "INSERT INTO automacao_eventos (automacao_id, comment_id, midia_id, usuario, texto, quando, plataforma) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                automacao_id, comentario["id"], midia_id, comentario.get("username", ""),
                comentario.get("text", ""),
                # a hora do comentário, não a da inserção: é ela que manda na prioridade da
                # fila e na janela de 7 dias do private reply
                (_parse_ts(comentario.get("timestamp")) or datetime.now(timezone.utc)).isoformat(),
                plataforma,
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


def _sou_eu(comentario: dict, plataforma: str) -> bool:
    """A conta comentando nela mesma — inclusive a resposta pública que a automação
    acabou de publicar.

    No Instagram a comparação é pelo @. No Facebook o autor é a PÁGINA, com id e nome
    próprios, que nunca batem com o @ do Instagram: era por isso que a resposta pública
    voltava como comentário novo e a automação respondia a si mesma.
    """
    if plataforma == "fb":
        page_id, _ = _pagina()
        if comentario.get("autor_id") and page_id and str(comentario["autor_id"]) == str(page_id):
            return True
        nome = _normalizar(_PAGINA.get("nome") or "")
        return bool(nome and _normalizar(comentario.get("username", "")) == nome)
    usuario = comentario.get("username", "")
    return bool(usuario and _normalizar(usuario) == _usuario_proprio())


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


def responder_comentario(comment_id: str, mensagem: str, plataforma: str = "ig"):
    """Resposta pública. Os dois lados divergem: no Instagram é /replies com token de
    usuário; no Facebook é /comments com token da Página."""
    if plataforma == "fb":
        _, page_token = _pagina()
        alvo, token = f"{config.GRAPH}/{comment_id}/comments", page_token
    else:
        alvo, token = f"{config.GRAPH}/{comment_id}/replies", get_token()
    r = requests.post(alvo, params={"message": mensagem, "access_token": token}, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"resposta pública falhou: {_erro_graph(r)}")


# Limites do template de botão da Meta
LIMITE_TEXTO_BOTAO = 640
LIMITE_TITULO_BOTAO = 20

# Ritmo do direct. A Meta aceita 750 private replies por hora; mandamos 1 a cada 6s, que
# dá 600/h com folga e, principalmente, sem rajada — rajada é o que dispara o (#613).
# O teto por hora fica como segunda trava, caso o intervalo escorregue.
# Ritmo do direct. O objetivo é latência baixa, não cadência bonita: quem comentou tem
# que receber em segundos. Então a regra é janela deslizante com rajada, não intervalo fixo.
#   - até RAJADA envios seguidos, separados só por ESPACO_MIN_S (fila vazia = resposta na hora)
#   - no máximo LIMITE_MINUTO em qualquer janela de 60s
#   - no máximo LIMITE_DM_HORA em qualquer janela de 1h (teto da Meta é 750)
# A janela de minuto é o que segura rajada, que é o que dispara (#613); o teto de hora é a
# rede de segurança. Os dois cedem sozinhos quando a Meta reclama.
ESPACO_MIN_S = 0.8
RAJADA = 10
LIMITE_MINUTO_BASE = 10
LIMITE_DM_HORA = 690
DESCANSOS_MIN = (3, 8, 15, 30, 45, 60)
_DESCANSO_ATE: dict = {"quando": None, "nivel": 0}
_RITMO: dict = {"minuto": LIMITE_MINUTO_BASE, "limpos": 0}
# pulso do marca-passo: sem isso não dá pra saber se a thread está viva ou morreu calada
_PULSO: dict = {"quando": None, "voltas": 0}
_THREAD: dict = {"t": None}


def ritmo_atual() -> dict:
    return {"por_minuto": _RITMO["minuto"], "por_hora": LIMITE_DM_HORA,
            "espaco_min_s": ESPACO_MIN_S, "rajada": RAJADA}


def _acelerar():
    _RITMO["limpos"] += 1
    if _RITMO["limpos"] >= 60 and _RITMO["minuto"] < LIMITE_MINUTO_BASE:
        _RITMO["minuto"] += 1
        _RITMO["limpos"] = 0
        print(f"[automacoes] ritmo de volta pra {_RITMO['minuto']}/min.")


def _frear():
    _RITMO["minuto"] = max(3, _RITMO["minuto"] - 2)
    _RITMO["limpos"] = 0
    print(f"[automacoes] ritmo reduzido pra {_RITMO['minuto']}/min.")


def _registrar_envio():
    """Grava a CHAMADA em disco: se o container reiniciar, o ritmo continua de onde parou."""
    c = db.conn()
    c.execute("INSERT INTO dm_envios (quando) VALUES (?)", (datetime.now(timezone.utc).isoformat(),))
    c.execute("DELETE FROM dm_envios WHERE quando < ?",
              ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),))
    c.commit()


def _pode_enviar_dm() -> tuple[bool, str]:
    """(pode, motivo). A resposta pública sai logo depois do direct, no mesmo passo,
    então quem comentou é atendido inteiro de uma vez."""
    agora = datetime.now(timezone.utc)
    if _DESCANSO_ATE["quando"] and agora < _DESCANSO_ATE["quando"]:
        return False, "descansando"
    _init()
    c = db.conn()
    ultimo = c.execute("SELECT MAX(quando) FROM dm_envios").fetchone()[0]
    if ultimo and (agora - datetime.fromisoformat(ultimo)).total_seconds() < ESPACO_MIN_S:
        return False, "espaco"
    no_minuto = c.execute("SELECT COUNT(*) FROM dm_envios WHERE quando >= ?",
                          ((agora - timedelta(seconds=60)).isoformat(),)).fetchone()[0]
    if no_minuto >= _RITMO["minuto"]:
        return False, "teto-do-minuto"
    na_hora = c.execute("SELECT COUNT(*) FROM dm_envios WHERE quando >= ?",
                        ((agora - timedelta(hours=1)).isoformat(),)).fetchone()[0]
    if na_hora >= LIMITE_DM_HORA:
        return False, "teto-da-hora"
    return True, ""


def enviados_na_hora() -> int:
    _init()
    corte = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    return db.conn().execute("SELECT COUNT(*) FROM dm_envios WHERE quando >= ?", (corte,)).fetchone()[0]


def _tomar_folego():
    _frear()
    n = min(_DESCANSO_ATE["nivel"], len(DESCANSOS_MIN) - 1)
    minutos = DESCANSOS_MIN[n]
    _DESCANSO_ATE["nivel"] = n + 1
    _DESCANSO_ATE["quando"] = datetime.now(timezone.utc) + timedelta(minutes=minutos)
    print(f"[automacoes] (#613) limite da Meta. Direct pausado {minutos} min, "
          f"até {_DESCANSO_ATE['quando']:%H:%M:%S}.")

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
# Última resposta de envio, pra quem chamou aproveitar o recipient_id. Só a thread do
# marca-passo escreve aqui, e ela manda um direct por vez.
_ULTIMA_RESPOSTA: dict = {"corpo": None}

# Página ligada à conta, resolvida uma vez e reaproveitada
_PAGINA: dict = {"id": "", "token": "", "nome": "", "quando": None}
_PAGINA_VALIDA_H = 6


def _pagina() -> tuple[str, str]:
    """(page_id, page_token) da Página ligada ao Instagram, ou ("", "") se não houver.

    É o caminho preferencial do direct: no graph.facebook.com mensagem é Messenger
    Platform, que fala com o ID da PÁGINA usando TOKEN DA PÁGINA. Enquanto o token
    de usuário não trouxer Página (granular_scopes sem target_ids, /me/accounts
    vazio), isso devolve vazio e o envio cai nos hosts de HOSTS_DM.
    """
    agora = datetime.now(timezone.utc)
    # sucesso vale horas; falha vale 1 minuto. Guardar falha por 6h derrubaria o direct
    # a tarde inteira por causa de um tropeço de rede na subida.
    if _PAGINA["quando"]:
        validade = timedelta(hours=_PAGINA_VALIDA_H) if _PAGINA["id"] else timedelta(minutes=1)
        if agora - _PAGINA["quando"] < validade:
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
    _PAGINA["nome"] = escolhida.get("name", "") if escolhida else ""
    if escolhida:
        print(f"[automacoes] direct vai pela Página {escolhida.get('name')} ({escolhida['id']}).")
    return _PAGINA["id"], _PAGINA["token"]


def enviar_dm(comment_id: str, texto: str, botao_texto: str = "", botao_url: str = "",
              plataforma: str = "ig"):
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
        if plataforma == "fb":
            return []            # sem Página não existe direct no Facebook
        t = [(h.split("//")[1].split("/")[0],
              f"{h}/{config.INSTAGRAM_BUSINESS_ID}/messages", get_token()) for h in HOSTS_DM]
        if _HOST_BOM["url"]:
            t.sort(key=lambda x: x[1] != _HOST_BOM["url"])
        return t

    def _enviar(payload: dict) -> requests.Response | None:
        """Primeira resposta boa. Devolve None quando nenhum caminho aceitou."""
        for rotulo, url, token in _tentativas():
            # conta ANTES de chamar: o limite da Meta é por chamada, não por entrega.
            # Contar só o sucesso deixava uma fila de falhas passar batido pelos tetos.
            _registrar_envio()
            r = requests.post(
                url, params={"access_token": token},
                json={"recipient": {"comment_id": comment_id}, "message": payload},
                timeout=30,
            )
            if r.status_code < 400:
                try:
                    _ULTIMA_RESPOSTA["corpo"] = r.json()
                except Exception:  # noqa: BLE001
                    _ULTIMA_RESPOSTA["corpo"] = None
                if _HOST_BOM["url"] != url:
                    _HOST_BOM["url"] = url
                    print(f"[automacoes] direct sai por {rotulo} ({url}).")
                _acelerar()
                _DESCANSO_ATE["nivel"] = 0     # passou: o castigo acabou, zera a escada
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
    # o marco é quando a automação passou a esperar, não quando foi criada: se ela foi
    # rearmada depois de uma publicação, a publicação anterior não conta
    marco = (_parse_ts(a.get("esperando_desde")) or _parse_ts(a.get("criada_em"))
             or datetime.now(timezone.utc))
    candidatas = [m for m in buscar_midias() if (_parse_ts(m.get("timestamp")) or marco) > marco]
    if not candidatas:
        return a
    nova = min(candidatas, key=lambda m: _parse_ts(m["timestamp"]))
    print(f"[automacoes] #{a['id']} engatou no post {nova['id']} ({nova.get('permalink')})")
    # deixa de ser "próxima" e vira o post concreto: o painel passa a mostrar qual é
    return atualizar(a["id"], {
        "escopo": "midia",
        "midia_id": nova["id"],
        "engatada_em": _parse_ts(nova["timestamp"]).isoformat(),
    }, interno=True) or a


def engatar_publicacao(media_id: str) -> int:
    """Fixa na publicação que acabou de sair toda automação que estava esperando.

    É o caminho instantâneo. O Instagram não tem webhook de publicação (só comments,
    messages, mentions e story_insights), então os gatilhos possíveis são:
      1. o próprio servidor publicando — chama isto na hora, latência zero;
      2. o webhook `page/feed` do Facebook, que dispara no crosspost;
      3. a varredura de 60s, que fica só de rede de segurança.
    """
    if not media_id:
        return 0
    agora = datetime.now(timezone.utc)
    engatadas = 0
    for a in listar(so_ativas=True):
        if a["escopo"] != "proximo" or a.get("midia_id"):
            continue
        atualizar(a["id"], {"escopo": "midia", "midia_id": media_id,
                            "engatada_em": agora.isoformat()}, interno=True)
        engatadas += 1
        print(f"[automacoes] #{a['id']} engatou na hora no post {media_id}.")
    return engatadas


def _midias_alvo(a: dict) -> list[str]:
    if a["escopo"] == "todos":
        return [m["id"] for m in buscar_midias(limite=5)]
    return [a["midia_id"]] if a.get("midia_id") else []


def tratar_comentario(a: dict, midia_id: str, comentario: dict, plataforma: str = "ig") -> bool:
    """Responde um comentário. Devolve True se acionou a automação."""
    texto = comentario.get("text", "")
    usuario = comentario.get("username", "")
    if not casa(texto, a["palavras"], a["modo"]):
        return False
    if _sou_eu(comentario, plataforma):
        return False
    # a própria resposta pública, voltando como comentário. Cinto e suspensório: mesmo que
    # a identidade escape, o texto idêntico a uma resposta configurada nunca é gatilho.
    if texto.strip() and texto.strip() in {r.strip() for r in a["respostas"]}:
        return False
    if a["uma_vez_por_pessoa"] and _ja_atendeu(a["id"], usuario):
        return False
    if not _reservar_comentario(a["id"], comentario, midia_id, plataforma):
        return False

    # NADA sai daqui: o comentário entra na fila e o marca-passo trata a pessoa inteira.
    # A ordem importa e é inegociável: o direct PRIMEIRO, a resposta pública depois. Se a
    # resposta pública sai antes, a Meta passa a considerar o comentário já respondido e
    # recusa a private reply com (#-1) 2534023.
    quando = _parse_ts(comentario.get("timestamp"))
    fora = quando and datetime.now(timezone.utc) - quando > timedelta(days=JANELA_DM_DIAS)
    _fechar_evento(comentario["id"], "", "fora-da-janela" if fora else "na-fila", "")
    return True


_ERRO_RODADA: dict = {}      # automacao_id -> última falha da varredura
# O crosspost do Instagram nasce no Facebook em segundos; essa é a folga pra casar os dois
JANELA_CROSSPOST_MIN = 20
# Marco de quando a perna do Facebook entrou no ar (guardado na tabela meta)
CHAVE_INICIO_FB = "automacoes_facebook_desde"


def rodar():
    """Varre os comentários dos posts alvo das automações ativas (job do scheduler)."""
    iniciar_marca_passo()          # religa o marca-passo se ele tiver morrido
    for a in listar(so_ativas=True):
        try:
            if a["escopo"] == "proximo" and not a.get("midia_id"):
                a = engatar_proximo(a)
            desde = _desde(a)
            alvos = [("ig", m, _buscar_comentarios, desde) for m in _midias_alvo(a)]
            # o Facebook entra em try próprio: falha lá não pode derrubar o Instagram junto,
            # que foi exatamente o que aconteceu quando _posts_facebook quebrou
            if a.get("facebook") and config.AUTOMACOES_FACEBOOK:
                try:
                    desde_fb = max(desde, _inicio_facebook())
                    alvos += [("fb", p, _comentarios_facebook, desde_fb) for p in _posts_facebook(a)]
                except Exception as e:  # noqa: BLE001
                    _ERRO_RODADA[a["id"]] = f"perna do Facebook: {type(e).__name__}: {e}"
                    print(f"[automacoes] #{a['id']} perna do Facebook falhou: {e}")
            for plataforma, alvo_id, buscar, corte in alvos:
                tratados = 0
                for c in buscar(alvo_id, corte):
                    quando = _parse_ts(c.get("timestamp"))
                    if quando and quando < corte:
                        continue
                    if tratar_comentario(a, alvo_id, c, plataforma):
                        tratados += 1
                        if tratados >= MAX_POR_RODADA:
                            print(f"[automacoes] #{a['id']} atingiu o teto da rodada em {plataforma}.")
                            break
        except Exception as e:  # noqa: BLE001
            # guarda pra aparecer no painel: falha por automação some no log do container
            _ERRO_RODADA[a["id"]] = f"{type(e).__name__}: {e}"
            print(f"[automacoes] #{a['id']} falhou na rodada: {e}")
        else:
            _ERRO_RODADA.pop(a["id"], None)


def _definitivo(erro: str) -> str:
    """Status final quando o direct nunca mais vai sair pra aquele comentário.

    Sem isso a fila fica batendo pra sempre num comentário que a Meta já fechou. O 2534023
    é "esse comentário já recebeu resposta privada" (a cota é de uma por comentário)."""
    e = erro.lower()
    if "2534023" in e or "já tem uma resposta" in e:
        return "ja-respondido"
    if "10900" in e or "already replied" in e:
        return "ja-respondido"          # mesmo caso, do lado do Facebook
    if "2534001" in e or "arquivou ou excluiu esta conversa" in e:
        return "conversa-indisponivel"
    if "10903" in e or "não são permitidas respostas privadas a páginas" in e:
        return "comentario-de-pagina"    # Página comentando: a Meta nunca aceita DM pra Página
    if "inválido" in e and "comment_id" in e:
        return "comentario-sumiu"
    return ""


# Estados que ainda esperam direct. 'na-fila' é o normal; os outros são tentativa que
# não vingou e continua valendo enquanto estiver dentro dos 7 dias.
PENDENTES = ("na-fila", "erro", "aguardando-limite")


def enviar_fila() -> int:
    """Atende UMA pessoa por vez: direct primeiro, resposta pública logo depois.

    Ordem de prioridade:
      1. quem acabou de comentar (evento mais recente primeiro)
      2. o que sobrou de trás, conforme a frente esvazia
    Quem segura o ritmo é _pode_enviar_dm; aqui é um por chamada.
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
            ORDER BY quando DESC, id DESC LIMIT 1""",
        (*PENDENTES, corte),
    ).fetchone()
    if not e:
        return 0

    a = get(e["automacao_id"])
    if not a or not a["enviar_dm"] or not a["dm_texto"]:
        _atualizar_dm(e["comment_id"], "sem-direct")
        return 0
    # 1º o direct. Só depois a resposta pública: invertido, a Meta recusa a private reply.
    try:
        plataforma = e["plataforma"] if "plataforma" in e.keys() else "ig"
        status = enviar_dm(e["comment_id"], a["dm_texto"], a["botao_texto"], a["botao_url"], plataforma)
    except Exception as err:  # noqa: BLE001
        _atualizar_dm(e["comment_id"], _definitivo(str(err)) or "erro", str(err))
        return 0
    _atualizar_dm(e["comment_id"], status)

    # no Facebook o nome só aparece depois do direct: aproveita o recipient_id da resposta
    if plataforma == "fb" and not (e["usuario"] or ""):
        psid = ((_ULTIMA_RESPOSTA["corpo"] or {}).get("recipient_id") or "")
        nome = nome_por_psid(psid) or (f"fb:{psid[-8:]}" if psid else "")
        if nome:
            c = db.conn()
            c.execute("UPDATE automacao_eventos SET usuario = ? WHERE comment_id = ?",
                      (nome, e["comment_id"]))
            c.commit()
            e = dict(e) | {"usuario": nome}

    if a["responder_publico"] and a["respostas"] and not (e["resposta"] or ""):
        resposta = random.choice(a["respostas"])
        try:
            responder_comentario(e["comment_id"], resposta, plataforma)
            c = db.conn()
            c.execute("UPDATE automacao_eventos SET resposta = ? WHERE comment_id = ?",
                      (resposta, e["comment_id"]))
            c.commit()
        except Exception as err:  # noqa: BLE001
            print(f"[automacoes] resposta pública de @{e['usuario']} falhou: {err}")
    print(f"[automacoes] [{plataforma}] @{e['usuario']}: direct {status} + resposta pública.")
    return 1


def garantir_webhook() -> dict:
    """Assina o webhook de comentários da Meta. É isso que dá resposta em segundos.

    Sem assinatura, o comentário só aparece no polling e a pessoa espera até um ciclo
    inteiro. Roda na subida e é idempotente: reassinar não duplica nada.
    """
    if not (config.FACEBOOK_APP_ID and config.FACEBOOK_APP_SECRET and config.PUBLIC_BASE_URL):
        return {"ok": False, "motivo": "faltam FACEBOOK_APP_ID/SECRET ou PUBLIC_BASE_URL"}
    if not config.IG_WEBHOOK_VERIFY_TOKEN:
        return {"ok": False, "motivo": "falta IG_WEBHOOK_VERIFY_TOKEN"}

    app_token = f"{config.FACEBOOK_APP_ID}|{config.FACEBOOK_APP_SECRET}"
    callback = f"{config.PUBLIC_BASE_URL.rstrip('/')}/webhook/instagram"
    out: dict = {"callback": callback}

    r = requests.post(
        f"{config.GRAPH}/{config.FACEBOOK_APP_ID}/subscriptions",
        params={
            "object": "instagram", "callback_url": callback, "fields": "comments",
            "verify_token": config.IG_WEBHOOK_VERIFY_TOKEN, "include_values": "true",
            "access_token": app_token,
        },
        timeout=30,
    )
    out["assinatura_app"] = "ok" if r.status_code < 400 else _erro_graph(r)

    # o Facebook entrega comentário pelo objeto 'page', campo 'feed'
    r = requests.post(
        f"{config.GRAPH}/{config.FACEBOOK_APP_ID}/subscriptions",
        params={
            "object": "page", "callback_url": callback, "fields": "feed",
            "verify_token": config.IG_WEBHOOK_VERIFY_TOKEN, "include_values": "true",
            "access_token": app_token,
        },
        timeout=30,
    )
    out["assinatura_app_facebook"] = "ok" if r.status_code < 400 else _erro_graph(r)

    # a Página também precisa ter o app inscrito, senão a Meta não entrega o evento
    page_id, page_token = _pagina()
    if page_id:
        r = requests.post(
            f"{config.GRAPH}/{page_id}/subscribed_apps",
            params={"subscribed_fields": "feed", "access_token": page_token}, timeout=30,
        )
        out["assinatura_pagina"] = "ok" if r.status_code < 400 else _erro_graph(r)
    else:
        out["assinatura_pagina"] = "sem Página resolvida"

    out["ok"] = out.get("assinatura_app") == "ok"
    print(f"[automacoes] webhook: {out}")
    return out


_WEBHOOK_CACHE: dict = {"quando": None, "dados": None}


def webhook_status(forcar: bool = False) -> dict:
    """Se a Meta está mesmo entregando comentário na hora, nas DUAS redes.

    Pergunta pra Meta, não pro .env: ter verify_token configurado nunca significou
    assinatura ativa. E olha os dois objetos — checar só o Instagram deixaria a queda do
    Facebook invisível, que é exatamente como o webhook ficou desligado sem ninguém ver.
    """
    agora = datetime.now(timezone.utc)
    if not forcar and _WEBHOOK_CACHE["quando"] and agora - _WEBHOOK_CACHE["quando"] < timedelta(minutes=10):
        return _WEBHOOK_CACHE["dados"]
    esperado = {"instagram": "comments", "page": "feed"}
    dados: dict = {"instagram": False, "facebook": False, "callback": ""}
    try:
        r = requests.get(
            f"{config.GRAPH}/{config.FACEBOOK_APP_ID}/subscriptions",
            params={"access_token": f"{config.FACEBOOK_APP_ID}|{config.FACEBOOK_APP_SECRET}"},
            timeout=20,
        )
        for s in (r.json().get("data", []) if r.status_code < 400 else []):
            obj = s.get("object")
            if obj not in esperado:
                continue
            campos = [f.get("name") for f in s.get("fields", [])]
            vivo = bool(s.get("active")) and esperado[obj] in campos
            dados["instagram" if obj == "instagram" else "facebook"] = vivo
            dados["callback"] = s.get("callback_url", "") or dados["callback"]
    except Exception as e:  # noqa: BLE001
        dados["erro"] = str(e)
    dados["ativo"] = dados["instagram"] and dados["facebook"]
    _WEBHOOK_CACHE.update({"quando": agora, "dados": dados})
    return dados



def _inicio_facebook() -> datetime:
    """Marco zero da perna do Facebook: o instante em que ela entrou no ar.

    Sem isso, ligar o Facebook faria a automação tratar todo comentário antigo do post
    cruzado de uma vez — 449 pessoas de dois dias atrás recebendo direct sem contexto.
    A regra combinada é daqui pra frente, então o marco é gravado uma vez e não muda.
    """
    valor = db.get_meta(CHAVE_INICIO_FB)
    if not valor:
        valor = datetime.now(timezone.utc).isoformat()
        db.set_meta(CHAVE_INICIO_FB, valor)
        print(f"[automacoes] perna do Facebook começa a valer em {valor} (comentário antigo fica de fora).")
    return datetime.fromisoformat(valor)


def _quando_da_midia(midia_id: str) -> datetime | None:
    """Quando a mídia do Instagram foi publicada, pra ancorar o pareamento do crosspost."""
    for m in buscar_midias(limite=25):
        if m.get("id") == midia_id:
            return _parse_ts(m.get("timestamp"))
    return None


def _posts_facebook(a: dict) -> list[str]:
    """Posts da Página que correspondem ao alvo da automação.

    O post do Facebook é outro objeto, com outro id. Como o Pedro publica nos dois ao
    mesmo tempo, o pareamento é por horário: o post do Facebook publicado junto do post
    do Instagram que a automação engatou. Escopo 'todos' pega tudo desde a criação.
    """
    page_id, page_token = _pagina()
    if not page_id:
        return []
    r = requests.get(
        f"{config.GRAPH}/{page_id}/posts",
        params={"fields": "id,created_time", "limit": 25, "access_token": page_token}, timeout=30,
    )
    if r.status_code >= 400:
        print(f"[automacoes] não li os posts da Página: {_erro_graph(r)}")
        return []
    posts = r.json().get("data", [])

    if a["escopo"] == "todos":
        desde = _desde(a)
        return [p["id"] for p in posts if (_parse_ts(p.get("created_time")) or desde) >= desde]

    if not a.get("midia_id"):
        return []
    # âncora é a hora da PRÓPRIA mídia do Instagram. Usar a criação da automação erra
    # quando o post foi escolhido a mão, e dois reels publicados em sequência caem os
    # dois na janela — então além disso devolvemos só o post mais próximo, nunca dois.
    alvo = (_quando_da_midia(a["midia_id"]) or _parse_ts(a.get("engatada_em")) or _desde(a))
    folga = timedelta(minutes=JANELA_CROSSPOST_MIN)
    perto = [(abs(q - alvo), p["id"]) for p in posts
             if (q := _parse_ts(p.get("created_time"))) and abs(q - alvo) <= folga]
    return [min(perto)[1]] if perto else []


_NOME_ULTIMO_ERRO: dict = {"motivo": ""}


def nome_por_psid(psid: str) -> str:
    """Nome de quem recebeu o direct no Facebook.

    A Meta não devolve `from` nos comentários de post de Página, então o nome não vem
    pela leitura do comentário. Depois do direct entregue, porém, a pessoa é um contato
    da Página e o id dela (recipient_id) resolve o nome normalmente.
    """
    _, page_token = _pagina()
    if not (psid and page_token):
        return ""
    try:
        r = requests.get(f"{config.GRAPH}/{psid}",
                         params={"fields": "name", "access_token": page_token}, timeout=20)
        if r.status_code < 400:
            return r.json().get("name", "")
        motivo = _erro_graph(r)
        if motivo != _NOME_ULTIMO_ERRO["motivo"]:      # cada motivo novo loga uma vez só
            _NOME_ULTIMO_ERRO["motivo"] = motivo
            print(f"[automacoes] nome do Facebook indisponível: {motivo}")
    except Exception as e:  # noqa: BLE001
        _NOME_ULTIMO_ERRO["motivo"] = str(e)
    return ""


def _comentarios_facebook(post_id: str, desde: datetime | None = None) -> list[dict]:
    """Comentários de um post da Página, no mesmo formato do lado do Instagram."""
    _, page_token = _pagina()
    url = f"{config.GRAPH}/{post_id}/comments"
    params: dict | None = {"fields": "id,message,created_time,from{id,name}",
                           "filter": "stream", "limit": 50, "access_token": page_token}
    achatado: list[dict] = []
    for _ in range(MAX_PAGINAS):
        r = requests.get(url, params=params, timeout=30)
        if r.status_code >= 400:
            raise RuntimeError(_erro_graph(r))
        corpo = r.json()
        pagina = corpo.get("data", [])
        for c in pagina:
            quem = c.get("from") or {}
            achatado.append({"id": c["id"], "text": c.get("message", ""),
                             "username": quem.get("name") or quem.get("id", ""),
                             "autor_id": quem.get("id", ""),
                             "timestamp": c.get("created_time")})
        proxima = (corpo.get("paging") or {}).get("next")
        if not pagina or not proxima:
            break
        if desde and all((_parse_ts(c.get("created_time")) or desde) < desde for c in pagina):
            break
        url, params = proxima, None
    return achatado


def _motivo_sem_facebook(a: dict) -> str:
    """Por que a automação está sem alvo no Facebook.

    Lista vazia sozinha é ambígua: pode ser foto (que o Pedro não compartilha lá, então
    é o esperado) ou crosspost que não saiu. Confundir os dois já custou um alarme falso.
    """
    if not a.get("facebook"):
        return "Facebook desligado nesta automação"
    if not a.get("midia_id"):
        return "ainda sem post alvo"
    tipo = ""
    for m in buscar_midias(limite=25):
        if m.get("id") == a["midia_id"]:
            tipo = m.get("media_product_type") or m.get("media_type") or ""
            break
    if tipo and tipo != "REELS":
        return f"o post é {tipo.lower()}, e foto não é compartilhada no Facebook"
    return "não achei o post cruzado na Página (crosspost pode não ter saído)"


def diagnostico_alvos() -> list[dict]:
    """O que cada automação ativa está mirando e quanto tem pra processar.

    Serve pra separar "não disparou" de "não tem comentário": sem isso os dois parecem
    a mesma coisa no painel.
    """
    out = []
    for a in listar(so_ativas=True):
        item = {"id": a["id"], "nome": a["nome"], "escopo": a["escopo"],
                "midia_id": a.get("midia_id"), "desde": _desde(a).isoformat(),
                "erro_ultima_rodada": _ERRO_RODADA.get(a["id"])}
        # só resolve os alvos: contar comentário aqui varria milhares por automação e
        # fazia o próprio diagnóstico estourar o tempo da requisição
        try:
            item["alvos_ig"] = _midias_alvo(a)
        except Exception as e:  # noqa: BLE001
            item["alvos_ig"] = f"erro: {e}"
        try:
            item["alvos_fb"] = _posts_facebook(a) if a.get("facebook") else []
            if not item["alvos_fb"]:
                item["motivo_fb"] = _motivo_sem_facebook(a)
        except Exception as e:  # noqa: BLE001
            item["alvos_fb"] = f"erro: {e}"
        out.append(item)
    return out


def diagnostico_facebook() -> dict:
    """Só leitura: o que dá pra fazer na Página do Facebook com o token que já temos.

    Existe pra decidir a perna do Facebook com dado, não com suposição: se dá pra ler
    post e comentário, e se o app enxerga a caixa de mensagens da Página.
    """
    page_id, page_token = _pagina()
    if not page_id:
        return {"ok": False, "motivo": "sem Página resolvida no token"}
    out: dict = {"page_id": page_id}

    r = requests.get(
        f"{config.GRAPH}/{page_id}/posts",
        params={"fields": "id,created_time,permalink_url,comments.limit(1).summary(true)",
                "limit": 5, "access_token": page_token},
        timeout=30,
    )
    if r.status_code >= 400:
        out["posts"] = {"erro": _erro_graph(r)}
    else:
        out["posts"] = [{
            "id": p.get("id"), "quando": (p.get("created_time") or "")[:19],
            "comentarios": ((p.get("comments") or {}).get("summary") or {}).get("total_count", 0),
        } for p in r.json().get("data", [])]

    # a caixa de mensagens da Página: se isso responde, o produto de mensagem está liberado
    r = requests.get(
        f"{config.GRAPH}/{page_id}/conversations",
        params={"fields": "id", "limit": 1, "access_token": page_token}, timeout=30,
    )
    out["mensagens_da_pagina"] = "ok" if r.status_code < 400 else _erro_graph(r)

    r = requests.get(
        f"{config.GRAPH}/{page_id}/subscribed_apps",
        params={"access_token": page_token}, timeout=30,
    )
    # o /posts nem sempre lista reel cruzado do Instagram: compara com /feed e /video_reels
    for edge, campos in (("feed", "id,created_time"), ("video_reels", "id,created_time")):
        r = requests.get(f"{config.GRAPH}/{page_id}/{edge}",
                         params={"fields": campos, "limit": 6, "access_token": page_token}, timeout=30)
        out[edge] = ([{"id": x.get("id"), "quando": (x.get("created_time") or "")[:19]}
                      for x in r.json().get("data", [])] if r.status_code < 400 else _erro_graph(r))

    # amostra crua de um comentário: é onde dá pra ver quais campos a Meta entrega mesmo
    if isinstance(out.get("posts"), list) and out["posts"]:
        r = requests.get(
            f"{config.GRAPH}/{out['posts'][0]['id']}/comments",
            params={"fields": "id,message,created_time,from{id,name},username",
                    "limit": 2, "access_token": page_token}, timeout=30,
        )
        out["amostra_comentario"] = (r.json().get("data", []) if r.status_code < 400
                                     else {"erro": _erro_graph(r)})

    out["app_inscrito_na_pagina"] = (
        [s.get("subscribed_fields") for s in r.json().get("data", [])]
        if r.status_code < 400 else _erro_graph(r))
    return out


def marca_passo_vivo() -> dict:
    t = _THREAD["t"]
    return {"vivo": bool(t and t.is_alive()),
            "ultimo_pulso": _PULSO["quando"].isoformat() if _PULSO["quando"] else None,
            "voltas": _PULSO["voltas"]}


def iniciar_marca_passo():
    """Thread dedicada ao direct, com pulso e auto-religa.

    Não fica no APScheduler porque tick curto lá é descartado como misfire quando o
    worker está ocupado varrendo comentário. E como uma thread que morre calada já custou
    caro aqui, quem chama `rodar` confere o pulso e religa se preciso.
    """
    t = _THREAD["t"]
    if t and t.is_alive():
        return

    def laco():
        for tarefa in (garantir_webhook,):
            try:
                tarefa()
            except Exception as e:  # noqa: BLE001
                print(f"[automacoes] {tarefa.__name__} falhou: {e}")
        print(f"[automacoes] marca-passo do direct ligado ({_RITMO['minuto']}/min).")
        while True:
            _PULSO["quando"] = datetime.now(timezone.utc)
            _PULSO["voltas"] += 1
            try:
                pode, motivo = _pode_enviar_dm()
                if pode:
                    if not enviar_fila():
                        time.sleep(1)      # fila vazia: acorda rápido quando chegar comentário
                elif motivo in ("teto-da-hora", "descansando"):
                    time.sleep(10)
                elif motivo == "teto-do-minuto":
                    time.sleep(2)
                else:                      # só o espaçamento mínimo entre envios
                    time.sleep(ESPACO_MIN_S / 2)
            except Exception as e:  # noqa: BLE001
                print(f"[automacoes] marca-passo tropeçou: {e}")
                time.sleep(5)

    novo_t = threading.Thread(target=laco, name="direct", daemon=True)
    _THREAD["t"] = novo_t
    novo_t.start()


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
    """Eventos da Meta chegando pelo webhook (caminho instantâneo).

    Dois formatos no mesmo endereço:
      - Instagram: `field: comments`.
      - Facebook: `field: feed`, que traz de tudo da Página. Interessam dois casos:
        `item: comment` + `verb: add` (comentário novo) e publicação nova
        (`item: status|photo|video|reel|share` + `verb: add`), que é o gatilho de
        "próxima publicação" — o Instagram não tem webhook de publicação, então o
        crosspost do Facebook é o aviso mais rápido quando o post não sai daqui.
    """
    tratados = 0
    for entry in payload.get("entry", []):
        for ch in entry.get("changes", []):
            campo = ch.get("field")
            v = ch.get("value", {})
            if campo == "feed":
                # publicação nova na Página (o crosspost do reel): engata na hora quem
                # estava esperando, sem depender da varredura
                if v.get("item") in ("status", "photo", "video", "reel", "share") and v.get("verb") == "add":
                    for a in listar(so_ativas=True):
                        if a["escopo"] == "proximo" and not a.get("midia_id"):
                            engatar_proximo(a)
                    continue
                if v.get("item") != "comment" or v.get("verb") != "add":
                    continue
                plataforma = "fb"
                comentario = {
                    "id": v.get("comment_id"),
                    "text": v.get("message", ""),
                    "username": (v.get("from") or {}).get("name", ""),
                    "autor_id": (v.get("from") or {}).get("id", ""),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                midia_id = v.get("post_id", "")
            elif campo == "comments":
                plataforma = "ig"
                comentario = {
                    "id": v.get("id"),
                    "text": v.get("text", ""),
                    "username": (v.get("from") or {}).get("username", ""),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                midia_id = (v.get("media") or {}).get("id", "")
            else:
                continue
            if not comentario["id"]:
                continue
            for a in listar(so_ativas=True):
                if plataforma == "fb" and not (a.get("facebook") and config.AUTOMACOES_FACEBOOK):
                    continue
                if a["escopo"] == "proximo" and not a.get("midia_id"):
                    a = engatar_proximo(a)
                alvos = _posts_facebook(a) if plataforma == "fb" else _midias_alvo(a)
                if a["escopo"] != "todos" and midia_id not in alvos:
                    continue
                if plataforma == "fb" and datetime.now(timezone.utc) < _inicio_facebook():
                    continue
                if tratar_comentario(a, midia_id, comentario, plataforma):
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
