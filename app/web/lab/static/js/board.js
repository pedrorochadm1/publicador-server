/* A bancada: captura no topo, três colunas embaixo.

   No Mac as colunas ficam lado a lado. No celular viram abas com swipe — não um
   scroll infinito de tudo junto, que é o que torna um board inútil numa tela
   estreita.

   A captura é progressiva: o campo está sempre visível, e tipo/formato/botão só
   aparecem quando o Pedro começa a escrever. Quem só quer despejar a ideia não
   vê nada disso; quem já sabe o formato marca ali mesmo. */

import { get, post, put, del, esc, data, aviso, SemRede } from "./api.js";
import { confirmar } from "./painel.js";
import * as fila from "./fila.js";
import * as regua from "./regua.js";
import { abrirEditor } from "./editor.js";
import { loteParaMarkdown, copiar } from "./markdown.js";
import { TIPOS, FORMATOS, chipsDe, tagsDe } from "./opcoes.js";

const COLUNAS = [
  { id: "ideia", nome: "Ideia" },
  { id: "producao", nome: "Produção" },
  { id: "publicado", nome: "Publicado" },
];

let cards = [];
let config = {};
let filtros = { tipo: [], formato: [] };
let busca = "";
let colunaAtiva = "ideia";
let selecao = new Set();
let verTudoPublicado = false;
let raiz = null;
let novoTipo = null;
let novoFormato = null;
let fecharPorFora = null;

export async function montar(alvo, dadosIniciais) {
  raiz = alvo;
  cards = dadosIniciais.cards || [];
  config = dadosIniciais.config || {};
  filtros = {
    tipo: [...(config.filtros?.tipo || [])],
    formato: [...(config.filtros?.formato || [])],
  };
  selecao = new Set();
  raiz.innerHTML = esqueleto();
  ligarCaptura();
  ligarFiltros();
  pintar();
  const tentar = fila.vigiar((criados) => { juntar(criados); pintar(); });
  tentar();
}

function esqueleto() {
  return `
  <div class="board">
    <div class="topo-board">
      <form class="captura" id="captura" autocomplete="off">
        <input type="text" id="captura-campo" placeholder="uma ideia…"
               enterkeyhint="done" autocapitalize="sentences" autocorrect="on">
        <div class="captura-extra">
          <div class="grupo-chips" id="novo-tipo"></div>
          <div class="grupo-chips" id="novo-formato"></div>
          <button class="bt bt-add" type="submit">Adicionar ideia</button>
        </div>
      </form>

      <div class="filtros" id="filtros">
        <div class="filtros-linha">
          <input type="search" id="busca" placeholder="buscar" enterkeyhint="search">
          <button class="bt-filtros" id="bt-filtros" type="button">Filtros</button>
        </div>
        <div class="filtros-abertos" id="filtros-abertos"></div>
      </div>
    </div>

    <div class="colunas-abas" id="colunas-abas"></div>
    <div class="colunas" id="colunas"></div>
    <div class="acoes-lote" id="acoes-lote" hidden></div>
  </div>`;
}

/* ─────────────────────────── Captura ─────────────────────────── */

function ligarCaptura() {
  const bloco = raiz.querySelector("#captura");
  const campo = raiz.querySelector("#captura-campo");

  const abrir = () => bloco.classList.add("aberta");
  const talvezFechar = () => {
    if (!campo.value.trim()) bloco.classList.remove("aberta");
  };

  campo.addEventListener("focus", abrir);
  campo.addEventListener("input", abrir);
  // Fecha só quando o toque cai fora do bloco e não há nada escrito. Usar
  // focusout fecharia ao tocar num chip, que é justamente o que queremos abrir.
  // O listener anterior é removido: montar() roda a cada troca de aba, e eles
  // se acumulariam ao longo do dia.
  if (fecharPorFora) document.removeEventListener("pointerdown", fecharPorFora);
  fecharPorFora = (e) => {
    if (bloco.isConnected && !bloco.contains(e.target)) talvezFechar();
  };
  document.addEventListener("pointerdown", fecharPorFora);

  if (config.autofoco !== false) {
    // No iOS o teclado não abre sem gesto do usuário, mas o foco já posiciona o
    // cursor e a rolagem no lugar certo.
    setTimeout(() => campo.focus(), 80);
  }

  pintarChipsNovo();
  bloco.onsubmit = (e) => { e.preventDefault(); capturar(campo, bloco); };
}

function pintarChipsNovo() {
  raiz.querySelector("#novo-tipo").innerHTML =
    `<span class="grupo-rot">Tipo (opcional)</span>` + chipsDe(TIPOS, novoTipo, "tipo");
  raiz.querySelector("#novo-formato").innerHTML =
    `<span class="grupo-rot">Formato (opcional)</span>` + chipsDe(FORMATOS, novoFormato, "fmt");

  raiz.querySelectorAll("#novo-tipo .op").forEach((b) => {
    b.onclick = () => { novoTipo = novoTipo === b.dataset.v ? null : b.dataset.v; pintarChipsNovo(); };
  });
  raiz.querySelectorAll("#novo-formato .op").forEach((b) => {
    b.onclick = () => { novoFormato = novoFormato === b.dataset.v ? null : b.dataset.v; pintarChipsNovo(); };
  });
}

async function capturar(campo, bloco) {
  const titulo = campo.value.trim();
  if (!titulo) { campo.focus(); return; }

  const tipo = novoTipo, formato = novoFormato;
  // Só limpar o value: recriar o nó mataria o foco e fecharia o teclado do
  // iPhone, quebrando o "despejar quatro ideias seguidas".
  campo.value = "";
  campo.focus();
  novoTipo = null;
  novoFormato = null;
  pintarChipsNovo();
  bloco.classList.remove("aberta");

  const item = fila.enfileirar(titulo, tipo, formato);
  const provisorio = {
    id: "tmp-" + item.client_uuid, client_uuid: item.client_uuid, titulo,
    status: "ideia", tipo, formato, hook: "", fechamento: "",
    desenvolvimentos: [], referencias: [], reacoes: [], tags: [],
    provisorio: true, criado_em: item.em,
  };
  cards = [provisorio, ...cards];
  colunaAtiva = "ideia";
  pintar();

  try {
    const card = await post("/lab/api/cards", {
      titulo, client_uuid: item.client_uuid, tipo, formato,
    });
    fila.remover(item.client_uuid);
    cards = cards.map((c) => (c.id === provisorio.id ? card : c));
    pintar();
    // Um filtro ativo pode esconder a ideia recém-salva. Dizer isso evita ler
    // "sumiu" como "não salvou".
    if (!passaNoFiltro(card)) aviso("Salvo, mas escondido pelo filtro ativo.");
  } catch (err) {
    if (err instanceof SemRede) aviso("Sem rede. A ideia ficou guardada e sobe depois.");
    else if (err.status !== 401) aviso("Não deu pra salvar agora. Ficou na fila.");
  }
}

function juntar(novos) {
  const porUuid = new Map(novos.map((c) => [c.client_uuid, c]));
  cards = cards.map((c) => {
    if (!c.provisorio) return c;
    return porUuid.get(c.client_uuid) || c;
  });
  const ids = new Set(cards.filter((c) => !c.provisorio).map((c) => c.id));
  cards = [...novos.filter((c) => !ids.has(c.id)), ...cards.filter((c) => !c.provisorio || !porUuid.has(c.client_uuid))];
  // Remove duplicata por id, mantendo a primeira ocorrência.
  const vistos = new Set();
  cards = cards.filter((c) => (vistos.has(c.id) ? false : (vistos.add(c.id), true)));
}

/* ─────────────────────────── Filtros ─────────────────────────── */

function ligarFiltros() {
  const b = raiz.querySelector("#busca");
  b.oninput = () => { busca = b.value.trim().toLowerCase(); pintar(); };
  raiz.querySelector("#bt-filtros").onclick = () => {
    raiz.querySelector("#filtros").classList.toggle("aberto");
  };
}

function pintarFiltros() {
  const n = filtros.tipo.length + filtros.formato.length;
  const bt = raiz.querySelector("#bt-filtros");
  bt.classList.toggle("tem", n > 0);
  bt.innerHTML = `Filtros${n ? `<span class="conta-f">${n}</span>` : ""}`;

  raiz.querySelector("#filtros-abertos").innerHTML = `
    <div class="grupo-chips"><span class="grupo-rot">Tipo</span>
      ${chipsDe(TIPOS, filtros.tipo, "tipo")}</div>
    <div class="grupo-chips"><span class="grupo-rot">Formato</span>
      ${chipsDe(FORMATOS, filtros.formato, "fmt")}</div>
    ${n ? `<button class="bt-limpar-f" type="button" id="limpar-f">limpar filtros</button>` : ""}`;

  raiz.querySelectorAll("#filtros-abertos .op").forEach((el) => {
    el.onclick = () => {
      const grupo = el.dataset.g === "tipo" ? "tipo" : "formato";
      const lista = filtros[grupo];
      const i = lista.indexOf(el.dataset.v);
      if (i >= 0) lista.splice(i, 1); else lista.push(el.dataset.v);
      salvarFiltros();
      pintar();
    };
  });
  const limpar = raiz.querySelector("#limpar-f");
  if (limpar) limpar.onclick = () => {
    filtros = { tipo: [], formato: [] };
    salvarFiltros();
    pintar();
  };
}

let timerFiltros = null;
function salvarFiltros() {
  // Filtros são persistentes: o board reabre como Pedro deixou.
  clearTimeout(timerFiltros);
  timerFiltros = setTimeout(() => put("/lab/api/config", { filtros }).catch(() => {}), 400);
}

function passaNoFiltro(c) {
  if (filtros.tipo.length && !filtros.tipo.includes(c.tipo)) return false;
  if (filtros.formato.length && !filtros.formato.includes(c.formato)) return false;
  if (busca) {
    const alvo = [
      c.titulo, c.hook, c.fechamento,
      ...(c.desenvolvimentos || []).map((d) => d.texto),
      ...(c.referencias || []).map((l) => `${l.url} ${l.nota}`),
      ...(c.reacoes || []).map((l) => `${l.url} ${l.nota}`),
      ...(c.tags || []),
    ].join(" ").toLowerCase();
    if (!alvo.includes(busca)) return false;
  }
  return true;
}

/* ─────────────────────────── Pintura ─────────────────────────── */

function daColuna(status) {
  const lista = cards.filter((c) => c.status === status && passaNoFiltro(c));
  if (status === "publicado") {
    lista.sort((a, b) => (b.publicado_em || "").localeCompare(a.publicado_em || ""));
  }
  return lista;
}

function pintar() {
  if (!raiz) return;
  pintarFiltros();
  pintarAbas();
  pintarColunas();
  pintarAcoesLote();
}

function pintarAbas() {
  raiz.querySelector("#colunas-abas").innerHTML = COLUNAS.map((col) => `
    <button class="col-aba${colunaAtiva === col.id ? " ativa" : ""}" type="button" data-col="${col.id}">
      ${col.nome}<span class="cont">${daColuna(col.id).length}</span>
    </button>`).join("");
  raiz.querySelectorAll(".col-aba").forEach((b) => {
    b.onclick = () => { colunaAtiva = b.dataset.col; pintarAbas(); rolarPraColuna(); };
  });
}

const noCelular = () => window.matchMedia("(max-width: 860px)").matches;

function rolarPraColuna() {
  const i = COLUNAS.findIndex((c) => c.id === colunaAtiva);
  const trilho = raiz.querySelector("#colunas");
  if (trilho && noCelular()) trilho.scrollTo({ left: i * trilho.clientWidth, behavior: "smooth" });
}

function pintarColunas() {
  const alvo = raiz.querySelector("#colunas");
  const rolagem = alvo.scrollLeft;

  alvo.innerHTML = COLUNAS.map((col) => {
    const lista = daColuna(col.id);
    const corte = col.id === "publicado" && !verTudoPublicado;
    return `
      <section class="coluna" data-col="${col.id}">
        <h2 class="col-titulo">${col.nome} <span class="cont">${lista.length}</span></h2>
        <div class="col-lista">
          ${lista.map(cartao).join("") || `<p class="vazio col-vazia">${vazioDe(col.id)}</p>`}
          ${corte && lista.length ? `<button class="ver-tudo" type="button">ver tudo</button>` : ""}
        </div>
      </section>`;
  }).join("");

  // Repintar zera o scroll horizontal; devolver a posição evita o board pular
  // pra primeira coluna a cada tecla digitada na busca.
  if (noCelular()) alvo.scrollLeft = rolagem;

  alvo.querySelectorAll(".cartao").forEach((el) => {
    el.onclick = (ev) => {
      if (ev.target.closest(".cartao-sel")) return;
      const card = cards.find((c) => String(c.id) === el.dataset.id);
      if (card && !card.provisorio) abrirEditor(card, aoMudarCard);
    };
  });
  alvo.querySelectorAll(".cartao-sel").forEach((el) => {
    el.onclick = (ev) => {
      ev.stopPropagation();
      const id = el.closest(".cartao").dataset.id;
      if (selecao.has(id)) selecao.delete(id); else selecao.add(id);
      pintarColunas();
      pintarAcoesLote();
    };
  });
  const vt = alvo.querySelector(".ver-tudo");
  if (vt) vt.onclick = async () => {
    verTudoPublicado = true;
    try { cards = await get("/lab/api/cards?tudo=1"); } catch (e) { /* fica com o que tem */ }
    pintar();
  };

  let t = null;
  alvo.onscroll = () => {
    if (!noCelular()) return;
    clearTimeout(t);
    t = setTimeout(() => {
      const i = Math.round(alvo.scrollLeft / alvo.clientWidth);
      const nova = COLUNAS[Math.max(0, Math.min(2, i))].id;
      if (nova !== colunaAtiva) { colunaAtiva = nova; pintarAbas(); }
    }, 90);
  };
}

function vazioDe(status) {
  if (status === "ideia") return "Nenhuma ideia solta. Escreva uma ali em cima.";
  if (status === "producao") return "Nada em produção. Abra uma ideia e escreva o hook.";
  return "Nada publicado ainda.";
}

function cartao(c) {
  const sel = selecao.has(String(c.id));
  const marcas = tagsDe(c);
  // Etiquetas em cima: dizem em que caixa a ideia cai antes de o olho ler o
  // título. Embaixo fica só o andamento do roteiro, que é leitura secundária.
  const rodape = c.status === "publicado"
    ? (c.publicado_em
        ? `<div class="cartao-pe"><span class="cartao-quando">publicado em ${data(c.publicado_em, false)}</span></div>`
        : "")
    : barraProgresso(c);

  return `
    <article class="cartao${c.provisorio ? " provisorio" : ""}${sel ? " selecionado" : ""}"
             data-id="${c.id}">
      <button class="cartao-sel" type="button" aria-label="Selecionar"></button>
      ${marcas ? `<div class="cartao-topo">${marcas}</div>` : ""}
      <h3>${esc(c.titulo) || "(sem título)"}</h3>
      ${rodape}
    </article>`;
}

function barraProgresso(c) {
  const temHook = !!(c.hook || "").trim();
  const temFech = !!(c.fechamento || "").trim();
  const n = (c.desenvolvimentos || []).filter((d) => (d.texto || "").trim()).length;
  const links = (c.referencias || []).length + (c.reacoes || []).length;
  if (!temHook && !temFech && !n && !links) return "";
  return `<div class="prog">
      <span class="${temHook ? "ok" : ""}">hook</span>
      <span class="${n ? "ok" : ""}">${n} desenv.</span>
      <span class="${temFech ? "ok" : ""}">fecho</span>
      ${links ? `<span class="anexos">🔗 ${links}</span>` : ""}
    </div>`;
}

/* ─────────────────────────── Seleção em lote ─────────────────────────── */

function selecionados() {
  return COLUNAS.flatMap((col) => daColuna(col.id)).filter((c) => selecao.has(String(c.id)));
}

function pintarAcoesLote() {
  const el = raiz.querySelector("#acoes-lote");
  el.hidden = selecao.size === 0;
  if (!selecao.size) return;
  el.innerHTML = `
    <span class="lote-cont">${selecao.size} selecionado${selecao.size === 1 ? "" : "s"}</span>
    <button class="bt sec" id="lote-copiar" type="button">Copiar</button>
    <button class="bt sec perigo" id="lote-excluir" type="button">Excluir</button>
    <button class="bt sec" id="lote-limpar" type="button" aria-label="Limpar seleção">✕</button>`;

  el.querySelector("#lote-copiar").onclick = async (ev) => {
    // Texto montado ANTES do await: o Safari invalida a permissão de clipboard
    // se houver await antes da chamada dentro do handler.
    const texto = loteParaMarkdown(selecionados(), opcoesExport());
    const bt = ev.currentTarget;
    const ok = await copiar(texto);
    bt.textContent = ok ? "Copiado" : "Não deu";
    setTimeout(() => { bt.textContent = "Copiar"; }, 1600);
  };
  el.querySelector("#lote-excluir").onclick = excluirSelecionados;
  el.querySelector("#lote-limpar").onclick = () => { selecao.clear(); pintar(); };
}

async function excluirSelecionados() {
  const alvos = selecionados();
  if (!alvos.length) return;
  const n = alvos.length;
  const publicados = alvos.filter((c) => c.status === "publicado").length;

  const ok = await confirmar({
    titulo: `Excluir ${n} card${n === 1 ? "" : "s"}?`,
    texto: `<p>Apaga ${n === 1 ? "o card" : "os cards"}, o roteiro e os links de vez.
              Não dá pra desfazer.</p>
            ${publicados ? `<p class="nota">${publicados} ${publicados === 1
              ? "deles está publicado, então sai" : "deles estão publicados, então saem"}
              da conta da régua e o ponteiro vai se mexer.</p>` : ""}`,
    ok: `Excluir ${n}`,
    perigo: true,
  });
  if (!ok) return;

  // Um DELETE por card, reaproveitando a rota já existente. A régua do primeiro
  // e a do último dão as pontas da animação, então o ponteiro faz um movimento
  // só em vez de pular a cada card apagado.
  let antes = null, depois = null, falhas = 0;
  for (const c of alvos) {
    try {
      const r = await del(`/lab/api/cards/${c.id}`);
      if (!antes) antes = r?.regua_antes;
      depois = r?.regua_depois || depois;
      cards = cards.filter((x) => x.id !== c.id);
      selecao.delete(String(c.id));
    } catch (e) {
      falhas++;
    }
  }
  if (depois) regua.animarPara(depois, antes);
  pintar();
  aviso(falhas ? `${n - falhas} de ${n} excluídos.` : `${n} excluído${n === 1 ? "" : "s"}.`);
}

function opcoesExport() {
  return {
    incluirTipoFormato: config.export?.incluir_tipo_formato !== false,
    marcarLacunas: !!config.export?.marcar_lacunas,
  };
}

/* ─────────────────────────── Mudanças vindas do editor ─────────────────────────── */

function aoMudarCard(card, { removido = false, reguaAntes = null, reguaDepois = null } = {}) {
  if (removido) {
    cards = cards.filter((c) => c.id !== card.id);
    selecao.delete(String(card.id));
  } else {
    const i = cards.findIndex((c) => c.id === card.id);
    if (i >= 0) cards[i] = card; else cards = [card, ...cards];
  }
  if (reguaDepois) regua.animarPara(reguaDepois, reguaAntes);
  pintar();
}
