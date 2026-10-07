"""Registro das instruções de copy: ver todas num lugar e editar sem deploy.

O que importa aqui é a regra de precedência — o que o Pedro escreve vence o que
está no código, e apagar devolve o padrão — porque é ela que decide o texto que a
IA recebe.
"""
from test_lab_api import cliente


def _lista(cliente):
    return {i["chave"]: i for i in cliente.get("/lab/api/copy").json()["instrucoes"]}


def test_exige_sessao(cliente):
    cliente.post("/lab/api/sair")
    assert cliente.get("/lab/api/copy").status_code == 401
    assert cliente.put("/lab/api/copy/carrossel", json={"texto": "x"}).status_code == 401


def test_mostra_as_quatro_instrucoes_em_uso(cliente):
    d = _lista(cliente)
    assert set(d) == {"carrossel", "abertura", "legenda_reel", "seo_youtube"}
    for i in d.values():
        assert i["texto"].strip(), "instrução vazia não existe"
        assert i["onde"].strip(), "a tela precisa dizer onde cada uma é usada"
        assert i["personalizado"] is False


def test_o_texto_mostrado_e_o_mesmo_que_a_ia_recebe(cliente):
    """Se a tela mostrasse uma cópia, ela divergiria no primeiro deploy."""
    from app import lab_carrossel_ia, lab_prompts
    assert _lista(cliente)["carrossel"]["texto"] == lab_carrossel_ia.PROMPT
    assert lab_prompts.texto("carrossel") == lab_carrossel_ia.PROMPT


def test_editar_vence_o_padrao(cliente):
    from app import lab_prompts
    r = cliente.put("/lab/api/copy/carrossel", json={"texto": "Escreva assim e pronto."}).json()
    assert r["personalizado"] is True
    assert lab_prompts.texto("carrossel") == "Escreva assim e pronto."
    d = _lista(cliente)["carrossel"]
    assert d["texto"] == "Escreva assim e pronto."
    assert d["padrao"] != d["texto"], "o padrão continua à mão pra poder voltar"


def test_apagar_volta_ao_padrao_do_codigo(cliente):
    from app import lab_carrossel_ia, lab_prompts
    cliente.put("/lab/api/copy/carrossel", json={"texto": "Outra coisa."})
    r = cliente.put("/lab/api/copy/carrossel", json={"texto": "  "}).json()
    assert r["personalizado"] is False
    assert lab_prompts.texto("carrossel") == lab_carrossel_ia.PROMPT


def test_escrever_o_proprio_padrao_nao_marca_como_editado(cliente):
    """Senão o carrossel ficaria preso numa cópia do texto de hoje, e as
    melhorias que vierem no código nunca chegariam nele."""
    from app import lab_carrossel_ia
    r = cliente.put("/lab/api/copy/carrossel",
                    json={"texto": lab_carrossel_ia.PROMPT}).json()
    assert r["personalizado"] is False


def test_uma_instrucao_nao_mexe_na_outra(cliente):
    cliente.put("/lab/api/copy/carrossel", json={"texto": "Só do carrossel."})
    d = _lista(cliente)
    assert d["carrossel"]["personalizado"] is True
    assert d["abertura"]["personalizado"] is False


def test_chave_que_nao_existe(cliente):
    assert cliente.put("/lab/api/copy/inventada", json={"texto": "x"}).status_code == 404


def test_o_texto_editado_e_o_que_vai_pra_geracao(cliente, monkeypatch):
    """A prova final: o que o Pedro escreve na tela chega na chamada da IA."""
    from app import config, lab_carrossel_ia as mod
    monkeypatch.setattr(mod.config, "OPENAI_API_KEY", "sk-teste")
    cliente.put("/lab/api/copy/carrossel", json={"texto": "INSTRUÇÃO NOVA DO PEDRO"})

    vistos = {}

    class FakeResp:
        output_parsed = None

    def fake_parse(**kwargs):
        vistos["sistema"] = kwargs["input"][0]["content"]
        raise RuntimeError("parar aqui: já vi o que precisava")

    class FakeClient:
        def __init__(self, **kw):
            self.responses = type("R", (), {"parse": staticmethod(fake_parse)})()

    import sys
    import types
    falso = types.ModuleType("openai")
    falso.OpenAI = FakeClient
    monkeypatch.setitem(sys.modules, "openai", falso)

    r = cliente.post("/lab/api/carrosseis/gerar", json={"origem": "ideia", "ideia": "a" * 40})
    assert r.status_code == 502, "a chamada falha de propósito no dublê"
    assert vistos["sistema"] == "INSTRUÇÃO NOVA DO PEDRO"
