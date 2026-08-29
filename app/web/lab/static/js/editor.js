/* Editor do card: tela cheia no celular, painel lateral no Mac.

   Uma rolagem só, na ordem em que o roteiro é pensado: título, tipo e formato,
   hook, desenvolvimentos, fechamento, e no fim o material de apoio —
   Referências (o embasamento) e Reação (vídeos que o Pedro vai reagir dentro do
   vídeo dele).

   Não existe botão Salvar. Tudo é gravado por debounce de 600ms, e o status
   muda de coluna sozinho — quem decide isso é o servidor, que recalcula em toda
   escrita e devolve o status junto da resposta. */

import { patch, post, del, get, put, esc, data, aviso, SemRede } from "./api.js";
import { abrirPainel, fecharPainel, confirmar } from "./painel.js";
import { cardParaMarkdown, copiar } from "./markdown.js";
import { TIPOS, formatos, chipsDe } from "./opcoes.js";
import * as regua from "./regua.js";

const DEBOUNCE_MS = 600;

const LISTAS = {
  referencias: {
    rotulo: "Referências",
    intro: "Estudo, matéria, post — o que embasa o que você vai falar.",
    vazio: "Nenhuma referência ainda.",
    placeholder: "https://…",
    nota: "de onde é / o que interessa aqui",
  },
  reacoes: {
    rotulo: "Reação",
    intro: "Vídeos que você vai reagir dentro deste vídeo. Cole o link e anote o trecho.",
    vazio: "Nenhum vídeo pra reagir ainda.",
    placeholder: "https://…",
    nota: "trecho / o que comentar",
  },
};

let card = null;
let aoMudar = null;
let timer = null;
let salvando = false;
let sujo = false;
let folha = null;

// Como a exportação está configurada. Vem da config do servidor, então a
// escolha vale pra todos os cards e sincroniza entre o iPhone e o Mac.
const PADRAO_EXPORT = { incluir_tipo_formato: true, incluir_links: true, marcar_lacunas: false };
let exp = { ...PADRAO_EXPORT };

export function abrirEditor(cardInicial, callback, config = {}) {
  exp = { ...PADRAO_EXPORT, ...(config.export || {}) };
  card = normalizar(cardInicial);
  aoMudar = callback;
  sujo = false;
  folha = abrirPainel(desenhar(), {
    classe: "painel-editor",
    // Fechar não pode engolir edição pendente: grava o que estiver na tela.
    aoFechar: () => {
      window.removeEventListener("resize", reajustarTudo);
      if (sujo) salvarAgora();
    },
  });
  ligar();
}

function normalizar(c) {
  const copia = JSON.parse(JSON.stringify(c));
  copia.desenvolvimentos = copia.desenvolvimentos || [];
  for (const k of Object.keys(LISTAS)) copia[k] = copia[k] || [];
  return copia;
}

const $ = (s) => folha.querySelector(s);

/* ─────────────────────────── Desenho ─────────────────────────── */

function desenhar() {
  return `
  <header class="painel-topo">
    <span class="ed-titulo-topo" id="ed-topo">${esc(card.titulo) || "Sem título"}</span>
    <span class="ed-status" id="ed-status">${rotuloStatus()}</span>
    <button class="fechar" type="button" aria-label="Fechar">✕</button>
  </header>

  <div class="painel-corpo">
    <textarea id="ed-titulo" class="ed-titulo" rows="1"
              placeholder="título da ideia">${esc(card.titulo)}</textarea>

    <div class="ed-meta">
      <div class="grupo-chips" id="ed-tipo"></div>
      <div class="grupo-chips" id="ed-formato"></div>
    </div>

    <div class="ed-prog" id="ed-prog"></div>

    ${rotulo("ed-hook", "HOOK")}
    <textarea id="ed-hook" class="ed-campo" rows="1" placeholder="os primeiros segundos">${esc(card.hook)}</textarea>

    ${rotulo("ed-tela", "TÍTULO / TEXTO NA TELA")}
    <textarea id="ed-tela" class="ed-campo" rows="1"
              placeholder="o que aparece escrito no vídeo">${esc(card.titulo_tela || "")}</textarea>

    <div id="ed-desen"></div>
    <button class="ed-add" id="ed-add" type="button">+ adicionar desenvolvimento</button>

    ${rotulo("ed-fech", "FECHAMENTO")}
    <textarea id="ed-fech" class="ed-campo" rows="1" placeholder="como termina">${esc(card.fechamento)}</textarea>

    ${["referencias", "reacoes"].map((k) => `
      <section class="ed-secao">
        <h3 class="ed-rot">${LISTAS[k].rotulo}<span class="n" id="n-${k}"></span></h3>
        <p class="link-intro">${LISTAS[k].intro}</p>
        <div id="lista-${k}"></div>
        <button class="ed-add" data-add="${k}" type="button">+ adicionar link</button>
      </section>`).join("")}

    <div class="ed-acoes">
      ${card.status === "publicado"
        ? `<div class="ed-publicado">
             <span class="chip on">publicado em ${data(card.publicado_em, false)}</span>
             <p class="nota">Card publicado é histórico. Para reaproveitar, duplique.</p>
           </div>`
        : `<button class="bt bt-publiquei" id="ed-publicar" type="button">✓ Publiquei</button>`}
    </div>

    <!-- As opções ficam à vista, logo acima do botão: elas SÃO a configuração
         da cópia, e escondidas atrás de um "opções" ninguém abria. -->
    <div class="ed-export">
      <span class="ed-rot">Exportar</span>
      ${caixa("op-meta", exp.incluir_tipo_formato, "Incluir tipo e formato")}
      ${caixa("op-links", exp.incluir_links, "Incluir referências e reação")}
      ${caixa("op-lacunas", exp.marcar_lacunas, "Marcar o que está faltando")}
      <button class="bt sec" id="ed-md" type="button">Copiar markdown</button>
    </div>

    <div class="ed-secundarias">
      <button class="bt sec" id="ed-dup" type="button">Duplicar</button>
      <button class="bt perigo" id="ed-excluir" type="button">Excluir</button>
    </div>
  </div>`;
}

const rotulo = (id, texto) => `<label class="ed-rot" for="${id}">${texto}</label>`;

const caixa = (id, ligada, rotulo) =>
  `<label class="sw"><input type="checkbox" id="${id}"${ligada ? " checked" : ""}> ${rotulo}</label>`;

function rotuloStatus() {
  const nomes = { ideia: "ideia", producao: "produção", publicado: "publicado" };
  return `<span class="ponto ${card.status}"></span>${nomes[card.status] || card.status}`;
}

/* ─────────────────────────── Ligações ─────────────────────────── */

function ligar() {
  $(".fechar").onclick = fecharPainel;

  const tit = $("#ed-titulo");
  tit.addEventListener("input", () => {
    // Título é uma frase, não um parágrafo: quebra de linha colada vira espaço.
    if (tit.value.includes("\n")) {
      const pos = tit.selectionStart;
      tit.value = tit.value.replace(/\n+/g, " ");
      tit.setSelectionRange(pos, pos);
    }
    $("#ed-topo").textContent = tit.value || "Sem título";
    autoAltura(tit);
    agendar();
  });
  tit.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); tit.blur(); } });
  for (const id of ["#ed-hook", "#ed-tela", "#ed-fech"]) {
    const el = $(id);
    el.addEventListener("input", () => { autoAltura(el); agendar(); });
  }

  pintarChipsMeta();

  $("#ed-add").onclick = () => {
    card.desenvolvimentos = [...colherDesenvolvimentos(), { texto: "" }];
    pintarDesenvolvimentos();
    const campos = folha.querySelectorAll(".desen-campo");
    campos[campos.length - 1]?.focus();
  };

  folha.querySelectorAll("[data-add]").forEach((b) => {
    b.onclick = () => {
      const k = b.dataset.add;
      card[k] = [...colherLinks(k), { url: "", nota: "" }];
      pintarLinks(k);
      folha.querySelector(`#lista-${k} .link-item:last-child .url`)?.focus();
    };
  });

  const publicar = $("#ed-publicar");
  if (publicar) publicar.onclick = aoPublicar;

  $("#ed-md").onclick = aoCopiarMarkdown;
  for (const id of ["#op-meta", "#op-links", "#op-lacunas"]) {
    $(id).addEventListener("change", guardarExport);
  }
  $("#ed-dup").onclick = aoDuplicar;
  $("#ed-excluir").onclick = aoExcluir;

  pintarDesenvolvimentos();
  for (const k of Object.keys(LISTAS)) pintarLinks(k);
  pintarProgresso();
  reajustarTudo();
  window.addEventListener("resize", reajustarTudo);
}

/* Cada campo tem exatamente a altura do que está escrito: uma linha de texto,
   uma linha visível. Sem mínimo artificial — era o min-height de 66px que
   deixava um hook de uma linha dentro de uma caixa de três.

   scrollHeight não inclui a borda, e com box-sizing:border-box definir a altura
   sem ela cortaria o texto por 2px; daí somar (offsetHeight - clientHeight). */
function autoAltura(el) {
  if (!el) return;
  el.style.height = "auto";
  const borda = el.offsetHeight - el.clientHeight;
  el.style.height = (el.scrollHeight + borda) + "px";
}

/** Recalcula tudo: a quebra de linha muda quando a largura muda. */
function reajustarTudo() {
  if (!folha) return;
  folha.querySelectorAll("textarea").forEach(autoAltura);
}

function pintarChipsMeta() {
  $("#ed-tipo").innerHTML = `<span class="grupo-rot">Tipo</span>` + chipsDe(TIPOS, card.tipo, "tipo");
  $("#ed-formato").innerHTML = `<span class="grupo-rot">Formato</span>` + chipsDe(formatos(), card.formato, "fmt");

  folha.querySelectorAll("#ed-tipo .op").forEach((b) => {
    b.onclick = () => {
      card.tipo = card.tipo === b.dataset.v ? null : b.dataset.v;
      pintarChipsMeta();
      agendar(0);          // escolha explícita grava na hora, sem debounce
    };
  });
  folha.querySelectorAll("#ed-formato .op").forEach((b) => {
    b.onclick = () => {
      card.formato = card.formato === b.dataset.v ? null : b.dataset.v;
      pintarChipsMeta();
      agendar(0);
    };
  });
}

function pintarDesenvolvimentos() {
  const lista = card.desenvolvimentos || [];
  $("#ed-desen").innerHTML = lista.map((d, i) => `
    <div class="desen" data-i="${i}">
      <div class="desen-topo">
        <span class="ed-rot">DESENVOLVIMENTO ${i + 1}</span>
        <span class="desen-bts">
          <button class="desen-bt" type="button" data-mv="-1" ${i === 0 ? "disabled" : ""} aria-label="Subir">↑</button>
          <button class="desen-bt" type="button" data-mv="1" ${i === lista.length - 1 ? "disabled" : ""} aria-label="Descer">↓</button>
          <button class="desen-bt" type="button" data-rm="1" aria-label="Remover">✕</button>
        </span>
      </div>
      <textarea class="ed-campo desen-campo" rows="1" placeholder="um ponto do roteiro">${esc(d.texto || "")}</textarea>
    </div>`).join("");

  folha.querySelectorAll("#ed-desen .desen").forEach((el) => {
    const i = Number(el.dataset.i);
    const ta = el.querySelector(".desen-campo");
    autoAltura(ta);
    ta.addEventListener("input", () => { autoAltura(ta); agendar(); });
    el.querySelectorAll("[data-mv]").forEach((b) => {
      b.onclick = () => {
        const j = i + Number(b.dataset.mv);
        const atual = colherDesenvolvimentos();
        if (j < 0 || j >= atual.length) return;
        const [item] = atual.splice(i, 1);
        atual.splice(j, 0, item);
        card.desenvolvimentos = atual;
        pintarDesenvolvimentos();
        agendar(0);
      };
    });
    el.querySelector("[data-rm]").onclick = () => {
      const atual = colherDesenvolvimentos();
      atual.splice(i, 1);
      card.desenvolvimentos = atual;
      pintarDesenvolvimentos();
      pintarProgresso();
      agendar(0);
    };
  });
}

function pintarLinks(k) {
  const cfg = LISTAS[k];
  const lista = card[k] || [];
  const alvo = folha.querySelector(`#lista-${k}`);
  alvo.innerHTML = lista.length ? lista.map((l, i) => `
    <div class="link-item" data-i="${i}">
      <div class="link-topo">
        <span class="n">${i + 1}</span>
        ${l.url ? `<a class="link-abrir" href="${esc(l.url)}" target="_blank" rel="noopener">abrir</a>` : ""}
        <button class="desen-bt" type="button" data-rm="1" aria-label="Remover">✕</button>
      </div>
      <input type="url" class="url" inputmode="url" autocapitalize="off" autocorrect="off"
             placeholder="${cfg.placeholder}" value="${esc(l.url || "")}">
      <input type="text" class="nota" placeholder="${cfg.nota}" value="${esc(l.nota || "")}">
    </div>`).join("") : `<p class="vazio link-vazio">${cfg.vazio}</p>`;

  alvo.querySelectorAll(".link-item").forEach((el) => {
    const i = Number(el.dataset.i);
    el.querySelectorAll("input").forEach((inp) => inp.addEventListener("input", () => agendar()));
    el.querySelector("[data-rm]").onclick = () => {
      const atual = colherLinks(k);
      atual.splice(i, 1);
      card[k] = atual;
      pintarLinks(k);
      agendar(0);
    };
  });
  atualizarContadores();
}

function atualizarContadores() {
  for (const k of Object.keys(LISTAS)) {
    const n = (card[k] || []).filter((l) => (l.url || "").trim() || (l.nota || "").trim()).length;
    const el = folha.querySelector(`#n-${k}`);
    if (el) { el.textContent = n || ""; el.style.display = n ? "" : "none"; }
  }
}

function pintarProgresso() {
  const c = coletar();
  const n = c.desenvolvimentos.filter((d) => (d.texto || "").trim()).length;
  const marca = (ok, txt) => `<span class="${ok ? "ok" : ""}">${txt}</span>`;
  $("#ed-prog").innerHTML =
    marca(!!c.hook.trim(), "hook") +
    marca(n > 0, `${n} desenvolvimento${n === 1 ? "" : "s"}`) +
    marca(!!c.fechamento.trim(), "fechamento");
}

/* ─────────────────────────── Coleta ─────────────────────────── */

function colherDesenvolvimentos() {
  return [...folha.querySelectorAll("#ed-desen .desen")].map((el) => ({
    id: (card.desenvolvimentos[Number(el.dataset.i)] || {}).id,
    texto: el.querySelector(".desen-campo").value,
  }));
}

function colherLinks(k) {
  return [...folha.querySelectorAll(`#lista-${k} .link-item`)].map((el) => ({
    id: (card[k][Number(el.dataset.i)] || {}).id,
    url: el.querySelector(".url").value,
    nota: el.querySelector(".nota").value,
  }));
}

function coletar() {
  const saida = {
    ...card,
    titulo: $("#ed-titulo").value,
    hook: $("#ed-hook").value,
    titulo_tela: $("#ed-tela").value,
    fechamento: $("#ed-fech").value,
    desenvolvimentos: colherDesenvolvimentos(),
  };
  for (const k of Object.keys(LISTAS)) saida[k] = colherLinks(k);
  return saida;
}

function opcoesMd() {
  return {
    incluirTipoFormato: $("#op-meta").checked,
    marcarLacunas: $("#op-lacunas").checked,
    incluirLinks: $("#op-links").checked,
  };
}

let timerExport = null;
function guardarExport() {
  exp = {
    incluir_tipo_formato: $("#op-meta").checked,
    incluir_links: $("#op-links").checked,
    marcar_lacunas: $("#op-lacunas").checked,
  };
  clearTimeout(timerExport);
  timerExport = setTimeout(() => put("/lab/api/config", { export: exp }).catch(() => {}), 350);
}

/* ─────────────────────────── Autosave ─────────────────────────── */

function agendar(atraso = DEBOUNCE_MS) {
  sujo = true;
  pintarProgresso();
  atualizarContadores();
  clearTimeout(timer);
  timer = setTimeout(salvarAgora, atraso);
}

async function salvarAgora() {
  if (salvando) { agendar(200); return; }
  clearTimeout(timer);
  const dados = coletar();
  salvando = true;
  try {
    const salvo = await patch(`/lab/api/cards/${card.id}`, {
      titulo: dados.titulo,
      hook: dados.hook,
      titulo_tela: dados.titulo_tela,
      fechamento: dados.fechamento,
      tipo: dados.tipo,
      formato: dados.formato,
      desenvolvimentos: dados.desenvolvimentos,
      referencias: dados.referencias,
      reacoes: dados.reacoes,
    });
    sujo = false;
    const statusMudou = salvo.status !== card.status;
    // Os ids vindos do servidor entram sem repintar: repintar tiraria o cursor
    // do campo enquanto o Pedro digita.
    for (const k of ["desenvolvimentos", ...Object.keys(LISTAS)]) {
      salvo[k].forEach((item, i) => { if (card[k][i]) card[k][i].id = item.id; });
      if (salvo[k].length !== card[k].length) card[k] = salvo[k];
    }
    card = { ...salvo, desenvolvimentos: card.desenvolvimentos,
             referencias: card.referencias, reacoes: card.reacoes };
    if (statusMudou) {
      const el = folha?.querySelector("#ed-status");
      if (el) el.innerHTML = rotuloStatus();
    }
    if (aoMudar) aoMudar(salvo);
  } catch (e) {
    // Autosave tolera falha: fica sujo e tenta de novo no próximo toque.
    if (e instanceof SemRede) aviso("Sem rede. Vai salvar quando voltar.");
    else if (e.status !== 401) aviso("Não salvou agora.");
    sujo = true;
  } finally {
    salvando = false;
  }
}

/* ─────────────────────────── Publiquei ─────────────────────────── */

async function aoPublicar() {
  if (sujo) await salvarAgora();

  if (!card.tipo || !card.formato) {
    aviso(!card.tipo ? "Marque se é conteúdo ou anúncio." : "Marque o formato.");
    folha.querySelector(!card.tipo ? "#ed-tipo" : "#ed-formato")
      .scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }

  // Anúncio recebe preview: o app não bloqueia, mas mostra onde o ponteiro vai
  // parar antes de confirmar. A decisão é do Pedro; a informação é do app.
  if (card.tipo === "anuncio") {
    let simulacao = null;
    try { simulacao = await get("/lab/api/regua?simular=anuncio"); } catch (e) { /* segue */ }
    const depois = simulacao?.simulado;
    if (depois && depois.zona !== "verde" && depois.saldo < 0) {
      const ok = await confirmar({
        titulo: "Publicar este anúncio?",
        texto: `<div class="preview-regua">${faixaPreview(simulacao, depois)}</div>
                <p>Isso vai te deixar com <b>${esc(depois.subtexto)}</b>.</p>`,
        ok: "Publicar mesmo assim",
        cancelar: "Cancelar",
      });
      if (!ok) return;
    }
  }

  try {
    const r = await post(`/lab/api/cards/${card.id}/publicar`);
    card = normalizar(r.card);
    regua.animarPara(r.regua_depois, r.regua_antes);
    if (aoMudar) aoMudar(r.card, { reguaAntes: r.regua_antes, reguaDepois: r.regua_depois });
    fecharPainel();
    aviso(r.regua_depois.zona === "verde" ? "No ritmo." : r.regua_depois.subtexto);
  } catch (e) {
    aviso(e.corpo?.mensagem || "Não deu pra marcar como publicado.");
  }
}

/** Faixa com dois ponteiros: onde está e onde vai parar. */
function faixaPreview(antes, depois) {
  const pos = (x) => (x + 1) * 50;
  return `
    <div class="regua-faixa">
      <span class="fx vermelho"></span><span class="fx amarelo"></span>
      <span class="fx verde"></span>
      <span class="fx amarelo"></span><span class="fx vermelho"></span>
      <span class="ponteiro antes" style="left:${pos(antes.cinza ? 0 : antes.x)}%"></span>
      <span class="ponteiro" style="left:${pos(depois.x)}%"></span>
    </div>
    <p class="preview-legenda"><span class="p-antes"></span> agora
       · <span class="p-depois"></span> depois de publicar</p>`;
}

/* ─────────────────────────── Ações secundárias ─────────────────────────── */

async function aoCopiarMarkdown(ev) {
  // A string é montada ANTES do await, senão o Safari recusa o clipboard.
  const texto = cardParaMarkdown(coletar(), opcoesMd());
  const bt = ev.currentTarget;
  const ok = await copiar(texto);
  bt.textContent = ok ? "Copiado" : "Não deu";
  setTimeout(() => { bt.textContent = "Copiar markdown"; }, 1600);
}

async function aoDuplicar() {
  if (sujo) await salvarAgora();
  try {
    const copia = await post(`/lab/api/cards/${card.id}/duplicar`);
    if (aoMudar) aoMudar(copia);
    fecharPainel();
    aviso("Cópia criada na bancada.");
  } catch (e) { aviso("Não deu pra duplicar."); }
}

async function aoExcluir() {
  // Apagar um card publicado tira a publicação da conta e move o ponteiro.
  // Isso precisa estar na tela ANTES de confirmar, não depois.
  const publicado = card.status === "publicado";
  const ok = await confirmar({
    titulo: "Excluir este card?",
    texto: publicado
      ? `<p>Apaga o card, o roteiro e os links de vez. Não dá pra desfazer.</p>
         <p class="nota">Como ele estava publicado, a publicação sai da conta da
            régua e o ponteiro vai se mexer.</p>`
      : `<p>Apaga o card, o roteiro e os links de vez. Não dá pra desfazer.</p>`,
    ok: "Excluir",
    perigo: true,
  });
  if (!ok) return;
  try {
    const r = await del(`/lab/api/cards/${card.id}`);
    if (r?.regua_depois) regua.animarPara(r.regua_depois, r.regua_antes);
    if (aoMudar) aoMudar(card, { removido: true });
    fecharPainel();
    aviso("Excluído.");
  } catch (e) { aviso("Não deu pra excluir."); }
}
