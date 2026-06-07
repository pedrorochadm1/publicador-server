# Publicador @pedrorochadm1

Serviço que **agenda publicações no Instagram sem usar o agendamento nativo**.

A agenda mora aqui no servidor. Um worker roda 24/7 e, na hora marcada, publica
imediatamente via Graph API. O Instagram enxerga como publicação imediata —
porque é: o "agendamento" foi só este servidor segurando a vez.

```
agenda (aqui comigo) ──► API /agendar ──► fila (SQLite)
                                              │
                                       worker a cada 30s
                                              │
                                  venceu? → publica AGORA no IG
```

As imagens são servidas por este mesmo serviço em `/img/<arquivo>` com HTTPS
(domínio do EasyPanel), que é o link que o Instagram baixa.

## Deploy no EasyPanel

1. **Crie um App** apontando para este repositório Git (branch `main`).
   Build: **Dockerfile** (já incluso).
2. **Porta interna:** `8000`.
3. **Domínio:** habilite um domínio (ex: `publicador-pedrorochadm1.srv1274587.hstgr.cloud`)
   com HTTPS ligado. Esse vira o `PUBLIC_BASE_URL`.
4. **Volume persistente:** monte um volume em `/data` (guarda agenda, imagens e token).
5. **Variáveis de ambiente** (aba Environment) — veja `.env.example`:
   - `INSTAGRAM_ACCESS_TOKEN`
   - `INSTAGRAM_BUSINESS_ID`
   - `FACEBOOK_APP_ID`, `FACEBOOK_APP_SECRET` (para renovar o token sozinho)
   - `PUBLICADOR_API_KEY` (segredo para agendar)
   - `PUBLIC_BASE_URL` (o domínio do passo 3, com `https://`)
   - `TZ=America/Sao_Paulo`
6. Deploy. Teste: `GET https://SEU_DOMINIO/health` deve responder `{"ok": true}`.

## API

Todas as rotas (menos `/health`) exigem o header `X-API-Key: <PUBLICADOR_API_KEY>`.

- `POST /agendar` — body:
  ```json
  {
    "publicar_em": "2026-06-10T07:00:00",
    "caption": "Sua legenda.",
    "imagens_b64": ["<base64 do slide 1>", "<base64 do slide 2>"]
  }
  ```
  1 imagem = foto única, 2-10 = carrossel. Sem timezone, assume America/Sao_Paulo.
- `GET /agenda?status=agendado` — lista a fila.
- `DELETE /agenda/{id}` — cancela um agendamento.
- `GET /health` — status do serviço.

## Rodar local (teste)

```bash
pip install -r requirements.txt
DATA_DIR=./data PUBLICADOR_API_KEY=teste uvicorn app.main:app --reload
```
