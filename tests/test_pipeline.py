from email.message import EmailMessage
from types import SimpleNamespace as NS

from devis_ia.agent import analyser_mail, verifier_proposition
from devis_ia.divalto import MockDivalto
from devis_ia.eml_parser import clean_body, parse_eml, to_user_content


def make_eml(tmp_path):
    m = EmailMessage()
    m["Subject"] = "TR: demande de prix"
    m["From"] = "Lilian SUAT <lilian.suat@mafdel.fr>"
    m["To"] = "contact@mafdel.fr"
    m.set_content("Bonjour\n\n<https://img.example/logo.png>\n\nDe : Jean <j@sefitransmission.fr>\n2 rouleaux de 30 m DFVR22SPI0001\n")
    m.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf", filename="dde.pdf")
    m.add_attachment(b"\x89PNG", maintype="image", subtype="png", filename="logo.png")
    p = tmp_path / "t.eml"
    p.write_bytes(bytes(m))
    return p


def test_parse_eml(tmp_path):
    mail = parse_eml(make_eml(tmp_path))
    assert "img.example" not in mail.body and "2 rouleaux" in mail.body
    assert [a.filename for a in mail.attachments] == ["dde.pdf"]
    assert mail.ignored_attachments == ["logo.png"]
    blocks = to_user_content(mail)
    assert blocks[0]["type"] == "document" and blocks[-1]["type"] == "text"


def test_clean_body_keeps_text():
    assert clean_body("a\n<https://x.y/z>\n\n\n\nb") == "a\n\nb"


def tool_use(i, name, **inp):
    return NS(type="tool_use", id=f"tu{i}", name=name, input=inp)


class FakeClient:
    """Rejoue des réponses scriptées (aucun appel réseau)."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.beta = NS(messages=NS(create=self.create))
        self.messages = NS(create=self.create)
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        content = self.steps.pop(0)
        stop = "tool_use" if any(b.type == "tool_use" for b in content) else "end_turn"
        return NS(content=content, stop_reason=stop, usage=NS(input_tokens=10, output_tokens=5))


def test_agent_flow_and_guardrail(tmp_path):
    mail = parse_eml(make_eml(tmp_path))
    proposition = {
        "intention": "nouveau_devis",
        "client": {"statut": "trouve", "code_client": "CSEFIS14"},
        "lignes": [
            {"designation_demandee": "DFVR22SPI0001", "code_article": "DFVR22SPI0001", "quantite": 60, "unite": "M",
             "prix_unitaire": 20.9703, "statut": "ok"},
            {"designation_demandee": "truc", "code_article": "INVENTE", "prix_unitaire": 12.0, "statut": "ok"},
        ],
        "points_a_verifier": [],
    }
    fake = FakeClient([
        [tool_use(1, "rechercher_client", email="j@sefitransmission.fr")],
        [tool_use(2, "rechercher_article", code="DFVR22SPI0001")],
        [tool_use(3, "prix_article", code_article="DFVR22SPI0001", code_client="CSEFIS14", quantite=60)],
        [tool_use(4, "proposer_devis", **proposition)],
    ])
    res = analyser_mail(mail, MockDivalto(), client=fake)
    l1, l2 = res.proposition["lignes"]
    assert l1["prix_unitaire"] == 20.9703 and l1["statut"] == "ok"
    assert l2["code_article"] is None and l2["prix_unitaire"] is None and l2["statut"] == "a_verifier"
    assert len(res.alertes) == 2 and len(res.trace) == 3
    assert res.usage["input_tokens"] == 40


def test_invented_price_removed():
    p = {"lignes": [{"code_article": "J60", "prix_unitaire": 1.0, "statut": "ok"}]}
    al = verifier_proposition(p, {"J60": 167.69}, {"J60"})
    assert p["lignes"][0]["prix_unitaire"] is None and al


def test_mock_divalto():
    d = MockDivalto()
    assert d.rechercher_client(email="x@sefitransmission.fr")[0]["code"] == "CSEFIS14"
    assert d.rechercher_article(texte="fer à souder MC50")[0]["code"] == "MC50"
    assert d.prix_article("DFBW3", "X", 1)["prix_disponible"] is False


# ---- API ----
from fastapi.testclient import TestClient  # noqa: E402

from devis_ia import api  # noqa: E402


def _client(monkeypatch, steps):
    monkeypatch.setenv("DEVIS_IA_TOKEN", "secret")
    fake = FakeClient(steps)
    api.app.dependency_overrides[api.get_client] = lambda: fake
    api.app.state.divalto = MockDivalto()
    return TestClient(api.app), fake


def test_api_auth(monkeypatch):
    c, _ = _client(monkeypatch, [])
    assert c.post("/analyse", json={"corps": "x"}).status_code == 401


def test_api_flow_chat_and_creation(monkeypatch):
    prop1 = {"intention": "nouveau_devis", "client": {"statut": "trouve", "code_client": "CSEFIS14"},
             "lignes": [{"designation_demandee": "x", "code_article": "J60", "quantite": 1, "statut": "ok"}],
             "points_a_verifier": []}
    prop2 = {**prop1, "lignes": [{"designation_demandee": "x", "code_article": "J60", "quantite": 3, "statut": "ok"}]}
    c, fake = _client(monkeypatch, [
        [tool_use(1, "rechercher_article", code="J60")], [tool_use(2, "proposer_devis", **prop1)],
        [tool_use(3, "proposer_devis", **prop2)],
    ])
    h = {"X-API-Key": "secret"}
    r = c.post("/analyse", headers=h, json={"corps": "Bonjour 1 pince J60", "objet": "ddp"})
    assert r.status_code == 200
    sid = r.json()["session_id"]
    r2 = c.post(f"/sessions/{sid}/corriger", headers=h, json={"message": "mets 3", "proposition_actuelle": prop1})
    assert r2.json()["proposition"]["lignes"][0]["quantite"] == 3
    # le tour suivant porte le tool_result en attente + le message (pas de tool_use orphelin)
    last_user = fake.calls[-1]["messages"][-2]["content"]
    assert last_user[0]["type"] == "tool_result" and last_user[-1]["type"] == "text"
    ok = c.post(f"/sessions/{sid}/creer-devis", headers=h, json={"proposition": r2.json()["proposition"]})
    assert ok.json()["devis"]["numero"].startswith("SIMUL")
    bad = c.post(f"/sessions/{sid}/creer-devis", headers=h, json={"proposition": {"client": {"statut": "introuvable"}, "lignes": []}})
    assert bad.status_code == 422 and "code client manquant" in bad.json()["detail"]["bloquants"]
    api.app.dependency_overrides.clear()


def test_demo_mode_flow(monkeypatch):
    monkeypatch.setenv("DEVIS_IA_TOKEN", "secret")
    monkeypatch.setenv("DEVIS_IA_DEMO", "1")
    api.app.dependency_overrides.clear()
    api.app.state.divalto = MockDivalto()
    c, h = TestClient(api.app), {"X-API-Key": "secret"}
    r = c.post("/analyse", headers=h, json={"corps": "bonjour"}).json()
    assert r["proposition"]["lignes"][0]["prix_unitaire"] == 238.07 and not r["alertes"]
    r2 = c.post(f"/sessions/{r['session_id']}/corriger", headers=h, json={"message": "mets 4 pièces"}).json()
    assert r2["proposition"]["lignes"][0]["quantite"] == 4
    r3 = c.post(f"/sessions/{r['session_id']}/corriger", headers=h, json={"message": "encore 7"}).json()
    assert r3["proposition"]["lignes"][0]["quantite"] == 7


def test_manifest_and_static(monkeypatch):
    monkeypatch.setenv("DEVIS_IA_PUBLIC_URL", "https://devis-ia.test:8443/")
    c = TestClient(api.app)
    m = c.get("/manifest.xml")
    assert m.status_code == 200 and "{{BASE_URL}}" not in m.text
    assert "https://devis-ia.test:8443/addin/taskpane.html" in m.text
    import xml.dom.minidom
    xml.dom.minidom.parseString(m.text)  # XML bien formé
    assert c.get("/addin/taskpane.html").status_code == 200
    assert c.get("/addin/taskpane.js").status_code == 200
