/* A bancada: captura relâmpago no topo e três colunas embaixo.

   No Mac as três colunas ficam lado a lado. No celular viram abas deslizáveis
   por swipe — não um scroll infinito de tudo junto, que é o que torna um board
   inútil numa tela estreita. */

import { get, post, put, esc, data, aviso, SemRede } from "./api.js";
import * as fila from "./fila.js";
import * as regua from "./regua.js";
import { abrirEditor } from "./editor.js";
import { loteParaMarkdown, copiar, baixar } from "./markdown.js";

const COLUNAS = [
  { id: "ideia", nome: "Ideia" },
  { id: "producao", nome: "Produção" },
  { id: "publicado", nome: "Publicado" },
];

const FORMATOS = [
  { id: "lofi", nome: "Lo-fi" },
  { id: "slide", nome: "Slide" },
  { id: "vlog", nome: "Vlog" },
  { id: "documentario", nome: "Documentário" },
];

const TIPOS = [
  { id: "conteudo", nome: "Conteúdo" },
  { id: "anuncio", nome: "Anúncio" },
];

let cards = [];
let config = {};
let filtros = { tipo: [], formato: [] };
let busca = "";
let colunaAtiva = "ideia";
let selecao = new Set();
let verTudoPublicado = false;
let raiz = null;

export async function montar(alvo, dadosIniciais) {
  raiz = alvo;
  cards = dadosIniciais.cards || [];
  config = dadosIniciais.config || {};
  filtros = {
    tipo: config.filtros?.tipo || [],
    formato: config.filtros?.formato || [],
  };
  raiz.innerHTML = esqueleto();
  ligarCaptura();
  ligarFiltros();
  pintar();
  // O que ficou na fila offline sobe assim que o app abre.
  const tentar = fila.vigiar((criados) => { juntar(criados); pintar(); });
  tentar();
}

function esqueleto() {
  return `
  <div class="board">
    <form class="captura" id="captura" autocomplete="off">
      <input type="text" id="captura-campo" placeholder="uma ideia…"
             enterkeyhint="done" autocapitalize="sentences" autocorrect="on" spellcheck="false">
      <button class="bt" type="submit" aria-label="Guardar ideia">+</button>
    </form>

    <div class="barra">
      <input type="search" id="busca" class="busca" placeholder="buscar" enterkeyhint="search">
      <div class="chips" id="chips"></div>
      <div class="acoes-lote" id="acoes-lote" hidden></div>
    </div>

    <div class="colunas-abas" id="colunas-abas"></div>
    <div class="colunas" id="colunas"></div>
  </div>`;
}

/* ─────────────────────────── Captura relâmpago ─────────────────────────── */

function ligarCaptura() {
  const form = raiz.querySelector("#captura");
  const campo = raiz.querySelector("#captura-campo");

  if (config.autofoco !== false) {
    // No iOS o teclado não abre sem gesto do usuário, mas o foco já deixa o
    // cursor no lugar certo e a rolagem na posição certa.
    setTimeout(() => campo.focus(), 60);
  }

  form.onsubmit = async (e) => {
    e.preventDefault();
    const titulo = campo.value.trim();
    if (!titulo) return;
    // Só limpar o value: recriar o nó do input mataria o foco e fecharia o
    // teclado do iPhone, quebrando o "despejar 4 ideias seguidas".
    campo.value = "";
    campo.focus();

    const item = fila.enfileirar(titulo);
    // Card provisório na tela na hora, sem esperar a rede.
    const provisorio = {
      id: "tmp-" + item.client_uuid, titulo, status: "ideia", tipo: null, formato: null,
      hook: "", fechamento: "", desenvolvimentos: [], tags: [], provisorio: true,
      criado_em: item.em,
    };
    cards = [provisorio, ...cards];
    colunaAtiva = "ideia";
    pintar();

    try {
      const card = await post("/lab/api/cards", { titulo, client_uuid: item.client_uuid });
      fila.remover(item.client_uuid);
      cards = cards.map((c) => (c.id === provisorio.id ? card : c));
      pintar();
    } catch (err) {
      if (err instanceof SemRede) aviso("Sem rede. A ideia ficou guardada e sobe depois.");
      else aviso("Não deu pra salvar agora. A ideia ficou na fila.");
    }
  };
}

function juntar(novos) {
  const porUuid = new Map(novos.map((c) => [c.client_uuid, c]));
  cards = cards.map((c) =>
    c.provisorio && porUuid.has(c.id.replace("tmp-", "")) ? porUuid.get(c.id.replace("tmp-", "")) : c);
  const ids = new Set(cards.map((c) => c.id));
  cards = [...novos.filter((c) => !ids.has(c.id)), ...cards];
}

/* ─────────────────────────── Filtros e busca ─────────────────────────── */

function ligarFiltros() {
  const b = raiz.querySelector("#busca");
  b.oninput = () => { busca = b.value.trim().toLowerCase(); pintar(); };
}

function pintarChips() {
  const chip = (grupo, id, nome) => {
    const ligado = filtros[grupo].includes(id);
    const cor = grupo === "formato" ? ` chip-${id}` : "";
    return `<button class="chip-f${cor}${ligado ? " on" : ""}" type="button"
             data-grupo="${grupo}" data-id="${id}">${nome}</button>`;
  };
  raiz.querySelector("#chips").innerHTML =
    TIPOS.map((t) => chip("tipo", t.id, t.nome)).join("") +
    `<span class="chips-sep"></span>` +
    FORMATOS.map((f) => chip("formato", f.id, f.nome)).join("");

  raiz.querySelectorAll(".chip-f").forEach((el) => {
    el.onclick = () => {
      const { grupo, id } = el.dataset;
      const lista = filtros[grupo];
      const i = lista.indexOf(id);
      if (i >= 0) lista.splice(i, 1); else lista.push(id);
      salvarFiltros();
      pintar();
    };
  });
}

let timerFiltros = null;
function salvarFiltros() {
  // Filtros são persistentes: o board reabre como Pedro deixou.
  clearTimeout(timerFiltros);
  timerFiltros = setTimeout(() => {
    put("/lab/api/config", { filtros }).catch(() => {});
  }, 400);
}

function passaNoFiltro(c) {
  if (filtros.tipo.length && !filtros.tipo.includes(c.tipo)) return false;
  if (filtros.formato.length && !filtros.formato.includes(c.formato)) return false;
  if (busca) {
    const alvo = [
      c.titulo, c.hook, c.fechamento,
      ...(c.desenvolvimentos || []).map((d) => d.texto),
      ...(c.tags || []),
    ].join(" ").toLowerCase();
    if (!alvo.includes(busca)) return false;
  }
  return true;
}

/* ─────────────────────────── Pintura ─────────────────────────── */

function daColuna(status) {
  let lista = cards.filter((c) => c.status === status && passaNoFiltro(c));
  if (status === "publicado") {
    lista.sort((a, b) => (b.publicado_em || "").localeCompare(a.publicado_em || ""));
  }
  return lista;
}

function pintar() {
  if (!raiz) return;
  pintarChips();
  pintarAbas();
  pintarColunas();
  pintarAcoesLote();
}

function pintarAbas() {
  raiz.querySelector("#colunas-abas").innerHTML = COLUNAS.map((col) => `
    <button class="col-aba${colunaAtiva === col.id ? " ativa" : ""}" type="button" data-col="${col.id}">
      ${col.nome} <span class="cont">${daColuna(col.id).length}</span>
    </button>`).join("");
  raiz.querySelectorAll(".col-aba").forEach((b) => {
    b.onclick = () => { colunaAtiva = b.dataset.col; pintar(); rolarPraColuna(); };
  });
}

function rolarPraColuna() {
  const i = COLUNAS.findIndex((c) => c.id === colunaAtiva);
  const trilho = raiz.querySelector("#colunas");
  if (trilho && window.matchMedia("(max-width: 860px)").matches) {
    trilho.scrollTo({ left: i * trilho.clientWidth, behavior: "smooth" });
  }
}

function pintarColunas() {
  const alvo = raiz.querySelector("#colunas");
  alvo.innerHTML = COLUNAS.map((col) => {
    const lista = daColuna(col.id);
    const corte = col.id === "publicado" && !verTudoPublicado;
    return `
      <section class="coluna${colunaAtiva === col.id ? " ativa" : ""}" data-col="${col.id}">
        <h2 class="col-titulo">${col.nome} <span class="cont">${lista.length}</span></h2>
        <div class="col-lista">
          ${lista.map(cartao).join("") || `<p class="vazio col-vazia">${vazioDe(col.id)}</p>`}
          ${corte ? `<button class="ver-tudo" type="button">ver tudo</button>` : ""}
        </div>
      </section>`;
  }).join("");

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
      pintar();
    };
  });
  const vt = alvo.querySelector(".ver-tudo");
  if (vt) vt.onclick = async () => {
    verTudoPublicado = true;
    try {
      cards = await get("/lab/api/cards?tudo=1");
    } catch (e) { /* fica com o que já tem */ }
    pintar();
  };

  // Swipe entre colunas no celular: a coluna ativa acompanha a rolagem.
  let t = null;
  alvo.onscroll = () => {
    if (!window.matchMedia("(max-width: 860px)").matches) return;
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
  const marcas = [
    c.formato ? `<span class="tag tag-${c.formato}">${nomeFormato(c.formato)}</span>` : "",
    c.tipo ? `<span class="tag-tipo ${c.tipo}">${c.tipo === "anuncio" ? "anúncio" : "conteúdo"}</span>` : "",
  ].filter(Boolean).join("");

  const progresso = c.status === "publicado" ? "" : barraProgresso(c);
  const quando = c.status === "publicado" && c.publicado_em
    ? `<span class="cartao-quando">${data(c.publicado_em, false)}</span>` : "";

  return `
    <article class="cartao${c.provisorio ? " provisorio" : ""}${sel ? " selecionado" : ""}"
             data-id="${c.id}">
      <button class="cartao-sel" type="button" aria-label="Selecionar"></button>
      <h3>${esc(c.titulo) || "(sem título)"}</h3>
      <div class="cartao-pe">${marcas}${quando}</div>
      ${progresso}
    </article>`;
}

function barraProgresso(c) {
  const temHook = !!(c.hook || "").trim();
  const temFech = !!(c.fechamento || "").trim();
  const n = (c.desenvolvimentos || []).filter((d) => (d.texto || "").trim()).length;
  if (!temHook && !temFech && !n) return "";
  return `<div class="prog">
      <span class="${temHook ? "ok" : ""}">hook</span>
      <span class="${n ? "ok" : ""}">${n || 0} desenv.</span>
      <span class="${temFech ? "ok" : ""}">fecho</span>
    </div>`;
}

const nomeFormato = (f) => (FORMATOS.find((x) => x.id === f) || {}).nome || f;

/* ─────────────────────────── Seleção e lote ─────────────────────────── */

function pintarAcoesLote() {
  const el = raiz.querySelector("#acoes-lote");
  el.hidden = selecao.size === 0;
  if (!selecao.size) return;
  el.innerHTML = `
    <span class="lote-cont">${selecao.size} selecionado${selecao.size === 1 ? "" : "s"}</span>
    <button class="bt sec" id="lote-copiar" type="button">Copiar markdown</button>
    <button class="bt sec" id="lote-baixar" type="button">Baixar .md</button>
    <button class="bt sec" id="lote-limpar" type="button">Limpar</button>`;

  const selecionados = () =>
    COLUNAS.flatMap((col) => daColuna(col.id)).filter((c) => selecao.has(String(c.id)));

  el.querySelector("#lote-copiar").onclick = async (ev) => {
    // Texto montado ANTES de qualquer await: o Safari invalida a permissão de
    // clipboard se houver await antes da chamada dentro do handler.
    const texto = loteParaMarkdown(selecionados(), opcoesExport());
    const bt = ev.currentTarget;
    const ok = await copiar(texto);
    bt.textContent = ok ? "Copiado" : "Não deu";
    setTimeout(() => { bt.textContent = "Copiar markdown"; }, 1600);
  };
  el.querySelector("#lote-baixar").onclick = () => {
    baixar("roteiros.md", loteParaMarkdown(selecionados(), opcoesExport()));
  };
  el.querySelector("#lote-limpar").onclick = () => { selecao.clear(); pintar(); };
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
  } else {
    const i = cards.findIndex((c) => c.id === card.id);
    if (i >= 0) cards[i] = card; else cards = [card, ...cards];
  }
  if (reguaDepois) regua.animarPara(reguaDepois, reguaAntes);
  pintar();
}

export function adicionarCard(card) {
  cards = [card, ...cards];
  pintar();
}

export function atualizarConfig(nova) {
  config = nova;
  filtros = { tipo: nova.filtros?.tipo || [], formato: nova.filtros?.formato || [] };
}
