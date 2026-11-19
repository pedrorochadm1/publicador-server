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
import queue
import secrets
import threading

from fastapi import APIRouter, Body, Cookie, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import automacoes, config, sessoes, tiktok_comentarios
from .token_store import status as token_status

router = APIRouter()

_WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
_COOKIE = "insta_sess"
_SESSAO_VALIDA_S = 30 * 24 * 3600        # 30 dias


def _html(nome: str) -> str:
    with open(os.path.join(_WEB_DIR, nome), encoding="utf-8") as f:
        return f.read()


# As sessões vivem no SQLite (app/sessoes.py) pra sobreviver a redeploy.
def _logado(sess: str | None) -> bool:
    return sessoes.valida(sess)


def _exige(sess: str | None):
    if not _logado(sess):
        raise HTTPException(status_code=401, detail="Sessão expirada. Entre de novo.")


def _bloqueado(ip: str) -> bool:
    return sessoes.bloqueado(ip)


def ip_do_pedido(request: Request) -> str:
    bruto = request.headers.get("x-forwarded-for", "") or (
        request.client.host if request.client else ""
    )
    return bruto.split(",")[0].strip()


# ─────────────────────────── Login ───────────────────────────

@router.get("/insta")
def painel():
    """As automações agora são a segunda aba do Laboratório. O endereço antigo
    continua funcionando: quem tem link ou atalho salvo cai no lugar certo."""
    return RedirectResponse("/automacoes", status_code=307)


@router.get("/insta/classico", response_class=HTMLResponse)
def painel_classico(insta_sess: str | None = Cookie(default=None)):
    """Escotilha: o painel antigo, intocado. Se o porte pra aba nova quebrar
    qualquer coisa, esta URL devolve o painel que funciona, sem precisar de
    deploy. Some depois de algumas semanas de aba nova rodando limpa."""
    if not config.INSTA_UI_PASSWORD:
        return HTMLResponse("<h1>Painel desativado</h1><p>Falta a variável INSTA_UI_PASSWORD.</p>", 503)
    return HTMLResponse(_html("insta.html") if _logado(insta_sess) else _html("insta_login.html"))


def senha_confere(senha: str) -> bool:
    return bool(config.INSTA_UI_PASSWORD) and secrets.compare_digest(senha, config.INSTA_UI_PASSWORD)


def gravar_cookie(resp, token: str):
    """Cookie de sessão. Usado pelo login clássico e pelo login do Lab."""
    resp.set_cookie(_COOKIE, token, max_age=_SESSAO_VALIDA_S, httponly=True,
                    secure=True, samesite="lax", path="/")


def apagar_cookie(resp):
    resp.delete_cookie(_COOKIE, path="/")


@router.post("/insta/login")
async def login(request: Request):
    ip = ip_do_pedido(request)
    if _bloqueado(ip):
        return HTMLResponse(_html("insta_login.html").replace(
            "<!--ERRO-->", '<p class="erro">Muitas tentativas. Espere alguns minutos.</p>'), 429)
    form = await request.form()
    if not senha_confere(str(form.get("senha", ""))):
        sessoes.registrar_falha(ip)
        return HTMLResponse(_html("insta_login.html").replace(
            "<!--ERRO-->", '<p class="erro">Senha incorreta.</p>'), 401)
    token = sessoes.criar(request.headers.get("user-agent", ""))
    resp = RedirectResponse("/insta", status_code=303)
    gravar_cookie(resp, token)
    return resp


@router.get("/insta/sair")
def sair(insta_sess: str | None = Cookie(default=None)):
    sessoes.encerrar(insta_sess)
    resp = RedirectResponse("/insta", status_code=303)
    apagar_cookie(resp)
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
    ig = automacoes.eventos(automacao_id)
    # o TikTok mora em tabela própria; aqui só entra no mesmo histórico, no mesmo formato
    tt = [{
        "quando": e["quando"], "usuario": e["usuario"] or (e["autor_id"] or "")[-8:],
        "texto": e["texto"], "resposta": e["resposta"], "plataforma": "tt",
        "dm_status": {"ok": "ok · só pública", "na-fila": "na-fila"}.get(e["status"], e["status"]),
        "erro": e["erro"],
    } for e in tiktok_comentarios.eventos(automacao_id)]
    return sorted(ig + tt, key=lambda e: e["quando"] or "", reverse=True)[:150]


# ─────────────────────────── TikTok (via Zernio) ───────────────────────────

@router.get("/insta/api/tiktok/status")
def api_tt_status(insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    return tiktok_comentarios.status()


@router.post("/insta/api/tiktok/chave")
def api_tt_chave(dados: dict = Body(...), insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    try:
        tiktok_comentarios.gravar_chave(dados.get("chave", ""))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Chave não funcionou: {e}")
    return tiktok_comentarios.status()


@router.post("/insta/api/tiktok/conectar")
def api_tt_conectar(request: Request, insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "insta.pedrorochadm1.com"
    try:
        url = tiktok_comentarios.url_conectar(f"https://{host}/automacoes?tiktok=conectado")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Zernio não respondeu: {e}")
    return {"url": url}


@router.post("/insta/api/tiktok/ativar")
def api_tt_ativar(insta_sess: str | None = Cookie(default=None)):
    """Depois de conectar: cadastra o webhook (tempo real) e faz a primeira varredura."""
    _exige(insta_sess)
    try:
        wh = tiktok_comentarios.garantir_webhook()
    except Exception as e:  # noqa: BLE001
        wh = {"ok": False, "erro": str(e)[:300]}
    return {"webhook": wh, "acionados": tiktok_comentarios.rodar(), "status": tiktok_comentarios.status()}


@router.post("/insta/api/tiktok/rodar")
def api_tt_rodar(insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    return {"acionados": tiktok_comentarios.rodar(), "status": tiktok_comentarios.status()}


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


@router.post("/insta/api/retroativo")
def api_retroativo(enfileirar: int = 1, automacao_id: int | None = None,
                   insta_sess: str | None = Cookie(default=None)):
    """Relê os posts inteiros e devolve pra fila quem ficou sem resposta.
    Com ?enfileirar=0 é só auditoria, não mexe em ninguém."""
    _exige(insta_sess)
    return automacoes.varredura_retrospectiva(bool(enfileirar), automacao_id)


@router.get("/insta/api/eventos/todos")
def api_eventos_todos(limite: int = 1000, insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    return automacoes.eventos(limite=min(limite, 5000))


@router.get("/insta/api/fila")
def api_fila(enviar: int = 0, insta_sess: str | None = Cookie(default=None)):
    """Estado do marca-passo do direct. Com ?enviar=1 força um envio e mostra a falha
    crua se houver — sem isso, erro dentro do job do scheduler morre no log do container."""
    _exige(insta_sess)
    pode, motivo = automacoes._pode_enviar_dm()
    out = {"nome_indisponivel": automacoes._NOME_ULTIMO_ERRO["motivo"],
           "token_invalido": automacoes.token_invalido(),
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
    # Processa FORA do loop de eventos, numa thread só (fila serial). Antes isso rodava
    # aqui dentro: tratar_webhook faz banco e Graph síncronos, e numa rajada de
    # comentários o servidor inteiro parava de responder (2026-11-19).
    try:
        _fila_webhook.put_nowait(payload)
    except queue.Full:
        pass                     # a varredura de 60s pega o que ficar de fora
    _garantir_worker()
    # sempre 200 e na hora: a Meta reenvia (e desativa a inscrição) se demorar ou errar
    return JSONResponse({"ok": True, "na_fila": _fila_webhook.qsize()})


_fila_webhook: "queue.Queue[dict]" = queue.Queue(maxsize=5000)
_worker: dict = {"t": None}


def _processar_webhooks():
    while True:
        payload = _fila_webhook.get()
        try:
            automacoes.tratar_webhook(payload)
        except Exception as e:  # noqa: BLE001
            print(f"[webhook] falhou: {e}")


def _garantir_worker():
    t = _worker["t"]
    if t is None or not t.is_alive():
        t = threading.Thread(target=_processar_webhooks, name="webhook-ig", daemon=True)
        t.start()
        _worker["t"] = t


# ─────────────────────────── Webhook do Zernio (comentário do TikTok) ───────────────────────────

@router.post("/webhook/zernio")
async def webhook_zernio(request: Request):
    corpo = await request.body()
    assinatura = request.headers.get("x-zernio-signature") or request.headers.get("x-late-signature") or ""
    if not await run_in_threadpool(tiktok_comentarios.assinatura_ok, corpo, assinatura):
        raise HTTPException(status_code=403, detail="Assinatura inválida.")
    try:
        payload = json.loads(corpo or b"{}")
        # fora do loop de eventos: tratar_webhook faz banco e HTTP síncronos
        tratados = await run_in_threadpool(tiktok_comentarios.tratar_webhook, payload)
    except Exception as e:  # noqa: BLE001
        print(f"[webhook zernio] falhou: {e}")
        tratados = 0
    # responde rápido e sempre 2xx: o Zernio exige resposta em 5s e reenvia em erro
    return JSONResponse({"ok": True, "tratados": tratados})
