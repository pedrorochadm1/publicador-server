"""Aba Carrossel: documento, imagens de origem e a ponte com a fila do publicador."""
from test_lab_api import cliente

# JPEG mínimo: só o cabeçalho importa pra validação do servidor.
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


def _novo(cliente, **extra):
    return cliente.post("/lab/api/carrosseis", json={"titulo": "Pseudo-hipo", **extra}).json()


def test_exige_sessao(cliente):
    cliente.post("/lab/api/sair")
    assert cliente.get("/lab/api/carrosseis").status_code == 401
    assert cliente.post("/lab/api/carrosseis", json={}).status_code == 401


def test_cria_com_um_slide_em_branco_e_idempotente(cliente):
    a = _novo(cliente, client_uuid="u-1")
    assert a["status"] == "rascunho"
    assert len(a["slides"]) == 1 and a["slides"][0]["header"] is True
    b = _novo(cliente, client_uuid="u-1")
    assert a["id"] == b["id"]
    lista = cliente.get("/lab/api/carrosseis").json()
    assert [c["id"] for c in lista["carrosseis"]] == [a["id"]]
    assert lista["tiktok_max_fotos"] >= 1


def test_autosave_limpa_slides(cliente):
    c = _novo(cliente)
    slides = [
        {"texto": "Capa", "imagem": {"arquivo": "a" * 32 + ".jpg", "tamanho": "GG", "zoom": 9, "cx": -1}},
        {"texto": "Dois", "header": False, "imagem": {"arquivo": "../etc/passwd"}, "lixo": 1},
    ]
    d = cliente.patch(f"/lab/api/carrosseis/{c['id']}", json={"slides": slides, "legenda": "E você?"}).json()
    assert d["legenda"] == "E você?"
    s1, s2 = d["slides"]
    assert s1["imagem"] == {"arquivo": "a" * 32 + ".jpg", "tamanho": "GG", "zoom": 5, "cx": 0, "cy": 0.5}
    assert s2["header"] is False and s2["imagem"] is None and "lixo" not in s2


def test_limite_de_dez_slides(cliente):
    c = _novo(cliente)
    r = cliente.patch(f"/lab/api/carrosseis/{c['id']}", json={"slides": [{}] * 11})
    assert r.status_code == 400


def test_imagem_de_origem_sobe_e_volta(cliente):
    r = cliente.post("/lab/api/carrossel/imagens", files={"arquivo": ("foto.png", b"\x89PNG..", "image/png")})
    nome = r.json()["arquivo"]
    assert nome.endswith(".png")
    assert cliente.get(f"/lab/api/carrossel/imagens/{nome}").content == b"\x89PNG.."
    assert cliente.get("/lab/api/carrossel/imagens/..%2Fagenda.db").status_code == 404
    ruim = cliente.post("/lab/api/carrossel/imagens", files={"arquivo": ("x.gif", b"GIF", "image/gif")})
    assert ruim.status_code == 400
    # Extensão de imagem com conteúdo que não é imagem: barrado pelo cabeçalho.
    html = cliente.post("/lab/api/carrossel/imagens", files={"arquivo": ("f.png", b"<html>", "image/png")})
    assert html.status_code == 400


def _publicar(cliente, cid, n=3, **form):
    files = [("slides", (f"s{i}.jpg", JPEG, "image/jpeg")) for i in range(n)]
    return cliente.post(f"/lab/api/carrosseis/{cid}/publicar", files=files, data=form)


def test_publicar_exige_legenda(cliente):
    c = _novo(cliente)
    assert _publicar(cliente, c["id"]).status_code == 400


def test_publicar_cria_post_em_ordem_com_tiktok_igual(cliente):
    from app import db
    c = _novo(cliente, legenda="Você já teve isso?")
    d = _publicar(cliente, c["id"], n=3).json()
    assert d["status"] == "agendado"
    post = db.get_post(d["post_id"])
    assert len(post["imagens"]) == 3 and all(n.endswith(".jpg") for n in post["imagens"])
    assert post["caption"] == post["tiktok_caption"] == "Você já teve isso?"


def test_acima_do_teto_do_buffer_tiktok_fica_de_fora(cliente):
    from app import buffer_api, db
    c = _novo(cliente, legenda="E aí?")
    d = _publicar(cliente, c["id"], n=buffer_api.MAX_FOTOS + 1).json()
    assert db.get_post(d["post_id"])["tiktok_caption"] == ""


def test_publicar_rejeita_png_e_horario_passado(cliente):
    c = _novo(cliente, legenda="E aí?")
    files = [("slides", ("s.png", b"\x89PNG", "image/png"))]
    assert cliente.post(f"/lab/api/carrosseis/{c['id']}/publicar", files=files).status_code == 400
    assert _publicar(cliente, c["id"], agendar_para="2020-01-01T07:00").status_code == 400


def test_reagendar_substitui_e_cancelar_volta_pra_rascunho(cliente):
    from app import db
    c = _novo(cliente, legenda="E aí?")
    a = _publicar(cliente, c["id"], agendar_para="2099-01-01T07:00").json()
    b = _publicar(cliente, c["id"], agendar_para="2099-01-02T07:00").json()
    assert db.get_post(a["post_id"])["status"] == "cancelado"
    assert b["publicacao"]["status"] == "agendado"
    d = cliente.post(f"/lab/api/carrosseis/{c['id']}/cancelar").json()
    assert d["status"] == "rascunho"


def test_publicado_nao_republica_mas_duplica(cliente):
    from app import db
    c = _novo(cliente, legenda="E aí?")
    d = _publicar(cliente, c["id"]).json()
    db.marcar(d["post_id"], "publicado", ig_post_id="123")
    assert _publicar(cliente, c["id"]).status_code == 409
    copia = cliente.post(f"/lab/api/carrosseis/{c['id']}/duplicar").json()
    assert copia["status"] == "rascunho" and copia["legenda"] == "E aí?"


def test_arquivar_some_da_lista(cliente):
    c = _novo(cliente)
    assert cliente.delete(f"/lab/api/carrosseis/{c['id']}").json() == {"ok": True}
    assert cliente.get("/lab/api/carrosseis").json()["carrosseis"] == []
