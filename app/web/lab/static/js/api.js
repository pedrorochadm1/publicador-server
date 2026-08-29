/* Camada de rede do Lab.

   Duas responsabilidades além do fetch: tratar 401 abrindo o login DENTRO do
   app (nunca um location.reload, que no PWA em standalone joga o Pedro pra
   fora), e sinalizar quando a rede caiu pra que a captura vá pra fila local. */

let aoPerderSessao = () => {};

export function quandoPerderSessao(fn) { aoPerderSessao = fn; }

export class ErroApi extends Error {
  constructor(status, corpo) {
    super(typeof corpo === "string" ? corpo : (corpo?.mensagem || `HTTP ${status}`));
    this.status = status;
    this.corpo = corpo;
    this.codigo = corpo?.codigo || null;
  }
}

export class SemRede extends Error {}

async function pedir(rota, opts = {}) {
  let r;
  try {
    r = await fetch(rota, {
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      ...opts,
    });
  } catch (e) {
    throw new SemRede("sem conexão");
  }
  if (r.status === 401) {
    aoPerderSessao();
    throw new ErroApi(401, "Sessão expirada.");
  }
  if (!r.ok) {
    let corpo = null;
    try { corpo = (await r.json()).detail; } catch (e) { corpo = await r.text().catch(() => ""); }
    throw new ErroApi(r.status, corpo);
  }
  if (r.status === 204) return null;
  return r.json();
}

export const get = (rota) => pedir(rota);
export const post = (rota, corpo) =>
  pedir(rota, { method: "POST", body: JSON.stringify(corpo ?? {}) });
export const patch = (rota, corpo) =>
  pedir(rota, { method: "PATCH", body: JSON.stringify(corpo ?? {}) });
export const put = (rota, corpo) =>
  pedir(rota, { method: "PUT", body: JSON.stringify(corpo ?? {}) });
export const del = (rota) => pedir(rota, { method: "DELETE" });

/* ─────────────────────────── Helpers de UI ─────────────────────────── */

let timerAviso = null;

export function aviso(texto) {
  const el = document.getElementById("aviso");
  if (!el) return;
  el.textContent = texto;
  el.classList.add("ver");
  clearTimeout(timerAviso);
  timerAviso = setTimeout(() => el.classList.remove("ver"), 2600);
}

export const esc = (t) =>
  String(t ?? "").replace(/[<>&"]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" }[c]));

export function data(iso, comHora = true) {
  if (!iso) return "";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  return d.toLocaleString("pt-BR", comHora
    ? { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }
    : { day: "2-digit", month: "2-digit", year: "2-digit" });
}

export function uuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return "u-" + Date.now() + "-" + Math.random().toString(36).slice(2, 10);
}
