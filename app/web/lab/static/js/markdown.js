/* Exportação em markdown, pro roteiro sair do app e ir pro WhatsApp, Notion ou
   pro chat de uma IA sem que o estrategista precise de acesso ao sistema.

   NEGRITO COM UM ASTERISCO SÓ. O destino principal é o WhatsApp, onde *texto* é
   negrito e **texto** sai literal, com os asteriscos à mostra. Pelo mesmo motivo
   o título não usa cabeçalho de markdown: um "#" apareceria cru na conversa.

   Duas regras que definem o formato:
   - Hook e fechamento SÃO rotulados: são os dois momentos que o estrategista
     avalia isoladamente.
   - Os desenvolvimentos NÃO têm rótulo nem numeração. Saem como parágrafos
     consecutivos, na ordem do card. É como o roteiro soa quando falado.

   Roda no cliente porque precisa funcionar offline, e porque a escrita no
   clipboard tem que acontecer de forma síncrona dentro do gesto do usuário. */

const NOME_TIPO = { conteudo: "Conteúdo", anuncio: "Anúncio" };
const NOME_FORMATO = { lofi: "Lo-fi", slide: "Slide", vlog: "Vlog", documentario: "Documentário" };

const LACUNA = {
  hook: "_[falta o hook]_",
  desenvolvimento: "_[falta desenvolvimento]_",
  fechamento: "_[falta o fechamento]_",
  tela: "_[falta o texto na tela]_",
};

/** Uma lista de links vira bullets "- url — nota". Vazios são descartados. */
function secaoDeLinks(rotulo, itens) {
  const uteis = (itens || [])
    .map((l) => (typeof l === "string" ? { url: l } : l))
    .filter((l) => (l.url || "").trim() || (l.nota || "").trim());
  if (!uteis.length) return [];
  return ["", `*${rotulo}*`, ...uteis.map((l) => {
    const url = (l.url || "").trim();
    const nota = (l.nota || "").trim();
    return url && nota ? `- ${url} — ${nota}` : `- ${url || nota}`;
  })];
}

export function cardParaMarkdown(card, opcoes = {}) {
  const { incluirTipoFormato = true, marcarLacunas = false, incluirLinks = true } = opcoes;
  const linhas = ["*IDEIA CENTRAL*", card.titulo || "(sem título)"];

  if (incluirTipoFormato) {
    const tipo = NOME_TIPO[card.tipo] || "—";
    const formato = NOME_FORMATO[card.formato] || "—";
    linhas.push("", `*Tipo:* ${tipo} · *Formato:* ${formato}`);
  }

  const hook = (card.hook || "").trim();
  if (hook || marcarLacunas) {
    linhas.push("", "*HOOK*", hook || LACUNA.hook);
  }

  // A chave é a declaração: desligada, este vídeo não tem texto na tela e a
  // seção nem aparece. Ligada e vazia, a pendência é explícita — e independe do
  // "marcar lacunas", porque ligar já foi o Pedro dizendo que vai ter.
  if (card.tela_ativa) {
    const tela = (card.titulo_tela || "").trim();
    linhas.push("", "*TEXTO NA TELA*", tela || LACUNA.tela);
  }

  // Desenvolvimentos vazios são descartados: não geram parágrafo em branco.
  const desen = (card.desenvolvimentos || [])
    .map((d) => (typeof d === "string" ? d : d.texto || "").trim())
    .filter(Boolean);
  if (desen.length) {
    for (const d of desen) linhas.push("", d);
  } else if (marcarLacunas) {
    linhas.push("", LACUNA.desenvolvimento);
  }

  const fechamento = (card.fechamento || "").trim();
  if (fechamento || marcarLacunas) {
    linhas.push("", "*FECHAMENTO*", fechamento || LACUNA.fechamento);
  }

  // Material de apoio vai DEPOIS do roteiro e só quando existe: quem lê está
  // avaliando o roteiro, não a bibliografia.
  if (incluirLinks) {
    linhas.push(...secaoDeLinks("REFERÊNCIAS", card.referencias));
    linhas.push(...secaoDeLinks("REAGIR A", card.reacoes));
  }

  return linhas.join("\n").trim() + "\n";
}

/** Vários cards concatenados, separados por ---, na ordem em que aparecem. */
export function loteParaMarkdown(cards, opcoes = {}) {
  return cards.map((c) => cardParaMarkdown(c, opcoes)).join("\n---\n\n");
}

/* ─────────────────────────── Clipboard ───────────────────────────
   O Safari invalida a permissão de escrita se houver qualquer await antes da
   chamada dentro do handler de clique. Por isso o texto tem que chegar aqui
   pronto, e writeText tem que ser a PRIMEIRA operação assíncrona. */

export async function copiar(texto) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(texto);
      return true;
    }
  } catch (e) { /* cai no fallback abaixo */ }

  try {
    const ta = document.createElement("textarea");
    ta.value = texto;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;top:0;left:0;opacity:0;pointer-events:none";
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, texto.length);   // iOS ignora o select() sozinho
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch (e) {
    return false;
  }
}
