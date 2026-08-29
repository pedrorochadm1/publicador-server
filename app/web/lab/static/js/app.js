/* Bootstrap do Lab DM1: login, navegação entre as duas abas e boot do estado.

   A rota vem do pathname (/ e /lab abrem o Laboratório, /automacoes abre as
   automações) e é trocada com history.pushState, sem recarregar a página —
   trocar de aba não pode custar um round-trip nem perder o estado do board. */

import { get, post, aviso, quandoPerderSessao } from "./api.js";
import { logo, ICONE_LAB, ICONE_AUTO, ICONE_AJUSTES } from "./logo.js";
import * as board from "./board.js";
import * as automacoes from "./automacoes.js";
import * as regua from "./regua.js";
import { precisaOnboarding, abrirOnboarding } from "./onboarding.js";
import { fecharPainel } from "./painel.js";
import { abrirAjustes } from "./ajustes.js";

const $ = (s) => document.querySelector(s);


let abaAtual = null;
let estadoInicial = null;
let labMontado = false;

/* ─────────────────────────── Login ─────────────────────────── */

function mostrarLogin(mensagem = "") {
  $("#carregando").hidden = true;
  $("#app").hidden = true;
  $("#login").hidden = false;
  $("#login-erro").textContent = mensagem;
  $("#marca-login").innerHTML = logo(56);
  setTimeout(() => $("#senha").focus(), 80);
}

function ligarLogin() {
  $("#login-form").onsubmit = async (e) => {
    e.preventDefault();
    const senha = $("#senha").value;
    try {
      await post("/lab/api/login", { senha });
      $("#senha").value = "";
      $("#login").hidden = true;
      await iniciar();
    } catch (err) {
      $("#login-erro").textContent = err.status === 429
        ? "Muitas tentativas. Espere alguns minutos."
        : "Senha incorreta.";
    }
  };
}

/* ─────────────────────────── Navegação ─────────────────────────── */

function abaDoCaminho() {
  return location.pathname.startsWith("/automacoes") ? "automacoes" : "lab";
}

async function irPara(aba, { push = true } = {}) {
  if (aba === abaAtual) return;
  fecharPainel();

  // A aba de automações mantém dois ciclos batendo na API: desmontar é
  // obrigatório, senão eles continuam rodando com a aba fechada.
  if (abaAtual === "automacoes") automacoes.desmontar();

  abaAtual = aba;
  if (push) history.pushState({ aba }, "", aba === "lab" ? "/lab" : "/automacoes");
  document.querySelectorAll("[data-aba]").forEach((b) =>
    b.classList.toggle("ativa", b.dataset.aba === aba));

  const tela = $("#tela");
  if (aba === "lab") {
    regua.desenhar(estadoInicial.regua);
    tela.innerHTML = "";
    await board.montar(tela, estadoInicial);
    labMontado = true;
  } else {
    regua.esconder();
    tela.innerHTML = "";
    try { await automacoes.montar(tela); }
    catch (e) { tela.innerHTML = `<p class="vazio erro-aba">Não deu pra carregar as automações agora.</p>`; }
  }
}

const ICONES = { lab: ICONE_LAB, automacoes: ICONE_AUTO };

function ligarNavegacao() {
  $("#marca").innerHTML = logo(32);
  $("#ic-lab").innerHTML = ICONE_LAB;
  $("#ic-auto").innerHTML = ICONE_AUTO;
  $("#ic-ajustes").innerHTML = ICONE_AJUSTES;
  document.querySelectorAll(".tabbar button").forEach((b) => {
    b.querySelector(".ic").innerHTML = b.dataset.ajustes ? ICONE_AJUSTES : ICONES[b.dataset.aba];
  });
  document.querySelectorAll("[data-aba]").forEach((b) => {
    b.onclick = () => irPara(b.dataset.aba);
  });
  // Ajustes é painel, não aba: abre por cima e não troca de rota.
  document.querySelectorAll("[data-ajustes]").forEach((b) => {
    b.onclick = () => abrirAjustes(estadoInicial.config, (nova) => {
      estadoInicial.config = nova;
      if (abaAtual === "lab") board.reconfigurar(nova);
    });
  });
  window.addEventListener("popstate", () => irPara(abaDoCaminho(), { push: false }));
}

/* ─────────────────────────── Boot ─────────────────────────── */

async function limparCaches() {
  try {
    if (window.caches) {
      const nomes = await caches.keys();
      await Promise.all(nomes.map((n) => caches.delete(n)));
    }
    if ("serviceWorker" in navigator) {
      const regs = await navigator.serviceWorker.getRegistrations();
      await Promise.all(regs.map((r) => r.unregister()));
    }
  } catch (e) { /* recarregar já ajuda mesmo sem limpar tudo */ }
}

async function iniciar() {
  $("#carregando").hidden = false;
  try {
    estadoInicial = await get("/lab/api/estado");
  } catch (e) {
    if (e.status === 401) return;           // quandoPerderSessao já abriu o login
    $("#carregando").textContent = "Não deu pra carregar. Verifique a conexão.";
    return;
  }
  // Rede de segurança contra cache velho: se o shell que está rodando não é da
  // mesma versão que o servidor, limpa tudo e recarrega uma vez. O guard no
  // sessionStorage impede laço infinito se a limpeza não resolver.
  if (estadoInicial.versao && window.LAB_V && estadoInicial.versao !== window.LAB_V
      && !sessionStorage.getItem("lab_relimpou")) {
    sessionStorage.setItem("lab_relimpou", "1");
    await limparCaches();
    location.reload();
    return;
  }
  sessionStorage.removeItem("lab_relimpou");

  $("#login").hidden = true;
  $("#app").hidden = false;
  $("#carregando").hidden = true;

  ligarNavegacao();
  abaAtual = null;
  await irPara(abaDoCaminho(), { push: false });

  // Partida a frio: saldo zero sem nenhuma publicação é verde falso.
  if (precisaOnboarding(estadoInicial.config, estadoInicial.total_publicacoes)) {
    abrirOnboarding((novaRegua) => { estadoInicial.regua = novaRegua; });
  }
}

quandoPerderSessao(() => {
  if (labMontado) aviso("Sessão expirada.");
  mostrarLogin();
});

ligarLogin();

(async () => {
  let sessao;
  try {
    sessao = await get("/lab/api/sessao");
  } catch (e) {
    $("#carregando").textContent = "Sem conexão com o servidor.";
    return;
  }
  if (sessao.logado) await iniciar();
  else mostrarLogin();
})();
