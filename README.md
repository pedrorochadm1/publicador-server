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

### Push na `main` deploya sozinho

**Apontar o App pro repositório não basta** — o EasyPanel não fica vigiando o
Git. Quem dispara é o *deploy hook* do App (aba Deploy, no painel), cadastrado
como webhook de `push` no GitHub:

```bash
gh api repos/pedrorochadm1/publicador-server/hooks -X POST \
  -f name=web -f "config[url]=$PUBLICADOR_DEPLOY_HOOK" \
  -f "config[content_type]=json" -F active=true -f "events[]=push"
```

Sem isso, o push entra no GitHub e **o servidor continua rodando a versão
antiga**, sem nenhum erro em lugar nenhum — o sintoma é a feature nova
simplesmente não existir no ar. Pra conferir se um deploy chegou:
`GET /lab/api/sessao` devolve a `versao` que está rodando.

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
**5 conteúdos para 1 anúncio** está saudável. O **Carrossel** (onde o slide é
desenhado e publicado) e as automações de comentário→direct são as outras abas.

| Rota | O que é |
|---|---|
| `GET /` com `Host: insta.*` | O Laboratório (os outros domínios continuam vendo `home.html`) |
| `GET /lab` · `GET /carrossel` · `GET /automacoes` | As três abas. Mesmo shell; o JS lê o pathname |
| `GET /insta` | 307 → `/automacoes` (endereço antigo continua funcionando) |
| `GET /insta/classico` | **Escotilha**: o painel antigo, intocado. Se a aba nova quebrar, essa URL devolve o que funciona, sem deploy |
| `GET /manifest.webmanifest` · `GET /sw.js` | PWA. Gerados em Python com a versão injetada |
| `GET /lab/reset` | Desregistra o service worker e limpa os caches |
| `/lab/api/*` | API do Lab. Cookie de sessão, mesma senha `INSTA_UI_PASSWORD` |

**Módulos:** `lab_calculo.py` (motor da régua, puro e testável), `lab_db.py`
(tabelas `lab_*` no mesmo `/data/agenda.db`), `lab_web.py` (rotas),
`lab_carrossel.py` (a aba Carrossel), `sessoes.py` (sessão em SQLite — antes
vivia em RAM e todo redeploy deslogava).

### A aba Carrossel

O slide é desenhado **no navegador**. `carrossel_render.js` tem uma função só que
pinta o canvas de 1080×1350, e é ela que gera tanto o preview quanto o JPEG que
sobe pro Instagram: duas implementações divergiriam, e o Pedro publicaria algo
diferente do que viu. O servidor recebe os JPEGs prontos e só enfileira.

O carrossel é **um documento**: os slides vivem numa coluna JSON de
`lab_carrosseis` e o editor manda o documento inteiro a cada autosave, porque um
slide não tem vida fora do carrossel (ordem, texto e recorte só fazem sentido
juntos). Daí não haver tabela filha.

As imagens que o Pedro sobe ficam em **`/data/carrossel`, não em `/data/img`**.
A segunda é hospedagem temporária — o scheduler apaga a mídia depois de publicar
— e a foto recortada precisa sobreviver pra ele duplicar ou reeditar o carrossel.
O nome do arquivo é um uuid e o conteúdo é checado pelo cabeçalho, não pela
extensão.

**Gerar com IA** (`lab_carrossel_ia.py`) preenche o rascunho a partir de duas
fontes: um reel já publicado (baixa, transcreve com Whisper e escreve) ou uma
ideia digitada na hora — que pode vir de um card do Lab, e aí entra com o roteiro
*e* as observações, que é onde mora o raciocínio solto dele. O resultado é um
carrossel comum em rascunho: mesmo editor, mesmo botão de publicar, nada vai pro
ar sozinho. É síncrono (30-50s) de propósito, na mesma ordem de grandeza da
sugestão de abertura dos cards, que já roda assim há semanas; fila com polling
seria mais infraestrutura do que o problema pede. O vídeo baixado vive num
arquivo temporário e é apagado: ali ele é matéria-prima de leitura, não mídia a
publicar. Uma geração por vez (trava no módulo), porque clicar duas vezes seria
pagar duas chamadas.

**A estrutura segue o playbook de carrossel, a voz segue o Pedro.** O prompt pede
8 slides por padrão (5 a 10 conforme o tema), capa de 3 a 7 palavras, slide 2 que
se sustenta sozinho porque o Instagram o remostra a quem pulou o 1, a entrega
mais densa no slide 3, payoff no penúltimo e CTA no último. A IA também escolhe e
devolve a estratégia (playbook, TOFU/BOFU, categoria de hook), a intenção de cada
slide e duas capas alternativas — tudo isso vive na coluna `ia` e aparece no
editor, onde trocar a capa devolve a antiga para a lista.

Onde o playbook briga com as regras do repositório de conteúdo, **o guia ganha na
estrutura e o Pedro ganha na voz** (foi a escolha dele, em 2026-10-06). Por isso a
capa tem de 3 a 7 palavras e o último slide pede UMA ação só, que são regras do
guia; e por isso a legenda continua sendo uma linha com uma pergunta e **sem
hashtag nenhuma**, que são regras dele e valem mesmo contra o guia, que manda
usar de 5 a 10. Hashtag tem rede dupla: o prompt
proíbe e `_sem_hashtag()` remove o que escapar, porque esse é o erro mais
provável de um modelo que leu aquele guia. Os dois contratos têm teste próprio —
um para as regras de voz, outro para as peças de estrutura.

E o editor peneira de novo antes de publicar: `avisosVoz()` aponta capa acima de
7 palavras, último slide pedindo duas ações, legenda acima de 12 palavras,
travessão, hashtag e "Arrasta". Ela existe porque o prompt pede, mas não garante;
o aviso aparece no modal de publicação, onde ainda dá pra consertar.

Publicar cai no mesmo caminho de qualquer post: `db.criar_post` com a legenda
idêntica no Instagram e no TikTok (modo foto, via Buffer). Acima de
`BUFFER_TIKTOK_MAX_FOTOS` (4 por padrão) o TikTok fica de fora em vez de sair
cortado, e o modal avisa antes. Republicar um carrossel agendado cancela o
agendamento anterior; publicado não republica, duplica.

**O card é uma rolagem só:** título, tipo e formato, hook, texto na tela,
desenvolvimentos, fechamento e, no fim, o que sustenta o roteiro: Observações
(o Pedro discorrendo sobre a própria ideia), Referências (o embasamento) e
Reação (vídeos que o Pedro vai reagir dentro do vídeo dele). As duas listas de
link ficam em `lab_links`, separadas pela coluna `lista`; as observações ficam em
`lab_observacoes`. Nenhuma das três **entra na derivação do status** — escrever
observação ou colar link não move o card pra produção, porque nem o material de
apoio nem o pensamento solto são roteiro.

**Observação é o rascunho antes da estrutura.** Serve pra despejar a ideia
inteira sem encaixá-la em hook/desenvolvimento/fechamento, então é texto
corrido: sem rótulo numerado, sem setas de ordem, e gravada com os parágrafos
exatamente como foram escritos (as outras listas recortam as pontas; aqui
recortar mexeria no que está sendo digitado). Em branco não é gravada, e por isso
o id de cada uma vive no DOM (`data-id`) e não no índice da lista — a lista que
volta do servidor pode ser menor que a da tela. No markdown ela sai depois do
roteiro e antes dos links, com chave própria no bloco Exportar: é o que uma IA
precisa ler pra escrever o resto.

**Cada coluna se ordena pela pergunta que ela responde.** *Ideia* é caixa de
entrada: a última capturada em cima. Inverter faria a ideia recém escrita nascer
fora da tela e parecer que não salvou — o apodrecimento de ideia velha se
resolve com a marca de parada (14 dias sem toque), não com a ordem. *Produção* é
pilha de trabalho: em cima o que foi editado por último, porque é nele que o
Pedro volta a mexer. *Publicado* é histórico: mais recente em cima.

Como Produção depende de `atualizado_em`, **mexer só nos filhos** (um
desenvolvimento, uma observação, um link) também bumpa esse campo. Sem isso, o card em que ele
acabou de trabalhar ficaria parado no meio da pilha.

O **texto na tela** tem uma chave própria (`tela_ativa`). Desligada, a seção nem
existe na exportação; ligada e vazia, sai como pendência explícita. Ligar a chave
é o Pedro declarando que aquele vídeo vai ter texto na tela, então a falta
aparece independente da opção "marcar o que está faltando".

### Armadilhas de layout que já custaram caro

Todas custaram um ciclo de "está quebrado" e estão aqui pra não voltarem.

**`height:100%` num irmão da tabbar.** O app ocupa a tela inteira e quem rola é a
lista, nunca a página. Em flex column os irmãos do meio precisam de
`flex:1; min-height:0` — com `height:100%` o rodapé é empurrado pra fora e o fim
da lista some. Parece "não está salvando", porque o card novo renderiza numa área
invisível.

**Filho de container que rola encolhendo.** Item de flex encolhe por padrão
quando o conteúdo passa da altura. Em `.painel-corpo` e `.col-lista` isso
espremia os campos de texto e cortava o que estava escrito; ambos têm
`> * { flex: none }`.

**`min-width:auto` em item de grid.** Não encolhe abaixo do conteúdo mais largo
que tem dentro: a tabela do histórico esticava a coluna e fazia o editor de
automação rolar de lado. Daí os `min-width: 0` em `automacoes.css`.

**`dvh` no PWA instalado.** O iOS desconta a barra do navegador mesmo em
standalone, onde ela não existe, e sobra faixa vazia embaixo do rodapé. Media
query de `display-mode: standalone` devolve `100vh`, e o `app.js` ainda aplica
`window.innerHeight`.

**O saldo nunca é armazenado.** É recalculado a cada leitura a partir das
publicações dos últimos 90 dias, com peso 3× / 2× / 1× por faixa de idade. Por
isso ele decai sozinho, sem nenhum job em background.

### Mexeu no front? Bumpe a versão

`LAB_VERSAO` em `app/lab_web.py` é a **fonte única**. Ela é injetada no shell e
no `sw.js` (substituindo `__V__`) e nomeia o cache do service worker. Trocar esse
número invalida todo o CSS/JS de uma vez. Sem isso, o navegador pode continuar
servindo o app antigo do cache.

Os ícones do PWA são PNGs commitados em `app/web/lab/static/` (o Safari do iOS
não aceita SVG na tela de início). O favicon é separado e transparente; o ícone
da tela de início é opaco, porque o iOS compõe sobre preto. Para regerá-los a
partir da logo:

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

## Sugestão de abertura com IA

No editor de cada card, **Sugerir hook e texto na tela** envia o markdown completo
da edição atual (incluindo observações, formato e referências) e apresenta apenas
um hook falado e um texto curto de tela. Não salva nem altera os campos do card.
Links são contexto textual; o sistema não assiste vídeos externos.

A rota autenticada é `POST /lab/api/cards/{id}/sugerir-abertura`, com
`{"markdown": "..."}`. Usa `OPENAI_API_KEY` já existente e, opcionalmente,
`LAB_HOOK_MODEL` (padrão `gpt-5.4`). A chamada é sob demanda, sem coleta do
Instagram ou banco de memória. Saída estruturada pela Responses API com `store=False`.
Documentação: https://developers.openai.com/api/docs/guides/structured-outputs

Verificação: `python -m pytest -q tests/test_lab_hook.py tests/test_lab_api.py tests/test_lab_calculo.py`.
