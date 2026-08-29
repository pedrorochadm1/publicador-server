"""Sessões do painel web, persistidas no SQLite.

Antes as sessões viviam num dict em memória: todo redeploy do container derrubava
o login. Pra um app que o Pedro abre dezenas de vezes por dia no celular (e que
vira PWA na tela de início), ser deslogado a cada deploy é inaceitável.

Guardamos o sha256 do token, nunca o token em si — se alguém ler o banco, não
consegue forjar um cookie. A validade é DESLIZANTE: cada requisição válida
renova o prazo, então quem usa o app nunca é deslogado.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from .db import conn

_VALIDADE_DIAS = 30
# Só regrava o último uso se já passou isso — evita um UPDATE por requisição.
_RENOVA_APOS_HORAS = 12

_iniciado = False


def _init():
    global _iniciado
    if _iniciado:
        return
    c = conn()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS ui_sessoes (
            token_hash TEXT PRIMARY KEY,      -- sha256 do token do cookie, nunca o token
            criado_em  TEXT NOT NULL,
            ultimo_uso TEXT NOT NULL,
            agente     TEXT NOT NULL DEFAULT ''
        )
        """
    )
    c.execute("CREATE INDEX IF NOT EXISTS ix_ui_sessoes_uso ON ui_sessoes (ultimo_uso)")
    c.commit()
    _iniciado = True


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def criar(agente: str = "") -> str:
    """Cria uma sessão e devolve o token que vai no cookie."""
    _init()
    token = secrets.token_urlsafe(32)
    agora = _agora().isoformat()
    c = conn()
    c.execute(
        "INSERT INTO ui_sessoes (token_hash, criado_em, ultimo_uso, agente) VALUES (?, ?, ?, ?)",
        (_hash(token), agora, agora, (agente or "")[:200]),
    )
    c.commit()
    _limpar_velhas()
    return token


def valida(token: str | None) -> bool:
    """A sessão existe e está dentro da validade? Renova o prazo de quebra."""
    if not token:
        return False
    _init()
    c = conn()
    r = c.execute(
        "SELECT ultimo_uso FROM ui_sessoes WHERE token_hash = ?", (_hash(token),)
    ).fetchone()
    if not r:
        return False
    try:
        ultimo = datetime.fromisoformat(r["ultimo_uso"])
    except ValueError:
        return False
    agora = _agora()
    if agora - ultimo > timedelta(days=_VALIDADE_DIAS):
        encerrar(token)
        return False
    # Validade deslizante: quem usa o app não é deslogado.
    if agora - ultimo > timedelta(hours=_RENOVA_APOS_HORAS):
        c.execute(
            "UPDATE ui_sessoes SET ultimo_uso = ? WHERE token_hash = ?",
            (agora.isoformat(), _hash(token)),
        )
        c.commit()
    return True


def encerrar(token: str | None):
    if not token:
        return
    _init()
    c = conn()
    c.execute("DELETE FROM ui_sessoes WHERE token_hash = ?", (_hash(token),))
    c.commit()


def _limpar_velhas():
    corte = (_agora() - timedelta(days=_VALIDADE_DIAS)).isoformat()
    c = conn()
    c.execute("DELETE FROM ui_sessoes WHERE ultimo_uso < ?", (corte,))
    c.commit()


# ─────────────────────── Rate-limit de senha errada ───────────────────────
# Continua em memória de propósito: perder o contador num redeploy é inofensivo
# (o atacante ganha 10 tentativas a mais), e não vale um write no banco por falha.

_TENTATIVAS: dict[str, list[float]] = {}
_MAX_FALHAS = 10
_JANELA_S = 600


def bloqueado(ip: str) -> bool:
    import time

    agora = time.time()
    tentativas = [t for t in _TENTATIVAS.get(ip, []) if agora - t < _JANELA_S]
    _TENTATIVAS[ip] = tentativas
    return len(tentativas) >= _MAX_FALHAS


def registrar_falha(ip: str):
    import time

    _TENTATIVAS.setdefault(ip, []).append(time.time())
