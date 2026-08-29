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

const problemas = [];

for (const arquivo of fs.readdirSync(CSS).filter((f) => f.endsWith(".css"))) {
  const bruto = fs.readFileSync(path.join(CSS, arquivo), "utf8");
  // Fora os comentários: eles citam <select> e <textarea> em prosa e criariam
  // falso positivo.
  const texto = bruto.replace(/\/\*[\s\S]*?\*\//g, "");

  for (const bloco of texto.matchAll(/([^{}]+)\{([^}]*)\}/g)) {
    const seletor = bloco[1].trim();
    const corpo = bloco[2];
    if (!CAMPO.test(seletor)) continue;
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
