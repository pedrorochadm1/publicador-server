/* Fila local da captura relâmpago.

   A ideia é salva localmente e sobe quando houver rede. Uso localStorage e não
   IndexedDB de propósito: a escrita é síncrona, então uma ideia digitada meio
   segundo antes do app ser fechado não se perde numa transação pendente.

   A segurança contra duplicata é do servidor: cada item carrega um client_uuid
   com índice UNIQUE do outro lado, então reenviar é inofensivo. O item só sai
   da fila depois que o POST volta 2xx. */

import { post, uuid, SemRede } from "./api.js";

const CHAVE = "lab_fila";

function ler() {
  try { return JSON.parse(localStorage.getItem(CHAVE) || "[]"); }
  catch (e) { return []; }
}

function gravar(itens) {
  try { localStorage.setItem(CHAVE, JSON.stringify(itens)); }
  catch (e) { /* cota estourada: o card já foi pro servidor ou se perde mesmo */ }
}

export function enfileirar(titulo) {
  const item = { client_uuid: uuid(), titulo, em: new Date().toISOString() };
  gravar([...ler(), item]);
  return item;
}

export function remover(client_uuid) {
  gravar(ler().filter((i) => i.client_uuid !== client_uuid));
}

/** Tenta subir tudo que está pendente. Devolve os cards criados no servidor. */
async function escoar() {
  const itens = ler();
  if (!itens.length) return [];
  const criados = [];
  for (const item of itens) {
    try {
      const card = await post("/lab/api/cards", {
        titulo: item.titulo,
        client_uuid: item.client_uuid,
      });
      remover(item.client_uuid);
      criados.push(card);
    } catch (e) {
      // Sem rede: para aqui e tenta de novo no próximo gatilho, sem perder nada.
      if (e instanceof SemRede) break;
      // Erro do servidor (título vazio, por exemplo): descarta pra fila não travar.
      if (e.status >= 400 && e.status < 500 && e.status !== 401) remover(item.client_uuid);
      else break;
    }
  }
  return criados;
}

/** Liga o escoamento aos momentos em que faz sentido tentar de novo. */
export function vigiar(aoEscoar) {
  const tentar = async () => {
    const criados = await escoar();
    if (criados.length) aoEscoar(criados);
  };
  window.addEventListener("online", tentar);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) tentar(); });
  return tentar;
}
