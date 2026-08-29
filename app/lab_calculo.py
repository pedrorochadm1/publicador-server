"""Motor de proporção 5:1 do Laboratório DM1.

Módulo PURO: sem banco, sem rede, sem estado. Recebe uma lista de publicações e
um "agora", devolve a régua pronta. É assim de propósito — esta é a parte do
produto com mais chance de erro sutil, e aqui ela fica testável sem browser.

A regra: publicar 5 conteúdos para cada 1 anúncio. Publicações dos últimos 90
dias entram na conta com peso decrescente, e o saldo é SEMPRE calculado, nunca
armazenado — assim ele decai sozinho conforme as publicações envelhecem, sem
nenhum job rodando em background.
"""
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("America/Sao_Paulo")

CONTEUDO = "conteudo"
ANUNCIO = "anuncio"

# Um anúncio "custa" 5 conteúdos da mesma idade.
PROPORCAO = 5
# A escala da régua cobre 4 anúncios de desequilíbrio para cada lado (4 * 15 = 60).
ESCALA = 60
# Um conteúdo publicado hoje vale 3 pontos; um anúncio de hoje consome 15.
PESO_MAXIMO = 3
CUSTO_ANUNCIO_HOJE = PESO_MAXIMO * PROPORCAO  # 15

LIMITE_VERDE = 7
LIMITE_VERMELHO = 30

META_SEMANAL_PADRAO = 6

JANELAS = (("0_30", 0, 30), ("31_60", 30, 60), ("61_90", 60, 90))


def peso(dias: float) -> int:
    """Peso de uma publicação pela idade. Fronteiras inclusivas à direita."""
    if dias <= 30:
        return 3
    if dias <= 60:
        return 2
    if dias <= 90:
        return 1
    return 0


def idade_em_dias(quando: datetime, agora: datetime) -> float:
    """Diferença exata em dias — não dia de calendário."""
    return (agora - quando).total_seconds() / 86400


def _normalizar(publicacoes, agora: datetime):
    """[(tipo, datetime|str iso)] -> [(tipo, datetime aware, dias, peso)], só as que pesam."""
    saida = []
    for tipo, quando in publicacoes:
        if isinstance(quando, str):
            quando = datetime.fromisoformat(quando)
        if quando.tzinfo is None:
            quando = quando.replace(tzinfo=timezone.utc)
        dias = idade_em_dias(quando, agora)
        p = peso(dias)
        if p:
            saida.append((tipo, quando, dias, p))
    return saida


def somas(publicacoes, agora: datetime) -> tuple[int, int]:
    """(Pc, Pa) — soma dos pesos de conteúdos e de anúncios dos últimos 90 dias."""
    pc = pa = 0
    for tipo, _quando, _dias, p in _normalizar(publicacoes, agora):
        if tipo == ANUNCIO:
            pa += p
        else:
            pc += p
    return pc, pa


def saldo(publicacoes, agora: datetime) -> int:
    """SALDO = Pc - (5 * Pa). Positivo = conteúdo demais; negativo = anúncio demais."""
    pc, pa = somas(publicacoes, agora)
    return pc - PROPORCAO * pa


def zona(s: float) -> str:
    """As 5 faixas cobrem a reta inteira, sem buraco entre elas."""
    if s <= -LIMITE_VERMELHO:
        return "vermelho_esq"
    if s < -LIMITE_VERDE:
        return "amarelo_esq"
    if s <= LIMITE_VERDE:
        return "verde"
    if s < LIMITE_VERMELHO:
        return "amarelo_dir"
    return "vermelho_dir"


def posicao(s: float) -> float:
    """Onde o ponteiro para, em [-1, +1]. Um anúncio desloca 5x mais que um conteúdo."""
    return max(-1.0, min(1.0, s / ESCALA))


def contagem_atraso(s: float) -> int:
    """Quantas publicações inteiras faltam. Nunca expressa 'crédito'."""
    if s < 0:
        return math.ceil(abs(s) / PESO_MAXIMO)
    # floor(x + 0.5), não round(): o round() do Python é bankers' rounding e
    # devolveria 22 para 22.5, quando o esperado aqui é arredondar pra cima.
    return max(1, math.floor(s / CUSTO_ANUNCIO_HOJE + 0.5))


def _plural(n: int, um: str, varios: str) -> str:
    return f"{n} {um}" if n == 1 else f"{n} {varios}"


def mensagem(s: float) -> tuple[str, str]:
    """(mensagem principal, subtexto) da zona atual."""
    z = zona(s)
    if z == "verde":
        return "VOCÊ ESTÁ NO RITMO", "equilíbrio saudável"
    n = contagem_atraso(s)
    if s < 0:
        return "VOCÊ PRECISA PRODUZIR CONTEÚDO", _plural(n, "conteúdo atrasado", "conteúdos atrasados")
    return "VOCÊ PRECISA PRODUZIR ANÚNCIO", _plural(n, "anúncio atrasado", "anúncios atrasados")


# ─────────────────────────── Meta semanal ───────────────────────────

def semana_sp(agora: datetime) -> tuple[datetime, datetime]:
    """Segunda 00:00 até domingo 23:59:59.999 em São Paulo, devolvido em UTC.

    A conta é feita no fuso local porque 'esta semana' é uma noção de calendário;
    fazer em UTC erraria a fronteira por 3 horas todo domingo à noite.
    """
    local = agora.astimezone(FUSO)
    inicio_local = local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=local.weekday())
    fim_local = inicio_local + timedelta(days=7) - timedelta(microseconds=1)
    return inicio_local.astimezone(timezone.utc), fim_local.astimezone(timezone.utc)


def meta_semanal(publicacoes, agora: datetime, meta: int = META_SEMANAL_PADRAO) -> dict:
    """Quantos conteúdos saíram nesta semana e se o ritmo está atrasado."""
    inicio, _fim = semana_sp(agora)
    feitos = 0
    for tipo, quando in publicacoes:
        if tipo != CONTEUDO:
            continue
        if isinstance(quando, str):
            quando = datetime.fromisoformat(quando)
        if quando.tzinfo is None:
            quando = quando.replace(tzinfo=timezone.utc)
        if quando >= inicio:
            feitos += 1
    decorridos = min(7.0, max(0.0, (agora - inicio).total_seconds() / 86400))
    esperado = math.ceil(meta * decorridos / 7) if meta > 0 else 0
    return {
        "meta": meta,
        "feitos": feitos,
        "esperado": esperado,
        # Folga de 1 pra não alertar na terça de manhã por causa do arredondamento.
        "atrasado": bool(meta > 0 and feitos < esperado - 1),
        "faltam": max(0, meta - feitos),
        "dias_restantes": max(0, math.ceil(7 - decorridos)),
        "inicio": inicio.isoformat(),
    }


# ─────────────────────────── Régua completa ───────────────────────────

CORES = {
    "vermelho_esq": "#e5484d",
    "amarelo_esq": "#e8a33d",
    "verde": "#3dd68c",
    "amarelo_dir": "#e8a33d",
    "vermelho_dir": "#e5484d",
    "cinza": "#4a5058",
}


def regua(publicacoes, agora: datetime, meta: int = META_SEMANAL_PADRAO,
          cinza: bool = False) -> dict:
    """A struct que a UI desenha. Toda a regra de negócio resolvida aqui."""
    pc, pa = somas(publicacoes, agora)
    s = pc - PROPORCAO * pa
    if cinza:
        # Partida a frio com 'pular': sem ponteiro até a primeira publicação real.
        return {
            "cinza": True, "saldo": 0, "pc": 0, "pa": 0, "x": 0.0,
            "zona": "cinza", "cor": CORES["cinza"],
            "mensagem": "SEM HISTÓRICO AINDA",
            "subtexto": "marque a primeira publicação pra régua começar",
            "contagem": 0, "total": 0,
            "meta_semanal": meta_semanal(publicacoes, agora, meta),
        }
    z = zona(s)
    principal, sub = mensagem(s)
    return {
        "cinza": False,
        "saldo": s,
        "pc": pc,
        "pa": pa,
        "x": posicao(s),
        "zona": z,
        "cor": CORES[z],
        "mensagem": principal,
        "subtexto": sub,
        "contagem": 0 if z == "verde" else contagem_atraso(s),
        "total": len(_normalizar(publicacoes, agora)),
        "meta_semanal": meta_semanal(publicacoes, agora, meta),
    }


def detalhe(publicacoes, agora: datetime) -> dict:
    """Detalhamento que abre ao tocar na régua: proporção real por janela."""
    janelas = {}
    for nome, ini, fim in JANELAS:
        conteudos = anuncios = 0
        for tipo, _q, dias, _p in _normalizar(publicacoes, agora):
            if ini < dias <= fim or (ini == 0 and dias <= fim):
                if tipo == ANUNCIO:
                    anuncios += 1
                else:
                    conteudos += 1
        janelas[nome] = {
            "conteudos": conteudos,
            "anuncios": anuncios,
            # Quantos conteúdos por anúncio, de fato. None = nenhum anúncio no período.
            "proporcao": round(conteudos / anuncios, 1) if anuncios else None,
        }
    pc, pa = somas(publicacoes, agora)
    return {"saldo": pc - PROPORCAO * pa, "pc": pc, "pa": pa, "janelas": janelas}


# ─────────────────────────── Backfill da partida a frio ───────────────────────────

def datas_backfill(inicio_dias: int, fim_dias: int, n: int, agora: datetime) -> list[datetime]:
    """n datas espalhadas uniformemente dentro da janela [inicio_dias, fim_dias].

    Espalhar importa: se todas ficassem coladas na borda da janela, envelheceriam
    em bloco e o saldo daria um degrau artificial de um dia pro outro.
    """
    if n <= 0:
        return []
    largura = fim_dias - inicio_dias
    return [
        agora - timedelta(days=inicio_dias + largura * (i + 0.5) / n)
        for i in range(n)
    ]
