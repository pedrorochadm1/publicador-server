"""Aba Carrossel do Laboratório: o documento do carrossel, as imagens de origem
e a publicação.

Três decisões que valem explicação:

* O CARROSSEL É UM DOCUMENTO. Os slides vivem numa coluna JSON e o editor manda
  o documento inteiro a cada autosave. Um slide não tem vida fora do carrossel
  (ordem, texto e recorte só fazem sentido juntos), então tabela filha seria
  só cerimônia.

* QUEM DESENHA O SLIDE É O NAVEGADOR. O preview e o JPEG final saem da mesma
  função de canvas (carrossel_render.js), pra que o que o Pedro vê seja o que
  sai. O servidor só recebe os JPEGs prontos na hora de publicar.

* AS IMAGENS DE ORIGEM FICAM FORA DE /data/img. Aquela pasta é hospedagem
  temporária: o scheduler apaga a mídia depois de publicar. A foto que o Pedro
  subiu e recortou precisa sobreviver pra ele duplicar ou reeditar o carrossel.
"""
import json
import os
import re
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Cookie, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from . import buffer_api, config, db, sessoes
from .db import conn

router = APIRouter()

MAX_SLIDES = 10                       # teto do Instagram
TAMANHOS = ("P", "M", "G", "GG")      # alturas moram no front (carrossel_render.js)
_CAMPOS_TEXTO = ("titulo", "legenda")
_EXT_IMG = {"jpg": "jpg", "jpeg": "jpg", "png": "png", "webp": "webp"}
_NOME_IMG = re.compile(r"^[0-9a-f]{32}\.(jpg|png|webp)$")
_MAX_BYTES = 15 * 1024 * 1024

DIR_IMAGENS = os.path.join(config.DATA_DIR, "carrossel")
os.makedirs(DIR_IMAGENS, exist_ok=True)

_iniciado = False


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _exige(sess: str | None):
    if not sessoes.valida(sess):
        raise HTTPException(status_code=401, detail="Sessão expirada. Entre de novo.")


def _init():
    global _iniciado
    if _iniciado:
        return
    c = conn()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS lab_carrosseis (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            client_uuid   TEXT,
            origem        TEXT    NOT NULL DEFAULT 'manual',  -- 'manual'|'reel'|'ideia'
            media_id      TEXT,                               -- reel de origem
            ideia         TEXT    NOT NULL DEFAULT '',
            titulo        TEXT    NOT NULL DEFAULT '',
            legenda       TEXT    NOT NULL DEFAULT '',
            slides        TEXT    NOT NULL DEFAULT '[]',
            pesquisa      TEXT    NOT NULL DEFAULT '{}',
            ia            TEXT    NOT NULL DEFAULT '{}',
            post_id       INTEGER,                            -- posts.id da última publicação
            arquivado     INTEGER NOT NULL DEFAULT 0,
            criado_em     TEXT    NOT NULL,
            atualizado_em TEXT    NOT NULL
        )
        """
    )
    c.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_lab_carrossel_uuid "
        "ON lab_carrosseis (client_uuid) WHERE client_uuid IS NOT NULL"
    )
    c.commit()
    _iniciado = True


# ─────────────────────────── Documento ───────────────────────────

def _num(v, padrao: float, minimo: float, maximo: float) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return padrao
    return max(minimo, min(maximo, n))


def _imagem(i) -> dict | None:
    if not isinstance(i, dict):
        return None
    arquivo = str(i.get("arquivo") or "")
    if not _NOME_IMG.match(arquivo):
        return None
    return {
        "arquivo": arquivo,
        "tamanho": i.get("tamanho") if i.get("tamanho") in TAMANHOS else "M",
        # zoom 1 = a imagem cobre o slot exatamente; cx/cy = centro do recorte (0-1)
        "zoom": _num(i.get("zoom"), 1, 1, 5),
        "cx": _num(i.get("cx"), 0.5, 0, 1),
        "cy": _num(i.get("cy"), 0.5, 0, 1),
    }


def limpar_slide(s) -> dict:
    s = s if isinstance(s, dict) else {}
    return {
        "texto": str(s.get("texto") or ""),
        "texto_abaixo": str(s.get("texto_abaixo") or ""),
        "header": s.get("header") is not False,
        "imagem": _imagem(s.get("imagem")),
        "intencao": str(s.get("intencao") or ""),
    }


def _slides(lista) -> list[dict]:
    if not isinstance(lista, list):
        raise ValueError("slides precisa ser uma lista.")
    if len(lista) > MAX_SLIDES:
        raise ValueError(f"O Instagram aceita no máximo {MAX_SLIDES} slides.")
    return [limpar_slide(s) for s in lista]


def _status(d: dict, post: dict | None) -> str:
    if not post:
        return "rascunho"
    return {"agendado": "agendado", "publicando": "publicando",
            "publicado": "publicado", "erro": "erro"}.get(post["status"], "rascunho")


def _linha(r) -> dict:
    d = dict(r)
    for campo, padrao in (("slides", []), ("pesquisa", {}), ("ia", {})):
        d[campo] = json.loads(d[campo] or json.dumps(padrao))
    d["arquivado"] = bool(d["arquivado"])
    post = db.get_post(d["post_id"]) if d.get("post_id") else None
    d["status"] = _status(d, post)
    d["publicacao"] = None if not post else {
        "id": post["id"], "status": post["status"], "publicar_em": post["publicar_em"],
        "erro": post.get("erro"), "ig_post_id": post.get("ig_post_id"),
        "resultados": post.get("resultados"),
    }
    return d


def get(cid: int) -> dict | None:
    _init()
    r = conn().execute("SELECT * FROM lab_carrosseis WHERE id = ?", (cid,)).fetchone()
    return _linha(r) if r else None


def listar() -> list[dict]:
    _init()
    rows = conn().execute(
        "SELECT * FROM lab_carrosseis WHERE arquivado = 0 ORDER BY atualizado_em DESC"
    ).fetchall()
    return [_linha(r) for r in rows]


def criar(dados: dict) -> dict:
    _init()
    c = conn()
    uid = dados.get("client_uuid") or None
    if uid:
        r = c.execute("SELECT id FROM lab_carrosseis WHERE client_uuid = ?", (uid,)).fetchone()
        if r:
            return get(r["id"])
    origem = dados.get("origem") if dados.get("origem") in ("manual", "reel", "ideia") else "manual"
    slides = _slides(dados.get("slides") or [limpar_slide({})])
    agora = _agora()
    cur = c.execute(
        "INSERT INTO lab_carrosseis (client_uuid, origem, media_id, ideia, titulo, legenda, slides, "
        "pesquisa, ia, criado_em, atualizado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (uid, origem, dados.get("media_id") or None, str(dados.get("ideia") or ""),
         str(dados.get("titulo") or "").strip(), str(dados.get("legenda") or ""),
         json.dumps(slides), json.dumps(dados.get("pesquisa") or {}),
         json.dumps(dados.get("ia") or {}), agora, agora),
    )
    c.commit()
    return get(cur.lastrowid)


def atualizar(cid: int, dados: dict) -> dict | None:
    _init()
    if not get(cid):
        return None
    sets, params = [], []
    for campo in _CAMPOS_TEXTO:
        if campo in dados:
            sets.append(f"{campo} = ?")
            params.append(str(dados[campo] or ""))
    if "slides" in dados:
        sets.append("slides = ?")
        params.append(json.dumps(_slides(dados["slides"])))
    for campo in ("pesquisa", "ia"):
        if campo in dados and isinstance(dados[campo], dict):
            sets.append(f"{campo} = ?")
            params.append(json.dumps(dados[campo]))
    if not sets:
        return get(cid)
    sets.append("atualizado_em = ?")
    params += [_agora(), cid]
    c = conn()
    c.execute(f"UPDATE lab_carrosseis SET {', '.join(sets)} WHERE id = ?", params)
    c.commit()
    return get(cid)


def arquivar(cid: int) -> bool:
    _init()
    c = conn()
    cur = c.execute("UPDATE lab_carrosseis SET arquivado = 1, atualizado_em = ? WHERE id = ?",
                    (_agora(), cid))
    c.commit()
    return cur.rowcount > 0


def duplicar(cid: int) -> dict | None:
    d = get(cid)
    if not d:
        return None
    return criar({
        "origem": d["origem"], "media_id": d["media_id"], "ideia": d["ideia"],
        "titulo": (d["titulo"] + " (cópia)").strip(), "legenda": d["legenda"],
        "slides": d["slides"], "pesquisa": d["pesquisa"], "ia": d["ia"],
    })


def _vincular_post(cid: int, post_id: int):
    c = conn()
    c.execute("UPDATE lab_carrosseis SET post_id = ?, atualizado_em = ? WHERE id = ?",
              (post_id, _agora(), cid))
    c.commit()


# ─────────────────────────── Rotas: documento ───────────────────────────

@router.get("/lab/api/carrosseis")
def api_listar(insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    return {"carrosseis": listar(), "tiktok_max_fotos": buffer_api.MAX_FOTOS}


@router.post("/lab/api/carrosseis")
def api_criar(dados: dict = Body(...), insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    try:
        return criar(dados)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/lab/api/carrosseis/{cid}")
def api_um(cid: int, insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    d = get(cid)
    if not d:
        raise HTTPException(status_code=404, detail="Carrossel não encontrado.")
    return d


@router.patch("/lab/api/carrosseis/{cid}")
def api_atualizar(cid: int, dados: dict = Body(...), insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    try:
        d = atualizar(cid, dados)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not d:
        raise HTTPException(status_code=404, detail="Carrossel não encontrado.")
    return d


@router.post("/lab/api/carrosseis/{cid}/duplicar")
def api_duplicar(cid: int, insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    d = duplicar(cid)
    if not d:
        raise HTTPException(status_code=404, detail="Carrossel não encontrado.")
    return d


@router.delete("/lab/api/carrosseis/{cid}")
def api_arquivar(cid: int, insta_sess: str | None = Cookie(default=None)):
    """Arquiva, não apaga: o carrossel some da lista mas o registro fica."""
    _exige(insta_sess)
    if not arquivar(cid):
        raise HTTPException(status_code=404, detail="Carrossel não encontrado.")
    return {"ok": True}


# ─────────────────────────── Rotas: imagens de origem ───────────────────────────

def _tipo_real(dados: bytes) -> str | None:
    if dados.startswith(b"\xff\xd8"):
        return "jpg"
    if dados.startswith(b"\x89PNG"):
        return "png"
    if dados[:4] == b"RIFF" and dados[8:12] == b"WEBP":
        return "webp"
    return None


@router.post("/lab/api/carrossel/imagens")
async def api_subir_imagem(arquivo: UploadFile = File(...),
                           insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    ext = (arquivo.filename or "").rsplit(".", 1)[-1].lower()
    ext = _EXT_IMG.get(ext) or {"image/jpeg": "jpg", "image/png": "png",
                                "image/webp": "webp"}.get(arquivo.content_type or "")
    if not ext:
        raise HTTPException(status_code=400, detail="Use uma imagem JPG, PNG ou WEBP.")
    dados = await arquivo.read(_MAX_BYTES + 1)
    if len(dados) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="Imagem acima de 15MB.")
    # A extensão pode mentir; o cabeçalho do arquivo não. É ele que decide.
    ext = _tipo_real(dados)
    if not ext:
        raise HTTPException(status_code=400, detail="Esse arquivo não é uma imagem JPG, PNG ou WEBP.")
    nome = f"{uuid.uuid4().hex}.{ext}"
    with open(os.path.join(DIR_IMAGENS, nome), "wb") as f:
        f.write(dados)
    return {"arquivo": nome}


@router.get("/lab/api/carrossel/imagens/{nome}")
def api_imagem(nome: str, insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    caminho = os.path.join(DIR_IMAGENS, nome)
    if not _NOME_IMG.match(nome) or not os.path.exists(caminho):
        raise HTTPException(status_code=404, detail="Imagem não encontrada.")
    # Nome é uuid: o conteúdo nunca muda, pode cachear pra sempre.
    return FileResponse(caminho, headers={"Cache-Control": "private, max-age=31536000, immutable"})


# ─────────────────────────── Rota: publicar ───────────────────────────

def _quando(agendar_para: str) -> datetime:
    if not agendar_para.strip():
        return datetime.now(timezone.utc)
    try:
        q = datetime.fromisoformat(agendar_para.strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="Horário inválido.")
    if q.tzinfo is None:
        q = q.replace(tzinfo=ZoneInfo(config.TZ))
    q = q.astimezone(timezone.utc)
    if q < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Esse horário já passou.")
    return q


@router.post("/lab/api/carrosseis/{cid}/publicar")
async def api_publicar(cid: int,
                       slides: list[UploadFile] = File(...),
                       agendar_para: str = Form(""),
                       tiktok: str = Form("1"),
                       insta_sess: str | None = Cookie(default=None)):
    """Recebe os JPEGs já desenhados pelo navegador, na ordem, e põe na fila do
    publicador. Daqui pra frente é o mesmo caminho de qualquer post: o scheduler
    publica no IG e faz o fan-out do TikTok pela legenda idêntica."""
    _exige(insta_sess)
    d = get(cid)
    if not d:
        raise HTTPException(status_code=404, detail="Carrossel não encontrado.")
    legenda = d["legenda"].strip()
    if not legenda:
        raise HTTPException(status_code=400, detail="Escreva a legenda antes de publicar.")
    if not 1 <= len(slides) <= MAX_SLIDES:
        raise HTTPException(status_code=400, detail=f"Envie de 1 a {MAX_SLIDES} slides.")
    anterior = d["publicacao"]
    if anterior and anterior["status"] in ("publicado", "publicando"):
        raise HTTPException(status_code=409, detail="Esse carrossel já foi publicado. Duplique pra fazer outro.")
    quando = _quando(agendar_para)

    nomes = []
    for up in slides:
        raw = await up.read(_MAX_BYTES + 1)
        # O Graph API só aceita JPEG em image_url; o front já exporta assim.
        if not raw.startswith(b"\xff\xd8") or len(raw) > _MAX_BYTES:
            raise HTTPException(status_code=400, detail="Os slides precisam ser JPEG de até 15MB.")
        nome = f"{uuid.uuid4().hex}.jpg"
        with open(os.path.join(config.IMG_DIR, nome), "wb") as f:
            f.write(raw)
        nomes.append(nome)

    # Carrossel cortado no meio é pior que carrossel ausente: acima do teto do
    # Buffer, o TikTok fica de fora e o front já avisou isso no modal.
    vai_tiktok = tiktok == "1" and len(nomes) <= buffer_api.MAX_FOTOS
    # Republicar um agendado substitui o agendamento anterior.
    if anterior and anterior["status"] == "agendado":
        db.cancelar(anterior["id"])
    post = db.criar_post(quando.isoformat(), legenda, nomes,
                         tiktok_caption=legenda if vai_tiktok else "")
    _vincular_post(cid, post["id"])
    return get(cid)


@router.post("/lab/api/carrosseis/{cid}/cancelar")
def api_cancelar(cid: int, insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    d = get(cid)
    if not d:
        raise HTTPException(status_code=404, detail="Carrossel não encontrado.")
    pub = d["publicacao"]
    if not pub or not db.cancelar(pub["id"]):
        raise HTTPException(status_code=409, detail="Não há agendamento pra cancelar.")
    return get(cid)
