/* Partida a frio.

   No primeiro uso o saldo é zero sem nenhuma publicação por trás — verde falso.
   Duas saídas: informar o histórico das três janelas (padrão, resolve em 30
   segundos) ou começar do zero, com a régua cinza até a primeira publicação. */

import { post, aviso } from "./api.js";
import { abrirPainel, fecharPainel } from "./painel.js";
import * as regua from "./regua.js";

const JANELAS = [
  ["0_30", "Últimos 30 dias"],
  ["31_60", "De 31 a 60 dias"],
  ["61_90", "De 61 a 90 dias"],
];

export function precisaOnboarding(config, totalPublicacoes) {
  return !config?.onboarding?.feito && !totalPublicacoes;
}

export function abrirOnboarding(aoTerminar) {
  const linha = ([id, rot]) => `
    <div class="ob-linha">
      <span class="ob-rot">${rot}</span>
      <label>conteúdos <input type="number" min="0" max="200" value="0" data-j="${id}" data-c="conteudos"></label>
      <label>anúncios <input type="number" min="0" max="200" value="0" data-j="${id}" data-c="anuncios"></label>
    </div>`;

  const folha = abrirPainel(`
    <header class="painel-topo">
      <h2>Como está seu histórico?</h2>
    </header>
    <div class="painel-corpo ob">
      <p class="ob-intro">A régua precisa saber o que você já publicou, senão o
         ponteiro nasce no verde sem nada por trás. Dois números por linha e pronto.</p>
      ${JANELAS.map(linha).join("")}
      <div class="ob-acoes">
        <button class="bt" id="ob-ok" type="button">Usar esses números</button>
        <button class="bt sec" id="ob-pular" type="button">Começar do zero</button>
      </div>
      <p class="nota">Começando do zero, a régua fica cinza até você marcar a
         primeira publicação. Dá pra informar o histórico depois.</p>
    </div>`, { classe: "painel-onboarding" });

  folha.querySelector("#ob-ok").onclick = async () => {
    const janelas = {};
    folha.querySelectorAll("input[data-j]").forEach((el) => {
      const j = el.dataset.j;
      janelas[j] = janelas[j] || {};
      janelas[j][el.dataset.c] = Math.max(0, Number(el.value) || 0);
    });
    try {
      const r = await post("/lab/api/onboarding/backfill", { janelas });
      regua.desenhar(r.regua);
      fecharPainel();
      aviso(r.criadas ? `${r.criadas} publicações registradas.` : "Régua começando do zero.");
      if (aoTerminar) aoTerminar(r.regua);
    } catch (e) { aviso("Não deu pra registrar agora."); }
  };

  folha.querySelector("#ob-pular").onclick = async () => {
    try {
      const r = await post("/lab/api/onboarding/pular");
      regua.desenhar(r.regua);
      fecharPainel();
      if (aoTerminar) aoTerminar(r.regua);
    } catch (e) { aviso("Não deu pra salvar agora."); }
  };
}
