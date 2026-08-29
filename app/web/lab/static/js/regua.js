/* A régua de equilíbrio: faixa fina fixa no topo da aba do Lab.

   Toda a regra de negócio (saldo, zona, mensagem, contagem de atraso) vem
   pronta do servidor em lab_calculo.py. Aqui só convertemos `x` (que vem em
   [-1, +1]) em posição de pixel e animamos. Isso é deliberado: o preview que
   aparece antes de publicar um anúncio precisa bater EXATAMENTE com o que o
   servidor vai calcular depois, senão a animação mente. */

import { get, esc, data } from "./api.js";
import { abrirPainel, fecharPainel } from "./painel.js";

let atual = null;
let aoAbrirDetalhe = null;

const NOME_FORMATO = { lofi: "Lo-fi", slide: "Slide", vlog: "Vlog", documentario: "Documentário" };

/** Converte x em [-1,1] para a posição percentual do ponteiro na faixa. */
const paraPercentual = (x) => (x + 1) * 50;

export function desenhar(regua, { animarDe = null } = {}) {
  atual = regua;
  const el = document.getElementById("regua");
  if (!el) return;
  el.hidden = false;

  const pos = paraPercentual(regua.cinza ? 0 : regua.x);
  const de = animarDe == null ? pos : paraPercentual(animarDe);

  el.className = "regua zona-" + (regua.zona || "verde") + (regua.cinza ? " cinza" : "");
  el.innerHTML = `
    <button class="regua-toque" type="button" aria-label="Ver detalhamento do equilíbrio">
      <div class="regua-faixa">
        <span class="fx vermelho"></span><span class="fx amarelo"></span>
        <span class="fx verde"></span>
        <span class="fx amarelo"></span><span class="fx vermelho"></span>
        ${regua.cinza ? "" : `<span class="ponteiro" style="left:${de}%"></span>`}
      </div>
      <div class="regua-linha">
        <span class="regua-msg">${esc(curta(regua))}</span>
        <span class="regua-sub">${esc(regua.subtexto)}</span>
        ${metaResumo(regua)}
      </div>
    </button>`;

  el.querySelector(".regua-toque").onclick = () => abrirDetalhe();

  // A animação é o feedback central: o ponteiro sai de onde estava e vai pro
  // novo lugar, pra Pedro ver em meio segundo o efeito da decisão dele.
  const ponteiro = el.querySelector(".ponteiro");
  if (ponteiro && animarDe != null) {
    requestAnimationFrame(() => {
      ponteiro.classList.add("movendo");
      ponteiro.style.left = pos + "%";
    });
  }
}

/* A régua ocupa uma faixa fina e a tela do iPhone é estreita: a frase inteira
   ("VOCÊ PRECISA PRODUZIR CONTEÚDO") não cabe ao lado do resto sem virar
   reticências. A versão curta diz a mesma coisa; a íntegra fica no detalhe. */
function curta(regua) {
  if (regua.cinza) return "SEM HISTÓRICO";
  if (regua.zona === "verde") return "NO RITMO";
  return regua.saldo < 0 ? "FAÇA CONTEÚDO" : "FAÇA ANÚNCIO";
}

function metaResumo(regua) {
  const m = regua.meta_semanal;
  if (!m || !m.meta) return "";
  return `<span class="regua-meta${m.atrasado ? " atrasada" : ""}">${m.feitos}/${m.meta} na semana</span>`;
}

export function esconder() {
  const el = document.getElementById("regua");
  if (el) el.hidden = true;
}

export async function recarregar() {
  const r = await get("/lab/api/regua");
  desenhar(r);
  return r;
}

/** Anima do estado anterior para o novo (usado depois de publicar). */
export function animarPara(reguaDepois, reguaAntes) {
  desenhar(reguaDepois, { animarDe: reguaAntes?.cinza ? 0 : reguaAntes?.x ?? 0 });
}

/* ─────────────────────────── Detalhamento ─────────────────────────── */

async function abrirDetalhe() {
  if (aoAbrirDetalhe) aoAbrirDetalhe();
  let d;
  try { d = await get("/lab/api/regua/detalhe"); }
  catch (e) { return; }

  const janela = (rot, j) => `
    <div class="jan">
      <span class="jan-rot">${rot}</span>
      <span class="jan-prop">${j.proporcao == null
        ? (j.conteudos ? "sem anúncio" : "—")
        : j.proporcao.toFixed(1).replace(".", ",") + ":1"}</span>
      <span class="jan-nums">${j.conteudos} conteúdo${j.conteudos === 1 ? "" : "s"}
        · ${j.anuncios} anúncio${j.anuncios === 1 ? "" : "s"}</span>
    </div>`;

  const linhas = (d.publicacoes || []).map((p) => `
    <tr>
      <td>${data(p.publicado_em, false)}</td>
      <td>${p.sintetico ? '<span class="chip">histórico informado</span>'
                        : esc((p.titulo || "").slice(0, 44) || "—")}</td>
      <td><span class="tag-tipo ${p.tipo}">${p.tipo === "anuncio" ? "anúncio" : "conteúdo"}</span></td>
      <td>${p.formato ? esc(NOME_FORMATO[p.formato] || p.formato) : "—"}</td>
      <td class="peso">×${p.peso}</td>
    </tr>`).join("");

  abrirPainel(`
    <header class="painel-topo">
      <h2>Equilíbrio</h2>
      <button class="fechar" type="button" aria-label="Fechar">✕</button>
    </header>
    <div class="painel-corpo">
      <div class="saldo-bruto">
        <span class="saldo-num">${d.saldo > 0 ? "+" : ""}${d.saldo}</span>
        <span class="saldo-rot">pontos de conteúdo</span>
        <p class="saldo-conta">${d.pc} de conteúdo menos 5 × ${d.pa} de anúncio.
           Zero é a proporção perfeita.</p>
      </div>

      <h3 class="secao">Proporção real</h3>
      <div class="janelas">
        ${janela("últimos 30 dias", d.janelas["0_30"])}
        ${janela("31 a 60 dias", d.janelas["31_60"])}
        ${janela("61 a 90 dias", d.janelas["61_90"])}
      </div>

      <h3 class="secao">Histórico</h3>
      ${linhas ? `<div class="rolagem-x"><table class="tabela">
        <tr><th>Quando</th><th>O quê</th><th>Tipo</th><th>Formato</th><th>Peso</th></tr>
        ${linhas}</table></div>`
      : `<p class="vazio">Nenhuma publicação registrada ainda.</p>`}
      <p class="nota">Publicação com mais de 90 dias sai da conta sozinha. Por isso
         o saldo encolhe com o tempo, sem nada precisar rodar em segundo plano.</p>
    </div>`);

  document.querySelector("#painel .fechar").onclick = fecharPainel;
}
