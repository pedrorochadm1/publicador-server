/* Painel de ajustes: meta semanal, autofoco e sair.

   Aparência não entra aqui: claro com azul é decisão fechada, não preferência.

   No celular não existe trilho lateral, então este painel é também o único
   lugar onde dá pra sair da conta. */

import { put, post } from "./api.js";
import { abrirPainel, fecharPainel } from "./painel.js";

export function abrirAjustes(config, aoSalvar) {
  const folha = abrirPainel(`
    <header class="painel-topo">
      <h2>Ajustes</h2>
      <button class="fechar" type="button" aria-label="Fechar">✕</button>
    </header>
    <div class="painel-corpo">
      <div class="aj-bloco">
        <label class="ed-rot" for="aj-meta">Meta semanal de conteúdos</label>
        <div class="aj-linha">
          <input type="number" id="aj-meta" min="0" max="30" value="${config.meta_semanal ?? 6}">
          <span class="nota">Zero desliga o alerta de frequência.</span>
        </div>
      </div>

      <div class="aj-bloco">
        <label class="sw">
          <input type="checkbox" id="aj-autofoco" ${config.autofoco !== false ? "checked" : ""}>
          Focar o campo de captura ao abrir
        </label>
        <p class="nota">No iPhone o teclado só abre com um toque seu, mas o cursor
           já fica no lugar certo.</p>
      </div>

      <div class="aj-bloco">
        <button class="bt sec" id="aj-sair" type="button">Sair da conta</button>
      </div>
    </div>`, { classe: "painel-ajustes" });

  folha.querySelector(".fechar").onclick = fecharPainel;

  let timer = null;
  function guardar() {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try {
        const nova = await put("/lab/api/config", {
          meta_semanal: Math.max(0, Number(folha.querySelector("#aj-meta").value) || 0),
          autofoco: folha.querySelector("#aj-autofoco").checked,
        });
        if (aoSalvar) aoSalvar(nova);
      } catch (e) { /* tenta de novo na próxima mexida */ }
    }, 350);
  }

  folha.querySelector("#aj-meta").addEventListener("input", guardar);
  folha.querySelector("#aj-autofoco").addEventListener("change", guardar);

  folha.querySelector("#aj-sair").onclick = async () => {
    try { await post("/lab/api/sair"); } catch (e) { /* segue */ }
    location.href = "/lab";
  };
}
