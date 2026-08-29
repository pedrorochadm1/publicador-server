"""Laboratório DM1 — a casa principal do insta.pedrorochadm1.com.

Serve o shell do app (uma página só, duas abas: Laboratório e Automações), os
assets estáticos, o manifest do PWA, o service worker e toda a API /lab/api/*.

Duas decisões que valem explicação:

* O SHELL É SERVIDO SEM SESSÃO. Ele não contém nenhum dado — só a casca. Assim o
  service worker pode cacheá-lo à vontade e o PWA em modo standalone nunca cai
  num redirect de login fora do app. A tela de login é desenhada DENTRO do app
  quando GET /lab/api/sessao responde {"logado": false}.

* O CÁLCULO DA RÉGUA MORA NO SERVIDOR. O preview que aparece antes de publicar um
  anúncio precisa bater exatamente com o que o servidor vai calcular depois,
  senão a animação do ponteiro mente. Uma implementação só, em lab_calculo.py.
"""
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Cookie, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from . import config, insta_web, lab_calculo as lc, lab_db, sessoes

router = APIRouter()

# Fonte ÚNICA da versão do front. Bumpar aqui invalida o cache do service worker
# e o cache-bust de todo CSS/JS de uma vez. É o único lugar a mexer num deploy.
LAB_VERSAO = "6"

_WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
_LAB_DIR = os.path.join(_WEB_DIR, "lab")

# Quantos dias de publicados a coluna mostra por padrão.
JANELA_PUBLICADOS_DIAS = 30


def _arquivo(caminho: str) -> str:
    with open(caminho, encoding="utf-8") as f:
        return f.read()


def shell() -> str:
    """O HTML do app, com a versão injetada. Mesmo shell pras duas abas."""
    return _arquivo(os.path.join(_LAB_DIR, "index.html")).replace("__V__", LAB_VERSAO)


def _exige(sess: str | None):
    if not sessoes.valida(sess):
        raise HTTPException(status_code=401, detail="Sessão expirada. Entre de novo.")


def _agora() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────────────── Páginas ───────────────────────────

@router.get("/lab", response_class=HTMLResponse)
@router.get("/automacoes", response_class=HTMLResponse)
def pagina():
    if not config.INSTA_UI_PASSWORD:
        return HTMLResponse(
            "<h1>Painel desativado</h1><p>Falta a variável INSTA_UI_PASSWORD.</p>", 503)
    return HTMLResponse(shell())


@router.get("/manifest.webmanifest")
def manifest():
    v = LAB_VERSAO
    return JSONResponse(
        {
            "name": "Laboratório DM1",
            "short_name": "Lab DM1",
            "description": "Laboratório de conteúdo do @pedrorochadm1",
            "start_url": "/lab",
            "scope": "/",
            "display": "standalone",
            "orientation": "portrait",
            # Cores da splash screen. Acompanham o tema PADRÃO (claro); a barra
            # de status segue a meta theme-color do shell.

            "background_color": "#f7f8fa",
            "theme_color": "#f7f8fa",
            "lang": "pt-BR",
            "icons": [
                {"src": f"/lab/static/icone-192.png?v={v}", "sizes": "192x192", "type": "image/png"},
                {"src": f"/lab/static/icone-512.png?v={v}", "sizes": "512x512", "type": "image/png"},
                {"src": f"/lab/static/icone-maskable-512.png?v={v}", "sizes": "512x512",
                 "type": "image/png", "purpose": "maskable"},
            ],
        },
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/sw.js")
def service_worker():
    js = _arquivo(os.path.join(_LAB_DIR, "sw.js")).replace("__V__", LAB_VERSAO)
    return Response(
        content=js,
        media_type="application/javascript",
        headers={
            # Sem isso o SW só controlaria /lab/static/, não a raiz do app.
            "Service-Worker-Allowed": "/",
            # O próprio SW nunca pode vir de cache, senão um bug nele gruda.
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@router.get("/lab/reset", response_class=HTMLResponse)
def reset():
    """Escotilha: um service worker ruim serve um app quebrado mesmo depois do
    deploy do conserto. Esta página o desregistra e limpa os caches."""
    return HTMLResponse(_arquivo(os.path.join(_LAB_DIR, "reset.html")))


# ─────────────────────────── Sessão ───────────────────────────

@router.get("/lab/api/sessao")
def api_sessao(insta_sess: str | None = Cookie(default=None)):
    """Única rota sem auth: é ela que diz ao app se deve desenhar o login."""
    return {"logado": sessoes.valida(insta_sess), "versao": LAB_VERSAO}


@router.post("/lab/api/login")
def api_login(request: Request, dados: dict = Body(...)):
    ip = insta_web.ip_do_pedido(request)
    if sessoes.bloqueado(ip):
        raise HTTPException(status_code=429, detail="Muitas tentativas. Espere alguns minutos.")
    if not insta_web.senha_confere(str(dados.get("senha", ""))):
        sessoes.registrar_falha(ip)
        raise HTTPException(status_code=401, detail="Senha incorreta.")
    token = sessoes.criar(request.headers.get("user-agent", ""))
    resp = JSONResponse({"ok": True})
    insta_web.gravar_cookie(resp, token)
    return resp


@router.post("/lab/api/sair")
def api_sair(insta_sess: str | None = Cookie(default=None)):
    sessoes.encerrar(insta_sess)
    resp = JSONResponse({"ok": True})
    insta_web.apagar_cookie(resp)
    return resp


# ─────────────────────────── Régua ───────────────────────────

def _montar_regua(extra: tuple[str, datetime] | None = None) -> dict:
    """A régua de agora. Com `extra`, simula o efeito de mais uma publicação —
    é o preview que aparece antes de confirmar um anúncio."""
    cfg = lab_db.get_config()
    agora = _agora()
    pubs = lab_db.publicacoes(desde=agora - timedelta(days=91))
    if extra:
        pubs = pubs + [extra]
    cinza = bool(cfg["onboarding"].get("pulou")) and not pubs
    return lc.regua(pubs, agora, meta=int(cfg.get("meta_semanal") or 0), cinza=cinza)


@router.get("/lab/api/regua")
def api_regua(simular: str | None = None, insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    out = _montar_regua()
    if simular in lab_db.TIPOS:
        out["simulado"] = _montar_regua(extra=(simular, _agora()))
    return out


@router.get("/lab/api/regua/detalhe")
def api_regua_detalhe(insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    agora = _agora()
    d = lc.detalhe(lab_db.publicacoes(desde=agora - timedelta(days=91)), agora)
    d["publicacoes"] = lab_db.historico()
    return d


@router.post("/lab/api/regua/testar")
def api_regua_testar(dados: dict = Body(...), insta_sess: str | None = Cookie(default=None)):
    """Exercita as 5 zonas na UI real sem sujar o banco. Só verificação."""
    _exige(insta_sess)
    agora = datetime.fromisoformat(dados["agora"]) if dados.get("agora") else _agora()
    pubs = [(t, q) for t, q in (dados.get("publicacoes") or [])]
    meta = int(dados.get("meta", lc.META_SEMANAL_PADRAO))
    return lc.regua(pubs, agora, meta=meta, cinza=bool(dados.get("cinza")))


# ─────────────────────────── Cards ───────────────────────────

def _janela_publicados(tudo: bool) -> str | None:
    if tudo:
        return None
    return (_agora() - timedelta(days=JANELA_PUBLICADOS_DIAS)).isoformat()


@router.get("/lab/api/estado")
def api_estado(tudo: int = 0, insta_sess: str | None = Cookie(default=None)):
    """Boot do app em um round-trip só: cards + régua + config."""
    _exige(insta_sess)
    return {
        "cards": lab_db.listar_cards(publicados_desde=_janela_publicados(bool(tudo))),
        "regua": _montar_regua(),
        "config": lab_db.get_config(),
        "total_publicacoes": lab_db.total_publicacoes(),
        "versao": LAB_VERSAO,
    }


@router.get("/lab/api/cards")
def api_cards(tudo: int = 0, insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    return lab_db.listar_cards(publicados_desde=_janela_publicados(bool(tudo)))


@router.post("/lab/api/cards")
def api_criar(dados: dict = Body(...), insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    titulo = str(dados.get("titulo") or "").strip()
    if not titulo:
        raise HTTPException(status_code=400, detail="A ideia precisa de um título.")
    card = lab_db.criar_card(titulo, dados.get("client_uuid") or None)
    # Tipo e formato podem vir já da captura, quando o Pedro marca os chips.
    marcados = {c: dados[c] for c in ("tipo", "formato") if dados.get(c)}
    if marcados and not (card["tipo"] or card["formato"]):
        try:
            card = lab_db.atualizar_card(card["id"], marcados)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    return card


@router.get("/lab/api/cards/{card_id}")
def api_card(card_id: int, insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    card = lab_db.get_card(card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Card não encontrado.")
    return card


@router.patch("/lab/api/cards/{card_id}")
def api_atualizar(card_id: int, dados: dict = Body(...),
                  insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    try:
        card = lab_db.atualizar_card(card_id, dados)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not card:
        raise HTTPException(status_code=404, detail="Card não encontrado.")
    return card


@router.post("/lab/api/cards/{card_id}/publicar")
def api_publicar(card_id: int, insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    antes = _montar_regua()
    try:
        card = lab_db.publicar_card(card_id)
    except lab_db.ErroPublicar as e:
        status = 404 if e.codigo == "nao_encontrado" else 409
        raise HTTPException(status_code=status, detail={"codigo": e.codigo, "mensagem": e.detalhe})
    # As duas réguas voltam juntas pro cliente animar o ponteiro sem recalcular nada.
    return {"card": card, "regua_antes": antes, "regua_depois": _montar_regua()}


@router.post("/lab/api/cards/{card_id}/duplicar")
def api_duplicar(card_id: int, insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    card = lab_db.duplicar_card(card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Card não encontrado.")
    return card


@router.delete("/lab/api/cards/{card_id}")
def api_remover(card_id: int, insta_sess: str | None = Cookie(default=None)):
    """Apaga de vez. Devolve a régua depois, porque apagar um card publicado
    tira a publicação da conta e move o ponteiro."""
    _exige(insta_sess)
    antes = _montar_regua()
    if not lab_db.remover_card(card_id):
        raise HTTPException(status_code=404, detail="Card não encontrado.")
    return {"ok": True, "regua_antes": antes, "regua_depois": _montar_regua()}


# ─────────────────────────── Onboarding e config ───────────────────────────

@router.post("/lab/api/onboarding/backfill")
def api_backfill(dados: dict = Body(...), insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    criadas = lab_db.backfill(dados.get("janelas") or {})
    lab_db.set_config({"onboarding": {"feito": True, "pulou": False, "em": _agora().isoformat()}})
    return {"criadas": criadas, "regua": _montar_regua()}


@router.post("/lab/api/onboarding/pular")
def api_pular(insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    lab_db.set_config({"onboarding": {"feito": True, "pulou": True, "em": _agora().isoformat()}})
    return {"ok": True, "regua": _montar_regua()}


@router.get("/lab/api/config")
def api_config(insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    return lab_db.get_config()


@router.put("/lab/api/config")
def api_config_salvar(dados: dict = Body(...), insta_sess: str | None = Cookie(default=None)):
    _exige(insta_sess)
    return lab_db.set_config(dados)
