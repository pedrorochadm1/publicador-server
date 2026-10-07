/* Motor de layout do slide: quebra de linha, negrito, "não cabe" e recorte.

   Roda com: node tests/test_carrossel_render.mjs */

import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const RAIZ = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const R = await import(pathToFileURL(path.join(RAIZ, "app/web/lab/static/js/carrossel_render.js")));

// Medidor fake: 20px por caractere, 22px em negrito.
const medir = (t, b) => t.length * (b ? 22 : 20);
let ok = 0;
const caso = (nome, fn) => { fn(); ok++; };

caso("parágrafos e negrito", () => {
  const p = R.paragrafos("Trocar a **insulina basal** mata.\n\nSegundo.");
  assert.equal(p.length, 2);
  assert.deepEqual(p[0][0], [
    { t: "Trocar a ", b: false }, { t: "insulina basal", b: true }, { t: " mata.", b: false }]);
});

caso("asterisco sem par vira texto", () => {
  const p = R.paragrafos("2 ** 3 é oito");
  assert.deepEqual(p[0][0].map((s) => s.t).join(""), "2 ** 3 é oito");
  assert.ok(p[0][0].every((s) => !s.b));
});

caso("quebra simples é linha forçada", () => {
  const p = R.paragrafos("um\ndois");
  assert.equal(p[0].length, 2);
});

caso("texto vazio não gera bloco", () => {
  assert.deepEqual(R.paragrafos("  \n "), []);
  const lay = R.layout({ texto: "", texto_abaixo: "", header: false, imagem: null }, medir);
  assert.equal(lay.blocos.length, 0);
});

caso("quebra gulosa respeita a largura", () => {
  // 920px / 20px = 46 caracteres por linha.
  const palavra = "abcdefghij";                      // 10 chars = 200px
  const linhas = R.quebrar([{ t: Array(10).fill(palavra).join(" "), b: false }], medir);
  // 4 palavras + 3 espaços = 860 ≤ 920; a 5ª estoura.
  assert.equal(linhas[0].length, 4);
  assert.equal(linhas.length, 3);
  assert.equal(linhas[1][0].x, 0);
});

caso("palavra colada com estilo diferente não quebra no meio", () => {
  const segs = [{ t: "x".repeat(44) + " neg", b: false }, { t: "rito", b: true }];
  const linhas = R.quebrar(segs, medir);
  assert.equal(linhas.length, 2);
  assert.deepEqual(linhas[1].map((s) => s.t), ["neg", "rito"]);
  assert.equal(linhas[1][1].x, 60);
});

caso("blocos centralizados na vertical com gap de 40", () => {
  const lay = R.layout({ texto: "oi", header: true, imagem: { tamanho: "M" } }, medir);
  assert.deepEqual(lay.blocos.map((b) => b.tipo), ["header", "texto", "imagem"]);
  const altura = R.HEADER_H + R.ENTRELINHA + 473 + 2 * R.GAP;
  assert.ok(Math.abs(lay.altura - altura) < 1e-9);
  assert.ok(Math.abs(lay.blocos[0].y - (80 + (1190 - altura) / 2)) < 1e-9);
  assert.equal(lay.cabe, true);
});

caso("texto abaixo da imagem fica depois dela", () => {
  const lay = R.layout({ texto: "a", texto_abaixo: "Entenda 👉", imagem: { tamanho: "P" } }, medir);
  assert.deepEqual(lay.blocos.map((b) => b.tipo), ["header", "texto", "imagem", "texto"]);
});

caso("excesso detectado", () => {
  const longo = Array(40).fill("linha").join("\n");
  const lay = R.layout({ texto: longo, imagem: { tamanho: "GG" } }, medir);
  assert.equal(lay.cabe, false);
  assert.ok(lay.excesso > 0);
  assert.equal(lay.blocos[0].y, 80);
});

caso("tamanhos de imagem", () => {
  for (const [k, h] of Object.entries(R.TAMANHOS)) {
    const lay = R.layout({ header: false, imagem: { tamanho: k } }, medir);
    assert.equal(lay.blocos[0].h, h);
  }
});

caso("recorte cobre o slot sem borda", () => {
  // Imagem quadrada 1000×1000 num slot 920×473: escala por largura.
  const r = R.recorte(1000, 1000, 920, 473);
  assert.equal(r.dw, 920);
  assert.equal(r.dx, 0);
  assert.ok(r.dy <= 0 && r.dy + r.dh >= 473);
  // Centro no canto extremo é limitado: não pode aparecer fundo.
  const c = R.recorte(1000, 1000, 920, 473, 1, 0, 0);
  assert.equal(c.dy, 0);
  assert.equal(c.dx, 0);
  // Zoom 2 dobra o tamanho desenhado.
  assert.equal(R.recorte(1000, 1000, 920, 473, 2).dw, 1840);
});

console.log(`ok ${ok} casos`);
