"""Registro único das instruções de copy que a IA recebe.

Elas nasceram espalhadas: uma em `lab_carrossel_ia.py`, outra em `lab_hook.py`,
outra em `copy_ia.py`, cada uma colada ao código que a usa. Isso é bom pra quem
lê o código e péssimo pro Pedro, que escreve o conteúdo e não abre o repositório:
não havia um lugar onde ver tudo que manda na voz dos textos.

Duas decisões:

* O PADRÃO CONTINUA NO CÓDIGO, junto de quem usa. Este módulo não copia texto
  nenhum: ele importa cada PROMPT de onde ele vive. Assim não existe uma segunda
  cópia pra divergir, e mexer no prompt num deploy continua sendo mexer num lugar
  só.

* O QUE O PEDRO EDITA MORA NO BANCO, e vence o padrão. Ele ajusta o tom pela
  própria tela, sem deploy e sem mim. Apagar o texto na tela volta ao padrão do
  código — por isso "voltar ao padrão" é só gravar vazio, e não guardar uma
  terceira versão em algum canto.

O import dos módulos é preguiçoso de propósito: eles importam este aqui de volta,
e no topo do arquivo isso seria uma referência circular.
"""
from . import lab_db

# chave → (nome na tela, onde é usado, atributo que guarda o padrão)
_ONDE = {
    "carrossel": (
        "Carrossel",
        "Botão Gerar com IA, na aba Carrossel. Escreve os slides e a legenda.",
        ("lab_carrossel_ia", "PROMPT"),
    ),
    "abertura": (
        "Abertura do reel",
        "Botão de sugerir abertura, dentro do card do Lab. Escreve o hook falado "
        "e o texto na tela.",
        ("lab_hook", "PROMPT"),
    ),
    "legenda_reel": (
        "Legenda do reel",
        "Publicação de vídeo. Escreve a legenda do Instagram e do TikTok a partir "
        "da transcrição.",
        ("copy_ia", "_SYSTEM"),
    ),
    "seo_youtube": (
        "SEO do YouTube",
        "Publicação de vídeo. Escreve o título e a descrição do YouTube Shorts.",
        ("copy_ia", "_SYSTEM_YT"),
    ),
}

CHAVES = tuple(_ONDE)


def padrao(chave: str) -> str:
    """O texto que está no código, ignorando o que o Pedro editou."""
    import importlib

    _, _, (modulo, atributo) = _ONDE[chave]
    mod = importlib.import_module(f".{modulo}", __package__)
    return getattr(mod, atributo)


def texto(chave: str) -> str:
    """O texto que vale agora: o editado, se houver, senão o do código."""
    salvos = lab_db.get_config().get("prompts") or {}
    escrito = str(salvos.get(chave) or "").strip()
    return escrito or padrao(chave)


def listar() -> list[dict]:
    saida = []
    for chave, (nome, onde, _) in _ONDE.items():
        pad = padrao(chave)
        atual = texto(chave)
        saida.append({
            "chave": chave,
            "nome": nome,
            "onde": onde,
            "texto": atual,
            "padrao": pad,
            "personalizado": atual != pad,
        })
    return saida


def definir(chave: str, novo: str) -> dict:
    """Grava o texto do Pedro. Vazio apaga e volta ao padrão do código."""
    if chave not in _ONDE:
        raise ValueError(f"instrução desconhecida: {chave}")
    salvos = dict(lab_db.get_config().get("prompts") or {})
    novo = (novo or "").strip()
    # Comparar com o padrão TAMBÉM sem as pontas: colar o texto de volta é a
    # forma mais natural de desfazer, e guardá-lo como personalização deixaria o
    # Pedro preso numa cópia do prompt de hoje, sem receber o que melhorar depois.
    if novo and novo != padrao(chave).strip():
        salvos[chave] = novo
    else:
        salvos.pop(chave, None)
    lab_db.set_config({"prompts": salvos})
    return {"chave": chave, "texto": texto(chave), "personalizado": bool(salvos.get(chave))}
