/* Painel deslizante: folha de baixo pra cima no celular, painel lateral no Mac.
   É onde abrem o editor do card, o detalhamento da régua e o onboarding. */

let aoFechar = null;

export function abrirPainel(html, { aoFechar: cb = null, classe = "" } = {}) {
  const el = document.getElementById("painel");
  aoFechar = cb;
  el.className = "aberto " + classe;
  el.innerHTML = `<div class="painel-fundo"></div><section class="painel-folha">${html}</section>`;
  el.querySelector(".painel-fundo").onclick = fecharPainel;
  document.addEventListener("keydown", escFecha);
  requestAnimationFrame(() => el.classList.add("visivel"));
  return el.querySelector(".painel-folha");
}

function escFecha(e) { if (e.key === "Escape") fecharPainel(); }

export function fecharPainel() {
  const el = document.getElementById("painel");
  if (!el.classList.contains("aberto")) return;
  document.removeEventListener("keydown", escFecha);
  el.classList.remove("visivel");
  const cb = aoFechar;
  aoFechar = null;
  setTimeout(() => {
    el.className = "";
    el.innerHTML = "";
    if (cb) cb();
  }, 180);
}

/* Confirmação, no lugar do confirm() do navegador.

   Vive numa camada PRÓPRIA, não no #painel: o preview de publicação é aberto de
   dentro do editor, e reaproveitar o mesmo elemento destruiria o editor por
   baixo. */
export function confirmar({ titulo, texto, ok = "Confirmar", cancelar = "Cancelar", perigo = false }) {
  return new Promise((resolve) => {
    const el = document.createElement("div");
    el.className = "confirma aberto";
    el.innerHTML = `
      <div class="painel-fundo"></div>
      <section class="painel-folha">
        <header class="painel-topo"><h2>${titulo}</h2>
          <button class="fechar" type="button" aria-label="Fechar">✕</button></header>
        <div class="painel-corpo">
          <div class="confirma-txt">${texto}</div>
          <div class="confirma-acoes">
            <button class="bt sec" data-r="0" type="button">${cancelar}</button>
            <button class="bt ${perigo ? "perigo" : ""}" data-r="1" type="button">${ok}</button>
          </div>
        </div>
      </section>`;
    document.body.appendChild(el);
    requestAnimationFrame(() => el.classList.add("visivel"));

    const encerrar = (r) => {
      document.removeEventListener("keydown", tecla);
      el.classList.remove("visivel");
      setTimeout(() => { el.remove(); resolve(r); }, 180);
    };
    const tecla = (e) => { if (e.key === "Escape") encerrar(false); };
    document.addEventListener("keydown", tecla);

    el.querySelector(".painel-fundo").onclick = () => encerrar(false);
    el.querySelector(".fechar").onclick = () => encerrar(false);
    el.querySelectorAll("[data-r]").forEach((b) => {
      b.onclick = () => encerrar(b.dataset.r === "1");
    });
  });
}
