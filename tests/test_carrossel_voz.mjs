/* As checagens de voz e de estrutura que o editor mostra antes de publicar.

   Elas são a última peneira entre o que a IA escreveu e o que vai pro ar, então
   cada regra aqui corresponde a uma que já custou um post ruim ou está escrita
   no guia de carrosséis.

   Roda com: node tests/test_carrossel_voz.mjs */

import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const RAIZ = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const { avisosVoz } = await import(
  pathToFileURL(path.join(RAIZ, "app/web/lab/static/js/carrossel.js")));

let ok = 0;
const caso = (nome, fn) => {
  try { fn(); ok++; }
  catch (e) { console.error(`FALHOU: ${nome}\n  ${e.message}\n`); process.exitCode = 1; }
};

const doc = (slides, legenda = "Já aconteceu com você?") => ({
  slides: slides.map((s) => (typeof s === "string" ? { texto: s, texto_abaixo: "" } : s)),
  legenda,
});

const bom = doc(
  ["Basal alta demais", "A glicemia cai sozinha em jejum.", "Salva pra quando acontecer."],
);

caso("carrossel dentro das regras não gera aviso nenhum", () => {
  assert.deepEqual(avisosVoz(bom), []);
});

/* ─── Capa ─── */

caso("capa acima de 7 palavras é apontada", () => {
  const d = doc(["Basal demais existe e muita gente com DM1 vive corrigindo comida com ela",
                 "Dois", "Três"]);
  const a = avisosVoz(d);
  assert.equal(a.length, 1);
  assert.match(a[0], /Capa com 13 palavras/);
  assert.match(a[0], /3 a 7/, "o aviso precisa dizer o alvo, não só reclamar");
});

caso("capa de exatamente 7 palavras passa", () => {
  assert.deepEqual(avisosVoz(doc(["Um dois tres quatro cinco seis sete", "Dois"])), []);
});

caso("pontuação não conta como palavra na capa", () => {
  assert.deepEqual(avisosVoz(doc(["Basal alta: o sinal da madrugada", "Dois"])), []);
});

/* ─── CTA do último slide ─── */

caso("último slide com duas ações é apontado", () => {
  const a = avisosVoz(doc(["Capa curta", "Meio", "Salva esse post e comenta aqui embaixo."]));
  assert.equal(a.length, 1);
  assert.match(a[0], /2 ações/);
});

caso("último slide com uma ação só passa", () => {
  assert.deepEqual(avisosVoz(doc(["Capa curta", "Meio", "Salva pra quando acontecer."])), []);
});

caso("a ação é procurada no último slide, não no meio", () => {
  // "comenta" no meio do carrossel é texto comum, não CTA.
  const d = doc(["Capa curta", "Todo mundo comenta isso na consulta.", "Salva esse post."]);
  assert.deepEqual(avisosVoz(d), []);
});

/* ─── Legenda ─── */

caso("legenda longa demais é apontada com o número", () => {
  const leg = "Quando sua glicose cai sozinha, costuma ser mais comum de madrugada, "
            + "em jejum ou depois de pular refeição?";
  const a = avisosVoz(doc(["Capa curta", "Meio"], leg));
  assert.equal(a.length, 1);
  assert.match(a[0], /Legenda com 18 palavras/);
});

caso("legenda curta com uma pergunta passa", () => {
  assert.deepEqual(avisosVoz(doc(["Capa curta", "Meio"], "Já aconteceu com você?")), []);
});

caso("legenda vazia, sem pergunta, com duas perguntas ou com hashtag", () => {
  assert.deepEqual(avisosVoz(doc(["Capa", "Meio"], "")), ["Falta a legenda."]);
  assert.deepEqual(avisosVoz(doc(["Capa", "Meio"], "Isso acontece.")), ["Legenda sem pergunta."]);
  assert.deepEqual(avisosVoz(doc(["Capa", "Meio"], "E você? Já viu?")),
                   ["Legenda com mais de uma pergunta."]);
  assert.ok(avisosVoz(doc(["Capa", "Meio"], "E você? #dm1")).includes("Legenda com hashtag."));
});

/* ─── Regras antigas que não podem se perder ─── */

caso("travessão, hashtag e Arrasta continuam sendo apontados no slide", () => {
  const a = avisosVoz(doc(["Capa curta", "Isso é ruim — muito ruim.", "Arrasta pro lado", "Fim"]));
  assert.ok(a.some((x) => /Slide 2 tem travessão/.test(x)));
  assert.ok(a.some((x) => /Slide 3 tem "Arrasta"/.test(x)));
  assert.ok(avisosVoz(doc(["Capa", "Veja #dm1", "Fim"])).some((x) => /Slide 2 tem hashtag/.test(x)));
});

console.log(`${ok} casos de voz passaram`);
