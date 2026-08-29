/* Painel de ajustes: tema, cor de ação, meta semanal, autofoco e sair.

   No celular não existe trilho lateral, então este painel é também o único
   lugar onde dá pra sair da conta. */

import { put, post, aviso } from "./api.js";
import { abrirPainel, fecharPainel } from "./painel.js";
import { chipsDe } from "./opcoes.js";
import { TEMAS, ACENTOS, aplicar, lerLocal } from "./tema.js";

export function abrirAjustes(config, aoSalvar) {
  const atualTema = lerLocal();

  const folha = abrirPainel(`
    <header class="painel-topo">
      <h2>Ajustes</h2>
      <button class="fechar" type="button" aria-label="Fechar">✕</button>
    </header>
    <div class="painel-corpo">
      <div class="grupo-chips" id="aj-tema"></div>
      <div class="grupo-chips" id="aj-acento"></div>

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

  /* ─── Tema e cor: aplicam na hora, sem botão de salvar ─── */

  const pintarTema = () => {
    const p = lerLocal();
    folha.querySelector("#aj-tema").innerHTML =
      `<span class="grupo-rot">Tema</span>` + chipsDe(TEMAS, p.tema, "tema");
    folha.querySelector("#aj-acento").innerHTML =
      `<span class="grupo-rot">Cor</span>` + chipsDe(ACENTOS, p.acento, "acento");

    folha.querySelectorAll("#aj-tema .op").forEach((b) => {
      b.onclick = () => { aplicar({ tema: b.dataset.v }); pintarTema(); guardar(); };
    });
    folha.querySelectorAll("#aj-acento .op").forEach((b) => {
      b.onclick = () => { aplicar({ acento: b.dataset.v }); pintarTema(); guardar(); };
    });
  };

  let timer = null;
  function guardar() {
    // A UI já mudou: as cores todas saem de variáveis CSS, então trocar o
    // data-tema no <html> repinta tudo sozinho. Aqui só persistimos.
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const p = lerLocal();
      try {
        const nova = await put("/lab/api/config", {
          tema: p.tema,
          acento: p.acento,
          meta_semanal: Math.max(0, Number(folha.querySelector("#aj-meta").value) || 0),
          autofoco: folha.querySelector("#aj-autofoco").checked,
        });
        if (aoSalvar) aoSalvar(nova);
      } catch (e) { /* a escolha já está aplicada e no localStorage */ }
    }, 350);
  }

  pintarTema();
  folha.querySelector("#aj-meta").addEventListener("input", guardar);
  folha.querySelector("#aj-autofoco").addEventListener("change", guardar);

  folha.querySelector("#aj-sair").onclick = async () => {
    try { await post("/lab/api/sair"); } catch (e) { /* segue */ }
    location.href = "/lab";
  };

  // Se o tema do servidor divergir do local (outro aparelho mudou), o local
  // vence aqui: foi ele que o Pedro acabou de ver na tela.
  if (config.tema && config.tema !== atualTema.tema) guardar();
}
