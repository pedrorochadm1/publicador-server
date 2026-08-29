/* Tipo e formato num lugar só, e o chip clicável que os desenha.

   Eram <select> na v1. No celular um select abre a roleta do iOS e esconde as
   opções; como são poucas e curtas, chip clicável resolve num toque e deixa
   tudo visível de uma vez. */

export const TIPOS = [
  { id: "conteudo", nome: "Conteúdo" },
  { id: "anuncio", nome: "Anúncio" },
];

export const FORMATOS = [
  { id: "lofi", nome: "Lo-fi" },
  { id: "slide", nome: "Slide" },
  { id: "vlog", nome: "Vlog" },
  { id: "documentario", nome: "Documentário" },
];

export const nomeFormato = (f) => (FORMATOS.find((x) => x.id === f) || {}).nome || f || "";
export const nomeTipo = (t) => (TIPOS.find((x) => x.id === t) || {}).nome || t || "";

/** `ativo` aceita um valor (escolha única) ou um array (filtro multi). */
export function chipsDe(opcoes, ativo, prefixo) {
  const ligado = (id) => (Array.isArray(ativo) ? ativo.includes(id) : ativo === id);
  return opcoes.map((o) => `
    <button type="button" class="op ${prefixo}-${o.id}${ligado(o.id) ? " on" : ""}"
            data-v="${o.id}" data-g="${prefixo}"
            aria-pressed="${ligado(o.id)}">${o.nome}</button>`).join("");
}
