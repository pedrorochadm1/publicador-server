"""Geração de carrossel a partir de um reel publicado ou de uma ideia escrita.

A chamada à OpenAI e o download do reel são trocados por dublês: o que está sob
teste é a cola (validação da entrada, o documento que nasce, a trava de uma
geração por vez), não o texto que o modelo escreve.
"""
import sys

import pytest

from test_lab_api import cliente

FAKE = {
    "titulo": "Pseudo-hipoglicemia",
    "legenda": "Já sentiu isso com a glicose normal?",
    "slides": [
        {"texto": "Glicose em 100 e você tremendo.\n\n**Não é frescura.**\n\nEntenda 👉"},
        {"texto": "Seu corpo se acostumou com a glicose alta.\n\nJá aconteceu com você?"},
    ],
}


@pytest.fixture()
def ia(monkeypatch):
    """Módulo de geração com a IA e o Instagram dublados."""
    from app import config, lab_carrossel_ia as mod
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-teste")
    monkeypatch.setattr(mod.config, "OPENAI_API_KEY", "sk-teste")
    chamadas = []

    def _gerar(fonte):
        chamadas.append(fonte)
        return {k: (v if k != "slides" else [dict(s) for s in v]) for k, v in FAKE.items()}

    monkeypatch.setattr(mod, "gerar", _gerar)
    monkeypatch.setattr(mod, "transcrever_reel", lambda mid: f"transcricao do {mid}")
    mod.chamadas = chamadas
    return mod


def test_exige_sessao(cliente):
    cliente.post("/lab/api/sair")
    assert cliente.post("/lab/api/carrosseis/gerar", json={"origem": "ideia"}).status_code == 401
    assert cliente.get("/lab/api/carrossel/reels").status_code == 401


def test_sem_chave_da_ia_avisa_em_vez_de_quebrar(cliente, monkeypatch):
    from app import config, lab_carrossel_ia as mod
    monkeypatch.setattr(mod.config, "OPENAI_API_KEY", "")
    r = cliente.post("/lab/api/carrosseis/gerar",
                     json={"origem": "ideia", "ideia": "a" * 40})
    assert r.status_code == 503


def test_origem_invalida_e_ideia_curta(cliente, ia):
    assert cliente.post("/lab/api/carrosseis/gerar", json={"origem": "chute"}).status_code == 400
    assert cliente.post("/lab/api/carrosseis/gerar",
                        json={"origem": "ideia", "ideia": "oi"}).status_code == 400
    assert cliente.post("/lab/api/carrosseis/gerar",
                        json={"origem": "reel", "media_id": ""}).status_code == 400


def test_de_uma_ideia_nasce_rascunho_editavel(cliente, ia):
    ideia = "gente sente hipo com a glicose normal depois que melhora o controle"
    d = cliente.post("/lab/api/carrosseis/gerar",
                     json={"origem": "ideia", "ideia": ideia}).json()
    assert d["origem"] == "ideia"
    assert d["status"] == "rascunho", "gerar nunca publica nada"
    assert d["titulo"] == FAKE["titulo"]
    assert d["legenda"] == FAKE["legenda"]
    assert [s["texto"] for s in d["slides"]] == [s["texto"] for s in FAKE["slides"]]
    # Slide gerado entra no mesmo formato do slide feito à mão.
    assert d["slides"][0]["header"] is True and d["slides"][0]["imagem"] is None
    assert d["ideia"] == ideia, "a fonte fica guardada"
    assert d["ia"]["fonte"] == "ideia" and d["ia"]["modelo"]
    assert ia.chamadas[-1] == ideia


def test_de_um_reel_passa_a_transcricao_pra_ia(cliente, ia):
    d = cliente.post("/lab/api/carrosseis/gerar",
                     json={"origem": "reel", "media_id": "17900"}).json()
    assert d["origem"] == "reel" and d["media_id"] == "17900"
    assert "transcricao do 17900" in ia.chamadas[-1]
    assert "TRANSCRIÇÃO" in ia.chamadas[-1], "a IA precisa saber que é fala de vídeo"
    assert len(d["slides"]) == 2


def test_reel_sem_fala_nao_vira_carrossel_vazio(cliente, ia, monkeypatch):
    monkeypatch.setattr(ia, "transcrever_reel", lambda mid: "   ")
    r = cliente.post("/lab/api/carrosseis/gerar", json={"origem": "reel", "media_id": "1"})
    assert r.status_code == 422
    assert not cliente.get("/lab/api/carrosseis").json()["carrosseis"]


def test_erro_da_ia_nao_deixa_rascunho_pela_metade(cliente, ia, monkeypatch):
    def explode(fonte):
        raise RuntimeError("modelo fora do ar")
    monkeypatch.setattr(ia, "gerar", explode)
    r = cliente.post("/lab/api/carrosseis/gerar", json={"origem": "ideia", "ideia": "a" * 40})
    assert r.status_code == 502
    assert not cliente.get("/lab/api/carrosseis").json()["carrosseis"]


def test_uma_geracao_por_vez(cliente, ia):
    """Clicar duas vezes não pode custar duas chamadas de IA."""
    assert ia._em_uso.acquire(blocking=False)
    try:
        r = cliente.post("/lab/api/carrosseis/gerar", json={"origem": "ideia", "ideia": "a" * 40})
        assert r.status_code == 409
    finally:
        ia._em_uso.release()


def test_fonte_do_card_leva_roteiro_e_observacoes(cliente, ia):
    card = cliente.post("/lab/api/cards", json={"titulo": "Pseudo-hipo"}).json()
    cliente.patch(f"/lab/api/cards/{card['id']}", json={
        "hook": "Você treme com a glicose em 100.",
        "desenvolvimentos": [{"texto": "O corpo se acostumou com a alta."}],
        "fechamento": "Isso melhora em semanas.",
        "observacoes": [{"texto": "Lembrar do caso da consulta de ontem."}],
    })
    d = cliente.post("/lab/api/carrosseis/gerar",
                     json={"origem": "ideia", "card_id": card["id"]}).json()
    fonte = ia.chamadas[-1]
    assert "Você treme com a glicose em 100." in fonte
    assert "O corpo se acostumou com a alta." in fonte
    assert "Isso melhora em semanas." in fonte
    assert "Lembrar do caso da consulta de ontem." in fonte, \
        "a observação é o raciocínio solto dele: é o que mais ajuda a IA"
    assert d["status"] == "rascunho"


def test_card_que_nao_existe(cliente, ia):
    r = cliente.post("/lab/api/carrosseis/gerar", json={"origem": "ideia", "card_id": 9999})
    assert r.status_code == 404


def test_prompt_carrega_as_regras_de_voz(ia):
    """O prompt é o contrato de voz. Se alguém apagar uma regra sem querer, cai aqui."""
    p = ia.PROMPT
    for regra in ["hashtag", "travessão", "coach", "endocrinologista",
                  "Entenda 👉", "parágrafo próprio", "**asteriscos**", "uma pergunta"]:
        assert regra in p, f"o prompt perdeu a regra: {regra}"
