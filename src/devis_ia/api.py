"""API HTTP interne appelée par le complément Outlook.

Lancement : uvicorn devis_ia.api:app --host 0.0.0.0 --port 8443 --ssl-keyfile ... --ssl-certfile ...
Variables : DEVIS_IA_TOKEN (jeton partagé, obligatoire), DEVIS_IA_DIVALTO=mock|ws, ANTHROPIC_API_KEY.
Les sessions sont en mémoire : un seul processus (pas de multi-workers) pour ce POC.
"""
from __future__ import annotations

import base64
import hmac
import os
import time
import uuid

from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import SessionDevis
from .divalto import DivaltoClient, DivaltoWebServiceClient, MockDivalto
from .eml_parser import ATTACHMENT_TYPES, MAX_ATTACHMENT_BYTES, Attachment, ParsedMail, clean_body

SESSION_TTL_S = 4 * 3600

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Devis IA Mafdel", version="0.1.0")
_sessions: dict[str, tuple[float, SessionDevis]] = {}


def get_divalto() -> DivaltoClient:
    if not hasattr(app.state, "divalto"):
        app.state.divalto = MockDivalto() if os.environ.get("DEVIS_IA_DIVALTO", "mock") == "mock" else DivaltoWebServiceClient()
    return app.state.divalto


def get_client():
    """Client Anthropic ; surchargé dans les tests. En mode démo : faux client sans réseau."""
    if os.environ.get("DEVIS_IA_DEMO") == "1":
        from .demo import DemoClient
        return DemoClient()
    return None  # SessionDevis crée anthropic.Anthropic()


def auth(x_api_key: str = Header(default="")):
    token = os.environ.get("DEVIS_IA_TOKEN", "")
    if not token or not hmac.compare_digest(x_api_key, token):
        raise HTTPException(401, "jeton invalide")


class PieceJointe(BaseModel):
    nom: str
    type: str = "application/pdf"
    contenu_base64: str


class MailIn(BaseModel):
    objet: str = ""
    expediteur: str = ""
    destinataires: str = ""
    date: str = ""
    corps: str
    pieces_jointes: list[PieceJointe] = Field(default_factory=list)


class CorrectionIn(BaseModel):
    message: str
    proposition_actuelle: dict | None = None


class CreationIn(BaseModel):
    proposition: dict  # état validé et éventuellement édité par le collaborateur


def _purger():
    now = time.time()
    for k in [k for k, (t, _) in _sessions.items() if now - t > SESSION_TTL_S]:
        del _sessions[k]


def _reponse(sid: str, res):
    return {"session_id": sid, "proposition": res.proposition, "alertes": res.alertes, "usage": res.usage,
            "trace": [{"outil": t["outil"], "entree": t["entree"]} for t in res.trace]}


def _get(sid: str) -> SessionDevis:
    _purger()
    if sid not in _sessions:
        raise HTTPException(404, "session expirée ou inconnue : relancer l'analyse")
    _sessions[sid] = (time.time(), _sessions[sid][1])
    return _sessions[sid][1]


@app.get("/sante")
def sante():
    return {"ok": True}


@app.post("/analyse", dependencies=[Depends(auth)])
def analyse(mail: MailIn, divalto: DivaltoClient = Depends(get_divalto), client=Depends(get_client)):
    pjs, ignorees = [], []
    for pj in mail.pieces_jointes:
        try:
            data = base64.b64decode(pj.contenu_base64, validate=True)
        except Exception:
            raise HTTPException(422, f"pièce jointe illisible : {pj.nom}")
        if pj.type in ATTACHMENT_TYPES and 0 < len(data) <= MAX_ATTACHMENT_BYTES:
            pjs.append(Attachment(pj.nom, pj.type, data))
        else:
            ignorees.append(pj.nom)
    parsed = ParsedMail(mail.objet, mail.expediteur, mail.destinataires, mail.date, clean_body(mail.corps), pjs, ignorees)
    session = SessionDevis(divalto, client)
    res = session.analyser(parsed)
    sid = uuid.uuid4().hex
    _sessions[sid] = (time.time(), session)
    return _reponse(sid, res)


@app.post("/sessions/{sid}/corriger", dependencies=[Depends(auth)])
def corriger(sid: str, body: CorrectionIn):
    return _reponse(sid, _get(sid).corriger(body.message, body.proposition_actuelle))


def bloquants(p: dict) -> list[str]:
    """Conditions minimales avant création dans Divalto (le prix n'en fait pas partie : Divalto tarifie)."""
    pb = []
    if not (p.get("client") or {}).get("code_client"):
        pb.append("code client manquant")
    if not p.get("lignes"):
        pb.append("aucune ligne")
    for i, l in enumerate(p.get("lignes", []), 1):
        if not l.get("code_article"):
            pb.append(f"ligne {i}: code article manquant")
        if not (l.get("quantite") or 0) > 0:
            pb.append(f"ligne {i}: quantité manquante")
    return pb


@app.post("/sessions/{sid}/creer-devis", dependencies=[Depends(auth)])
def creer_devis(sid: str, body: CreationIn, divalto: DivaltoClient = Depends(get_divalto)):
    _get(sid)
    p = body.proposition
    pb = bloquants(p)
    if pb:
        raise HTTPException(422, {"bloquants": pb})
    lignes = [{k: l.get(k) for k in ("code_article", "quantite", "unite", "longueur_mm", "remarque")} for l in p["lignes"]]
    out = divalto.creer_devis(p["client"]["code_client"], p.get("reference_client"), lignes)
    return {"devis": out}


@app.get("/manifest.xml")
def manifest(request: Request):
    """Manifeste du complément Outlook. URL publique : DEVIS_IA_PUBLIC_URL, sinon déduite de la requête."""
    base = os.environ.get("DEVIS_IA_PUBLIC_URL", str(request.base_url)).rstrip("/")
    xml = (STATIC / "manifest.template.xml").read_text(encoding="utf-8").replace("{{BASE_URL}}", base)
    return Response(xml, media_type="application/xml")


app.mount("/addin", StaticFiles(directory=STATIC), name="addin")
