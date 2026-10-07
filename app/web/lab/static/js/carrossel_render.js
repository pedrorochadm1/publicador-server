/* Motor de desenho do slide do carrossel.

   UMA função desenha o preview e o JPEG que vai pro Instagram. Se fossem dois
   caminhos (HTML no preview, canvas no export) eles divergiriam no primeiro
   ajuste fino, e o Pedro aprovaria uma coisa e publicaria outra.

   Os números são os do template no Figma (os mesmos de scripts/gerar_slide.py
   no repositório de conteúdo): canvas 1080×1350, coluna de 920, Inter 42px
   500/700, entrelinha 50,83, 40px entre parágrafos e entre blocos, imagem com
   canto 20, header de 798px. O bloco inteiro fica centralizado na vertical,
   como o `align-items: center` do HTML original.

   O layout é puro (recebe a função de medir texto) pra rodar no Node nos
   testes; só `desenhar` encosta no canvas. */

export const W = 1080;
export const H = 1350;
export const MARGEM = 80;
export const LARGURA = 920;
export const GAP = 40;
export const FONTE = 42;
export const ENTRELINHA = 50.83;
export const ENTRE_PARAGRAFOS = 40;
export const RAIO = 20;
export const HEADER_W = 798;
export const HEADER_H = Math.round(HEADER_W * 340 / 1586 * 100) / 100;   // proporção do PNG
export const AREA = H - 2 * MARGEM;                                       // 1190
// Inter: ascendente = 0,96875 em, e ascendente + descendente = 1,21 em = 50,83px
// a 42px. A entrelinha bate exatamente com a caixa da fonte, então a linha de
// base fica a uma ascendente do topo, sem meia-entrelinha.
export const BASE = FONTE * 0.96875;
export const FAMILIA = "InterSlide";

// Quatro alturas de imagem, sempre na largura da coluna. M é o padrão antigo.
export const TAMANHOS = { P: 320, M: 473, G: 640, GG: 860 };
export const NOMES_TAMANHO = { P: "Pequena", M: "Média", G: "Grande", GG: "Gigante" };

export const fonte = (negrito) => `${negrito ? 700 : 500} ${FONTE}px ${FAMILIA}`;

/* ─────────────────────────── Texto ─────────────────────────── */

/* "**x**" vira negrito; linha em branco separa parágrafo; quebra simples é
   quebra de linha forçada. Mesma marcação do gerar_slide.py, pro Pedro não ter
   duas sintaxes na cabeça. Asterisco sem par fica como texto. */
export function paragrafos(texto) {
  const limpo = String(texto ?? "").replace(/\r/g, "").trim();
  if (!limpo) return [];
  return limpo.split(/\n[ \t]*\n+/).map((p) => {
    const trechos = [];
    const partes = p.split("**");
    const fechado = partes.length % 2 === 1;
    partes.forEach((t, i) => {
      // Último pedaço depois de um ** sem par: volta o ** como texto comum.
      const negrito = i % 2 === 1 && (fechado || i < partes.length - 1);
      const txt = !fechado && i === partes.length - 1 && i > 0 ? "**" + t : t;
      if (txt) trechos.push({ t: txt, b: negrito });
    });
    // Quebra de linha forçada dentro do parágrafo.
    const linhas = [[]];
    for (const tr of trechos) {
      tr.t.split("\n").forEach((pedaco, j) => {
        if (j > 0) linhas.push([]);
        if (pedaco) linhas[linhas.length - 1].push({ t: pedaco, b: tr.b });
      });
    }
    return linhas;
  });
}

/* Quebra gulosa por palavra, como o navegador faz. `medir(texto, negrito)`
   devolve a largura em px. Cada linha sai como lista de segmentos com x. */
export function quebrar(linhaForcada, medir, largura = LARGURA) {
  const palavras = [];   // {t, b, espacoAntes}
  let espaco = false;
  for (const tr of linhaForcada) {
    for (const tok of tr.t.split(/(\s+)/)) {
      if (!tok) continue;
      if (/^\s+$/.test(tok)) { espaco = true; continue; }
      // Palavra colada a outra de estilo diferente ("**neg**rito") continua a
      // mesma palavra visual: não pode quebrar entre elas.
      const colada = !espaco && palavras.length > 0;
      palavras.push({ t: tok, b: tr.b, espacoAntes: espaco, colada });
      espaco = false;
    }
  }
  const linhas = [];
  let atual = [], x = 0;
  const espacoW = (b) => medir(" ", b);
  // Agrupa palavras coladas num bloco indivisível.
  const blocos = [];
  for (const p of palavras) {
    if (p.colada && blocos.length) blocos[blocos.length - 1].push(p);
    else blocos.push([p]);
  }
  for (const bloco of blocos) {
    const larg = bloco.reduce((s, p) => s + medir(p.t, p.b), 0);
    const esp = atual.length ? espacoW(bloco[0].b) : 0;
    if (atual.length && x + esp + larg > largura + 0.5) {
      linhas.push(atual);
      atual = []; x = 0;
    }
    let cx = x + (atual.length ? espacoW(bloco[0].b) : 0);
    for (const p of bloco) {
      atual.push({ t: p.t, b: p.b, x: cx });
      cx += medir(p.t, p.b);
    }
    x = cx;
  }
  if (atual.length) linhas.push(atual);
  return linhas;
}

/* Linhas visuais de um texto inteiro, separadas por parágrafo. */
export function linhasDoTexto(texto, medir) {
  return paragrafos(texto).map((par) =>
    par.flatMap((forcada) => (forcada.length ? quebrar(forcada, medir) : [[]])));
}

function alturaTexto(pars) {
  if (!pars.length) return 0;
  const n = pars.reduce((s, p) => s + p.length, 0);
  return n * ENTRELINHA + (pars.length - 1) * ENTRE_PARAGRAFOS;
}

/* ─────────────────────────── Layout ─────────────────────────── */

/* Posição de cada bloco no canvas. `cabe` é falso quando o conteúdo passa da
   área útil — aí o editor avisa em vez de deixar sair cortado. */
export function layout(slide, medir) {
  const blocos = [];
  if (slide.header !== false) blocos.push({ tipo: "header", h: HEADER_H });
  const acima = linhasDoTexto(slide.texto, medir);
  if (acima.length) blocos.push({ tipo: "texto", pars: acima, h: alturaTexto(acima) });
  if (slide.imagem) {
    blocos.push({ tipo: "imagem", h: TAMANHOS[slide.imagem.tamanho] || TAMANHOS.M });
  }
  const abaixo = linhasDoTexto(slide.texto_abaixo, medir);
  if (abaixo.length) blocos.push({ tipo: "texto", pars: abaixo, h: alturaTexto(abaixo) });

  const altura = blocos.reduce((s, b) => s + b.h, 0) + Math.max(0, blocos.length - 1) * GAP;
  const sobra = AREA - altura;
  let y = MARGEM + Math.max(0, sobra / 2);
  for (const b of blocos) { b.y = y; y += b.h + GAP; }
  return { blocos, altura, cabe: sobra >= -0.5, excesso: Math.max(0, Math.ceil(-sobra)) };
}

/* ─────────────────────────── Recorte da imagem ─────────────────────────── */

/* Onde desenhar a imagem pra cobrir o slot. zoom 1 = cobre exatamente (como
   object-fit: cover); acima disso aproxima. cx/cy é o ponto da imagem (0 a 1)
   que fica no centro do slot. Nunca sobra borda vazia: o centro é limitado. */
export function recorte(iw, ih, sw, sh, zoom = 1, cx = 0.5, cy = 0.5) {
  const escala = Math.max(sw / iw, sh / ih) * Math.max(1, zoom);
  const dw = iw * escala, dh = ih * escala;
  const c = limitarCentro(dw, dh, sw, sh, cx, cy);
  return { dx: sw / 2 - c.cx * dw, dy: sh / 2 - c.cy * dh, dw, dh, cx: c.cx, cy: c.cy };
}

export function limitarCentro(dw, dh, sw, sh, cx, cy) {
  const mx = Math.min(0.5, sw / 2 / dw), my = Math.min(0.5, sh / 2 / dh);
  return {
    cx: Math.min(1 - mx, Math.max(mx, cx)),
    cy: Math.min(1 - my, Math.max(my, cy)),
  };
}

/* ─────────────────────────── Canvas ─────────────────────────── */

export function medidorDe(ctx) {
  const cache = new Map();
  return (t, b) => {
    const k = (b ? "1" : "0") + t;
    let w = cache.get(k);
    if (w === undefined) {
      ctx.font = fonte(b);
      w = ctx.measureText(t).width;
      cache.set(k, w);
    }
    return w;
  };
}

function caminhoArredondado(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/* Desenha o slide inteiro em 1080×1350 no contexto dado (que pode estar
   escalado pra miniatura). `assets.header` é o PNG do cabeçalho e
   `assets.imagem(arquivo)` devolve o <img> carregado ou null. Devolve o layout
   pra quem precisar saber onde ficou a imagem (o modo de recorte). */
export function desenhar(ctx, slide, assets, medir = medidorDe(ctx)) {
  const lay = layout(slide, medir);
  ctx.save();
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, W, H);
  ctx.textBaseline = "alphabetic";
  ctx.fillStyle = "#000000";

  for (const b of lay.blocos) {
    if (b.tipo === "header") {
      if (assets.header) ctx.drawImage(assets.header, MARGEM, b.y, HEADER_W, HEADER_H);
    } else if (b.tipo === "texto") {
      let y = b.y;
      b.pars.forEach((par, i) => {
        if (i > 0) y += ENTRE_PARAGRAFOS;
        for (const linha of par) {
          for (const seg of linha) {
            ctx.font = fonte(seg.b);
            ctx.fillText(seg.t, MARGEM + seg.x, y + BASE);
          }
          y += ENTRELINHA;
        }
      });
    } else if (b.tipo === "imagem") {
      const im = slide.imagem;
      const el = assets.imagem ? assets.imagem(im.arquivo) : null;
      ctx.save();
      caminhoArredondado(ctx, MARGEM, b.y, LARGURA, b.h, RAIO);
      ctx.clip();
      if (el && el.naturalWidth) {
        const r = recorte(el.naturalWidth, el.naturalHeight, LARGURA, b.h, im.zoom, im.cx, im.cy);
        ctx.drawImage(el, MARGEM + r.dx, b.y + r.dy, r.dw, r.dh);
      } else {
        ctx.fillStyle = "#eef1f5";
        ctx.fillRect(MARGEM, b.y, LARGURA, b.h);
      }
      ctx.restore();
      ctx.fillStyle = "#000000";
    }
  }
  ctx.restore();
  return lay;
}
