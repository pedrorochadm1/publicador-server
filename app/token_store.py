"""Guarda o token do Instagram em disco e renova sozinho antes de expirar.

Na primeira execução pega o token de INSTAGRAM_ACCESS_TOKEN (env). Depois passa
a usar a cópia persistida no volume, renovada automaticamente. Assim o token
nunca morre, independente do Mac do Pedro estar ligado.
"""
import json
import os
from datetime import datetime, timezone

import requests

from . import config

_DIAS_PARA_RENOVAR = 15


def _carregar() -> dict:
    if os.path.exists(config.TOKEN_PATH):
        with open(config.TOKEN_PATH) as f:
            return json.load(f)
    return {"token": config.INSTAGRAM_ACCESS_TOKEN}


def _salvar(token: str):
    with open(config.TOKEN_PATH, "w") as f:
        json.dump({"token": token, "atualizado_em": datetime.now(timezone.utc).isoformat()}, f)


def get_token() -> str:
    return _carregar().get("token") or config.INSTAGRAM_ACCESS_TOKEN


def salvar_token(token: str):
    _salvar(token)


def status() -> int | None:
    try:
        return _dias_para_expirar(get_token())
    except Exception:  # noqa: BLE001
        return None


def _dias_para_expirar(token: str) -> int | None:
    r = requests.get(
        f"{config.GRAPH_BASE}/debug_token",
        params={"input_token": token, "access_token": token},
        timeout=30,
    )
    data = r.json().get("data", {})
    expira = data.get("expires_at", 0)
    if not expira:
        return None  # token sem expiração
    expira_dt = datetime.fromtimestamp(expira, tz=timezone.utc)
    return (expira_dt - datetime.now(timezone.utc)).days


def renovar_se_necessario():
    """Roda diariamente. Renova o token se faltar pouco para expirar."""
    token = get_token()
    if not token:
        print("[token] Nenhum token configurado.")
        return
    try:
        dias = _dias_para_expirar(token)
    except Exception as e:  # noqa: BLE001
        print(f"[token] Falha ao verificar: {e}")
        return

    if dias is None:
        print("[token] Token sem expiração. Nada a fazer.")
        return
    print(f"[token] Expira em {dias} dias.")
    if dias > _DIAS_PARA_RENOVAR:
        return

    r = requests.get(
        f"{config.GRAPH_BASE}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": config.FACEBOOK_APP_ID,
            "client_secret": config.FACEBOOK_APP_SECRET,
            "fb_exchange_token": token,
        },
        timeout=30,
    )
    data = r.json()
    if "access_token" in data:
        _salvar(data["access_token"])
        print("[token] Renovado com sucesso.")
    else:
        print(f"[token] Falha na renovação: {data}")
