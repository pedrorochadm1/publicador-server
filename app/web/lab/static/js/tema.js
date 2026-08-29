/* Tema (escuro/claro/automático) e cor de ação (verde/azul).

   A escolha vive na config do servidor, então sincroniza entre o iPhone e o
   Mac. Mas ela também é espelhada em localStorage e aplicada ANTES do primeiro
   render: sem isso o app abriria escuro e piscaria pra claro quando a resposta
   da API chegasse. */

const CHAVE = "lab_tema";

export const TEMAS = [
  { id: "claro", nome: "Claro" },
  { id: "escuro", nome: "Escuro" },
  { id: "auto", nome: "Automático" },
];

export const ACENTOS = [
  { id: "azul", nome: "Azul" },
  { id: "verde", nome: "Verde" },
];

const PADRAO = { tema: "claro", acento: "azul" };

const doSistema = () =>
  window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "escuro" : "claro";

export function lerLocal() {
  try {
    return { ...PADRAO, ...JSON.parse(localStorage.getItem(CHAVE) || "{}") };
  } catch (e) {
    return { ...PADRAO };
  }
}

function gravarLocal(p) {
  try { localStorage.setItem(CHAVE, JSON.stringify(p)); } catch (e) { /* segue */ }
}

/** Escreve no <html> e ajusta a cor da barra de status do iOS. */
export function aplicar({ tema, acento } = {}) {
  const p = { ...lerLocal(), ...(tema ? { tema } : {}), ...(acento ? { acento } : {}) };
  gravarLocal(p);

  const raiz = document.documentElement;
  raiz.dataset.tema = p.tema === "auto" ? doSistema() : p.tema;
  raiz.dataset.acento = p.acento;

  // A barra de status do iPhone em modo standalone segue esta meta.
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) {
    meta.content = getComputedStyle(raiz).getPropertyValue("--fundo").trim() || "#0d1014";
  }
  return p;
}

/** Com o tema em "automático", seguir a troca do sistema em tempo real. */
export function vigiarSistema() {
  const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
  if (!mq?.addEventListener) return;
  mq.addEventListener("change", () => {
    if (lerLocal().tema === "auto") aplicar();
  });
}
