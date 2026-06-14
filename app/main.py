import base64
import binascii
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import os

import re

import requests
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, scheduler


@asynccontextmanager
async def lifespan(_: FastAPI):
    scheduler.iniciar()
    yield
    scheduler.parar()


app = FastAPI(title="Publicador @pedrorochadm1", lifespan=lifespan)
app.mount("/img", StaticFiles(directory=config.IMG_DIR), name="img")


def auth(x_api_key: str = Header(default="")):
    if not config.PUBLICADOR_API_KEY or x_api_key != config.PUBLICADOR_API_KEY:
        raise HTTPException(status_code=401, detail="API key inválida.")


class Agendamento(BaseModel):
    publicar_em: datetime = Field(..., description="Horário do post. ISO 8601; sem timezone assume America/Sao_Paulo.")
    caption: str = ""
    arquivos: list[str] = Field(default=[], description="Nomes de arquivos já enviados via /upload (imagem ou vídeo).")
    imagens_b64: list[str] = Field(default=[], max_length=10, description="Imagens em base64 (alternativa ao /upload).")


class Upload(BaseModel):
    arquivo_b64: str = Field(..., description="Arquivo (imagem ou vídeo) em base64.")
    ext: str = Field("png", description="Extensão do arquivo (png, jpg, mp4, mov...).")


_EXT_OK = {"png", "jpg", "jpeg", "webp", "mp4", "mov"}

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_UPLOAD_TMP = os.path.join(config.DATA_DIR, "uploads_tmp")
os.makedirs(_UPLOAD_TMP, exist_ok=True)


def _salvar_arquivo_b64(b64: str, ext: str = "png") -> str:
    """Decodifica base64, salva em /data/img e retorna o nome do arquivo."""
    ext = ext.lower().lstrip(".")
    if ext not in _EXT_OK:
        raise HTTPException(status_code=400, detail=f"Extensão não suportada: {ext}")
    try:
        raw = base64.b64decode(b64.split(",")[-1], validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="Base64 inválido.")
    nome = f"{uuid.uuid4().hex}.{ext}"
    with open(os.path.join(config.IMG_DIR, nome), "wb") as f:
        f.write(raw)
    return nome


@app.get("/health")
def health():
    return {
        "ok": True,
        "public_base_url": config.PUBLIC_BASE_URL or None,
        "agendados": len(db.listar("agendado")),
    }


@app.post("/agendar", dependencies=[Depends(auth)])
def agendar(a: Agendamento):
    quando = a.publicar_em
    if quando.tzinfo is None:
        quando = quando.replace(tzinfo=ZoneInfo(config.TZ))
    quando_utc = quando.astimezone(timezone.utc)
    if quando_utc < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="publicar_em está no passado.")

    # arquivos já enviados via /upload: confere que existem no disco
    for nome in a.arquivos:
        if "/" in nome or not os.path.exists(os.path.join(config.IMG_DIR, nome)):
            raise HTTPException(status_code=400, detail=f"Arquivo não encontrado no servidor: {nome}")

    nomes = list(a.arquivos) + [_salvar_arquivo_b64(b64, "png") for b64 in a.imagens_b64]
    if not nomes:
        raise HTTPException(status_code=400, detail="Envie 'arquivos' ou 'imagens_b64'.")

    post = db.criar_post(quando_utc.isoformat(), a.caption, nomes)
    return {"id": post["id"], "publicar_em_utc": post["publicar_em"], "status": post["status"], "arquivos": len(nomes)}


@app.post("/upload", dependencies=[Depends(auth)])
def upload(u: Upload):
    """Hospeda imagem ou vídeo e devolve a URL pública HTTPS (substitui o litterbox)."""
    if not config.PUBLIC_BASE_URL:
        raise HTTPException(status_code=500, detail="PUBLIC_BASE_URL não configurado.")
    nome = _salvar_arquivo_b64(u.arquivo_b64, u.ext)
    return {"url": f"{config.PUBLIC_BASE_URL}/img/{nome}", "arquivo": nome}


@app.post("/upload-arquivo", dependencies=[Depends(auth)])
async def upload_arquivo(arquivo: UploadFile = File(...), ext: str = Form("")):
    """Hospeda imagem ou vídeo via multipart (streaming, sem base64). Para arquivos grandes."""
    if not config.PUBLIC_BASE_URL:
        raise HTTPException(status_code=500, detail="PUBLIC_BASE_URL não configurado.")
    extensao = (ext or os.path.splitext(arquivo.filename or "")[1]).lower().lstrip(".")
    if extensao not in _EXT_OK:
        raise HTTPException(status_code=400, detail=f"Extensão não suportada: {extensao}")
    nome = f"{uuid.uuid4().hex}.{extensao}"
    destino = os.path.join(config.IMG_DIR, nome)
    with open(destino, "wb") as f:
        while chunk := await arquivo.read(1024 * 1024):
            f.write(chunk)
    return {"url": f"{config.PUBLIC_BASE_URL}/img/{nome}", "arquivo": nome}


class UploadURL(BaseModel):
    url: str = Field(..., description="URL pública do arquivo a baixar (imagem ou vídeo).")
    ext: str = Field("", description="Extensão; se vazia, inferida da URL.")


class UploadFinalizar(BaseModel):
    upload_id: str = Field(..., description="ID devolvido por /upload-iniciar.")
    ext: str = Field(..., description="Extensão do arquivo (mp4, png...).")


@app.post("/upload-iniciar", dependencies=[Depends(auth)])
def upload_iniciar():
    """Inicia um upload em pedaços. Devolve um upload_id para enviar as partes."""
    upload_id = uuid.uuid4().hex
    open(os.path.join(_UPLOAD_TMP, upload_id), "wb").close()
    return {"upload_id": upload_id}


@app.put("/upload-parte/{upload_id}", dependencies=[Depends(auth)])
async def upload_parte(upload_id: str, request: Request):
    """Anexa um pedaço (corpo bruto) ao upload. Chame em sequência por arquivo."""
    if not _HEX32.match(upload_id):
        raise HTTPException(status_code=400, detail="upload_id inválido.")
    caminho = os.path.join(_UPLOAD_TMP, upload_id)
    if not os.path.exists(caminho):
        raise HTTPException(status_code=404, detail="upload_id não encontrado. Use /upload-iniciar.")
    with open(caminho, "ab") as f:
        async for chunk in request.stream():
            f.write(chunk)
    return {"ok": True, "bytes": os.path.getsize(caminho)}


@app.post("/upload-finalizar", dependencies=[Depends(auth)])
def upload_finalizar(u: UploadFinalizar):
    """Fecha o upload em pedaços: move o arquivo montado para a storage pública."""
    if not config.PUBLIC_BASE_URL:
        raise HTTPException(status_code=500, detail="PUBLIC_BASE_URL não configurado.")
    if not _HEX32.match(u.upload_id):
        raise HTTPException(status_code=400, detail="upload_id inválido.")
    origem = os.path.join(_UPLOAD_TMP, u.upload_id)
    if not os.path.exists(origem):
        raise HTTPException(status_code=404, detail="upload_id não encontrado.")
    extensao = u.ext.lower().lstrip(".")
    if extensao not in _EXT_OK:
        os.remove(origem)
        raise HTTPException(status_code=400, detail=f"Extensão não suportada: {extensao}")
    nome = f"{uuid.uuid4().hex}.{extensao}"
    os.replace(origem, os.path.join(config.IMG_DIR, nome))
    return {"url": f"{config.PUBLIC_BASE_URL}/img/{nome}", "arquivo": nome}


@app.post("/upload-de-url", dependencies=[Depends(auth)])
def upload_de_url(u: UploadURL):
    """Baixa o arquivo de uma URL para a storage do servidor. Evita o timeout de
    upload do proxy com arquivos grandes — o servidor puxa com banda de datacenter."""
    if not config.PUBLIC_BASE_URL:
        raise HTTPException(status_code=500, detail="PUBLIC_BASE_URL não configurado.")
    extensao = (u.ext or os.path.splitext(u.url.split("?")[0])[1]).lower().lstrip(".")
    if extensao not in _EXT_OK:
        raise HTTPException(status_code=400, detail=f"Extensão não suportada: {extensao}")
    nome = f"{uuid.uuid4().hex}.{extensao}"
    destino = os.path.join(config.IMG_DIR, nome)
    try:
        with requests.get(u.url, stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(destino, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)
    except requests.RequestException as e:
        if os.path.exists(destino):
            os.remove(destino)
        raise HTTPException(status_code=400, detail=f"Falha ao baixar a URL: {e}")
    return {"url": f"{config.PUBLIC_BASE_URL}/img/{nome}", "arquivo": nome}


@app.get("/agenda", dependencies=[Depends(auth)])
def agenda(status: str | None = None):
    return db.listar(status)


@app.delete("/agenda/{post_id}", dependencies=[Depends(auth)])
def cancelar(post_id: int):
    if not db.cancelar(post_id):
        raise HTTPException(status_code=404, detail="Post não encontrado ou já não está agendado.")
    return {"id": post_id, "status": "cancelado"}


class TokenUpdate(BaseModel):
    token: str


@app.post("/token", dependencies=[Depends(auth)])
def set_token(t: TokenUpdate):
    """Atualiza o token do Instagram (salvo em /data, usado imediatamente)."""
    from . import token_store
    token_store.salvar_token(t.token)
    return {"ok": True, "dias_para_expirar": token_store.status()}


@app.get("/token/status", dependencies=[Depends(auth)])
def token_status():
    from . import token_store
    return {"dias_para_expirar": token_store.status()}


@app.post("/reenfileirar/{post_id}", dependencies=[Depends(auth)])
def reenfileirar(post_id: int):
    """Volta um post 'erro' para 'agendado' para publicar imediato no próximo ciclo."""
    p = db.get_post(post_id)
    if not p:
        raise HTTPException(status_code=404, detail="Post não encontrado.")
    db.reabrir(post_id)
    return {"id": post_id, "status": "agendado"}
