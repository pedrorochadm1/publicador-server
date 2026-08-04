"""Painel das automações de comentário (insta.pedrorochadm1.com) + webhook da Meta.

Painel protegido por senha (INSTA_UI_PASSWORD): lista as automações, mostra o
histórico do que já foi respondido e deixa criar/editar/desligar automações à mão.

O webhook fica no mesmo app: POST /webhook/instagram. Se o callback não estiver
configurado no app do Facebook, o polling do scheduler cobre sozinho.
"""
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import APIRouter, Body, Cookie, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import automacoes, config
from .token_store import status as token_status

router = APIRouter()

_WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
_COOKIE = "insta_sess"
_SESSOES: dict[str, float] = {}          # token -> criado_em
_SESSAO_VALIDA_S = 30 * 24 * 3600        # 30 dias
_TENTATIVAS: dict[str, list] = {}        # ip -> timestamps de falha


def _html(nome: str) -> str:
    with open(os.path.join(_WEB_DIR, nome), encoding="utf-8") as f:
        return f.read()


def _logado(sess: str | None) -> bool:
    if not sess:
        return False
    criado = _SESSOES.get(sess)
    if not criado:
        return False
    if time.time() - criado > _SESSAO_VALIDA_S:
        _SESSOES.pop(sess, None)
        return False
    return True


def _exige(sess: str | None):
    if not _logado(sess):
        raise HTTPException(status_code=401, detail="Sessão expirada. Entre de novo.")


def _bloqueado(ip: str) -> bool:
    agora = time.time()
    tentativas = [t for t in _TENTATIVAS.get(ip, []) if agora - t < 600]
    _TENTATIVAS[ip] = tentativas
    return len(tentativas) >= 10


# ─────────────────────────── Login ───────────────────────────

@router.get("/insta", response_class=HTMLResponse)
def painel(insta_sess: str | None = Cookie(default=None)):
    if not config.INSTA_UI_PASSWORD:
        return HTMLResponse("<h1>Painel desativado</h1><p>Falta a variável INSTA_UI_PASSWORD.</p>", 503)
    return HTMLResponse(_html("insta.html") if _logado(insta_sess) else _html("insta_login.html"))


@router.post("/insta/login")
async def login(request: Request):
    ip = (request.headers.get("x-forwarded-for", "") or (request.client.host if request.client else "")).split(",")[0].strip()
    if _bloqueado(ip):
        return HTMLResponse(_html("insta_login.html").replace(
            "<!--ERRO-->", '<p class="erro">Muitas tentativas. Espere alguns minutos.</p>'), 429)
    form = await request.form()
    senha = str(form.get("senha", ""))
    if not config.INSTA_UI_PASSWORD or not secrets.compare_digest(senha, config.INSTA_UI_PASSWORD):
        _TENTATIVAS.setdefault(ip, []).append(time.time())
        return HTMLResponse(_html("insta_login.html").replace(
            "<!--ERRO-->", '<p class="erro">Senha incorreta.</p>'), 401)
    token = secrets.token_urlsafe(32)
    _SESSOES[token] = time.time()
    resp = RedirectResponse("/insta", status_code=303)
    resp.set_cookie(_COOKIE, token, max_age=_SESSAO_VALIDA_S, httponly=True, secure=True, samesite="lax", path="/")
    return resp


@router.get("/insta/sair")
def sair(insta_sess: str | None = Cookie(default=None)):
    _SESSOES.pop(insta_sess or "", None)
    resp = RedirectResponse("/insta", status_code=303)
    resp.delete_cookie(_COOKIE, path="/")
    return resp


# ─────────────────────────── API do painel ───────────────────────────

@router.get("/insta/api/automacoes")
def api_listar(insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    itens = []
    for a in automacoes.listar():
        a["contadores"] = automacoes.contadores(a["id"])
        itens.append(a)
    return itens


@router.post("/insta/api/automacoes")
def api_criar(dados: dict = Body(...), insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    return automacoes.criar(dados)


@router.put("/insta/api/automacoes/{aid}")
def api_atualizar(aid: int, dados: dict = Body(...), insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    a = automacoes.atualizar(aid, dados)
    if not a:
        raise HTTPException(status_code=404, detail="Automação não encontrada.")
    return a


@router.post("/insta/api/automacoes/{aid}/duplicar")
def api_duplicar(aid: int, insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    a = automacoes.duplicar(aid)
    if not a:
        raise HTTPException(status_code=404, detail="Automação não encontrada.")
    return a


@router.delete("/insta/api/automacoes/{aid}")
def api_remover(aid: int, insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    if not automacoes.remover(aid):
        raise HTTPException(status_code=404, detail="Automação não encontrada.")
    return {"ok": True}


@router.get("/insta/api/eventos")
def api_eventos(automacao_id: int | None = None, insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    return automacoes.eventos(automacao_id)


@router.get("/insta/api/midias")
def api_midias(insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    try:
        return automacoes.buscar_midias(limite=12)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Instagram não respondeu: {e}")


@router.post("/insta/api/testar")
def api_testar(dados: dict = Body(...), insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    palavras = dados.get("palavras") or []
    if isinstance(palavras, str):
        palavras = [p for p in palavras.replace("\n", ",").split(",") if p.strip()]
    return {"casa": automacoes.casa(dados.get("texto", ""), palavras, dados.get("modo", "contem"))}


@router.post("/insta/api/rodar")
def api_rodar(insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    automacoes.rodar()
    return {"ok": True, "direct_na_fila": automacoes.pendentes_na_fila()}


@router.get("/insta/api/fila")
def api_fila(enviar: int = 0, insta_sess: str | None = Cookie(default=None)):
    """Estado do marca-passo do direct. Com ?enviar=1 força um envio e mostra a falha
    crua se houver — sem isso, erro dentro do job do scheduler morre no log do container."""
    _exige(insta_sess)
    pode, motivo = automacoes._pode_enviar_dm()
    out = {"nome_indisponivel": automacoes._NOME_ULTIMO_ERRO["motivo"],
           "na_fila": automacoes.pendentes_na_fila(), "pode_enviar": pode, "motivo": motivo,
           "ritmo": automacoes.ritmo_atual(), "marca_passo": automacoes.marca_passo_vivo(),
           "enviados_na_hora": automacoes.enviados_na_hora(),
           "descanso_ate": (automacoes._DESCANSO_ATE["quando"].isoformat()
                            if automacoes._DESCANSO_ATE["quando"] else None),
           "nivel_castigo": automacoes._DESCANSO_ATE["nivel"]}
    if enviar:
        try:
            out["enviou"] = automacoes.enviar_fila()
        except Exception as e:  # noqa: BLE001
            out["excecao"] = f"{type(e).__name__}: {e}"
    return out


@router.post("/insta/api/webhook/assinar")
def api_assinar_webhook(insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    return automacoes.garantir_webhook()


@router.get("/insta/api/diagnostico/alvos")
def api_diag_alvos(insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    return automacoes.diagnostico_alvos()


@router.get("/insta/api/diagnostico/facebook")
def api_diagnostico_fb(insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    return automacoes.diagnostico_facebook()


@router.get("/insta/api/diagnostico")
def api_diagnostico(insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    return automacoes.diagnostico_dm()


@router.get("/insta/api/status")
def api_status(insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    return {
        "conta": config.INSTAGRAM_BUSINESS_ID,
        "token_dias": token_status(),
        "polling_segundos": config.AUTOMACOES_POLL_SEGUNDOS,
        "ligado": config.AUTOMACOES_ATIVAS,
        "webhook": automacoes.webhook_status(),
        "marca_passo": automacoes.marca_passo_vivo(),
    }


# ─────────────────────────── Webhook da Meta ───────────────────────────

@router.get("/webhook/instagram")
def webhook_verificar(request: Request):
    p = request.query_params
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == config.IG_WEBHOOK_VERIFY_TOKEN:
        return Response(content=p.get("hub.challenge", ""), media_type="text/plain")
    raise HTTPException(status_code=403, detail="verify_token inválido.")


@router.post("/webhook/instagram")
async def webhook_receber(request: Request):
    corpo = await request.body()
    assinatura = request.headers.get("x-hub-signature-256", "")
    if config.FACEBOOK_APP_SECRET and assinatura:
        esperado = "sha256=" + hmac.new(
            config.FACEBOOK_APP_SECRET.encode(), corpo, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(esperado, assinatura):
            raise HTTPException(status_code=403, detail="Assinatura inválida.")
    try:
        payload = json.loads(corpo or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="JSON inválido.")
    try:
        tratados = automacoes.tratar_webhook(payload)
    except Exception as e:  # noqa: BLE001
        print(f"[webhook] falhou: {e}")
        tratados = 0
    # sempre 200: a Meta reenvia (e desativa a inscrição) se o endpoint devolver erro
    return JSONResponse({"ok": True, "tratados": tratados})
