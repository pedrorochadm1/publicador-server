"""Banco do Laboratório DM1: cards, roteiro e publicações.

Mora no mesmo /data/agenda.db do publicador, com as tabelas prefixadas por lab_.
Um arquivo só = um backup consistente, e permite cruzar um card com a publicação
real em `posts` no futuro.

Duas regras de negócio vivem aqui, e não no cliente:

1. O STATUS é derivado do roteiro. Assim que qualquer campo (hook, um
   desenvolvimento ou o fechamento) ganha conteúdo, o card vira 'producao'
   sozinho; se o roteiro é esvaziado, volta pra 'ideia'. Não existe botão de
   mover coluna. Recalculamos em toda escrita pra que a regra valha mesmo se o
   cliente estiver bugado, offline ou numa aba velha.

2. Card 'publicado' é histórico imutável: nunca regride sozinho, só pode ser
   duplicado.
"""
import json
from datetime import datetime, timezone

from . import lab_calculo as lc
from .db import conn

TIPOS = ("conteudo", "anuncio")
FORMATOS = ("lofi", "slide", "vlog", "documentario")
STATUS = ("ideia", "producao", "publicado")

_iniciado = False


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init():
    global _iniciado
    if _iniciado:
        return
    c = conn()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS lab_cards (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo        TEXT    NOT NULL DEFAULT '',
            tipo          TEXT,                              -- 'conteudo'|'anuncio'|NULL
            formato       TEXT,                              -- 'lofi'|'slide'|'vlog'|'documentario'|NULL
            hook          TEXT    NOT NULL DEFAULT '',
            fechamento    TEXT    NOT NULL DEFAULT '',
            status        TEXT    NOT NULL DEFAULT 'ideia',  -- derivado, mas materializado
            publicado_em  TEXT,
            tags          TEXT    NOT NULL DEFAULT '[]',
            ordem         REAL    NOT NULL DEFAULT 0,        -- REAL: inserção fracionária no drag&drop
            arquivado     INTEGER NOT NULL DEFAULT 0,
            client_uuid   TEXT,                              -- idempotência da captura offline
            criado_em     TEXT    NOT NULL,
            atualizado_em TEXT    NOT NULL
        )
        """
    )
    c.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_lab_cards_uuid "
        "ON lab_cards (client_uuid) WHERE client_uuid IS NOT NULL"
    )
    c.execute("CREATE INDEX IF NOT EXISTS ix_lab_cards_col ON lab_cards (status, arquivado, ordem)")
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS lab_desenvolvimentos (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id INTEGER NOT NULL,
            ordem   INTEGER NOT NULL DEFAULT 0,
            texto   TEXT    NOT NULL DEFAULT ''
        )
        """
    )
    c.execute("CREATE INDEX IF NOT EXISTS ix_lab_desen_card ON lab_desenvolvimentos (card_id, ordem)")
    # Links do card, em duas listas:
    #   'referencia' = o embasamento (estudo, post, matéria)
    #   'reacao'     = vídeos que o Pedro vai reagir DENTRO do vídeo dele
    # Não entram na derivação do status: link não é roteiro.
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS lab_links (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id INTEGER NOT NULL,
            lista   TEXT    NOT NULL,
            url     TEXT    NOT NULL DEFAULT '',
            nota    TEXT    NOT NULL DEFAULT '',
            ordem   INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    c.execute("CREATE INDEX IF NOT EXISTS ix_lab_links_card ON lab_links (card_id, lista, ordem)")
    # Fonte ÚNICA do saldo. Separada de lab_cards porque o backfill cria registros
    # sem card, e duplicar um card publicado não pode duplicar a publicação.
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS lab_publicacoes (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id      INTEGER,                     -- NULL nos registros do backfill
            tipo         TEXT    NOT NULL,
            formato      TEXT,
            publicado_em TEXT    NOT NULL,
            sintetico    INTEGER NOT NULL DEFAULT 0,  -- 1 = veio do onboarding
            criado_em    TEXT    NOT NULL
        )
        """
    )
    c.execute("CREATE INDEX IF NOT EXISTS ix_lab_pub_quando ON lab_publicacoes (publicado_em)")
    c.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_lab_pub_card "
        "ON lab_publicacoes (card_id) WHERE card_id IS NOT NULL"
    )
    c.execute("CREATE TABLE IF NOT EXISTS lab_config (chave TEXT PRIMARY KEY, valor TEXT NOT NULL)")
    c.commit()
    _iniciado = True


# ─────────────────────────── Status derivado ───────────────────────────

def tem_roteiro(hook: str, fechamento: str, desenvolvimentos: list[str]) -> bool:
    return bool(hook.strip() or fechamento.strip() or any(d.strip() for d in desenvolvimentos))


def _status_derivado(atual: str, hook: str, fechamento: str, desen: list[str]) -> str:
    if atual == "publicado":
        return "publicado"        # histórico imutável: nunca regride sozinho
    return "producao" if tem_roteiro(hook, fechamento, desen) else "ideia"


# ─────────────────────────── Leitura ───────────────────────────

LISTAS_LINK = {"referencias": "referencia", "reacoes": "reacao"}


def _linha(r, filhos=True) -> dict:
    d = dict(r)
    d["tags"] = json.loads(d["tags"] or "[]")
    d["arquivado"] = bool(d["arquivado"])
    if filhos:
        d["desenvolvimentos"] = _desen_do_card(d["id"])
        for campo, lista in LISTAS_LINK.items():
            d[campo] = _links_do_card(d["id"], lista)
    else:
        d["desenvolvimentos"] = []
        for campo in LISTAS_LINK:
            d[campo] = []
    return d


def _desen_do_card(card_id: int) -> list[dict]:
    rows = conn().execute(
        "SELECT id, ordem, texto FROM lab_desenvolvimentos WHERE card_id = ? ORDER BY ordem, id",
        (card_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _links_do_card(card_id: int, lista: str) -> list[dict]:
    rows = conn().execute(
        "SELECT id, ordem, url, nota FROM lab_links WHERE card_id = ? AND lista = ? "
        "ORDER BY ordem, id", (card_id, lista),
    ).fetchall()
    return [dict(r) for r in rows]


def get_card(card_id: int) -> dict | None:
    _init()
    r = conn().execute("SELECT * FROM lab_cards WHERE id = ?", (card_id,)).fetchone()
    return _linha(r) if r else None


def listar_cards(publicados_desde: str | None = None) -> list[dict]:
    """Todos os cards com seus filhos, em três queries (sem N+1)."""
    _init()
    c = conn()
    # A coluna `arquivado` continua no schema, mas nada a liga: excluir agora
    # apaga de vez. O filtro fica como rede de segurança e custa zero.
    onde, params = ["arquivado = 0"], []
    if publicados_desde:
        # Cards publicados velhos demais somem da coluna; ideia/produção sempre ficam.
        onde.append("(status != 'publicado' OR publicado_em >= ?)")
        params.append(publicados_desde)
    sql = "SELECT * FROM lab_cards"
    if onde:
        sql += " WHERE " + " AND ".join(onde)
    sql += " ORDER BY ordem, id DESC"
    cards = [_linha(r, filhos=False) for r in c.execute(sql, params).fetchall()]
    if not cards:
        return []
    # Os filhos vêm em duas queries, não uma por card (evita N+1 no board).
    por_id = {c_["id"]: c_ for c_ in cards}
    for r in c.execute(
        "SELECT id, card_id, ordem, texto FROM lab_desenvolvimentos ORDER BY ordem, id"
    ).fetchall():
        alvo = por_id.get(r["card_id"])
        if alvo is not None:
            alvo["desenvolvimentos"].append({"id": r["id"], "ordem": r["ordem"], "texto": r["texto"]})
    invertido = {v: k for k, v in LISTAS_LINK.items()}
    for r in c.execute(
        "SELECT id, card_id, lista, ordem, url, nota FROM lab_links ORDER BY ordem, id"
    ).fetchall():
        alvo = por_id.get(r["card_id"])
        campo = invertido.get(r["lista"])
        if alvo is not None and campo:
            alvo[campo].append({"id": r["id"], "ordem": r["ordem"], "url": r["url"], "nota": r["nota"]})
    return cards


# ─────────────────────────── Escrita ───────────────────────────

def criar_card(titulo: str, client_uuid: str | None = None) -> dict:
    """Captura relâmpago. client_uuid repetido devolve o card existente em vez de
    criar outro — é o que torna seguro reenviar a fila offline."""
    _init()
    c = conn()
    if client_uuid:
        r = c.execute("SELECT * FROM lab_cards WHERE client_uuid = ?", (client_uuid,)).fetchone()
        if r:
            return _linha(r, _desen_do_card(r["id"]))
    agora = _agora()
    # Card novo entra no topo da coluna 'ideia'.
    menor = c.execute("SELECT MIN(ordem) FROM lab_cards").fetchone()[0]
    ordem = (menor - 1) if menor is not None else 0
    cur = c.execute(
        "INSERT INTO lab_cards (titulo, ordem, client_uuid, criado_em, atualizado_em) "
        "VALUES (?, ?, ?, ?, ?)",
        (titulo.strip(), ordem, client_uuid, agora, agora),
    )
    c.commit()
    return get_card(cur.lastrowid)


_CAMPOS_TEXTO = ("titulo", "hook", "fechamento")


def _sincronizar(c, tabela: str, card_id: int, filtro_extra: str, params_extra: list,
                 itens: list, colunas: list[str], valores) -> None:
    """A lista chega INTEIRA do cliente: atualiza quem veio com id, insere quem
    veio sem, apaga quem sumiu. Os ids voltam na resposta pra que as chaves do
    DOM continuem estáveis e o cursor não pule enquanto o Pedro digita."""
    mantidos = []
    for i, item in enumerate(itens):
        vals = valores(item, i)
        iid = item.get("id") if isinstance(item, dict) else None
        if iid:
            sets = ", ".join(f"{col} = ?" for col in colunas)
            c.execute(
                f"UPDATE {tabela} SET {sets} WHERE id = ? AND card_id = ?{filtro_extra}",
                [*vals, iid, card_id, *params_extra],
            )
            mantidos.append(iid)
        else:
            campos = ", ".join(["card_id", *colunas])
            marcas = ", ".join("?" * (len(colunas) + 1))
            cur = c.execute(
                f"INSERT INTO {tabela} ({campos}) VALUES ({marcas})", [card_id, *vals])
            mantidos.append(cur.lastrowid)
    sql = f"DELETE FROM {tabela} WHERE card_id = ?{filtro_extra}"
    args = [card_id, *params_extra]
    if mantidos:
        sql += f" AND id NOT IN ({','.join('?' * len(mantidos))})"
        args += mantidos
    c.execute(sql, args)


def _sincronizar_desenvolvimentos(c, card_id: int, itens: list) -> None:
    normalizados = [{"texto": i} if isinstance(i, str) else i for i in itens]
    _sincronizar(
        c, "lab_desenvolvimentos", card_id, "", [], normalizados,
        ["ordem", "texto"],
        lambda item, i: (i, str(item.get("texto") or "")),
    )


def _sincronizar_links(c, card_id: int, lista: str, itens: list) -> None:
    normalizados = [{"url": i} if isinstance(i, str) else i for i in itens]
    # Link sem URL nenhuma é linha vazia esquecida na tela: não vale gravar.
    normalizados = [i for i in normalizados
                    if str(i.get("url") or "").strip() or str(i.get("nota") or "").strip()]
    _sincronizar(
        c, "lab_links", card_id, " AND lista = ?", [lista], normalizados,
        ["lista", "ordem", "url", "nota"],
        lambda item, i: (lista, i, str(item.get("url") or "").strip(),
                         str(item.get("nota") or "").strip()),
    )


def atualizar_card(card_id: int, dados: dict) -> dict | None:
    """Autosave. Aceita qualquer subconjunto de campos; recalcula o status."""
    _init()
    atual = get_card(card_id)
    if not atual:
        return None
    c = conn()
    sets, params = [], []

    for campo in _CAMPOS_TEXTO:
        if campo in dados:
            sets.append(f"{campo} = ?")
            params.append(str(dados[campo] or ""))
    if "tipo" in dados:
        v = dados["tipo"] or None
        if v is not None and v not in TIPOS:
            raise ValueError(f"tipo inválido: {v}")
        sets.append("tipo = ?")
        params.append(v)
    if "formato" in dados:
        v = dados["formato"] or None
        if v is not None and v not in FORMATOS:
            raise ValueError(f"formato inválido: {v}")
        sets.append("formato = ?")
        params.append(v)
    if "tags" in dados:
        sets.append("tags = ?")
        params.append(json.dumps(list(dados["tags"] or [])))
    if "arquivado" in dados:
        sets.append("arquivado = ?")
        params.append(1 if dados["arquivado"] else 0)
    if "ordem" in dados:
        sets.append("ordem = ?")
        params.append(float(dados["ordem"]))

    if "desenvolvimentos" in dados:
        _sincronizar_desenvolvimentos(c, card_id, dados["desenvolvimentos"] or [])
    for campo, lista in LISTAS_LINK.items():
        if campo in dados:
            _sincronizar_links(c, card_id, lista, dados[campo] or [])

    if sets:
        sets.append("atualizado_em = ?")
        params.append(_agora())
        params.append(card_id)
        c.execute(f"UPDATE lab_cards SET {', '.join(sets)} WHERE id = ?", params)
    c.commit()

    # Status sempre recalculado a partir do que ficou gravado de fato.
    depois = get_card(card_id)
    novo = _status_derivado(
        depois["status"], depois["hook"], depois["fechamento"],
        [d["texto"] for d in depois["desenvolvimentos"]],
    )
    if novo != depois["status"]:
        c.execute("UPDATE lab_cards SET status = ?, atualizado_em = ? WHERE id = ?",
                  (novo, _agora(), card_id))
        c.commit()
        depois = get_card(card_id)
    return depois


class ErroPublicar(Exception):
    def __init__(self, codigo: str, detalhe: str):
        super().__init__(detalhe)
        self.codigo = codigo
        self.detalhe = detalhe


def publicar_card(card_id: int, quando: datetime | None = None) -> dict:
    """Botão 'Publiquei'. Exige tipo e formato porque a proporção depende deles."""
    _init()
    card = get_card(card_id)
    if not card:
        raise ErroPublicar("nao_encontrado", "Card não encontrado.")
    if card["status"] == "publicado":
        raise ErroPublicar("ja_publicado", "Esse card já foi publicado.")
    if not card["tipo"]:
        raise ErroPublicar("falta_tipo", "Defina se é conteúdo ou anúncio antes de publicar.")
    if not card["formato"]:
        raise ErroPublicar("falta_formato", "Defina o formato antes de publicar.")
    quando = quando or datetime.now(timezone.utc)
    iso = quando.isoformat()
    c = conn()
    c.execute(
        "UPDATE lab_cards SET status = 'publicado', publicado_em = ?, atualizado_em = ? WHERE id = ?",
        (iso, _agora(), card_id),
    )
    c.execute(
        "INSERT INTO lab_publicacoes (card_id, tipo, formato, publicado_em, sintetico, criado_em) "
        "VALUES (?, ?, ?, ?, 0, ?)",
        (card_id, card["tipo"], card["formato"], iso, _agora()),
    )
    c.commit()
    return get_card(card_id)


def duplicar_card(card_id: int) -> dict | None:
    """Card publicado é histórico: reaproveitar significa duplicar, nunca reabrir."""
    _init()
    orig = get_card(card_id)
    if not orig:
        return None
    novo = criar_card(f"{orig['titulo']} (cópia)")
    dados = {
        "tipo": orig["tipo"],
        "formato": orig["formato"],
        "hook": orig["hook"],
        "fechamento": orig["fechamento"],
        "tags": orig["tags"],
        "desenvolvimentos": [{"texto": d["texto"]} for d in orig["desenvolvimentos"]],
    }
    for campo in LISTAS_LINK:
        dados[campo] = [{"url": l["url"], "nota": l["nota"]} for l in orig[campo]]
    return atualizar_card(novo["id"], dados)


def remover_card(card_id: int) -> bool:
    """Apaga de vez: o card, o roteiro e os links.

    Se o card estava publicado, a publicação sai junto — e o saldo da régua
    muda, porque ela conta publicações, não cards. Quem chama é responsável por
    avisar o Pedro disso ANTES de confirmar.
    """
    _init()
    if not get_card(card_id):
        return False
    c = conn()
    c.execute("DELETE FROM lab_desenvolvimentos WHERE card_id = ?", (card_id,))
    c.execute("DELETE FROM lab_links WHERE card_id = ?", (card_id,))
    c.execute("DELETE FROM lab_publicacoes WHERE card_id = ?", (card_id,))
    c.execute("DELETE FROM lab_cards WHERE id = ?", (card_id,))
    c.commit()
    return True


# ─────────────────────────── Publicações (fonte do saldo) ───────────────────────────

def publicacoes(desde: datetime | None = None) -> list[tuple[str, str]]:
    """[(tipo, publicado_em)] pro motor de cálculo."""
    _init()
    if desde:
        rows = conn().execute(
            "SELECT tipo, publicado_em FROM lab_publicacoes WHERE publicado_em >= ? "
            "ORDER BY publicado_em", (desde.isoformat(),)
        ).fetchall()
    else:
        rows = conn().execute(
            "SELECT tipo, publicado_em FROM lab_publicacoes ORDER BY publicado_em"
        ).fetchall()
    return [(r["tipo"], r["publicado_em"]) for r in rows]


def historico(limite: int = 200) -> list[dict]:
    """Publicações com o título do card, pro detalhamento da régua."""
    _init()
    rows = conn().execute(
        "SELECT p.id, p.card_id, p.tipo, p.formato, p.publicado_em, p.sintetico, c.titulo "
        "FROM lab_publicacoes p LEFT JOIN lab_cards c ON c.id = p.card_id "
        "ORDER BY p.publicado_em DESC LIMIT ?", (limite,)
    ).fetchall()
    agora = datetime.now(timezone.utc)
    saida = []
    for r in rows:
        d = dict(r)
        d["sintetico"] = bool(d["sintetico"])
        quando = datetime.fromisoformat(d["publicado_em"])
        if quando.tzinfo is None:
            quando = quando.replace(tzinfo=timezone.utc)
        d["peso"] = lc.peso(lc.idade_em_dias(quando, agora))
        saida.append(d)
    return saida


def total_publicacoes() -> int:
    _init()
    return conn().execute("SELECT COUNT(*) FROM lab_publicacoes").fetchone()[0]


def backfill(janelas: dict) -> int:
    """Partida a frio: cria publicações sintéticas espalhadas dentro de cada janela.

    Roda uma vez só — chamar de novo apaga as sintéticas anteriores em vez de
    somar, pra Pedro poder corrigir o número sem inflar o saldo.
    """
    _init()
    c = conn()
    c.execute("DELETE FROM lab_publicacoes WHERE sintetico = 1")
    agora = datetime.now(timezone.utc)
    criadas = 0
    for nome, ini, fim in lc.JANELAS:
        dados = janelas.get(nome) or {}
        for tipo in ("conteudos", "anuncios"):
            n = int(dados.get(tipo) or 0)
            if n <= 0:
                continue
            rotulo = lc.CONTEUDO if tipo == "conteudos" else lc.ANUNCIO
            for quando in lc.datas_backfill(ini, fim, n, agora):
                c.execute(
                    "INSERT INTO lab_publicacoes (card_id, tipo, formato, publicado_em, "
                    "sintetico, criado_em) VALUES (NULL, ?, NULL, ?, 1, ?)",
                    (rotulo, quando.isoformat(), _agora()),
                )
                criadas += 1
    c.commit()
    return criadas


# ─────────────────────────── Config ───────────────────────────

PADRAO_CONFIG = {
    "onboarding": {"feito": False, "pulou": False, "em": None},
    "autofoco": True,
    "meta_semanal": lc.META_SEMANAL_PADRAO,
    "filtros": {"tipo": [], "formato": []},
    "export": {"incluir_tipo_formato": True, "marcar_lacunas": False},
}


def get_config() -> dict:
    _init()
    saida = json.loads(json.dumps(PADRAO_CONFIG))     # cópia funda
    for r in conn().execute("SELECT chave, valor FROM lab_config").fetchall():
        if r["chave"] in saida:
            try:
                saida[r["chave"]] = json.loads(r["valor"])
            except json.JSONDecodeError:
                pass
    return saida


def set_config(parcial: dict) -> dict:
    _init()
    c = conn()
    for chave, valor in (parcial or {}).items():
        if chave not in PADRAO_CONFIG:
            continue
        c.execute(
            "INSERT INTO lab_config (chave, valor) VALUES (?, ?) "
            "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
            (chave, json.dumps(valor)),
        )
    c.commit()
    return get_config()
