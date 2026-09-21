from test_lab_api import cliente


def test_exige_sessao(cliente):
    cliente.post("/lab/api/sair")
    assert cliente.post("/lab/api/cards/1/sugerir-abertura", json={"markdown": "ideia"}).status_code == 401


def test_usa_markdown_completo_sem_alterar_card(cliente, monkeypatch):
    from app import config, lab_hook
    monkeypatch.setattr(config, "OPENAI_API_KEY", "teste")
    card = cliente.post("/lab/api/cards", json={"titulo": "original"}).json()
    markdown = "*IDEIA CENTRAL*\nEdição atual\n*OBSERVAÇÕES*\nMinha intenção\n*REAGIR A*\nhttps://instagram.com/p/x/"
    recebido = []
    def gerar(texto):
        recebido.append(texto)
        return {"hook": "Hook específico.", "texto_tela": "Título curto"}
    monkeypatch.setattr(lab_hook, "sugerir", gerar)
    r = cliente.post(f"/lab/api/cards/{card['id']}/sugerir-abertura", json={"markdown": markdown})
    assert r.status_code == 200
    assert r.json() == {"hook": "Hook específico.", "texto_tela": "Título curto"}
    assert recebido == [markdown]
    assert cliente.get(f"/lab/api/cards/{card['id']}").json() == card


def test_configuracao_erros_e_lock(cliente, monkeypatch):
    from app import config, lab_hook
    cid = cliente.post("/lab/api/cards", json={"titulo": "a"}).json()["id"]
    url = f"/lab/api/cards/{cid}/sugerir-abertura"
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    assert cliente.post(url, json={"markdown": "a"}).status_code == 503
    monkeypatch.setattr(config, "OPENAI_API_KEY", "teste")
    lab_hook._em_uso.acquire()
    try:
        assert cliente.post(url, json={"markdown": "a"}).status_code == 409
    finally:
        lab_hook._em_uso.release()
    def falha(_):
        raise RuntimeError("segredo que não pode sair na resposta")
    monkeypatch.setattr(lab_hook, "sugerir", falha)
    r = cliente.post(url, json={"markdown": "a"})
    assert r.status_code == 502 and "segredo" not in r.text
    assert not lab_hook._em_uso.locked()


def test_entrada_invalida(cliente, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "OPENAI_API_KEY", "teste")
    cid = cliente.post("/lab/api/cards", json={"titulo": "a"}).json()["id"]
    url = f"/lab/api/cards/{cid}/sugerir-abertura"
    assert cliente.post(url, json={"markdown": " "}).status_code == 400
    assert cliente.post(url, json={"markdown": "x" * 30001}).status_code == 422
    assert cliente.post("/lab/api/cards/999999/sugerir-abertura", json={"markdown": "a"}).status_code == 404
