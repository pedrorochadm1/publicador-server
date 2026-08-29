/* Bootstrap do Lab DM1: login, navegação entre as duas abas e boot do estado.

   A rota vem do pathname (/ e /lab abrem o Laboratório, /automacoes abre as
   automações) e é trocada com history.pushState, sem recarregar a página —
   trocar de aba não pode custar um round-trip nem perder o estado do board. */

import { get, post, aviso, quandoPerderSessao } from "./api.js";
import { logo, ICONE_LAB, ICONE_AUTO } from "./logo.js";
import * as board from "./board.js";
import * as automacoes from "./automacoes.js";
import * as regua from "./regua.js";
import { precisaOnboarding, abrirOnboarding } from "./onboarding.js";
import { fecharPainel } from "./painel.js";

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

function ligarNavegacao() {
  $("#marca").innerHTML = logo(34);
  $("#ic-lab").innerHTML = ICONE_LAB;
  $("#ic-auto").innerHTML = ICONE_AUTO;
  document.querySelectorAll(".tabbar [data-aba]").forEach((b) => {
    b.querySelector(".ic").innerHTML = b.dataset.aba === "lab" ? ICONE_LAB : ICONE_AUTO;
  });
  document.querySelectorAll("[data-aba]").forEach((b) => {
    b.onclick = () => irPara(b.dataset.aba);
  });
  $("#sair").onclick = async () => {
    try { await post("/lab/api/sair"); } catch (e) { /* segue */ }
    location.href = "/lab";
  };
  window.addEventListener("popstate", () => irPara(abaDoCaminho(), { push: false }));
}

/* ─────────────────────────── Boot ─────────────────────────── */

async function iniciar() {
  $("#carregando").hidden = false;
  try {
    estadoInicial = await get("/lab/api/estado");
  } catch (e) {
    if (e.status === 401) return;           // quandoPerderSessao já abriu o login
    $("#carregando").textContent = "Não deu pra carregar. Verifique a conexão.";
    return;
  }
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
