/* Editor do card: tela cheia no celular, painel lateral no Mac.

   Não existe botão Salvar. Tudo é salvo por debounce de 600ms, e o status muda
   de coluna sozinho — quem decide isso é o servidor, que recalcula em toda
   escrita e devolve o status junto da resposta. */

import { patch, post, del, get, esc, data, aviso, SemRede } from "./api.js";
import { abrirPainel, fecharPainel, confirmar } from "./painel.js";
import { cardParaMarkdown, copiar, baixar, nomeDeArquivo } from "./markdown.js";
import * as regua from "./regua.js";

const DEBOUNCE_MS = 600;

const TIPOS = [["conteudo", "Conteúdo"], ["anuncio", "Anúncio"]];
const FORMATOS = [["lofi", "Lo-fi"], ["slide", "Slide"], ["vlog", "Vlog"], ["documentario", "Documentário"]];

let card = null;
let aoMudar = null;
let timer = null;
let salvando = false;
let sujo = false;
let folha = null;

export function abrirEditor(cardInicial, callback) {
  card = JSON.parse(JSON.stringify(cardInicial));
  aoMudar = callback;
  sujo = false;

  folha = abrirPainel(desenhar(), {
    classe: "painel-editor",
    // Fechar não pode engolir edição pendente: grava o que estiver na tela.
    aoFechar: () => { if (sujo) salvarAgora(); },
  });
  ligar();
}

function desenhar() {
  return `
  <header class="painel-topo">
    <div class="ed-seletores">
      ${seletor("tipo", TIPOS, card.tipo)}
      ${seletor("formato", FORMATOS, card.formato)}
    </div>
    <span class="ed-status" id="ed-status">${rotuloStatus()}</span>
    <button class="fechar" type="button" aria-label="Fechar">✕</button>
  </header>

  <div class="painel-corpo ed-corpo">
    <input type="text" id="ed-titulo" class="ed-titulo" value="${esc(card.titulo)}"
           placeholder="título da ideia">

    <div class="ed-prog" id="ed-prog"></div>

    <label class="ed-rot" for="ed-hook">HOOK</label>
    <textarea id="ed-hook" class="ed-campo" placeholder="os primeiros segundos"
              ${card.status === "publicado" ? "" : ""}>${esc(card.hook)}</textarea>

    <div id="ed-desen"></div>
    <button class="ed-add" id="ed-add" type="button">+ adicionar desenvolvimento</button>

    <label class="ed-rot" for="ed-fech">FECHAMENTO</label>
    <textarea id="ed-fech" class="ed-campo" placeholder="como termina">${esc(card.fechamento)}</textarea>

    <div class="ed-acoes">
      ${card.status === "publicado"
        ? `<div class="ed-publicado">
             <span class="chip on">publicado em ${data(card.publicado_em, false)}</span>
             <p class="nota">Card publicado é histórico. Para reaproveitar, duplique.</p>
           </div>`
        : `<button class="bt bt-publiquei" id="ed-publicar" type="button">✓ Publiquei</button>`}
    </div>

    <div class="ed-secundarias">
      <button class="bt sec" id="ed-md" type="button">Copiar markdown</button>
      <button class="bt sec" id="ed-md-op" type="button" aria-label="Opções de exportação">⋯</button>
      <button class="bt sec" id="ed-dup" type="button">Duplicar</button>
      <button class="bt sec" id="ed-arq" type="button">Arquivar</button>
    </div>
    <div class="ed-md-opcoes" id="ed-md-opcoes" hidden>
      <label class="sw"><input type="checkbox" id="op-meta" checked> Incluir tipo e formato</label>
      <label class="sw"><input type="checkbox" id="op-lacunas"> Marcar lacunas</label>
      <button class="bt sec" id="ed-baixar" type="button">Baixar .md</button>
    </div>
  </div>`;
}

function seletor(campo, opcoes, valor) {
  return `<select class="ed-sel ${campo}" id="ed-${campo}" aria-label="${campo}">
    <option value="">${campo === "tipo" ? "tipo?" : "formato?"}</option>
    ${opcoes.map(([id, nome]) =>
      `<option value="${id}"${valor === id ? " selected" : ""}>${nome}</option>`).join("")}
  </select>`;
}

function rotuloStatus() {
  const nomes = { ideia: "ideia", producao: "produção", publicado: "publicado" };
  return `<span class="ponto ${card.status}"></span>${nomes[card.status] || card.status}`;
}

/* ─────────────────────────── Ligações ─────────────────────────── */

const $ = (s) => folha.querySelector(s);

function ligar() {
  $(".fechar").onclick = fecharPainel;

  $("#ed-titulo").addEventListener("input", () => agendar());
  $("#ed-hook").addEventListener("input", () => { autoAltura($("#ed-hook")); agendar(); });
  $("#ed-fech").addEventListener("input", () => { autoAltura($("#ed-fech")); agendar(); });

  // Tipo e formato são escolha explícita: gravam na hora, sem debounce.
  $("#ed-tipo").onchange = () => agendar(0);
  $("#ed-formato").onchange = () => agendar(0);

  $("#ed-add").onclick = () => {
    card.desenvolvimentos = [...(card.desenvolvimentos || []), { texto: "" }];
    pintarDesenvolvimentos();
    const campos = folha.querySelectorAll(".desen-campo");
    campos[campos.length - 1]?.focus();
    agendar(0);
  };

  const publicar = $("#ed-publicar");
  if (publicar) publicar.onclick = aoPublicar;

  $("#ed-md").onclick = aoCopiarMarkdown;
  $("#ed-md-op").onclick = () => {
    const el = $("#ed-md-opcoes");
    el.hidden = !el.hidden;
  };
  $("#ed-baixar").onclick = () =>
    baixar(nomeDeArquivo(card), cardParaMarkdown(coletar(), opcoesMd()));
  $("#ed-dup").onclick = aoDuplicar;
  $("#ed-arq").onclick = aoArquivar;

  pintarDesenvolvimentos();
  pintarProgresso();
  autoAltura($("#ed-hook"));
  autoAltura($("#ed-fech"));
}

function autoAltura(el) {
  if (!el) return;
  el.style.height = "auto";
  el.style.height = Math.max(el.scrollHeight, 68) + "px";
}

function pintarDesenvolvimentos() {
  const alvo = $("#ed-desen");
  const lista = card.desenvolvimentos || [];
  alvo.innerHTML = lista.map((d, i) => `
    <div class="desen" data-i="${i}">
      <div class="desen-topo">
        <span class="ed-rot">DESENVOLVIMENTO ${i + 1}</span>
        <span class="desen-bts">
          <button class="desen-bt" type="button" data-mv="-1" ${i === 0 ? "disabled" : ""}
                  aria-label="Subir">↑</button>
          <button class="desen-bt" type="button" data-mv="1" ${i === lista.length - 1 ? "disabled" : ""}
                  aria-label="Descer">↓</button>
          <button class="desen-bt x" type="button" data-rm="1" aria-label="Remover">✕</button>
        </span>
      </div>
      <textarea class="ed-campo desen-campo" placeholder="um ponto do roteiro">${esc(d.texto || "")}</textarea>
    </div>`).join("");

  alvo.querySelectorAll(".desen").forEach((el) => {
    const i = Number(el.dataset.i);
    const ta = el.querySelector(".desen-campo");
    autoAltura(ta);
    ta.addEventListener("input", () => { autoAltura(ta); agendar(); });
    el.querySelectorAll("[data-mv]").forEach((b) => {
      b.onclick = () => {
        const passo = Number(b.dataset.mv);
        const lista2 = card.desenvolvimentos;
        const j = i + passo;
        if (j < 0 || j >= lista2.length) return;
        card.desenvolvimentos = colherDesenvolvimentos();
        const [item] = card.desenvolvimentos.splice(i, 1);
        card.desenvolvimentos.splice(j, 0, item);
        pintarDesenvolvimentos();
        agendar(0);
      };
    });
    el.querySelector("[data-rm]").onclick = () => {
      card.desenvolvimentos = colherDesenvolvimentos();
      card.desenvolvimentos.splice(i, 1);
      pintarDesenvolvimentos();
      pintarProgresso();
      agendar(0);
    };
  });
}

function colherDesenvolvimentos() {
  return [...folha.querySelectorAll(".desen")].map((el, i) => ({
    id: (card.desenvolvimentos[Number(el.dataset.i)] || {}).id,
    texto: el.querySelector(".desen-campo").value,
  }));
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

function coletar() {
  return {
    ...card,
    titulo: $("#ed-titulo").value,
    hook: $("#ed-hook").value,
    fechamento: $("#ed-fech").value,
    tipo: $("#ed-tipo").value || null,
    formato: $("#ed-formato").value || null,
    desenvolvimentos: colherDesenvolvimentos(),
  };
}

function opcoesMd() {
  return {
    incluirTipoFormato: $("#op-meta").checked,
    marcarLacunas: $("#op-lacunas").checked,
  };
}

/* ─────────────────────────── Autosave ─────────────────────────── */

function agendar(atraso = DEBOUNCE_MS) {
  sujo = true;
  pintarProgresso();
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
      fechamento: dados.fechamento,
      tipo: dados.tipo,
      formato: dados.formato,
      desenvolvimentos: dados.desenvolvimentos,
    });
    sujo = false;
    const statusMudou = salvo.status !== card.status;
    card = salvo;
    if (statusMudou) {
      const el = folha?.querySelector("#ed-status");
      if (el) el.innerHTML = rotuloStatus();
    }
    if (aoMudar) aoMudar(salvo);
  } catch (e) {
    // Autosave tolera falha: marca como sujo e tenta de novo no próximo toque.
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

  const c = coletar();
  if (!c.tipo || !c.formato) {
    aviso(!c.tipo ? "Falta dizer se é conteúdo ou anúncio." : "Falta o formato.");
    (!c.tipo ? $("#ed-tipo") : $("#ed-formato")).focus();
    return;
  }

  // Anúncio recebe preview: o app não bloqueia, mas mostra onde o ponteiro vai
  // parar antes de confirmar. A decisão é do Pedro; a informação é do app.
  if (c.tipo === "anuncio") {
    let simulacao = null;
    try { simulacao = await get("/lab/api/regua?simular=anuncio"); } catch (e) { /* segue */ }
    const depois = simulacao?.simulado;
    if (depois && depois.zona !== "verde" && depois.saldo < 0) {
      const ok = await confirmar({
        titulo: "Publicar este anúncio?",
        texto: `<div class="preview-regua">${faixaPreview(simulacao, depois)}</div>
                <p>Isso vai te deixar com <b>${depois.subtexto}</b>.</p>`,
        ok: "Publicar mesmo assim",
        cancelar: "Cancelar",
      });
      if (!ok) return;
    }
  }

  try {
    const r = await post(`/lab/api/cards/${card.id}/publicar`);
    card = r.card;
    regua.animarPara(r.regua_depois, r.regua_antes);
    if (aoMudar) aoMudar(r.card, { reguaAntes: r.regua_antes, reguaDepois: r.regua_depois });
    fecharPainel();
    aviso(r.regua_depois.zona === "verde" ? "No ritmo." : r.regua_depois.subtexto);
  } catch (e) {
    aviso(e.corpo?.mensagem || "Não deu pra marcar como publicado.");
  }
}

/** Faixa estática com dois ponteiros: onde está e onde vai parar. */
function faixaPreview(antes, depois) {
  const pos = (x) => (x + 1) * 50;
  return `
    <div class="regua-faixa preview">
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

async function aoArquivar() {
  const ok = await confirmar({
    titulo: "Arquivar este card?",
    texto: "Ele sai do board mas continua guardado. Nada é apagado.",
    ok: "Arquivar",
    perigo: true,
  });
  if (!ok) return;
  try {
    await del(`/lab/api/cards/${card.id}`);
    if (aoMudar) aoMudar(card, { removido: true });
    fecharPainel();
    aviso("Arquivado.");
  } catch (e) { aviso("Não deu pra arquivar."); }
}
