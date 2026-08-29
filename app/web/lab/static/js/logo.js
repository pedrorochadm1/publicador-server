/* Logo do Laboratório DM1.

   Erlenmeyer de traço geométrico (não o desenho de livro didático): ombros
   retos, base larga, e o líquido dentro sinalizando a proporção 5:1 — cinco
   gotas de conteúdo para uma de anúncio. Lê bem em 32px porque a silhueta é
   um trapézio, e o miolo some sem prejudicar o reconhecimento. */

export function logo(tamanho = 32, cor = "var(--acao)") {
  return `
<svg viewBox="0 0 32 32" width="${tamanho}" height="${tamanho}" fill="none"
     xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <path d="M12 3v8.2L4.8 24.4A3 3 0 0 0 7.4 29h17.2a3 3 0 0 0 2.6-4.6L20 11.2V3"
        stroke="${cor}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>
  <path d="M10.2 3h11.6" stroke="${cor}" stroke-width="2.2" stroke-linecap="round"/>
  <path d="M8.9 20.5h14.2l3.5 6.2A1.6 1.6 0 0 1 25.2 29H6.8a1.6 1.6 0 0 1-1.4-2.3z"
        fill="${cor}" fill-opacity=".92"/>
  <circle cx="13" cy="16.4" r="1.15" fill="${cor}" fill-opacity=".55"/>
  <circle cx="18.4" cy="14.2" r="1.5" fill="${cor}" fill-opacity=".8"/>
</svg>`;
}

/* Ícones do trilho e da tabbar. Mesma família de traço da logo. */

export const ICONE_LAB = `
<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <path d="M4 5h16M7 5v5L3.4 17.8A2 2 0 0 0 5.2 20.7h13.6a2 2 0 0 0 1.8-2.9L17 10V5"
        stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>
  <path d="M6.2 15h11.6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
</svg>`;

export const ICONE_AUTO = `
<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <path d="M20 11.5a7.5 7.5 0 0 1-10.9 6.7L4 19.5l1.4-4.7A7.5 7.5 0 1 1 20 11.5z"
        stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>
  <circle cx="9" cy="11.5" r="1.1" fill="currentColor"/>
  <circle cx="12.5" cy="11.5" r="1.1" fill="currentColor"/>
  <circle cx="16" cy="11.5" r="1.1" fill="currentColor"/>
</svg>`;
