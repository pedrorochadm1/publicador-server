/* Aba de automações: porte do painel que hoje vive em app/web/insta.html.

   Porte MECÂNICO de propósito. O backend não mudou nem uma linha — as rotas
   /insta/api/* são exatamente as mesmas — e todos os ids de campo (f_nome,
   f_palavras, f_dm_texto…) foram preservados, pra que o diff continue
   revisável linha a linha. As únicas mudanças são de encaixe:

     1. `$` busca dentro do container da aba, não no document inteiro;
     2. tudo virou montar(raiz) / desmontar();
     3. os dois setInterval são guardados e limpos no desmontar — senão
        continuariam batendo na API com a aba fechada;
     4. o 401 não dá mais location.reload: quem trata é o api.js, abrindo o
        login dentro do app (num PWA, reload joga o Pedro pra fora). */

import { get, post, put, del, esc, data, aviso } from "./api.js";

let raiz = null;
let automacoes = [], midias = [], atual = null;
let timerSalvar = null, salvando = false;
let intervalos = [];

const $ = (s) => raiz.querySelector(s);

const VAZIA = {
  nome: "", ativa: true, palavras: [], modo: "contem", escopo: "proximo", midia_id: null,
  respostas: [], respostas_sem_dm: [], dm_texto: "", botao_texto: "Participar", botao_url: "",
  responder_publico: true, enviar_dm: true, uma_vez_por_pessoa: true,
};

// Tetos da Meta. dmComBotao é o texto do template de botão; sem botão a private
// reply é texto puro e cabe mais. Botão: 20 chars. Comentário: 2200.
const LIM = { dmComBotao: 640, dmSemBotao: 1000, botao: 20, comentario: 2200 };

const api = async (rota, opts = {}) => {
  const metodo = (opts.method || "GET").toUpperCase();
  const corpo = opts.body ? JSON.parse(opts.body) : undefined;
  if (metodo === "GET") return get(rota);
  if (metodo === "POST") return post(rota, corpo);
  if (metodo === "PUT") return put(rota, corpo);
  if (metodo === "DELETE") return del(rota);
};

/* ─────────────────────────── Montagem ─────────────────────────── */

export async function montar(alvo) {
  raiz = alvo;
  raiz.innerHTML = `
    <div class="auto">
      <div class="auto-topo">
        <h1>Automações</h1>
        <span id="status" class="chip">carregando…</span>
      </div>
      <div class="auto-grade">
        <div class="col lista">
          <button class="bt" id="nova" type="button">+ Nova automação</button>
          <div id="cards"></div>
        </div>
        <div class="col">
          <div class="card" id="editor"></div>
          <div class="card">
            <h2>Histórico</h2>
            <div id="historico" class="vazio">Nada ainda.</div>
          </div>
        </div>
        <div class="col previa" id="previa"></div>
      </div>
    </div>`;

  $("#nova").onclick = () => {
    atual = JSON.parse(JSON.stringify(VAZIA));
    pintarLista();
    pintarEditor();
  };
  await inicio();
}

export function desmontar() {
  intervalos.forEach(clearInterval);
  intervalos = [];
  clearTimeout(timerSalvar);
  timerSalvar = null;
  atual = null;
  raiz = null;
}

/* ─────────────────────────── Lista ─────────────────────────── */

function rotuloEscopo(a) {
  if (a.escopo === "todos") return "Todos os posts";
  if (a.escopo === "midia") return "Post específico";
  return a.midia_id ? "Engatada no post" : "Aguardando a próxima publicação";
}

function pintarLista() {
  $("#cards").innerHTML = automacoes.map((a) => `
    <div class="card ${atual && atual.id === a.id ? "sel" : ""}" data-id="${a.id}">
      <h3><span class="ponto ${a.ativa ? "on" : ""}"></span>${esc(a.nome) || "Sem nome"}</h3>
      <div>${a.palavras.map((p) => `<span class="kw">${esc(p)}</span>`).join("")}</div>
      <div class="meta">${rotuloEscopo(a)}<br>
        ${a.contadores.acionamentos} acionamentos · ${a.contadores.dms} directs
        ${a.contadores.ultimo ? " · último em " + data(a.contadores.ultimo) : ""}</div>
    </div>`).join("") || `<p class="vazio">Nenhuma automação ainda.</p>`;
  raiz.querySelectorAll("#cards .card").forEach((el) =>
    (el.onclick = () => selecionar(automacoes.find((a) => a.id == el.dataset.id))));
}

/* ─────────────────────────── Editor ─────────────────────────── */

function pintarEditor() {
  const a = atual;
  if (!a) {
    $("#editor").innerHTML = `<p class="vazio">Escolha uma automação ou crie uma nova.</p>`;
    $("#previa").innerHTML = "";
    return;
  }
  $("#editor").innerHTML = `
    <h2>${a.id ? "Editar automação" : "Nova automação"}</h2>
    <label>Nome</label>
    <input type="text" id="f_nome" value="${esc(a.nome)}" placeholder="Ex.: consulta pública do sensor">
    <label class="sw"><input type="checkbox" id="f_ativa" ${a.ativa ? "checked" : ""}>Automação ligada</label>

    <div class="bloco">
      <label>Palavra-chave <span class="dica">separe por vírgula</span></label>
      <input type="text" id="f_palavras" value="${esc(a.palavras.join(", "))}" placeholder="sensor">
      <div class="linha">
        <div>
          <label>Como comparar</label>
          <select id="f_modo">
            <option value="contem" ${a.modo === "contem" ? "selected" : ""}>Comentário contém a palavra</option>
            <option value="exata" ${a.modo === "exata" ? "selected" : ""}>Comentário é exatamente a palavra</option>
          </select>
        </div>
        <div>
          <label>Onde vale</label>
          <select id="f_escopo">
            <option value="proximo" ${a.escopo === "proximo" ? "selected" : ""}>Próxima publicação</option>
            <option value="midia" ${a.escopo === "midia" ? "selected" : ""}>Post específico</option>
            <option value="todos" ${a.escopo === "todos" ? "selected" : ""}>Todos os posts</option>
          </select>
        </div>
      </div>
      <div id="escolha_post"></div>
      <label>Testar <span class="dica">escreve um comentário de exemplo</span></label>
      <div class="linha"><input type="text" id="f_teste" placeholder="quero o sensor"><span id="r_teste" class="chip" style="flex:none;align-self:center"></span></div>
    </div>

    <div class="bloco">
      <label class="sw"><input type="checkbox" id="f_pub" ${a.responder_publico ? "checked" : ""}>Responder no comentário</label>
      <label>Respostas <span class="dica">uma por linha; sorteia uma a cada comentário</span></label>
      <textarea id="f_respostas">${esc(a.respostas.join("\n"))}</textarea>
      <div class="conta" id="c_respostas"></div>
    </div>

    <div class="bloco">
      <label class="sw"><input type="checkbox" id="f_dm" ${a.enviar_dm ? "checked" : ""}>Mandar direct</label>
      <label>Mensagem do direct</label>
      <textarea id="f_dm_texto" style="min-height:64px" maxlength="${LIM.dmComBotao}">${esc(a.dm_texto)}</textarea>
      <div class="conta" id="c_dm_texto"></div>
      <div class="linha">
        <div><label>Texto do botão</label>
          <input type="text" id="f_botao" maxlength="${LIM.botao}" value="${esc(a.botao_texto)}">
          <div class="conta" id="c_botao"></div></div>
        <div><label>Link do botão</label>
          <input type="url" id="f_url" value="${esc(a.botao_url)}">
          <div class="conta" id="c_url"></div></div>
      </div>
      <label class="sw"><input type="checkbox" id="f_uma" ${a.uma_vez_por_pessoa ? "checked" : ""}>Só uma vez por pessoa</label>
      <label class="sw"><input type="checkbox" id="f_fb" ${a.facebook !== false ? "checked" : ""}>Valer também no Facebook <span class="dica">mesmo reel, post cruzado da Página</span></label>
      <div id="aviso_fb" class="meta"></div>
    </div>

    <div class="acoes">
      ${a.id ? `<span class="chip" id="estado">salvo</span>
                <button class="bt sec" id="duplicar" type="button">Duplicar</button>
                <button class="bt sec" id="rodar" type="button">Verificar comentários</button>
                <button class="bt perigo" id="excluir" type="button">Excluir</button>`
             : `<button class="bt" id="criar" type="button">Criar automação</button>
                <button class="bt sec" id="cancelar" type="button">Cancelar</button>`}
    </div>`;

  $("#f_escopo").onchange = () => { pintarEscolhaPost(); revisar(); };
  $("#f_teste").oninput = testar;
  if (a.id) { $("#rodar").onclick = rodar; $("#excluir").onclick = excluir; $("#duplicar").onclick = duplicar; }
  else {
    $("#criar").onclick = salvar;
    $("#cancelar").onclick = () => { atual = null; pintarEditor(); pintarLista(); };
  }
  pintarEscolhaPost();

  // tudo que o Pedro mexe salva sozinho: texto com folga pra ele terminar de digitar,
  // liga/desliga na hora. Não existe botão de salvar em automação já criada.
  const campos = ["f_nome", "f_palavras", "f_modo", "f_escopo", "f_respostas", "f_dm_texto", "f_botao", "f_url"];
  const chaves = ["f_ativa", "f_pub", "f_dm", "f_uma", "f_fb"];
  campos.forEach((id) => { const el = $("#" + id); if (el) el.addEventListener("input", () => { revisar(); agendar(700); }); });
  chaves.forEach((id) => { const el = $("#" + id); if (el) el.addEventListener("change", () => { revisar(); agendar(0); }); });
  ["f_modo", "f_escopo"].forEach((id) => $("#" + id).addEventListener("change", () => agendar(0)));
  revisar();
}

/* ─────────────────────────── Salvamento automático ─────────────────────────── */

function estado(txt, cls) {
  const el = $("#estado");
  if (!el) return;
  el.textContent = txt;
  el.className = "chip" + (cls ? " " + cls : "");
}

function agendar(atraso) {
  if (!atual || !atual.id) return;         // automação nova só nasce pelo botão Criar
  clearTimeout(timerSalvar);
  estado("editando…");
  timerSalvar = setTimeout(salvarAuto, atraso);
}

async function salvarAuto() {
  if (!atual || !atual.id || salvando) return;
  const dados = coletar();
  if (!dados.palavras.length) return estado("falta a palavra-chave", "erro");
  if (dados.escopo === "midia" && !dados.midia_id) return estado("escolha o post", "erro");
  if (!revisar()) return estado("campo passando do limite", "erro");
  if (dados.respostas.some((r) => /https?:\/\//i.test(r)))
    return estado("link não vai no comentário", "erro");
  salvando = true; estado("salvando…");
  try {
    const salva = await api("/insta/api/automacoes/" + atual.id, { method: "PUT", body: JSON.stringify(dados) });
    Object.assign(atual, salva);
    const i = automacoes.findIndex((a) => a.id === salva.id);
    if (i >= 0) automacoes[i] = { ...automacoes[i], ...salva };
    pintarLista();                          // só a lista: repintar o editor tiraria o foco
    estado("salvo", "on");
  } catch (e) {
    estado("não salvou", "erro");
  } finally { salvando = false; }
}

/* ─────────────────────────── Limites, contadores e prévia ─────────────────────────── */

function contar(idCampo, idConta, limite, rotulo) {
  const el = $("#" + idCampo), c = $("#" + idConta);
  if (!el || !c) return true;
  const n = (el.value || "").length;
  c.textContent = `${n} / ${limite}${rotulo ? " " + rotulo : ""}`;
  c.className = "conta" + (n > limite ? " estourou" : n > limite * 0.9 ? " perto" : "");
  el.classList.toggle("campo-ruim", n > limite);
  return n <= limite;
}

function contarLinhas(idCampo, idConta, limite) {
  const el = $("#" + idCampo), c = $("#" + idConta);
  const linhas = (el.value || "").split("\n").map((s) => s.trim()).filter(Boolean);
  const maior = linhas.reduce((m, l) => Math.max(m, l.length), 0);
  const ruim = linhas.filter((l) => l.length > limite).length;
  c.textContent = ruim ? `${ruim} resposta(s) passando de ${limite} caracteres`
                       : `${linhas.length} resposta(s) · maior tem ${maior} / ${limite}`;
  c.className = "conta" + (ruim ? " estourou" : maior > limite * 0.9 ? " perto" : "");
  el.classList.toggle("campo-ruim", !!ruim);
  return !ruim;
}

function temBotao() { return !!($("#f_botao").value.trim() && $("#f_url").value.trim()); }

function revisar() {
  const semPost = $("#f_escopo") && $("#f_escopo").value === "midia" && !(atual && atual.midia_id);
  const limDm = temBotao() ? LIM.dmComBotao : LIM.dmSemBotao;
  $("#f_dm_texto").maxLength = limDm;
  const ok = [
    contarLinhas("f_respostas", "c_respostas", LIM.comentario),
    contar("f_dm_texto", "c_dm_texto", limDm, temBotao() ? "(com botão)" : "(sem botão)"),
    contar("f_botao", "c_botao", LIM.botao),
  ].every(Boolean);
  const url = $("#f_url").value.trim();
  const urlOk = !url || /^https?:\/\/.+/i.test(url);
  $("#c_url").textContent = url ? (urlOk ? "link válido" : "o link precisa começar com https://") : "";
  $("#c_url").className = "conta" + (url && !urlOk ? " estourou" : "");
  $("#f_url").classList.toggle("campo-ruim", !!url && !urlOk);
  const bt = $("#criar");                  // só existe em automação nova
  if (bt) { bt.disabled = !(ok && urlOk && !semPost); bt.style.opacity = ok && urlOk && !semPost ? "" : ".5"; }
  avisarFacebook();
  pintarPrevia();
  return ok && urlOk && !semPost;
}

// foto não é compartilhada no Facebook, então a perna de lá fica sem alvo — dizer isso
// evita ler "sem post no Facebook" como se fosse falha
function avisarFacebook() {
  const el = $("#aviso_fb");
  if (!el) return;
  const m = midias.find((x) => x.id === (atual && atual.midia_id));
  const foto = m && (m.media_product_type || m.media_type) !== "REELS" && m.media_type !== "VIDEO";
  el.textContent = ($("#f_fb").checked && foto)
    ? "Este post é foto. Foto não vai pro Facebook, então aqui a automação vale só no Instagram."
    : "";
}

function pintarPrevia() {
  const alvo = $("#previa");
  if (!atual) { alvo.innerHTML = ""; return; }
  const palavra = ($("#f_palavras").value.split(",")[0] || "sensor").trim() || "sensor";
  const resps = $("#f_respostas").value.split("\n").map((s) => s.trim()).filter(Boolean);
  const dmTexto = $("#f_dm_texto").value.trim();
  const bt = $("#f_botao").value.trim(), url = $("#f_url").value.trim();

  const comentario = !$("#f_pub").checked ? `<p class="nada">Sem resposta no comentário.</p>` : `
    <div class="coment">
      <div class="item"><span class="ava cinza"></span>
        <div class="bal"><b>maria.souza</b><br>${esc(palavra)}</div></div>
      <div class="item resp"><span class="ava"></span>
        <div class="bal"><b>pedrorochadm1</b><br>${esc(resps[0] || "—")}</div></div>
    </div>${resps.length > 1 ? `<p class="quem" style="padding:0 12px 12px">+${resps.length - 1} variação(ões), sorteadas a cada comentário</p>` : ""}`;

  const direto = !$("#f_dm").checked || !dmTexto ? `<p class="nada">Sem direct.</p>` : (
    bt && url
      ? `<div class="corpo"><div class="cartao">
           <div class="txt">${esc(dmTexto)}</div>
           <div class="bt-dm">${esc(bt)}</div></div>
           <span class="quem">botão abre ${esc(url.replace(/^https?:\/\//, "").slice(0, 34))}…</span></div>`
      : `<div class="corpo"><div class="bolha">${esc(dmTexto)}</div></div>`);

  alvo.innerHTML = `
    <div class="card">
      <h4>No comentário</h4>
      <div class="fone">${comentario}</div>
    </div>
    <div class="card">
      <h4>No direct</h4>
      <div class="fone">
        <div class="topo"><span class="ava"></span>pedrorochadm1</div>
        ${direto}
      </div>
    </div>`;
}

function pintarEscolhaPost() {
  const escopo = $("#f_escopo").value, alvo = $("#escolha_post");
  if (escopo !== "midia") {
    alvo.innerHTML = escopo === "proximo"
      ? `<p class="meta">Aguardando a próxima publicação${atual.esperando_desde ? " desde " + data(atual.esperando_desde) : ""}. Quando ela sair, esta automação passa sozinha para <b>Post específico</b>, já apontando para ela.</p>`
      : "";
    return;
  }
  alvo.innerHTML = `<label>Escolha o post</label><div class="posts">${
    midias.map((m) => `<figure data-id="${m.id}" class="${atual.midia_id === m.id ? "sel" : ""}" title="${esc((m.caption || "").slice(0, 80))}">
      <img src="${m.thumbnail_url || m.media_url || ""}" alt=""></figure>`).join("")}</div>`;
  alvo.querySelectorAll("figure").forEach((f) => (f.onclick = () => {
    atual.midia_id = f.dataset.id;
    alvo.querySelectorAll("figure").forEach((o) => o.classList.toggle("sel", o === f));
    revisar();
    agendar(0);          // escolher o post É uma edição: grava na hora, como as chaves
  }));
}

async function testar() {
  const texto = $("#f_teste").value;
  const el = $("#r_teste");
  if (!texto) { el.textContent = ""; el.className = "chip"; return; }
  const r = await api("/insta/api/testar", { method: "POST", body: JSON.stringify(
    { texto, palavras: $("#f_palavras").value.split(","), modo: $("#f_modo").value }) });
  el.textContent = r.casa ? "aciona" : "não aciona";
  el.className = "chip" + (r.casa ? " on" : "");
}

function coletar() {
  return {
    nome: $("#f_nome").value.trim(),
    ativa: $("#f_ativa").checked,
    palavras: $("#f_palavras").value.split(",").map((s) => s.trim()).filter(Boolean),
    modo: $("#f_modo").value,
    escopo: $("#f_escopo").value,
    midia_id: $("#f_escopo").value === "midia" ? atual.midia_id : null,
    respostas: $("#f_respostas").value.split("\n").map((s) => s.trim()).filter(Boolean),
    respostas_sem_dm: [],
    dm_texto: $("#f_dm_texto").value.trim(),
    botao_texto: $("#f_botao").value.trim(),
    botao_url: $("#f_url").value.trim(),
    responder_publico: $("#f_pub").checked,
    enviar_dm: $("#f_dm").checked,
    uma_vez_por_pessoa: $("#f_uma").checked,
    facebook: $("#f_fb").checked,
  };
}

async function salvar() {
  const dados = coletar();
  if (!dados.palavras.length) return aviso("Falta a palavra-chave.");
  if (dados.escopo === "midia" && !dados.midia_id) return aviso("Escolha o post.");
  if (!revisar()) return aviso("Tem campo passando do limite. Corrija o que está em vermelho.");
  if (dados.respostas.some((r) => /https?:\/\//i.test(r)))
    return aviso("Link em resposta de comentário não é permitido. Link só vai no direct.");
  const salva = atual.id
    ? await api("/insta/api/automacoes/" + atual.id, { method: "PUT", body: JSON.stringify(dados) })
    : await api("/insta/api/automacoes", { method: "POST", body: JSON.stringify(dados) });
  aviso("Salvo.");
  await carregar();
  selecionar(automacoes.find((a) => a.id === salva.id));
}

async function duplicar() {
  const nova = await api("/insta/api/automacoes/" + atual.id + "/duplicar", { method: "POST" });
  await carregar();
  selecionar(automacoes.find((a) => a.id === nova.id));
  aviso("Cópia criada, desligada e esperando a próxima publicação.");
}

async function excluir() {
  if (!confirm("Excluir esta automação? O histórico fica.")) return;
  await api("/insta/api/automacoes/" + atual.id, { method: "DELETE" });
  atual = null; aviso("Excluída."); await carregar(); pintarEditor();
}

async function rodar() {
  aviso("Verificando…");
  await api("/insta/api/rodar", { method: "POST" });
  await carregar(); await pintarHistorico();
  aviso("Pronto.");
}

function selecionar(a) {
  atual = a ? JSON.parse(JSON.stringify(a)) : null;
  pintarLista(); pintarEditor(); pintarHistorico();
}

async function pintarHistorico() {
  const ev = await api("/insta/api/eventos" + (atual && atual.id ? "?automacao_id=" + atual.id : ""));
  $("#historico").className = ev.length ? "" : "vazio";
  $("#historico").innerHTML = ev.length ? `<table>
    <tr><th>Quando</th><th>Quem</th><th>Comentário</th><th>Resposta</th><th>Onde</th><th>Direct</th></tr>
    ${ev.map((e) => `<tr>
      <td>${data(e.quando)}</td>
      <td>@${esc(e.usuario)}</td>
      <td>${esc((e.texto || "").slice(0, 60))}</td>
      <td>${esc((e.resposta || "").slice(0, 60)) || "—"}</td>
      <td><span class="badge">${e.plataforma === "fb" ? "Facebook" : "Instagram"}</span></td>
      <td><span class="badge ${e.dm_status && e.dm_status.startsWith("ok") ? "ok" : (e.dm_status ? "erro" : "")}"
          title="${esc(e.erro || "")}">${esc(e.dm_status || "—")}</span></td>
    </tr>`).join("")}</table>` : "Nada ainda.";
}

async function carregar() {
  automacoes = await api("/insta/api/automacoes");
  if (atual && atual.id) atual = JSON.parse(JSON.stringify(automacoes.find((a) => a.id === atual.id) || atual));
  pintarLista();
}

// O servidor pode mudar a automação sozinho (é ele quem engata no post que saiu). Se a
// tela ficar com a cópia velha, o próximo autosave desfaz isso. Então quando não há
// edição pendente nem campo em foco, a tela se realinha com o servidor.
async function realinhar() {
  if (!raiz || !atual || !atual.id || timerSalvar || salvando) return;
  if (document.activeElement && /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) return;
  try {
    const lista = await api("/insta/api/automacoes");
    const nova = lista.find((a) => a.id === atual.id);
    if (!nova) return;
    automacoes = lista;
    const mudou = JSON.stringify(nova) !== JSON.stringify(atual);
    if (mudou) { selecionar(nova); aviso("Automação atualizada pelo servidor."); }
  } catch (e) { /* rede instável: tenta de novo no próximo ciclo */ }
}

async function inicio() {
  const st = await api("/insta/api/status");
  // o que importa saber de relance: chega na hora ou depende da varredura?
  const partes = [];
  if (!st.ligado) partes.push("automações desligadas no servidor");
  else if (st.webhook && st.webhook.ativo) partes.push("tempo real · Instagram + Facebook");
  else if (st.webhook && st.webhook.instagram) partes.push("tempo real só no Instagram · Facebook sem webhook");
  else if (st.webhook && st.webhook.facebook) partes.push("tempo real só no Facebook · Instagram sem webhook");
  else partes.push(`sem webhook · varrendo a cada ${st.polling_segundos}s`);
  if (st.ligado && st.marca_passo && !st.marca_passo.vivo) partes.push("envio parado");
  partes.push(st.token_dias != null ? `token ${st.token_dias}d` : "token sem prazo");
  $("#status").textContent = partes.join(" · ");
  const saudavel = st.ligado && st.webhook && st.webhook.ativo && st.marca_passo && st.marca_passo.vivo;
  $("#status").className = "chip" + (saudavel ? " on" : "");
  $("#status").title = st.ligado && st.webhook && st.webhook.ativo
    ? `Comentário chega pela Meta no instante em que é escrito (${st.webhook.callback}). A varredura a cada ${st.polling_segundos}s fica só de rede de segurança.`
    : "";
  await carregar();
  try { midias = await api("/insta/api/midias"); } catch (e) { midias = []; }
  if (automacoes.length) selecionar(automacoes[0]); else pintarEditor();
  await pintarHistorico();

  // Os dois ciclos ficam guardados pra serem limpos no desmontar.
  intervalos.push(setInterval(realinhar, 20000));
  intervalos.push(setInterval(async () => {
    if (!document.hidden && raiz) { await carregar(); await pintarHistorico(); }
  }, 60000));
}
