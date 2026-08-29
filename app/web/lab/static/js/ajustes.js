/* Painel de ajustes: formatos, meta semanal, autofoco e sair.

   Aparência não entra aqui: claro com azul é decisão fechada, não preferência.

   No celular não existe trilho lateral, então este painel é também o único
   lugar onde dá pra sair da conta. */

import { put, post, esc, aviso } from "./api.js";
import { abrirPainel, fecharPainel, confirmar } from "./painel.js";
import { formatos, configurarFormatos, corFormato, FORMATOS_PADRAO } from "./opcoes.js";

export function abrirAjustes(config, aoSalvar) {
  const folha = abrirPainel(`
    <header class="painel-topo">
      <h2>Ajustes</h2>
      <button class="fechar" type="button" aria-label="Fechar">✕</button>
    </header>
    <div class="painel-corpo">
      <div class="aj-bloco">
        <span class="ed-rot">Formatos</span>
        <p class="nota">Valem para conteúdo e para anúncio. Remover um formato não
           mexe nos cards que já usam — eles continuam com a etiqueta.</p>
        <div id="aj-formatos" class="aj-formatos"></div>
        <form class="aj-novo" id="aj-novo-formato" autocomplete="off">
          <input type="text" id="aj-formato-nome" placeholder="novo formato" maxlength="24">
          <button class="bt sec" type="submit">Adicionar</button>
        </form>
      </div>

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

  /* ─────────────────────────── Formatos ─────────────────────────── */

  const slug = (texto) => texto.toLowerCase().trim()
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 24);

  function pintarFormatos() {
    folha.querySelector("#aj-formatos").innerHTML = formatos().map((f) => `
      <div class="aj-formato">
        <span class="ponto-cor" style="background:${corFormato(f.id)}"></span>
        <input class="aj-formato-nome" type="text" maxlength="24"
               data-id="${esc(f.id)}" value="${esc(f.nome)}" aria-label="Nome do formato">
        <button class="desen-bt" type="button" data-rm="${esc(f.id)}"
                aria-label="Remover ${esc(f.nome)}">✕</button>
      </div>`).join("")
      || `<p class="vazio">Nenhum formato. Adicione ao menos um.</p>`;

    folha.querySelectorAll("#aj-formatos [data-rm]").forEach((b) => {
      b.onclick = () => removerFormato(b.dataset.rm);
    });

    // Renomear troca só o rótulo: o id fica, senão os cards que já usam o
    // formato perderiam o vínculo. E não repinta a lista enquanto digita, senão
    // o campo perderia o foco a cada tecla.
    folha.querySelectorAll(".aj-formato-nome").forEach((inp) => {
      const antes = inp.value;
      inp.addEventListener("input", () => {
        salvarFormatos(formatos().map((f) =>
          (f.id === inp.dataset.id ? { ...f, nome: inp.value } : f)), { repintar: false });
      });
      inp.addEventListener("blur", () => {
        if (inp.value.trim()) return;
        inp.value = antes;      // nome vazio some da tela: devolve o anterior
        salvarFormatos(formatos().map((f) =>
          (f.id === inp.dataset.id ? { ...f, nome: antes } : f)), { repintar: false });
      });
    });
  }

  let timerFormatos = null;
  function salvarFormatos(lista, { repintar = true } = {}) {
    configurarFormatos(lista);
    if (repintar) pintarFormatos();
    clearTimeout(timerFormatos);
    timerFormatos = setTimeout(async () => {
      try {
        const nova = await put("/lab/api/config", { formatos: lista });
        if (aoSalvar) aoSalvar(nova);
      } catch (e) { aviso("Não deu pra salvar os formatos."); }
    }, 350);
  }

  async function removerFormato(id) {
    const alvo = formatos().find((f) => f.id === id);
    if (!alvo) return;
    const ok = await confirmar({
      titulo: `Remover "${esc(alvo.nome)}"?`,
      texto: `<p>Ele sai da lista de escolha. Os cards que já usam esse formato
                 continuam como estão.</p>`,
      ok: "Remover",
      perigo: true,
    });
    if (ok) salvarFormatos(formatos().filter((f) => f.id !== id));
  }

  folha.querySelector("#aj-novo-formato").onsubmit = (e) => {
    e.preventDefault();
    const campo = folha.querySelector("#aj-formato-nome");
    const nome = campo.value.trim();
    const id = slug(nome);
    if (!id) return;
    if (formatos().some((f) => f.id === id)) {
      aviso("Esse formato já existe.");
      return;
    }
    campo.value = "";
    salvarFormatos([...formatos(), { id, nome }]);
  };

  configurarFormatos(config.formatos || FORMATOS_PADRAO);
  pintarFormatos();

  /* ─────────────────────────── Resto ─────────────────────────── */

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
