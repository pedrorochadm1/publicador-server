/* Service worker do Lab DM1.

   A VERSAO é injetada pelo Python a partir de LAB_VERSAO (app/lab_web.py). Um
   número, um arquivo: bumpar lá invalida todo o cache aqui.

   Estratégia por tipo de recurso, e o porquê de cada uma:

   - /lab/static/**  → cache-first. São imutáveis dentro de uma versão (a URL
     carrega ?v=VERSAO), então servir do cache é sempre correto e instantâneo.

   - shell (/, /lab, /automacoes) → network-first com cache de reserva. NUNCA
     cache-first: shell velho conversando com API nova é a receita clássica de
     app quebrado depois do deploy.

   - /lab/api/** e /insta/api/** → o SW nem encosta. Servir uma lista de
     automações obsoleta do cache seria regressão num sistema que responde
     comentário e manda direct de verdade. */

const VERSAO = "__V__";
const CACHE = "lab-" + VERSAO;

const SHELL = ["/lab", "/automacoes"];

// Todos os módulos precisam entrar: app.js importa os outros, e um import que
// falta derruba o app inteiro offline.
const ESTATICOS = [
  "css/base.css", "css/lab.css", "css/automacoes.css",
  "js/app.js", "js/api.js", "js/board.js", "js/editor.js", "js/regua.js",
  "js/painel.js", "js/markdown.js", "js/fila.js", "js/onboarding.js",
  "js/logo.js", "js/automacoes.js", "js/opcoes.js", "js/ajustes.js",
  "icone-192.png", "apple-touch-180.png",
].map((p) => `/lab/static/${p}`);

self.addEventListener("install", (e) => {
  e.waitUntil((async () => {
    const c = await caches.open(CACHE);
    // cache: "reload" é OBRIGATÓRIO. Sem ele o cache.add() pode buscar o arquivo
    // no cache HTTP do navegador, e a versão nova do app passa a servir CSS/JS
    // antigo indefinidamente — foi o que aconteceu entre a v7 e a v8, com o
    // CSS certo no servidor e o errado na tela.
    // Falha de um item não pode abortar a instalação inteira, daí o allSettled.
    await Promise.allSettled(
      [...SHELL, ...ESTATICOS].map(async (u) => {
        const r = await fetch(new Request(u, { cache: "reload" }));
        if (r.ok) await c.put(u, r);
      }));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    const nomes = await caches.keys();
    await Promise.all(nomes.filter((n) => n !== CACHE).map((n) => caches.delete(n)));
    await self.clients.claim();
  })());
});

const ehShell = (url) =>
  url.pathname === "/" || url.pathname === "/lab" || url.pathname === "/automacoes";

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // API nunca vem do cache.
  if (url.pathname.startsWith("/lab/api/") || url.pathname.startsWith("/insta/api/")
      || url.pathname.startsWith("/webhook/")) return;

  // O próprio service worker e o manifest sempre da rede.
  if (url.pathname === "/sw.js" || url.pathname === "/manifest.webmanifest") return;

  if (url.pathname.startsWith("/lab/static/")) {
    e.respondWith((async () => {
      const cache = await caches.open(CACHE);
      // ignoreSearch porque o shell pede app.js?v=N mas os imports internos dos
      // módulos vêm sem query. O cache-bust real vem do nome do cache, que já
      // carrega a versão — então casar ignorando a query é seguro.
      const guardado = await cache.match(req, { ignoreSearch: true });
      if (guardado) return guardado;
      try {
        const r = await fetch(req);
        if (r.ok) cache.put(req, r.clone());
        return r;
      } catch (err) {
        return guardado || Response.error();
      }
    })());
    return;
  }

  if (ehShell(url)) {
    e.respondWith((async () => {
      const cache = await caches.open(CACHE);
      try {
        const r = await fetch(req);
        if (r.ok) cache.put("/lab", r.clone());
        return r;
      } catch (err) {
        // Offline: abre com o shell guardado. O app desenha o board com o que
        // tiver e a captura cai na fila local.
        return (await cache.match("/lab")) || (await cache.match(req)) || Response.error();
      }
    })());
  }
});
