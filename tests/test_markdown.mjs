/* Testes do formato de exportação em markdown.
   Roda com: node tests/test_markdown.mjs   (não entra na imagem Docker) */

import assert from "node:assert/strict";
import { cardParaMarkdown, loteParaMarkdown }
  from "../app/web/lab/static/js/markdown.js";
import { configurarFormatos, FORMATOS_PADRAO }
  from "../app/web/lab/static/js/opcoes.js";

let passou = 0;
const teste = (nome, fn) => {
  try { fn(); passou++; }
  catch (e) { console.error(`FALHOU: ${nome}\n  ${e.message}\n`); process.exitCode = 1; }
};

const card = {
  titulo: "Todo mundo erra a contagem de carboidrato",
  tipo: "conteudo",
  formato: "lofi",
  hook: "Você conta carboidrato errado e nem sabe.",
  fechamento: "Pesa uma semana. Só isso.",
  desenvolvimentos: [
    { texto: "A conta de olho erra por 30% pra mais ou pra menos." },
    { texto: "Quem pesa a comida por duas semanas calibra o olho." },
  ],
};

teste("card completo sai pronto pra colar no WhatsApp", () => {
  const md = cardParaMarkdown(card);
  assert.equal(md, `*IDEIA CENTRAL*
Todo mundo erra a contagem de carboidrato

*Tipo:* Conteúdo · *Formato:* Lo-fi

*HOOK*
Você conta carboidrato errado e nem sabe.

A conta de olho erra por 30% pra mais ou pra menos.

Quem pesa a comida por duas semanas calibra o olho.

*FECHAMENTO*
Pesa uma semana. Só isso.
`);
});

teste("negrito é sempre um asterisco só", () => {
  const md = cardParaMarkdown({
    ...card, titulo_tela: "na tela",
    referencias: [{ url: "https://a.com" }], reacoes: [{ url: "https://b.com" }],
  });
  assert.ok(!md.includes("**"), "** sairia literal no WhatsApp");
  assert.ok(!md.includes("#"), "cabeçalho de markdown apareceria cru no WhatsApp");
});

teste("desenvolvimentos não têm rótulo nem numeração", () => {
  const md = cardParaMarkdown(card);
  assert.ok(!md.includes("DESENVOLVIMENTO"), "não pode rotular desenvolvimento");
  assert.ok(!/^\s*\d\./m.test(md), "não pode numerar desenvolvimento");
});

teste("hook e fechamento são rotulados", () => {
  const md = cardParaMarkdown(card);
  assert.ok(md.includes("*HOOK*"));
  assert.ok(md.includes("*FECHAMENTO*"));
});

teste("desenvolvimentos vazios são descartados", () => {
  const md = cardParaMarkdown({
    ...card,
    desenvolvimentos: [{ texto: "um" }, { texto: "   " }, { texto: "" }, { texto: "dois" }],
  });
  assert.ok(md.includes("um\n\ndois"), "os vazios não podem virar parágrafo em branco");
  assert.ok(!md.includes("\n\n\n"), "não pode sobrar linha em branco dupla");
});

teste("ordem dos desenvolvimentos é preservada", () => {
  const md = cardParaMarkdown({
    ...card,
    desenvolvimentos: [{ texto: "primeiro" }, { texto: "segundo" }, { texto: "terceiro" }],
  });
  assert.ok(md.indexOf("primeiro") < md.indexOf("segundo"));
  assert.ok(md.indexOf("segundo") < md.indexOf("terceiro"));
});

teste("metadados podem ser desligados", () => {
  const md = cardParaMarkdown(card, { incluirTipoFormato: false });
  assert.ok(!md.includes("*Tipo:*"));
  assert.ok(md.startsWith("*IDEIA CENTRAL*\nTodo mundo"));
});

teste("sem lacunas marcadas, campo vazio simplesmente não aparece", () => {
  const md = cardParaMarkdown({ titulo: "Só a ideia", desenvolvimentos: [] },
                              { incluirTipoFormato: false });
  assert.equal(md, "*IDEIA CENTRAL*\nSó a ideia\n");
});

teste("com lacunas marcadas, os buracos ficam explícitos", () => {
  const md = cardParaMarkdown({ titulo: "Só a ideia", desenvolvimentos: [] },
                              { incluirTipoFormato: false, marcarLacunas: true });
  assert.ok(md.includes("_[falta o hook]_"));
  assert.ok(md.includes("_[falta desenvolvimento]_"));
  assert.ok(md.includes("_[falta o fechamento]_"));
});

teste("formato criado pelo Pedro sai com o nome, não com travessão", () => {
  // Regressão: markdown.js tinha o próprio mapa com os quatro formatos
  // originais, então um formato novo saía como "—" enquanto aparecia certo no
  // card. Os nomes precisam vir da mesma fonte que a lista configurada.
  configurarFormatos([...FORMATOS_PADRAO, { id: "react", nome: "React" }]);
  const md = cardParaMarkdown({ ...card, tipo: "anuncio", formato: "react" });
  assert.ok(md.includes("*Tipo:* Anúncio · *Formato:* React"));
  configurarFormatos(FORMATOS_PADRAO);
});

teste("formato removido da lista ainda sai com o id, nunca vazio", () => {
  const md = cardParaMarkdown({ ...card, formato: "carrossel" });
  assert.ok(md.includes("*Formato:* carrossel"));
});

teste("tipo e formato indefinidos viram travessão simples", () => {
  const md = cardParaMarkdown({ titulo: "x", tipo: null, formato: null, desenvolvimentos: [] });
  assert.ok(md.includes("*Tipo:* — · *Formato:* —"));
});

teste("nada de status, data ou id no corpo", () => {
  const md = cardParaMarkdown({
    ...card, id: 42, status: "publicado", publicado_em: "2026-08-28T12:00:00Z", ordem: 3,
  });
  for (const ruido of ["42", "publicado", "2026-08", "status", "ordem"]) {
    assert.ok(!md.includes(ruido), `"${ruido}" é ruído pra quem está de fora`);
  }
});

teste("lote separa os cards por ---", () => {
  const md = loteParaMarkdown([card, { ...card, titulo: "Segundo" }]);
  assert.equal((md.match(/^---$/gm) || []).length, 1);
  assert.ok(md.indexOf("Todo mundo") < md.indexOf("Segundo"));
});

teste("lote de um card não ganha separador", () => {
  assert.ok(!loteParaMarkdown([card]).includes("---"));
});

teste("aceita desenvolvimento como texto puro", () => {
  const md = cardParaMarkdown({ titulo: "x", desenvolvimentos: ["direto como string"] },
                              { incluirTipoFormato: false });
  assert.ok(md.includes("direto como string"));
});

/* ─── Texto na tela ─── */

teste("com a chave ligada e texto escrito, sai depois do hook", () => {
  const md = cardParaMarkdown({
    ...card, tela_ativa: true, titulo_tela: "NPH ainda é o que o SUS entrega" });
  assert.ok(md.includes("*TEXTO NA TELA*\nNPH ainda é o que o SUS entrega"));
  assert.ok(md.indexOf("*HOOK*") < md.indexOf("*TEXTO NA TELA*"));
  assert.ok(md.indexOf("*TEXTO NA TELA*") < md.indexOf("A conta de olho"));
});

teste("chave desligada: a seção não existe, mesmo com texto guardado", () => {
  assert.ok(!cardParaMarkdown(card).includes("TEXTO NA TELA"));
  const md = cardParaMarkdown({ ...card, tela_ativa: false, titulo_tela: "sobrou daqui" });
  assert.ok(!md.includes("TEXTO NA TELA"));
  assert.ok(!md.includes("sobrou daqui"), "texto desligado não vaza pro roteiro");
});

teste("chave ligada e vazia vira pendência explícita", () => {
  for (const vazio of ["", "   ", undefined]) {
    const md = cardParaMarkdown({ ...card, tela_ativa: true, titulo_tela: vazio });
    assert.ok(md.includes("*TEXTO NA TELA*\n_[falta o texto na tela]_"),
              `vazio ${JSON.stringify(vazio)} deveria marcar a falta`);
  }
});

teste("a pendência do texto na tela não depende de marcar lacunas", () => {
  // Ligar a chave já foi o Pedro dizendo que este vídeo vai ter texto na tela.
  const md = cardParaMarkdown({ ...card, tela_ativa: true, titulo_tela: "" },
                              { marcarLacunas: false });
  assert.ok(md.includes("_[falta o texto na tela]_"));
});

/* ─── Referências e reação ─── */

const comLinks = {
  ...card,
  referencias: [
    { url: "https://pubmed.gov/123", nota: "meta-análise de 2025" },
    { url: "https://sbd.org.br/x", nota: "" },
  ],
  reacoes: [{ url: "https://youtube.com/watch?v=abc", nota: "reagir aos 2:10" }],
};

teste("links saem depois do roteiro, com rótulo", () => {
  const md = cardParaMarkdown(comLinks);
  assert.ok(md.indexOf("*FECHAMENTO*") < md.indexOf("*REFERÊNCIAS*"),
            "material de apoio vem depois do roteiro");
  assert.ok(md.includes("- https://pubmed.gov/123 — meta-análise de 2025"));
  assert.ok(md.includes("- https://sbd.org.br/x"), "sem nota, sai só a URL");
  assert.ok(md.includes("*REAGIR A*"));
  assert.ok(md.includes("- https://youtube.com/watch?v=abc — reagir aos 2:10"));
});

teste("sem link nenhum, nenhuma seção aparece", () => {
  const md = cardParaMarkdown(card);
  assert.ok(!md.includes("REFERÊNCIAS"));
  assert.ok(!md.includes("REAGIR A"));
});

teste("links vazios não geram bullet", () => {
  const md = cardParaMarkdown({
    ...card, referencias: [{ url: "  ", nota: "" }, { url: "", nota: "" }],
  });
  assert.ok(!md.includes("REFERÊNCIAS"));
});

teste("dá pra exportar sem os links", () => {
  const md = cardParaMarkdown(comLinks, { incluirLinks: false });
  assert.ok(!md.includes("REFERÊNCIAS"));
  assert.ok(!md.includes("pubmed"));
  assert.ok(md.includes("*HOOK*"), "o roteiro continua inteiro");
});

teste("só uma das listas preenchida traz só a seção dela", () => {
  const md = cardParaMarkdown({ ...card, reacoes: [{ url: "https://x.com" }] });
  assert.ok(!md.includes("REFERÊNCIAS"));
  assert.ok(md.includes("*REAGIR A*"));
});

console.log(`${passou} testes de markdown passaram`);
