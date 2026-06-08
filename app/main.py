import base64
import binascii
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import os

from fastapi import Depends, FastAPI, Header, HTTPException
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


@app.get("/agenda", dependencies=[Depends(auth)])
def agenda(status: str | None = None):
    return db.listar(status)


@app.delete("/agenda/{post_id}", dependencies=[Depends(auth)])
def cancelar(post_id: int):
    if not db.cancelar(post_id):
        raise HTTPException(status_code=404, detail="Post não encontrado ou já não está agendado.")
    return {"id": post_id, "status": "cancelado"}
