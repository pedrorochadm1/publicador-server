/* Aba Carrossel: lista de carrosséis e o editor com preview ao vivo.

   O preview é o próprio slide final: o mesmo canvas de 1080×1350 que vira o
   JPEG na hora de publicar (carrossel_render.js), só que exibido menor. Mexeu
   no texto, no tamanho ou no recorte da imagem, o preview redesenha na hora.

   Autosave igual ao editor de cards: sem botão de salvar, debounce de 600ms,
   documento inteiro no PATCH. Se a rede cair, fica sujo e tenta de novo. */

import { get, post, patch, del, postForm, aviso, esc, data, uuid, SemRede } from "./api.js";
import { abrirPainel, fecharPainel, confirmar } from "./painel.js";
import * as R from "./carrossel_render.js";

const DEBOUNCE_MS = 600;
const LADO_MAX_UPLOAD = 2400;   // foto do celular chega com 4000px+; o slot usa 920
const MINI = 0.2;               // escala das miniaturas (216×270)

let tela = null;
let assets = { header: null, imagens: new Map() };
let fontesProntas = null;
let repintar = () => {};
let tiktokMax = 4;

// Editor
let doc = null;
let idx = 0;
let timer = null;
let sujo = false;
let salvando = false;
let recortando = false;
let poll = null;

/* ─────────────────────────── Assets ─────────────────────────── */

function carregarFontes() {
  if (fontesProntas) return fontesProntas;
  const v = window.LAB_V || "";
  const ff = new FontFace(R.FAMILIA, `url(/lab/static/fonts/Inter.ttf?v=${v})`, { weight: "100 900" });
  fontesProntas = ff.load().then((f) => {
    document.fonts.add(f);
    return Promise.all([document.fonts.load(R.fonte(false)), document.fonts.load(R.fonte(true))]);
  });
  return fontesProntas;
}

function carregarHeader() {
  if (assets.header) return Promise.resolve();
  return new Promise((ok) => {
    const im = new Image();
    im.onload = () => { assets.header = im; ok(); };
    im.onerror = () => ok();
    im.src = `/lab/static/img/header-slide.png?v=${window.LAB_V || ""}`;
  });
}

/* Imagem de origem do slide. Mesma origem (rota com sessão), então o canvas não
   fica "contaminado" e o toBlob do export funciona. Quando termina de carregar,
   repinta o que estiver na tela. */
function imagem(arquivo) {
  let im = assets.imagens.get(arquivo);
  if (!im) {
    im = new Image();
    im.onload = () => repintar();
    im.src = `/lab/api/carrossel/imagens/${arquivo}`;
    assets.imagens.set(arquivo, im);
  }
  return im.complete && im.naturalWidth ? im : null;
}

function esperarImagens(slides) {
  return Promise.all(slides.filter((s) => s.imagem).map((s) => {
    imagem(s.imagem.arquivo);
    const im = assets.imagens.get(s.imagem.arquivo);
    if (im.complete) return null;
    return new Promise((ok) => { im.addEventListener("load", ok, { once: true }); im.addEventListener("error", ok, { once: true }); });
  }));
}

const assetsDesenho = { get header() { return assets.header; }, imagem };

function pintar(canvas, slide, escala = 1) {
  const ctx = canvas.getContext("2d");
  ctx.setTransform(escala, 0, 0, escala, 0, 0);
  return R.desenhar(ctx, slide, assetsDesenho);
}

function miniatura(slide) {
  const c = document.createElement("canvas");
  c.width = R.W * MINI;
  c.height = R.H * MINI;
  pintar(c, slide, MINI);
  return c;
}

/* ─────────────────────────── Montagem ─────────────────────────── */

export async function montar(el) {
  tela = el;
  tela.innerHTML = `<p class="vazio car-carregando">carregando…</p>`;
  await Promise.all([carregarFontes().catch(() => {}), carregarHeader()]);
  await abrirLista();
}

export function desmontar() {
  clearInterval(poll); poll = null;
  if (sujo) salvarAgora();
  clearTimeout(timer);
  repintar = () => {};
  doc = null;
  tela = null;
}

/* ─────────────────────────── Lista ─────────────────────────── */

const ROTULO_STATUS = {
  rascunho: "Rascunho", agendado: "Agendado", publicando: "Publicando",
  publicado: "Publicado", erro: "Erro",
};

function chipStatus(d) {
  const quando = d.publicacao && d.status === "agendado" ? " " + data(d.publicacao.publicar_em) : "";
  const cls = d.status === "publicado" ? "on" : d.status === "erro" ? "erro" : "";
  return `<span class="chip ${cls}">${ROTULO_STATUS[d.status] || d.status}${quando}</span>`;
}

async function abrirLista() {
  clearInterval(poll); poll = null;
  doc = null;
  let lista;
  try {
    const r = await get("/lab/api/carrosseis");
    lista = r.carrosseis;
    tiktokMax = r.tiktok_max_fotos;
  } catch (e) {
    if (tela) tela.innerHTML = `<p class="vazio erro-aba">Não deu pra carregar os carrosséis agora.</p>`;
    return;
  }
  if (!tela) return;
  tela.innerHTML = `
    <div class="car-lista">
      <header class="car-lista-topo">
        <h1>Carrossel</h1>
        <span class="car-lista-bts">
          <button class="bt sec car-gerar" type="button">Gerar com IA</button>
          <button class="bt car-novo" type="button">Novo carrossel</button>
        </span>
      </header>
      <div class="car-grade">
        ${lista.length ? "" : `<p class="vazio">Nenhum carrossel ainda. Comece um novo.</p>`}
        ${lista.map((d) => `
          <button class="car-item" type="button" data-id="${d.id}">
            <span class="car-item-capa"></span>
            <span class="car-item-info">
              <strong>${esc(d.titulo || "Sem título")}</strong>
              <span class="car-item-meta">${d.slides.length} slide${d.slides.length === 1 ? "" : "s"} · ${data(d.atualizado_em)}</span>
              ${chipStatus(d)}
            </span>
          </button>`).join("")}
      </div>
    </div>`;

  const capas = () => lista.forEach((d) => {
    const alvo = tela?.querySelector(`.car-item[data-id="${d.id}"] .car-item-capa`);
    if (alvo) alvo.replaceChildren(miniatura(d.slides[0] || {}));
  });
  repintar = capas;
  capas();

  tela.querySelector(".car-novo").onclick = async () => {
    try {
      const d = await post("/lab/api/carrosseis", { client_uuid: uuid(), origem: "manual" });
      abrirEditor(d);
    } catch (e) { aviso("Não deu pra criar agora."); }
  };
  tela.querySelector(".car-gerar").onclick = abrirGerar;
  tela.querySelectorAll(".car-item").forEach((b) => {
    b.onclick = () => abrirEditor(lista.find((d) => d.id === Number(b.dataset.id)));
  });
}

/* ─────────────────────────── Gerar com IA ───────────────────────────

   Duas fontes: um reel já publicado (o servidor baixa, transcreve e escreve) ou
   uma ideia digitada aqui. O resultado é um rascunho comum — abre no mesmo
   editor, e nada vai pro ar sem o Pedro clicar em Publicar.

   A geração leva uns 30-50s e é uma requisição só, então o botão vira estado de
   espera e o painel não pode ser fechado no meio: fechar perderia o resultado
   de uma chamada que já custou. */

let gerando = false;

function abrirGerar() {
  const folha = abrirPainel(`
    <header class="painel-topo"><h2>Gerar carrossel</h2>
      <button class="fechar" type="button" aria-label="Fechar">✕</button></header>
    <div class="painel-corpo car-gen">
      <div class="car-gen-abas">
        <button type="button" data-fonte="ideia" class="ativa">De uma ideia</button>
        <button type="button" data-fonte="reel">De um reel publicado</button>
      </div>

      <section data-painel="ideia">
        <textarea class="car-gen-ideia" rows="6"
          placeholder="Escreve a ideia do jeito que ela veio na sua cabeça. Pode ser bagunçado."></textarea>
        <p class="nota">A IA corta em slides no seu tom. Você edita tudo depois.</p>
      </section>

      <section data-painel="reel" hidden>
        <div class="car-gen-reels"><p class="vazio">carregando reels…</p></div>
      </section>

      <button class="bt car-gen-ok" type="button" disabled>Gerar carrossel</button>
      <p class="nota car-gen-espera" hidden>
        Baixando o reel, ouvindo e escrevendo. Leva até um minuto. Não feche.</p>
    </div>`, { classe: "painel-gerar", aoFechar: () => { gerando = false; } });

  const $$ = (s) => folha.querySelector(s);
  let fonte = "ideia";
  let reelEscolhido = null;
  let reelsCarregados = false;

  const atualizarBotao = () => {
    const pronto = fonte === "ideia"
      ? $$(".car-gen-ideia").value.trim().length >= 15
      : !!reelEscolhido;
    $$(".car-gen-ok").disabled = !pronto || gerando;
  };

  $$(".fechar").onclick = () => { if (!gerando) fecharPainel(); };
  $$(".car-gen-ideia").addEventListener("input", atualizarBotao);

  folha.querySelectorAll("[data-fonte]").forEach((b) => {
    b.onclick = () => {
      if (gerando) return;
      fonte = b.dataset.fonte;
      folha.querySelectorAll("[data-fonte]").forEach((o) => o.classList.toggle("ativa", o === b));
      folha.querySelectorAll("[data-painel]").forEach((s) => { s.hidden = s.dataset.painel !== fonte; });
      if (fonte === "reel" && !reelsCarregados) carregarReels();
      atualizarBotao();
    };
  });

  async function carregarReels() {
    reelsCarregados = true;
    const alvo = $$(".car-gen-reels");
    let reels;
    try {
      reels = (await get("/lab/api/carrossel/reels")).reels;
    } catch (e) {
      reelsCarregados = false;
      alvo.innerHTML = `<p class="vazio">Não deu pra listar seus reels agora.</p>`;
      return;
    }
    if (!reels.length) {
      alvo.innerHTML = `<p class="vazio">Nenhum reel publicado ainda.</p>`;
      return;
    }
    alvo.innerHTML = reels.map((r) => `
      <button class="car-reel" type="button" data-media="${esc(r.media_id)}">
        ${r.capa ? `<img src="${esc(r.capa)}" alt="" loading="lazy">` : `<span class="car-reel-sem"></span>`}
        <span class="car-reel-info">
          <strong>${esc(r.legenda.split("\n")[0].slice(0, 70) || "Sem legenda")}</strong>
          <span class="car-item-meta">${data(r.quando)}</span>
        </span>
      </button>`).join("");
    alvo.querySelectorAll(".car-reel").forEach((b) => {
      b.onclick = () => {
        if (gerando) return;
        reelEscolhido = b.dataset.media;
        alvo.querySelectorAll(".car-reel").forEach((o) => o.classList.toggle("on", o === b));
        atualizarBotao();
      };
    });
  }

  $$(".car-gen-ok").onclick = async () => {
    if (gerando) return;
    gerando = true;
    const bt = $$(".car-gen-ok");
    const espera = $$(".car-gen-espera");
    bt.disabled = true;
    bt.textContent = "Montando…";
    if (fonte === "reel") espera.hidden = false;
    const corpo = fonte === "reel"
      ? { origem: "reel", media_id: reelEscolhido }
      : { origem: "ideia", ideia: $$(".car-gen-ideia").value };
    try {
      const d = await post("/lab/api/carrosseis/gerar", corpo);
      gerando = false;
      fecharPainel();
      // A chamada já custou: mesmo que o painel tenha sido fechado no meio da
      // espera (Esc, toque fora), o rascunho abre em vez de se perder. Só não
      // abre se ele tiver trocado de aba, porque aí não há mais onde desenhar.
      if (tela) abrirEditor(d);
      aviso("Rascunho pronto. Ajuste o que quiser antes de publicar.");
    } catch (e) {
      gerando = false;
      if (bt.isConnected) {
        bt.textContent = "Gerar carrossel";
        bt.disabled = false;
        espera.hidden = true;
      }
      aviso(e instanceof SemRede ? "Sem rede." : (e.corpo || "Não deu pra gerar agora."));
    }
  };
}

/* ─────────────────────────── Editor ─────────────────────────── */

const $ = (s) => tela.querySelector(s);

function slideAtual() { return doc.slides[idx]; }

function abrirEditor(d) {
  doc = d;
  idx = 0;
  recortando = false;
  sujo = false;
  tela.innerHTML = `
    <div class="car-ed">
      <header class="car-topo">
        <button class="car-voltar" type="button" aria-label="Voltar">‹</button>
        <input class="car-titulo" type="text" placeholder="Título (só pra você)" maxlength="120">
        <span class="car-estado"></span>
        <button class="bt car-publicar" type="button">Publicar</button>
      </header>
      <div class="car-corpo">
        <section class="car-palco">
          <div class="car-moldura"><canvas class="car-preview" width="${R.W}" height="${R.H}"></canvas></div>
          <p class="car-cabe"></p>
          <div class="car-tira"></div>
        </section>
        <section class="car-campos">
          <div class="car-slide-topo">
            <strong class="car-slide-n"></strong>
            <span class="car-slide-acoes">
              <button type="button" data-acao="esq" title="Mover pra esquerda">←</button>
              <button type="button" data-acao="dir" title="Mover pra direita">→</button>
              <button type="button" data-acao="dup" title="Duplicar slide">Duplicar</button>
              <button type="button" data-acao="rem" title="Remover slide">Remover</button>
            </span>
          </div>

          <div class="car-bloco">
            <div class="car-rotulo"><span>Texto</span>
              <button type="button" class="car-negrito" data-alvo="texto" title="Negrito">B</button></div>
            <textarea class="car-txt" data-campo="texto" rows="5"
              placeholder="Linha em branco separa parágrafo. **assim** fica em negrito."></textarea>
          </div>

          <div class="car-bloco car-img"></div>

          <div class="car-bloco">
            <div class="car-rotulo"><span>Texto abaixo da imagem</span>
              <button type="button" class="car-negrito" data-alvo="texto_abaixo" title="Negrito">B</button></div>
            <textarea class="car-txt" data-campo="texto_abaixo" rows="2"
              placeholder="Opcional. Ex.: Entenda 👉"></textarea>
          </div>

          <label class="car-chave"><input type="checkbox" class="car-header"> Cabeçalho com foto e @</label>

          <div class="car-bloco car-legenda-bloco">
            <div class="car-rotulo"><span>Legenda (Instagram e TikTok)</span></div>
            <textarea class="car-legenda" rows="2" placeholder="Uma linha, uma pergunta."></textarea>
          </div>
          <ul class="car-voz"></ul>

          <div class="car-rodape">
            <button class="bt sec car-baixar" type="button">Baixar slides</button>
            <button class="bt sec car-duplicar" type="button">Duplicar carrossel</button>
            <button class="bt perigo car-arquivar" type="button">Arquivar</button>
          </div>
        </section>
      </div>
    </div>`;

  $(".car-titulo").value = doc.titulo;
  $(".car-legenda").value = doc.legenda;
  ligarEditor();
  repintar = () => { pintarPreview(); pintarTira(); };
  preencherCampos();
  pintarTudo();
  acompanharPublicacao();
}

function pintarTudo() {
  pintarPreview();
  pintarTira();
  pintarEstado();
  pintarVoz();
}

function pintarPreview() {
  if (!doc) return;
  const c = $(".car-preview");
  const lay = pintar(c, slideAtual());
  const cabe = $(".car-cabe");
  cabe.textContent = lay.cabe ? "" : `Passou ${lay.excesso}px da área do slide. Corte texto ou diminua a imagem.`;
  cabe.classList.toggle("ruim", !lay.cabe);
  // Contorno do modo recorte: desenhado por cima do preview, nunca no export.
  const bl = lay.blocos.find((b) => b.tipo === "imagem");
  c.classList.toggle("recortando", recortando && !!bl);
  if (recortando && bl) {
    const ctx = c.getContext("2d");
    ctx.save();
    ctx.strokeStyle = "#648dcb";
    ctx.lineWidth = 6;
    ctx.setLineDash([18, 12]);
    ctx.strokeRect(R.MARGEM + 3, bl.y + 3, R.LARGURA - 6, bl.h - 6);
    ctx.restore();
  }
  return lay;
}

function pintarTira() {
  const tira = $(".car-tira");
  tira.replaceChildren();
  doc.slides.forEach((s, i) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "car-mini" + (i === idx ? " ativa" : "");
    b.title = `Slide ${i + 1}`;
    b.append(miniatura(s));
    const n = document.createElement("span");
    n.textContent = i + 1;
    b.append(n);
    b.onclick = () => irSlide(i);
    tira.append(b);
  });
  if (doc.slides.length < 10) {
    const mais = document.createElement("button");
    mais.type = "button";
    mais.className = "car-mini car-mais";
    mais.textContent = "+";
    mais.title = "Novo slide";
    mais.onclick = () => {
      doc.slides.splice(idx + 1, 0, { texto: "", texto_abaixo: "", header: true, imagem: null, intencao: "" });
      mudou(0);
      irSlide(idx + 1);
    };
    tira.append(mais);
  }
}

function irSlide(i) {
  idx = Math.max(0, Math.min(doc.slides.length - 1, i));
  recortando = false;
  preencherCampos();
  pintarPreview();
  pintarTira();
}

function preencherCampos() {
  const s = slideAtual();
  $(".car-slide-n").textContent = `Slide ${idx + 1} de ${doc.slides.length}`;
  tela.querySelectorAll(".car-txt").forEach((t) => { t.value = s[t.dataset.campo] || ""; });
  $(".car-header").checked = s.header !== false;
  pintarImagem();
}

function pintarImagem() {
  const s = slideAtual();
  const box = $(".car-img");
  if (!s.imagem) {
    box.innerHTML = `
      <div class="car-rotulo"><span>Imagem</span></div>
      <label class="bt sec car-subir">Adicionar imagem<input type="file" accept="image/*" hidden></label>`;
  } else {
    const im = s.imagem;
    box.innerHTML = `
      <div class="car-rotulo"><span>Imagem</span></div>
      <div class="car-tamanhos">
        ${Object.keys(R.TAMANHOS).map((k) => `
          <button type="button" class="car-tam ${im.tamanho === k ? "on" : ""}" data-tam="${k}">${R.NOMES_TAMANHO[k]}</button>`).join("")}
      </div>
      <div class="car-recorte">
        <button type="button" class="bt sec car-ajustar">${recortando ? "Pronto" : "Ajustar recorte"}</button>
        <input type="range" class="car-zoom" min="1" max="4" step="0.01" value="${im.zoom}" aria-label="Zoom">
      </div>
      <p class="car-dica" ${recortando ? "" : "hidden"}>Arraste a imagem no preview pra enquadrar. Pinça ou o controle dão zoom.</p>
      <div class="car-img-acoes">
        <label class="bt sec car-subir">Trocar<input type="file" accept="image/*" hidden></label>
        <button type="button" class="bt perigo car-tirar">Remover imagem</button>
      </div>`;
    box.querySelectorAll(".car-tam").forEach((b) => {
      b.onclick = () => { im.tamanho = b.dataset.tam; mudou(0); pintarImagem(); pintarPreview(); pintarTira(); };
    });
    box.querySelector(".car-ajustar").onclick = () => { recortando = !recortando; pintarImagem(); pintarPreview(); };
    box.querySelector(".car-zoom").oninput = (e) => { definirZoom(Number(e.target.value)); };
    box.querySelector(".car-tirar").onclick = () => {
      s.imagem = null; recortando = false; mudou(0); pintarImagem(); pintarPreview(); pintarTira();
    };
  }
  const input = box.querySelector("input[type=file]");
  input.onchange = () => { if (input.files[0]) subirImagem(input.files[0]); };
}

/* ─────────────────────────── Imagem: upload e recorte ─────────────────────────── */

async function reduzir(arquivo) {
  // Reduz no próprio aparelho: sobe menos e o canvas desenha mais leve.
  try {
    const bmp = await createImageBitmap(arquivo);
    const k = Math.min(1, LADO_MAX_UPLOAD / Math.max(bmp.width, bmp.height));
    const c = document.createElement("canvas");
    c.width = Math.round(bmp.width * k);
    c.height = Math.round(bmp.height * k);
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    const blob = await new Promise((ok) => c.toBlob(ok, "image/jpeg", 0.9));
    return blob ? new File([blob], "imagem.jpg", { type: "image/jpeg" }) : arquivo;
  } catch (e) {
    return arquivo;   // formato que o navegador não decodifica: manda como veio
  }
}

async function subirImagem(arquivo) {
  const s = slideAtual();
  aviso("Enviando imagem…");
  try {
    const fd = new FormData();
    fd.append("arquivo", await reduzir(arquivo));
    const r = await postForm("/lab/api/carrossel/imagens", fd);
    s.imagem = { arquivo: r.arquivo, tamanho: s.imagem?.tamanho || "M", zoom: 1, cx: 0.5, cy: 0.5 };
    mudou(0);
    pintarImagem();
    pintarPreview();
    pintarTira();
  } catch (e) {
    aviso(e.status === 413 ? "Imagem grande demais."
      : e.status === 400 ? "Esse arquivo não é uma imagem que dê pra usar."
      : "Não deu pra enviar a imagem.");
  }
}

function geometriaImagem() {
  const s = slideAtual();
  if (!s.imagem) return null;
  const el = imagem(s.imagem.arquivo);
  if (!el) return null;
  const h = R.TAMANHOS[s.imagem.tamanho] || R.TAMANHOS.M;
  return { s, el, h, r: R.recorte(el.naturalWidth, el.naturalHeight, R.LARGURA, h, s.imagem.zoom, s.imagem.cx, s.imagem.cy) };
}

function definirZoom(z) {
  const g = geometriaImagem();
  if (!g) return;
  g.s.imagem.zoom = Math.max(1, Math.min(4, z));
  const r = R.recorte(g.el.naturalWidth, g.el.naturalHeight, R.LARGURA, g.h, g.s.imagem.zoom, g.s.imagem.cx, g.s.imagem.cy);
  g.s.imagem.cx = r.cx; g.s.imagem.cy = r.cy;
  const zoom = $(".car-zoom");
  if (zoom && Number(zoom.value) !== g.s.imagem.zoom) zoom.value = g.s.imagem.zoom;
  pintarPreview();
  mudou();
}

function ligarRecorte(canvas) {
  const toques = new Map();
  let base = null;   // estado no início do gesto

  const escala = () => R.W / canvas.clientWidth;
  const dist = () => {
    const [a, b] = [...toques.values()];
    return Math.hypot(a.x - b.x, a.y - b.y);
  };

  canvas.addEventListener("pointerdown", (e) => {
    if (!recortando || !geometriaImagem()) return;
    canvas.setPointerCapture(e.pointerId);
    toques.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const im = slideAtual().imagem;
    base = { cx: im.cx, cy: im.cy, zoom: im.zoom, x: e.clientX, y: e.clientY, d: toques.size === 2 ? dist() : 0 };
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!toques.has(e.pointerId) || !base) return;
    toques.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (toques.size === 2) {
      if (!base.d) base.d = dist();
      definirZoom(base.zoom * dist() / base.d);
      return;
    }
    const g = geometriaImagem();
    if (!g) return;
    const k = escala();
    const cx = base.cx - (e.clientX - base.x) * k / g.r.dw;
    const cy = base.cy - (e.clientY - base.y) * k / g.r.dh;
    const c = R.limitarCentro(g.r.dw, g.r.dh, R.LARGURA, g.h, cx, cy);
    g.s.imagem.cx = c.cx; g.s.imagem.cy = c.cy;
    pintarPreview();
    mudou();
  });
  const soltar = (e) => {
    toques.delete(e.pointerId);
    if (!toques.size) { base = null; pintarTira(); return; }
    // Saiu um dedo da pinça: recomeça o arrasto a partir de onde está.
    const [p] = toques.values();
    const im = slideAtual().imagem;
    base = { cx: im.cx, cy: im.cy, zoom: im.zoom, x: p.x, y: p.y, d: 0 };
  };
  canvas.addEventListener("pointerup", soltar);
  canvas.addEventListener("pointercancel", soltar);
  canvas.addEventListener("wheel", (e) => {
    if (!recortando || !geometriaImagem()) return;
    e.preventDefault();
    definirZoom(slideAtual().imagem.zoom * Math.exp(-e.deltaY * 0.002));
  }, { passive: false });
}

/* ─────────────────────────── Ligações ─────────────────────────── */

function negrito(campo) {
  const t = tela.querySelector(`.car-txt[data-campo="${campo}"]`);
  const { selectionStart: a, selectionEnd: b, value: v } = t;
  if (a === b) return;
  t.value = v.slice(0, a) + "**" + v.slice(a, b) + "**" + v.slice(b);
  t.setSelectionRange(a + 2, b + 2);
  t.focus();
  t.dispatchEvent(new Event("input"));
}

function ligarEditor() {
  $(".car-voltar").onclick = async () => { if (sujo) await salvarAgora(); abrirLista(); };
  $(".car-titulo").oninput = (e) => { doc.titulo = e.target.value; mudou(); };
  $(".car-legenda").oninput = (e) => { doc.legenda = e.target.value; mudou(); pintarVoz(); };
  tela.querySelectorAll(".car-txt").forEach((t) => {
    t.oninput = () => {
      slideAtual()[t.dataset.campo] = t.value;
      pintarPreview();
      pintarVoz();
      mudou();
      clearTimeout(t._mini);
      t._mini = setTimeout(pintarTira, 400);
    };
  });
  tela.querySelectorAll(".car-negrito").forEach((b) => {
    // mousedown pra não tirar o foco (e a seleção) do campo antes do clique.
    b.onmousedown = (e) => e.preventDefault();
    b.onclick = () => negrito(b.dataset.alvo);
  });
  $(".car-header").onchange = (e) => {
    slideAtual().header = e.target.checked; mudou(0); pintarPreview(); pintarTira();
  };
  tela.querySelectorAll("[data-acao]").forEach((b) => { b.onclick = () => acaoSlide(b.dataset.acao); });
  $(".car-publicar").onclick = abrirPublicar;
  $(".car-baixar").onclick = baixar;
  $(".car-duplicar").onclick = async () => {
    if (sujo) await salvarAgora();
    try { abrirEditor(await post(`/lab/api/carrosseis/${doc.id}/duplicar`)); aviso("Cópia criada."); }
    catch (e) { aviso("Não deu pra duplicar agora."); }
  };
  $(".car-arquivar").onclick = async () => {
    const ok = await confirmar({
      titulo: "Arquivar carrossel?", texto: "Ele sai da lista. O que já foi publicado continua no Instagram.",
      ok: "Arquivar", perigo: true,
    });
    if (!ok) return;
    try { await del(`/lab/api/carrosseis/${doc.id}`); sujo = false; abrirLista(); }
    catch (e) { aviso("Não deu pra arquivar agora."); }
  };
  ligarRecorte($(".car-preview"));
}

function acaoSlide(acao) {
  const sl = doc.slides;
  if (acao === "esq" && idx > 0) { [sl[idx - 1], sl[idx]] = [sl[idx], sl[idx - 1]]; idx--; }
  else if (acao === "dir" && idx < sl.length - 1) { [sl[idx + 1], sl[idx]] = [sl[idx], sl[idx + 1]]; idx++; }
  else if (acao === "dup" && sl.length < 10) { sl.splice(idx + 1, 0, structuredClone(sl[idx])); idx++; }
  else if (acao === "rem") {
    if (sl.length === 1) { aviso("O carrossel precisa de pelo menos um slide."); return; }
    sl.splice(idx, 1);
    idx = Math.min(idx, sl.length - 1);
  } else return;
  mudou(0);
  irSlide(idx);
}

/* ─────────────────────────── Voz ─────────────────────────── */

/* Avisos das regras de voz que dá pra checar sem IA. Não bloqueiam: o Pedro
   decide. Mas aparecem antes de publicar, que é quando ainda dá pra mexer. */
export function avisosVoz(d) {
  const out = [];
  d.slides.forEach((s, i) => {
    const t = `${s.texto}\n${s.texto_abaixo}`;
    if (t.includes("—")) out.push(`Slide ${i + 1} tem travessão.`);
    if (/#\w/.test(t)) out.push(`Slide ${i + 1} tem hashtag.`);
    if (/\barrast[ae]\b/i.test(t)) out.push(`Slide ${i + 1} tem "Arrasta". Antecipe o próximo slide em vez disso.`);
  });
  const leg = d.legenda.trim();
  if (!leg) out.push("Falta a legenda.");
  else {
    if (/#\w/.test(leg)) out.push("Legenda com hashtag.");
    if (leg.includes("—")) out.push("Legenda com travessão.");
    if (leg.includes("\n")) out.push("Legenda com mais de uma linha.");
    const q = (leg.match(/\?/g) || []).length;
    if (q === 0) out.push("Legenda sem pergunta.");
    if (q > 1) out.push("Legenda com mais de uma pergunta.");
  }
  return out;
}

function pintarVoz() {
  const ul = $(".car-voz");
  if (!ul) return;
  ul.innerHTML = avisosVoz(doc).map((a) => `<li>${esc(a)}</li>`).join("");
}

/* ─────────────────────────── Autosave ─────────────────────────── */

function mudou(ms = DEBOUNCE_MS) {
  sujo = true;
  pintarEstado();
  clearTimeout(timer);
  timer = setTimeout(salvarAgora, ms);
}

async function salvarAgora() {
  clearTimeout(timer);
  if (!doc || salvando || !sujo) return;
  salvando = true;
  sujo = false;
  const alvo = doc;
  try {
    const r = await patch(`/lab/api/carrosseis/${alvo.id}`, {
      titulo: alvo.titulo, legenda: alvo.legenda, slides: alvo.slides,
    });
    // Só o estado vem do servidor; os campos ficam como estão pra não pular o cursor.
    if (doc === alvo) { doc.status = r.status; doc.publicacao = r.publicacao; }
  } catch (e) {
    sujo = true;
    if (doc === alvo) aviso(e instanceof SemRede ? "Sem rede. Salvo quando voltar." : "Não salvou agora.");
    timer = setTimeout(salvarAgora, 4000);
  } finally {
    salvando = false;
    if (doc === alvo) pintarEstado();
    if (sujo && doc === alvo) { clearTimeout(timer); timer = setTimeout(salvarAgora, DEBOUNCE_MS); }
  }
}

function pintarEstado() {
  const el = tela?.querySelector(".car-estado");
  if (!el || !doc) return;
  const salvo = salvando || sujo ? `<span class="car-salvo">salvando…</span>` : `<span class="car-salvo">salvo</span>`;
  el.innerHTML = chipStatus(doc) + salvo;
  const pub = $(".car-publicar");
  const publicado = doc.status === "publicado" || doc.status === "publicando";
  pub.disabled = publicado;
  pub.textContent = doc.status === "agendado" ? "Reagendar" : "Publicar";
}

/* ─────────────────────────── Export ─────────────────────────── */

async function gerarJpegs() {
  await carregarFontes().catch(() => {});
  await esperarImagens(doc.slides);
  const blobs = [];
  for (const s of doc.slides) {
    const c = document.createElement("canvas");
    c.width = R.W; c.height = R.H;
    pintar(c, s);
    const b = await new Promise((ok) => c.toBlob(ok, "image/jpeg", 0.92));
    if (!b) throw new Error("export");
    blobs.push(b);
  }
  return blobs;
}

function slidesQueNaoCabem() {
  const medir = R.medidorDe(document.createElement("canvas").getContext("2d"));
  return doc.slides.map((s, i) => (R.layout(s, medir).cabe ? 0 : i + 1)).filter(Boolean);
}

async function baixar() {
  let blobs;
  try { blobs = await gerarJpegs(); } catch (e) { aviso("Não deu pra gerar os slides."); return; }
  const base = (doc.titulo || "carrossel").normalize("NFD").replace(/[^\w]+/g, "-").replace(/^-|-$/g, "").toLowerCase() || "carrossel";
  const arquivos = blobs.map((b, i) => new File([b], `${base}-${String(i + 1).padStart(2, "0")}.jpg`, { type: "image/jpeg" }));
  // No celular, a folha de compartilhar salva tudo na galeria de uma vez.
  if (navigator.canShare && navigator.canShare({ files: arquivos })) {
    try { await navigator.share({ files: arquivos }); return; } catch (e) { if (e.name === "AbortError") return; }
  }
  for (const f of arquivos) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(f);
    a.download = f.name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }
}

/* ─────────────────────────── Publicar ─────────────────────────── */

function amanha7h() {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  d.setHours(7, 0, 0, 0);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T07:00`;
}

async function abrirPublicar() {
  if (sujo) await salvarAgora();
  const fora = slidesQueNaoCabem();
  if (fora.length) {
    aviso(`Slide ${fora.join(", ")} passou da área. Ajuste antes de publicar.`);
    irSlide(fora[0] - 1);
    return;
  }
  if (!doc.legenda.trim()) { aviso("Escreva a legenda antes de publicar."); $(".car-legenda").focus(); return; }

  const n = doc.slides.length;
  const tiktokOk = n <= tiktokMax;
  const voz = avisosVoz(doc);
  const folha = abrirPainel(`
    <header class="painel-topo"><h2>Publicar carrossel</h2>
      <button class="fechar" type="button" aria-label="Fechar">✕</button></header>
    <div class="painel-corpo car-pub">
      <div class="car-pub-tira"></div>
      <div class="car-pub-destino">
        <strong>Instagram</strong>
        <p>${esc(doc.legenda)}</p>
      </div>
      <div class="car-pub-destino">
        <strong>TikTok</strong>
        <p>${tiktokOk ? "Mesma legenda, modo foto." : `Fica de fora: hoje o TikTok via Buffer aceita até ${tiktokMax} fotos e este tem ${n}. Sair cortado seria pior.`}</p>
      </div>
      ${voz.length ? `<ul class="car-voz">${voz.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>` : ""}
      <div class="car-pub-quando">
        <label><input type="radio" name="car-quando" value="agora" checked> Agora</label>
        <label><input type="radio" name="car-quando" value="agendar"> Agendar</label>
        <input type="datetime-local" class="car-quando-data" value="${amanha7h()}" hidden>
      </div>
      <button class="bt car-pub-ok" type="button">Publicar agora</button>
    </div>`, { classe: "car-painel" });

  doc.slides.forEach((s) => folha.querySelector(".car-pub-tira").append(miniatura(s)));
  folha.querySelector(".fechar").onclick = fecharPainel;
  const dataEl = folha.querySelector(".car-quando-data");
  const ok = folha.querySelector(".car-pub-ok");
  folha.querySelectorAll("input[name=car-quando]").forEach((r) => {
    r.onchange = () => {
      const agendar = r.value === "agendar" && r.checked;
      dataEl.hidden = !agendar;
      ok.textContent = agendar ? "Agendar" : "Publicar agora";
    };
  });
  ok.onclick = async () => {
    const agendar = !dataEl.hidden;
    ok.disabled = true;
    ok.textContent = "Gerando slides…";
    try {
      const blobs = await gerarJpegs();
      const fd = new FormData();
      blobs.forEach((b, i) => fd.append("slides", b, `slide-${i + 1}.jpg`));
      fd.append("agendar_para", agendar ? dataEl.value : "");
      fd.append("tiktok", tiktokOk ? "1" : "0");
      ok.textContent = "Enviando…";
      const r = await postForm(`/lab/api/carrosseis/${doc.id}/publicar`, fd);
      doc.status = r.status; doc.publicacao = r.publicacao;
      fecharPainel();
      pintarEstado();
      aviso(agendar ? "Agendado." : "Na fila. Sai em até 30 segundos.");
      acompanharPublicacao();
    } catch (e) {
      ok.disabled = false;
      ok.textContent = agendar ? "Agendar" : "Publicar agora";
      aviso(e.corpo && typeof e.corpo === "string" ? e.corpo : "Não deu pra publicar agora.");
    }
  };
}

/* Enquanto o post está pra sair nos próximos minutos, acompanha até virar
   publicado ou erro. Agendado pra depois não precisa de polling. */
function acompanharPublicacao() {
  clearInterval(poll); poll = null;
  const pub = doc?.publicacao;
  if (!pub || !["agendado", "publicando"].includes(doc.status)) return;
  const vence = new Date(pub.publicar_em).getTime();
  if (vence - Date.now() > 2 * 60 * 1000) return;
  const alvo = doc;
  const fim = Date.now() + 6 * 60 * 1000;
  poll = setInterval(async () => {
    if (doc !== alvo || Date.now() > fim) { clearInterval(poll); poll = null; return; }
    try {
      const r = await get(`/lab/api/carrosseis/${alvo.id}`);
      doc.status = r.status; doc.publicacao = r.publicacao;
      pintarEstado();
      if (r.status === "publicado") { clearInterval(poll); poll = null; aviso("Publicado no Instagram."); }
      if (r.status === "erro") { clearInterval(poll); poll = null; aviso("A publicação deu erro."); }
    } catch (e) { /* tenta no próximo ciclo */ }
  }, 6000);
}
