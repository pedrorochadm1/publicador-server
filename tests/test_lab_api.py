"""Testes da API do Laboratório, contra um SQLite temporário.

Cobre o que a matemática pura não cobre: transições automáticas de status, as
guardas do botão Publiquei, idempotência da captura offline e a régua vinda do
banco de verdade.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

SENHA = "senha-de-teste"


@pytest.fixture()
def cliente():
    """App novo, banco novo, sessão logada."""
    base = tempfile.mkdtemp(prefix="labapi-")
    os.environ["DATA_DIR"] = base
    os.environ["INSTA_UI_PASSWORD"] = SENHA
    os.environ["AUTOMACOES_ATIVAS"] = "false"

    # Os módulos leem config no import, então recarregamos tudo a cada teste.
    for nome in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[nome]

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app import lab_web

    app = FastAPI()
    app.include_router(lab_web.router)
    # base_url em https porque o cookie de sessão é secure=True e não seria
    # enviado de volta sobre http — em produção o EasyPanel serve tudo em TLS.
    c = TestClient(app, base_url="https://testserver")
    assert c.post("/lab/api/login", json={"senha": SENHA}).status_code == 200
    return c


# ─────────────────────────── Sessão ───────────────────────────

def test_sessao_e_login(cliente):
    assert cliente.get("/lab/api/sessao").json()["logado"] is True
    cliente.post("/lab/api/sair")
    assert cliente.get("/lab/api/sessao").json()["logado"] is False
    assert cliente.get("/lab/api/estado").status_code == 401
    assert cliente.post("/lab/api/login", json={"senha": "errada"}).status_code == 401


# ─────────────────────────── Captura ───────────────────────────

def test_captura_cria_card_em_ideia(cliente):
    card = cliente.post("/lab/api/cards", json={"titulo": "gancho sobre NPH"}).json()
    assert card["status"] == "ideia"
    assert card["tipo"] is None and card["formato"] is None
    assert card["desenvolvimentos"] == []


def test_captura_exige_titulo(cliente):
    assert cliente.post("/lab/api/cards", json={"titulo": "   "}).status_code == 400


def test_uuid_repetido_nao_duplica(cliente):
    """A fila offline reenvia; o servidor tem que devolver o mesmo card."""
    a = cliente.post("/lab/api/cards", json={"titulo": "ideia", "client_uuid": "u-1"}).json()
    b = cliente.post("/lab/api/cards", json={"titulo": "ideia", "client_uuid": "u-1"}).json()
    assert a["id"] == b["id"]
    assert len(cliente.get("/lab/api/cards").json()) == 1


# ─────────────────────────── Status derivado ───────────────────────────

def test_titulo_sozinho_nao_move_da_coluna_ideia(cliente):
    card = cliente.post("/lab/api/cards", json={"titulo": "a"}).json()
    r = cliente.patch(f"/lab/api/cards/{card['id']}", json={"titulo": "outro título"}).json()
    assert r["status"] == "ideia"


@pytest.mark.parametrize("campo,valor", [
    ("hook", {"hook": "todo mundo erra a contagem"}),
    ("fechamento", {"fechamento": "é isso"}),
    ("desenvolvimento", {"desenvolvimentos": [{"texto": "o ponto é a proporção"}]}),
])
def test_qualquer_campo_do_roteiro_move_pra_producao(cliente, campo, valor):
    card = cliente.post("/lab/api/cards", json={"titulo": "a"}).json()
    r = cliente.patch(f"/lab/api/cards/{card['id']}", json=valor).json()
    assert r["status"] == "producao", f"{campo} deveria mover pra produção"


def test_esvaziar_o_roteiro_volta_pra_ideia(cliente):
    card = cliente.post("/lab/api/cards", json={"titulo": "a"}).json()
    cid = card["id"]
    assert cliente.patch(f"/lab/api/cards/{cid}", json={"hook": "algo"}).json()["status"] == "producao"
    assert cliente.patch(f"/lab/api/cards/{cid}", json={"hook": ""}).json()["status"] == "ideia"


def test_espaco_em_branco_nao_conta_como_roteiro(cliente):
    card = cliente.post("/lab/api/cards", json={"titulo": "a"}).json()
    r = cliente.patch(f"/lab/api/cards/{card['id']}", json={"hook": "   \n  "}).json()
    assert r["status"] == "ideia"


def test_desenvolvimentos_mantem_ordem_e_ids(cliente):
    card = cliente.post("/lab/api/cards", json={"titulo": "a"}).json()
    cid = card["id"]
    r = cliente.patch(f"/lab/api/cards/{cid}", json={
        "desenvolvimentos": [{"texto": "um"}, {"texto": "dois"}, {"texto": "três"}]}).json()
    ids = [d["id"] for d in r["desenvolvimentos"]]
    assert [d["texto"] for d in r["desenvolvimentos"]] == ["um", "dois", "três"]

    # Reordenar mandando os mesmos ids em outra ordem preserva as chaves do DOM.
    r2 = cliente.patch(f"/lab/api/cards/{cid}", json={"desenvolvimentos": [
        {"id": ids[2], "texto": "três"}, {"id": ids[0], "texto": "um"}]}).json()
    assert [d["texto"] for d in r2["desenvolvimentos"]] == ["três", "um"]
    assert [d["id"] for d in r2["desenvolvimentos"]] == [ids[2], ids[0]]
    # O que não veio na lista foi removido.
    assert ids[1] not in [d["id"] for d in r2["desenvolvimentos"]]


def test_tipo_e_formato_invalidos_dao_400(cliente):
    card = cliente.post("/lab/api/cards", json={"titulo": "a"}).json()
    assert cliente.patch(f"/lab/api/cards/{card['id']}", json={"tipo": "xpto"}).status_code == 400
    assert cliente.patch(f"/lab/api/cards/{card['id']}", json={"formato": "xpto"}).status_code == 400


# ─────────────────────────── Publicar ───────────────────────────

def _card_pronto(cliente, tipo="conteudo", formato="lofi"):
    card = cliente.post("/lab/api/cards", json={"titulo": "pronto"}).json()
    return cliente.patch(f"/lab/api/cards/{card['id']}",
                         json={"hook": "h", "tipo": tipo, "formato": formato}).json()


def test_publicar_sem_tipo_da_409(cliente):
    card = cliente.post("/lab/api/cards", json={"titulo": "a"}).json()
    r = cliente.post(f"/lab/api/cards/{card['id']}/publicar")
    assert r.status_code == 409
    assert r.json()["detail"]["codigo"] == "falta_tipo"


def test_publicar_sem_formato_da_409(cliente):
    card = cliente.post("/lab/api/cards", json={"titulo": "a"}).json()
    cliente.patch(f"/lab/api/cards/{card['id']}", json={"tipo": "conteudo"})
    r = cliente.post(f"/lab/api/cards/{card['id']}/publicar")
    assert r.status_code == 409
    assert r.json()["detail"]["codigo"] == "falta_formato"


def test_publicar_move_a_regua(cliente):
    card = _card_pronto(cliente)
    r = cliente.post(f"/lab/api/cards/{card['id']}/publicar").json()
    assert r["card"]["status"] == "publicado"
    assert r["card"]["publicado_em"]
    assert r["regua_antes"]["saldo"] == 0
    assert r["regua_depois"]["saldo"] == 3          # 1 conteúdo de hoje = 3 pontos
    assert r["regua_depois"]["x"] != r["regua_antes"]["x"]


def test_publicar_duas_vezes_da_409(cliente):
    card = _card_pronto(cliente)
    cliente.post(f"/lab/api/cards/{card['id']}/publicar")
    r = cliente.post(f"/lab/api/cards/{card['id']}/publicar")
    assert r.status_code == 409
    assert r.json()["detail"]["codigo"] == "ja_publicado"


def test_anuncio_consome_cinco_conteudos(cliente):
    for _ in range(5):
        cliente.post(f"/lab/api/cards/{_card_pronto(cliente)['id']}/publicar")
    assert cliente.get("/lab/api/regua").json()["saldo"] == 15
    anuncio = _card_pronto(cliente, tipo="anuncio", formato="vlog")
    r = cliente.post(f"/lab/api/cards/{anuncio['id']}/publicar").json()
    assert r["regua_depois"]["saldo"] == 0
    assert r["regua_depois"]["zona"] == "verde"


def test_card_publicado_nao_regride_ao_apagar_o_roteiro(cliente):
    card = _card_pronto(cliente)
    cliente.post(f"/lab/api/cards/{card['id']}/publicar")
    r = cliente.patch(f"/lab/api/cards/{card['id']}", json={"hook": ""}).json()
    assert r["status"] == "publicado", "publicado é histórico imutável"


def test_duplicar_copia_o_roteiro_mas_nao_a_publicacao(cliente):
    card = _card_pronto(cliente)
    cliente.patch(f"/lab/api/cards/{card['id']}",
                  json={"desenvolvimentos": [{"texto": "um"}, {"texto": "dois"}]})
    cliente.post(f"/lab/api/cards/{card['id']}/publicar")
    copia = cliente.post(f"/lab/api/cards/{card['id']}/duplicar").json()
    assert copia["id"] != card["id"]
    assert copia["status"] == "producao"           # a cópia volta pra bancada
    assert copia["publicado_em"] is None
    assert [d["texto"] for d in copia["desenvolvimentos"]] == ["um", "dois"]
    assert copia["tipo"] == "conteudo" and copia["formato"] == "lofi"
    # O saldo não pode dobrar por causa de uma cópia.
    assert cliente.get("/lab/api/regua").json()["saldo"] == 3


# ─────────────────────────── Remover ───────────────────────────

def test_remover_arquiva_por_padrao(cliente):
    card = cliente.post("/lab/api/cards", json={"titulo": "a"}).json()
    cliente.delete(f"/lab/api/cards/{card['id']}")
    assert cliente.get("/lab/api/cards").json() == []
    assert len(cliente.get("/lab/api/cards?arquivados=1").json()) == 1


def test_nao_apaga_publicado_de_vez(cliente):
    card = _card_pronto(cliente)
    cliente.post(f"/lab/api/cards/{card['id']}/publicar")
    r = cliente.delete(f"/lab/api/cards/{card['id']}?definitivo=1")
    assert r.status_code == 409


# ─────────────────────────── Régua e onboarding ───────────────────────────

def test_simular_mostra_o_preview_sem_gravar(cliente):
    r = cliente.get("/lab/api/regua?simular=anuncio").json()
    assert r["saldo"] == 0
    assert r["simulado"]["saldo"] == -15
    assert r["simulado"]["mensagem"] == "VOCÊ PRECISA PRODUZIR CONTEÚDO"
    # Nada foi gravado.
    assert cliente.get("/lab/api/regua").json()["saldo"] == 0


def test_backfill_cria_publicacoes_sinteticas(cliente):
    r = cliente.post("/lab/api/onboarding/backfill", json={"janelas": {
        "0_30": {"conteudos": 6, "anuncios": 1},
        "31_60": {"conteudos": 3, "anuncios": 0},
        "61_90": {"conteudos": 0, "anuncios": 0},
    }}).json()
    assert r["criadas"] == 10
    assert r["regua"]["saldo"] == 18 - 15 + 6      # 6*3 - 5*3 + 3*2
    assert cliente.get("/lab/api/config").json()["onboarding"]["feito"] is True


def test_backfill_repetido_corrige_em_vez_de_somar(cliente):
    corpo = {"janelas": {"0_30": {"conteudos": 6, "anuncios": 0}}}
    cliente.post("/lab/api/onboarding/backfill", json=corpo)
    r = cliente.post("/lab/api/onboarding/backfill", json=corpo).json()
    assert r["criadas"] == 6
    assert r["regua"]["saldo"] == 18, "chamar de novo não pode inflar o saldo"


def test_pular_deixa_a_regua_cinza(cliente):
    r = cliente.post("/lab/api/onboarding/pular").json()
    assert r["regua"]["cinza"] is True
    assert cliente.get("/lab/api/regua").json()["cinza"] is True
    # A primeira publicação real tira o cinza.
    cliente.post(f"/lab/api/cards/{_card_pronto(cliente)['id']}/publicar")
    assert cliente.get("/lab/api/regua").json()["cinza"] is False


def test_detalhe_lista_o_historico_com_peso(cliente):
    cliente.post(f"/lab/api/cards/{_card_pronto(cliente)['id']}/publicar")
    d = cliente.get("/lab/api/regua/detalhe").json()
    assert d["saldo"] == 3
    assert d["janelas"]["0_30"]["conteudos"] == 1
    assert d["publicacoes"][0]["peso"] == 3
    assert d["publicacoes"][0]["titulo"] == "pronto"


def test_testar_regua_nao_toca_no_banco(cliente):
    r = cliente.post("/lab/api/regua/testar", json={
        "agora": "2026-08-28T15:00:00+00:00",
        "publicacoes": [["anuncio", "2026-08-27T15:00:00+00:00"]],
    }).json()
    assert r["saldo"] == -15
    assert r["zona"] == "amarelo_esq"
    assert cliente.get("/lab/api/regua").json()["saldo"] == 0


# ─────────────────────────── Estado e config ───────────────────────────

def test_estado_traz_tudo_num_round_trip(cliente):
    cliente.post("/lab/api/cards", json={"titulo": "a"})
    e = cliente.get("/lab/api/estado").json()
    assert len(e["cards"]) == 1
    assert e["regua"]["zona"] == "verde"
    assert e["config"]["meta_semanal"] == 6
    assert e["config"]["autofoco"] is True


def test_config_salva_parcial(cliente):
    r = cliente.put("/lab/api/config", json={"autofoco": False}).json()
    assert r["autofoco"] is False
    assert r["meta_semanal"] == 6, "o que não foi mandado continua no padrão"


def test_meta_semanal_configuravel_afeta_a_regua(cliente):
    cliente.put("/lab/api/config", json={"meta_semanal": 10})
    assert cliente.get("/lab/api/regua").json()["meta_semanal"]["meta"] == 10
