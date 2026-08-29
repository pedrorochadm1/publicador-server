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
- `POST /upload` — hospeda uma imagem (`{"imagem_b64": "..."}`) e devolve `{"url": "..."}`.
  Usado pelo fluxo de publicação imediata (substitui o litterbox).
- `GET /health` — status do serviço.

## Laboratório DM1 (`insta.pedrorochadm1.com`)

O mesmo container também serve o **Laboratório DM1**: onde a ideia de conteúdo é
capturada em dois segundos e amadurece numa estrutura fixa (HOOK →
Desenvolvimentos → Fechamento), com uma régua no topo dizendo se a proporção de
**5 conteúdos para 1 anúncio** está saudável. As automações de comentário→direct
viraram a segunda aba dele.

| Rota | O que é |
|---|---|
| `GET /` com `Host: insta.*` | O Laboratório (os outros domínios continuam vendo `home.html`) |
| `GET /lab` · `GET /automacoes` | As duas abas. Mesmo shell; o JS lê o pathname |
| `GET /insta` | 307 → `/automacoes` (endereço antigo continua funcionando) |
| `GET /insta/classico` | **Escotilha**: o painel antigo, intocado. Se a aba nova quebrar, essa URL devolve o que funciona, sem deploy |
| `GET /manifest.webmanifest` · `GET /sw.js` | PWA. Gerados em Python com a versão injetada |
| `GET /lab/reset` | Desregistra o service worker e limpa os caches |
| `/lab/api/*` | API do Lab. Cookie de sessão, mesma senha `INSTA_UI_PASSWORD` |

**Módulos:** `lab_calculo.py` (motor da régua, puro e testável), `lab_db.py`
(tabelas `lab_*` no mesmo `/data/agenda.db`), `lab_web.py` (rotas),
`sessoes.py` (sessão em SQLite — antes vivia em RAM e todo redeploy deslogava).

**O card tem três abas:** Roteiro (hook → desenvolvimentos → fechamento),
Referências (o embasamento) e Reação (vídeos que o Pedro vai reagir dentro do
vídeo dele). As duas listas de link ficam em `lab_links`, separadas pela coluna
`lista`, e **não entram na derivação do status** — colar um link não move o card
pra produção, porque link é material de apoio, não roteiro.

### Armadilha de layout que já custou caro

O app ocupa a tela inteira e quem rola é a lista de cards, nunca a página. Em
flex column, os irmãos do meio precisam de `flex:1; min-height:0`. Um
`height:100%` num irmão da tabbar empurra o rodapé pra fora da tela e esconde o
fim da lista — o que parece "não está salvando", porque o card novo renderiza
numa área invisível. Foi exatamente isso na v1.

**O saldo nunca é armazenado.** É recalculado a cada leitura a partir das
publicações dos últimos 90 dias, com peso 3× / 2× / 1× por faixa de idade. Por
isso ele decai sozinho, sem nenhum job em background.

### Mexeu no front? Bumpe a versão

`LAB_VERSAO` em `app/lab_web.py` é a **fonte única**. Ela é injetada no shell e
no `sw.js` (substituindo `__V__`) e nomeia o cache do service worker. Trocar esse
número invalida todo o CSS/JS de uma vez. Sem isso, o navegador pode continuar
servindo o app antigo do cache.

Os ícones do PWA são PNGs commitados em `app/web/lab/` (o Safari do iOS não
aceita SVG na tela de início). Para regerá-los a partir da logo:

```bash
qlmanage -t -s 1024 -o . logo.svg
sips -Z 512 logo.svg.png --out app/web/lab/icone-512.png
```

## Rodar local (teste)

```bash
pip install -r requirements.txt
DATA_DIR=./data PUBLICADOR_API_KEY=teste INSTA_UI_PASSWORD=teste \
  uvicorn app.main:app --reload
```

## Testes

Ficam em `tests/` e **não entram na imagem** (o Dockerfile só copia `app/`).

```bash
pip install pytest httpx          # só para desenvolvimento
python -m pytest tests/ -q        # motor da régua + API do Lab
node tests/test_markdown.mjs      # formato de exportação em markdown
node tests/test_css_zoom.mjs      # nenhum campo abaixo de 16px
```

`test_css_zoom.mjs` existe porque o Safari do iOS amplia a página ao focar
qualquer campo com fonte menor que 16px, e a ampliação deixa o app arrastável na
horizontal. Não dá pra desligar isso pelo navegador — a única defesa é a regra,
e o teste é quem a mantém.

O motor da régua é a parte com mais chance de erro sutil (cinco zonas, limites
inclusivos, arredondamento). A tabela dourada em `tests/test_lab_calculo.py`
cobre cada fronteira — inclusive o caso do `round()` do Python, que faz
bankers' rounding e devolveria 22 para 22.5.
