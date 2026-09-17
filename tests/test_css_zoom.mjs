/* Impede o auto-zoom do Safari no iOS.

   O Safari amplia a página ao focar qualquer campo com fonte menor que 16px, e
   a ampliação deixa a página arrastável na horizontal — o app "samba" na tela.
   Não existe como desligar isso pelo navegador: a única defesa é nenhum campo
   ficar abaixo de 16px.

   Este teste varre o CSS e falha se alguma regra que atinge campo de texto
   definir font-size menor que o mínimo. Densidade se ajusta pelo padding.

   Roda com: node tests/test_css_zoom.mjs */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const RAIZ = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const CSS = path.join(RAIZ, "app/web/lab/static/css");
const MINIMO = 16;

// Elementos que abrem teclado. <button> não conta: não recebe digitação.
const CAMPO = /(^|[\s,>+~(])(input|textarea|select)([\s.:[,)]|$)/i;

/* Nomear o elemento não é a única forma de atingir um campo: `.nota { font-size:
   12px }` valia num <input> e passou batido aqui por meses, ampliando o app
   toda vez que o Pedro tocava na nota de uma referência.

   Então a lista de classes de campo é lida do próprio front: todo class= que
   aparece num <input>, <textarea> ou <select>, no shell e nos templates do JS.
   Se uma classe dessas aparecer numa regra com fonte pequena, é problema. Uma
   classe usada em campo E em texto comum (era o caso de `.nota`) não pode
   existir: renomeie uma das duas, senão o teste acusa com razão. */
const FONTES = [path.join(RAIZ, "app/web/lab/index.html")];
const JS = path.join(RAIZ, "app/web/lab/static/js");
for (const f of fs.readdirSync(JS).filter((f) => f.endsWith(".js"))) FONTES.push(path.join(JS, f));

const CLASSES_DE_CAMPO = new Set();
for (const arquivo of FONTES) {
  const src = fs.readFileSync(arquivo, "utf8");
  for (const tag of src.matchAll(/<(?:input|textarea|select)\b[^>]*?class="([^"]*)"/gi)) {
    // Tokens com ${} são interpolação de template: não dá pra saber a classe.
    for (const cls of tag[1].split(/\s+/)) {
      if (/^[a-zA-Z][\w-]*$/.test(cls)) CLASSES_DE_CAMPO.add(cls);
    }
  }
}

const daClasse = (seletor) =>
  [...CLASSES_DE_CAMPO].some((c) => new RegExp(`\\.${c}(?![\\w-])`).test(seletor));

const problemas = [];

for (const arquivo of fs.readdirSync(CSS).filter((f) => f.endsWith(".css"))) {
  const bruto = fs.readFileSync(path.join(CSS, arquivo), "utf8");
  // Fora os comentários: eles citam <select> e <textarea> em prosa e criariam
  // falso positivo.
  const texto = bruto.replace(/\/\*[\s\S]*?\*\//g, "");

  for (const bloco of texto.matchAll(/([^{}]+)\{([^}]*)\}/g)) {
    const seletor = bloco[1].trim();
    const corpo = bloco[2];
    if (!CAMPO.test(seletor) && !daClasse(seletor)) continue;
    const m = corpo.match(/font-size:\s*([\d.]+)px/);
    if (m && parseFloat(m[1]) < MINIMO) {
      problemas.push(`${arquivo}: "${seletor}" usa ${m[1]}px (mínimo ${MINIMO}px)`);
    }
  }
}

// O shell também não pode reintroduzir zoom por estilo em linha.
const shell = fs.readFileSync(path.join(RAIZ, "app/web/lab/index.html"), "utf8");
for (const m of shell.matchAll(/<(?:input|textarea|select)[^>]*style="[^"]*font-size:\s*([\d.]+)px/gi)) {
  if (parseFloat(m[1]) < MINIMO) problemas.push(`index.html: campo com ${m[1]}px em linha`);
}

if (problemas.length) {
  console.error("Campos que causariam zoom no iOS:\n  " + problemas.join("\n  "));
  process.exit(1);
}
console.log("nenhum campo abaixo de 16px — sem auto-zoom no iOS");
