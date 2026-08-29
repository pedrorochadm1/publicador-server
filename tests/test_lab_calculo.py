"""Testes do motor da régua 5:1.

Puro: sem banco, sem rede, sem browser. Roda com `pytest` a partir da raiz do repo.
Não entra na imagem Docker (o Dockerfile só copia `app/`).
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import lab_calculo as lc  # noqa: E402

AGORA = datetime(2026, 8, 28, 15, 0, 0, tzinfo=timezone.utc)


def pub(tipo, dias_atras):
    return (tipo, AGORA - timedelta(days=dias_atras))


def conteudos(n, dias_atras):
    return [pub(lc.CONTEUDO, dias_atras) for _ in range(n)]


def anuncios(n, dias_atras):
    return [pub(lc.ANUNCIO, dias_atras) for _ in range(n)]


# ─────────────────────────── Pesos ───────────────────────────

@pytest.mark.parametrize("dias,esperado", [
    (0, 3), (29.9, 3), (30, 3),          # fronteira inclusiva
    (30.001, 2), (59.9, 2), (60, 2),
    (60.001, 1), (89.9, 1), (90, 1),
    (90.001, 0), (365, 0),
])
def test_peso_nas_fronteiras(dias, esperado):
    assert lc.peso(dias) == esperado


def test_publicacao_de_91_dias_nao_entra_na_conta():
    assert lc.saldo(conteudos(10, 91), AGORA) == 0


# ─────────────────────────── Zonas ───────────────────────────

@pytest.mark.parametrize("s,esperado", [
    (-100, "vermelho_esq"), (-31, "vermelho_esq"), (-30, "vermelho_esq"),
    (-29.9, "amarelo_esq"), (-8, "amarelo_esq"), (-7.1, "amarelo_esq"),
    (-7, "verde"), (0, "verde"), (7, "verde"),
    (7.1, "amarelo_dir"), (8, "amarelo_dir"), (29.9, "amarelo_dir"),
    (30, "vermelho_dir"), (31, "vermelho_dir"), (100, "vermelho_dir"),
])
def test_zonas_cobrem_a_reta_sem_buraco(s, esperado):
    assert lc.zona(s) == esperado


def test_mensagem_por_lado():
    assert lc.mensagem(-20)[0] == "VOCÊ PRECISA PRODUZIR CONTEÚDO"
    assert lc.mensagem(0)[0] == "VOCÊ ESTÁ NO RITMO"
    assert lc.mensagem(0)[1] == "equilíbrio saudável"
    assert lc.mensagem(20)[0] == "VOCÊ PRECISA PRODUZIR ANÚNCIO"


def test_subtexto_conta_as_publicacoes_que_faltam():
    # Do lado do conteúdo o mínimo alcançável é 3: a zona amarela só começa em
    # |saldo| > 7, e ceil(7.1/3) = 3. Singular aqui é inatingível por construção.
    assert lc.mensagem(-7.1)[1] == "3 conteúdos atrasados"
    assert lc.mensagem(-12)[1] == "4 conteúdos atrasados"
    assert lc.mensagem(-30)[1] == "10 conteúdos atrasados"


def test_subtexto_de_anuncio_no_singular():
    assert lc.mensagem(7.1)[1] == "1 anúncio atrasado"
    assert lc.mensagem(15)[1] == "1 anúncio atrasado"
    assert lc.mensagem(45)[1] == "3 anúncios atrasados"


# ─────────────────────────── Ponteiro ───────────────────────────

@pytest.mark.parametrize("s,x", [
    (0, 0.0), (60, 1.0), (-60, -1.0), (30, 0.5), (-15, -0.25),
    (100, 1.0), (-100, -1.0),          # clampado nos extremos
])
def test_posicao_do_ponteiro(s, x):
    assert lc.posicao(s) == pytest.approx(x)


def test_anuncio_desloca_cinco_vezes_mais_que_conteudo():
    """Propriedade central da escala: 1 anúncio pesa como 5 conteúdos."""
    so_conteudo = lc.posicao(lc.saldo(conteudos(1, 0), AGORA))
    so_anuncio = lc.posicao(lc.saldo(anuncios(1, 0), AGORA))
    assert abs(so_anuncio) == pytest.approx(5 * abs(so_conteudo))


# ─────────────────────────── Contagem de atraso ───────────────────────────

@pytest.mark.parametrize("s,esperado", [
    (-1, 1), (-3, 1), (-3.1, 2), (-30, 10), (-100, 34),
    (8, 1), (15, 1), (22, 1), (30, 2), (45, 3), (100, 7),
])
def test_contagem_de_atraso(s, esperado):
    assert lc.contagem_atraso(s) == esperado


def test_arredondamento_meio_pra_cima_nao_bankers():
    """round(22.5) do Python devolve 22 (bankers' rounding). Aqui tem que dar 2."""
    assert round(22.5) == 22                      # confirma a armadilha
    assert lc.contagem_atraso(22.5) == 2          # e que não caímos nela
    assert lc.contagem_atraso(37.5) == 3


def test_atraso_de_anuncio_nunca_e_zero_fora_do_verde():
    for s in [7.5, 8, 10, 14]:
        assert lc.contagem_atraso(s) >= 1


# ─────────────────────────── Cenário integrado ───────────────────────────

def test_cenario_do_prd_seis_conteudos_e_um_anuncio():
    pubs = conteudos(6, 10) + anuncios(1, 10)
    pc, pa = lc.somas(pubs, AGORA)
    assert (pc, pa) == (18, 3)
    assert lc.saldo(pubs, AGORA) == 3
    assert lc.zona(3) == "verde"


def test_mais_um_anuncio_joga_a_agulha_pra_esquerda():
    pubs = conteudos(6, 10) + anuncios(2, 10)
    s = lc.saldo(pubs, AGORA)
    assert s == 18 - 5 * 6           # -12
    assert lc.zona(s) == "amarelo_esq"
    assert lc.contagem_atraso(s) == 4


def test_saldo_decai_sozinho_sem_nenhuma_escrita():
    """A mesma lista, avaliada 31 dias depois, vale menos — sem job em background."""
    pubs = conteudos(6, 10)
    assert lc.saldo(pubs, AGORA) == 18
    assert lc.saldo(pubs, AGORA + timedelta(days=31)) == 12    # peso 3 -> 2
    assert lc.saldo(pubs, AGORA + timedelta(days=61)) == 6     # peso 2 -> 1
    assert lc.saldo(pubs, AGORA + timedelta(days=91)) == 0     # saiu da janela


def test_regua_completa():
    r = lc.regua(conteudos(6, 5) + anuncios(2, 5), AGORA)
    assert r["saldo"] == -12
    assert r["zona"] == "amarelo_esq"
    assert r["mensagem"] == "VOCÊ PRECISA PRODUZIR CONTEÚDO"
    assert r["subtexto"] == "4 conteúdos atrasados"
    assert r["x"] == pytest.approx(-0.2)
    assert r["cinza"] is False


def test_regua_cinza_na_partida_a_frio():
    r = lc.regua([], AGORA, cinza=True)
    assert r["cinza"] is True
    assert r["x"] == 0.0
    assert "PRECISA" not in r["mensagem"]


def test_regua_vazia_sem_cinza_fica_verde_neutra():
    r = lc.regua([], AGORA)
    assert r["saldo"] == 0
    assert r["zona"] == "verde"
    assert r["total"] == 0


def test_card_publicado_nao_conta_duas_vezes_por_idade():
    """Uma publicação entra em exatamente uma faixa de peso."""
    for dias in (0, 30, 30.5, 60, 60.5, 90):
        assert lc.peso(dias) in (1, 2, 3)


# ─────────────────────────── Detalhe ───────────────────────────

def test_detalhe_separa_as_tres_janelas():
    pubs = conteudos(5, 10) + conteudos(3, 45) + anuncios(1, 45) + conteudos(2, 75)
    d = lc.detalhe(pubs, AGORA)
    assert d["janelas"]["0_30"] == {"conteudos": 5, "anuncios": 0, "proporcao": None}
    assert d["janelas"]["31_60"] == {"conteudos": 3, "anuncios": 1, "proporcao": 3.0}
    assert d["janelas"]["61_90"]["conteudos"] == 2


def test_detalhe_bate_com_o_saldo():
    pubs = conteudos(4, 5) + anuncios(1, 40)
    d = lc.detalhe(pubs, AGORA)
    assert d["saldo"] == lc.saldo(pubs, AGORA)


# ─────────────────────────── Semana e meta ───────────────────────────

def test_semana_comeca_na_segunda_em_sao_paulo():
    quarta = datetime(2026, 8, 26, 18, 0, tzinfo=timezone.utc)
    inicio, fim = lc.semana_sp(quarta)
    inicio_local = inicio.astimezone(lc.FUSO)
    assert inicio_local.weekday() == 0                    # segunda
    assert (inicio_local.hour, inicio_local.minute) == (0, 0)
    assert fim.astimezone(lc.FUSO).weekday() == 6         # domingo


def test_domingo_a_noite_ainda_e_a_mesma_semana():
    """Em UTC já é segunda; em São Paulo ainda é domingo. A semana não pode virar."""
    domingo_noite_sp = datetime(2026, 8, 31, 1, 30, tzinfo=timezone.utc)  # dom 22:30 em SP
    inicio, fim = lc.semana_sp(domingo_noite_sp)
    assert inicio <= domingo_noite_sp <= fim
    assert inicio.astimezone(lc.FUSO).day == 24           # segunda anterior


def test_meta_semanal_conta_so_conteudo():
    inicio, _ = lc.semana_sp(AGORA)
    dentro = inicio + timedelta(hours=2)
    pubs = [(lc.CONTEUDO, dentro), (lc.CONTEUDO, dentro), (lc.ANUNCIO, dentro)]
    m = lc.meta_semanal(pubs, AGORA)
    assert m["feitos"] == 2
    assert m["meta"] == 6
    assert m["faltam"] == 4


def test_meta_nao_alerta_no_comeco_da_semana():
    segunda = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    assert lc.meta_semanal([], segunda)["atrasado"] is False


def test_meta_alerta_no_fim_da_semana_sem_conteudo():
    domingo = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)
    m = lc.meta_semanal([], domingo)
    assert m["esperado"] >= 5
    assert m["atrasado"] is True


def test_meta_batida_nao_alerta():
    inicio, _ = lc.semana_sp(AGORA)
    pubs = [(lc.CONTEUDO, inicio + timedelta(hours=1))] * 6
    assert lc.meta_semanal(pubs, AGORA)["atrasado"] is False


# ─────────────────────────── Backfill ───────────────────────────

def test_backfill_vazio():
    assert lc.datas_backfill(0, 30, 0, AGORA) == []


def test_backfill_de_um_cai_no_meio_da_janela():
    (d,) = lc.datas_backfill(0, 30, 1, AGORA)
    assert lc.idade_em_dias(d, AGORA) == pytest.approx(15.0)


def test_backfill_espalha_dentro_da_janela():
    datas = lc.datas_backfill(30, 60, 7, AGORA)
    assert len(datas) == 7
    idades = [lc.idade_em_dias(d, AGORA) for d in datas]
    assert all(30 < i < 60 for i in idades)
    assert idades == sorted(idades)                       # não repete nem inverte
    assert all(lc.peso(i) == 2 for i in idades)           # todas na faixa certa


def test_backfill_reproduz_o_saldo_esperado():
    """6 conteúdos e 1 anúncio na janela recente devolvem o saldo do PRD."""
    pubs = [(lc.CONTEUDO, d) for d in lc.datas_backfill(0, 30, 6, AGORA)]
    pubs += [(lc.ANUNCIO, d) for d in lc.datas_backfill(0, 30, 1, AGORA)]
    assert lc.saldo(pubs, AGORA) == 3


# ─────────────────────────── Entrada em string ISO ───────────────────────────

def test_aceita_data_em_texto_iso():
    iso = (AGORA - timedelta(days=5)).isoformat()
    assert lc.saldo([(lc.CONTEUDO, iso)], AGORA) == 3


def test_data_sem_fuso_e_tratada_como_utc():
    ingenua = (AGORA - timedelta(days=5)).replace(tzinfo=None).isoformat()
    assert lc.saldo([(lc.CONTEUDO, ingenua)], AGORA) == 3
