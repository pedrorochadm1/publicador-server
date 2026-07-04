"""Gera a copy de um reel a partir do vídeo, no tom do Pedro (@pedrorochadm1).

Dois passos, ambos via OpenAI:
1. Whisper transcreve o áudio do vídeo.
2. GPT-4o escreve legenda (IG/TikTok) + título/descrição do YouTube seguindo as
   regras de voz e SEO do projeto.

As regras abaixo são a versão condensada de .claude/rules/tom-de-voz.md +
seo-multiplataforma.md do repo pedrorochadm1. Se elas mudarem lá, atualizar aqui.
"""
import json

from . import config

_client = None


def _openai():
    global _client
    if _client is None:
        from openai import OpenAI  # import lazy: o módulo carrega mesmo sem a lib instalada
        _client = OpenAI(api_key=config.OPENAI_API_KEY)
    return _client


def disponivel() -> bool:
    return bool(config.OPENAI_API_KEY)


_SYSTEM = """\
Você escreve a copy de reels do Dr. Pedro Rocha (@pedrorochadm1), médico que atua na
área de endocrinologia. Conteúdo sobre diabetes tipo 1, saúde metabólica, CGM e SUS.

TOM (inviolável):
- Direto, curto, cru. Desabafo clínico, não copy estruturado.
- Português do Brasil. Frases curtas. Começa pelo fato, não por introdução.
- PROIBIDO: hashtags na legenda do Instagram, travessão (—), adjetivos genéricos
  (incrível, poderoso, revolucionário), linguagem de coach (jornada, mindset,
  potencial, você merece), saudações.
- NUNCA chamar o Pedro de "endocrinologista". Ele ATUA na área de endocrinologia.

LEGENDA DO INSTAGRAM (campo caption_ig):
- UMA linha. UMA pergunta. Nunca parágrafos. Zero hashtag.
- Pergunta de identificação DIRETA, como se perguntasse pra um paciente no consultório.
- PROIBIDO abrir com clickbait: nada de "Você sabia que", "Descubra", "Você não vai
  acreditar", "A verdade sobre". Vá direto na experiência que a pessoa vive.
- Não repetir frases faladas no vídeo.

YOUTUBE (só o YouTube tem SEO próprio):
- youtube_title: até 100 caracteres. Comece com o TERMO QUE A PESSOA DIGITA na busca
  (ex.: "insulina pelo SUS", "glicose normal com sintomas", "sensor de glicose"), NUNCA
  com o nome técnico do fenômeno (ex.: evite abrir com "Pseudo-hipoglicemia:"). Gancho
  de curiosidade real, sem clickbait falso, sem hashtag no título.
- youtube_description: 1-2 linhas com keywords naturais + CTA curto de se inscrever
  + 3 a 5 hashtags no fim. Cada hashtag é UMA palavra só, tudo minúsculo, sem hífen e
  sem camelCase (ex.: #diabetestipo1 #diabetes #cgm #saudemetabolica). Pode incluir #shorts.

Responda SOMENTE com JSON: {"caption_ig": "...", "youtube_title": "...", "youtube_description": "..."}
A legenda do TikTok é sempre idêntica à do Instagram, então não precisa devolvê-la."""


def transcrever(caminho_video: str) -> str:
    """Transcreve o áudio do vídeo via Whisper. Retorna '' se o vídeo não tiver fala."""
    with open(caminho_video, "rb") as f:
        r = _openai().audio.transcriptions.create(
            model="whisper-1", file=f, language="pt", response_format="text"
        )
    return (r or "").strip()


def gerar_copy(transcricao: str) -> dict:
    """Gera {caption_ig, youtube_title, youtube_description, tiktok_caption} no tom do Pedro."""
    conteudo = transcricao.strip() or "(vídeo sem fala; gere a copy pelo tema geral de diabetes tipo 1)"
    r = _openai().chat.completions.create(
        model="gpt-4o",
        temperature=0.7,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"Transcrição do reel:\n\n{conteudo}"},
        ],
    )
    data = json.loads(r.choices[0].message.content)
    caption_ig = (data.get("caption_ig") or "").strip()
    return {
        "caption_ig": caption_ig,
        "tiktok_caption": caption_ig,  # regra: TikTok = legenda idêntica à do IG
        "youtube_title": (data.get("youtube_title") or "").strip()[:100],
        "youtube_description": (data.get("youtube_description") or "").strip(),
    }


def gerar_do_video(caminho_video: str) -> dict:
    """Atalho: transcreve e gera a copy completa a partir do arquivo de vídeo."""
    return gerar_copy(transcrever(caminho_video))


_SYSTEM_YT = """\
Você escreve o SEO do YouTube Shorts de reels do Dr. Pedro Rocha (@pedrorochadm1),
médico que atua na área de endocrinologia (NUNCA chame de "endocrinologista"). Tema:
diabetes tipo 1, saúde metabólica, CGM, SUS. Português do Brasil, direto e cru, sem
travessão (—), sem adjetivo genérico, sem linguagem de coach.

- youtube_title: até 100 caracteres. Comece com o TERMO QUE A PESSOA DIGITA na busca
  (ex.: "insulina pelo SUS", "glicose normal com sintomas", "sensor de glicose"), NUNCA
  com o nome técnico do fenômeno. Sem hashtag no título. Sem clickbait falso.
- youtube_description: 1-2 linhas com keywords naturais + CTA curto de se inscrever
  + 3 a 5 hashtags no fim, cada uma UMA palavra minúscula sem hífen (ex.: #diabetestipo1
  #diabetes #cgm #saudemetabolica). Pode incluir #shorts.

Responda SOMENTE com JSON: {"youtube_title": "...", "youtube_description": "..."}"""


def gerar_youtube_seo(transcricao: str, legenda: str = "") -> dict:
    """SEO do YouTube a partir da transcrição do reel (a legenda do trial entra como apoio)."""
    base = transcricao.strip() or legenda.strip() or "(vídeo sem fala; use o tema geral de diabetes tipo 1)"
    conteudo = base if not legenda.strip() else f"Legenda do reel: {legenda.strip()}\n\nTranscrição: {base}"
    r = _openai().chat.completions.create(
        model="gpt-4o",
        temperature=0.7,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM_YT},
            {"role": "user", "content": conteudo},
        ],
    )
    data = json.loads(r.choices[0].message.content)
    return {
        "youtube_title": (data.get("youtube_title") or "").strip()[:100],
        "youtube_description": (data.get("youtube_description") or "").strip(),
    }
