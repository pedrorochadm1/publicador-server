/* Tipo e formato num lugar só, e o chip clicável que os desenha.

   Eram <select> na v1. No celular um select abre a roleta do iOS e esconde as
   opções; como são poucas e curtas, chip clicável resolve num toque e deixa
   tudo visível de uma vez.

   TIPO é fixo — a régua depende dele. FORMATO é editável pelo Pedro nos
   Ajustes, então a lista vem da config e a cor sai de uma paleta indexada, não
   de uma classe CSS por formato. */

export const TIPOS = [
  { id: "conteudo", nome: "Conteúdo" },
  { id: "anuncio", nome: "Anúncio" },
];

export const FORMATOS_PADRAO = [
  { id: "lofi", nome: "Lo-fi" },
  { id: "slide", nome: "Slide" },
  { id: "vlog", nome: "Vlog" },
  { id: "documentario", nome: "Documentário" },
];

/* Cores dos formatos. Escuras o bastante pra sustentar texto branco quando o
   chip está marcado, e pra ler como texto sobre a própria tinta na etiqueta.
   Verde e carmim ficam de fora de propósito: são as cores de conteúdo e
   anúncio, e repeti-las num formato confundiria os dois sistemas. */
const PALETA = [
  "#b45309", "#4338ca", "#a21caf", "#0e7490",
  "#7c3aed", "#0f766e", "#9a3412", "#4d7c0f",
];

let FORMATOS = [...FORMATOS_PADRAO];

export function configurarFormatos(lista) {
  FORMATOS = Array.isArray(lista) && lista.length ? lista.map((f) =>
    (typeof f === "string" ? { id: f, nome: f } : { id: f.id, nome: f.nome || f.id }))
    : [...FORMATOS_PADRAO];
}

export const formatos = () => FORMATOS;

export const nomeFormato = (f) =>
  (FORMATOS.find((x) => x.id === f) || {}).nome || f || "";
export const nomeTipo = (t) => (TIPOS.find((x) => x.id === t) || {}).nome || t || "";

/** Cor do formato pela posição na lista. Um formato removido da lista ainda
    aparece nos cards antigos, então cai num cinza neutro em vez de sumir. */
export function corFormato(id) {
  const i = FORMATOS.findIndex((f) => f.id === id);
  return i < 0 ? "#6b7684" : PALETA[i % PALETA.length];
}

const tinta = (hex, alfa) => {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alfa})`;
};

/** `ativo` aceita um valor (escolha única) ou um array (filtro multi). */
export function chipsDe(opcoes, ativo, prefixo) {
  const ligado = (id) => (Array.isArray(ativo) ? ativo.includes(id) : ativo === id);
  return opcoes.map((o) => {
    const on = ligado(o.id);
    // Formato pinta por estilo (a lista é dinâmica); tipo tem classe fixa.
    const estilo = prefixo === "fmt" && on
      ? ` style="background:${corFormato(o.id)};border-color:${corFormato(o.id)}"` : "";
    return `<button type="button" class="op ${prefixo}-${o.id}${on ? " on" : ""}"
            data-v="${o.id}" data-g="${prefixo}" aria-pressed="${on}"${estilo}>${o.nome}</button>`;
  }).join("");
}

/** Etiquetas do card: exatamente o que estiver marcado no topo. Nada marcado,
    nada de etiqueta — card sem classificação não ganha enfeite. */
export function tagsDe(card) {
  const t = [];
  if (card.tipo) t.push(`<span class="tag tag-${card.tipo}">${nomeTipo(card.tipo)}</span>`);
  if (card.formato) {
    const cor = corFormato(card.formato);
    t.push(`<span class="tag" style="color:${cor};background:${tinta(cor, 0.1)}">`
           + `${nomeFormato(card.formato)}</span>`);
  }
  return t.join("");
}
