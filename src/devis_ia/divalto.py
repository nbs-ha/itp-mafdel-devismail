"""Accès Divalto : interface + implémentation simulée + squelette web services.

Règle du projet : les prix, délais et codes viennent EXCLUSIVEMENT de Divalto.
Le LLM ne calcule ni ne devine aucun prix.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Protocol


class DivaltoClient(Protocol):
    def rechercher_client(self, texte: str = "", email: str = "", code: str = "") -> list[dict]: ...
    def historique_devis(self, code_client: str, limite: int = 10) -> list[dict]: ...
    def lire_devis(self, numero: str) -> dict | None: ...
    def rechercher_article(self, texte: str = "", code: str = "") -> list[dict]: ...
    def prix_article(self, code_article: str, code_client: str, quantite: float) -> dict: ...
    def delai_article(self, code_article: str, quantite: float) -> dict: ...


def _norm(s: str) -> set[str]:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return {t for t in re.findall(r"[a-z0-9]+", s) if len(t) > 1}


def _score(query: str, target: str) -> float:
    q, t = _norm(query), _norm(target)
    return len(q & t) / len(q) if q else 0.0


class MockDivalto:
    """Divalto simulé à partir de data/mock_divalto.json (tests et démonstration)."""

    def __init__(self, path: str | Path | None = None):
        path = path or Path(__file__).resolve().parents[2] / "data" / "mock_divalto.json"
        self.data = json.loads(Path(path).read_text(encoding="utf-8"))

    def rechercher_client(self, texte="", email="", code=""):
        out = []
        for c in self.data["clients"]:
            s = 0.0
            if code and c["code"].lower() == code.lower():
                s = 1.0
            if email:
                dom = email.rsplit("@", 1)[-1].lower()
                if email.lower() in [e.lower() for e in c["emails"]]:
                    s = max(s, 1.0)
                elif dom in c["domaines"]:
                    s = max(s, 0.6)
            if texte:
                s = max(s, _score(texte, c["nom"] + " " + c["ville"]))
            if s >= 0.5:
                out.append({**c, "score": round(s, 2)})
        return sorted(out, key=lambda x: -x["score"])[:5]

    def historique_devis(self, code_client, limite=10):
        d = [x for x in self.data["devis"] if x["code_client"] == code_client]
        return [{k: x[k] for k in ("numero", "date", "reference_client")} for x in d[:limite]]

    def lire_devis(self, numero):
        return next((x for x in self.data["devis"] if x["numero"] == numero), None)

    def rechercher_article(self, texte="", code=""):
        out = []
        for a in self.data["articles"]:
            s = 1.0 if code and a["code"].lower() == code.lower() else 0.0
            if texte:
                s = max(s, _score(texte, a["code"] + " " + a["designation"]))
            if s >= 0.4:
                out.append({"code": a["code"], "designation": a["designation"], "unite": a["unite"], "score": round(s, 2)})
        return sorted(out, key=lambda x: -x["score"])[:8]

    def _article(self, code):
        return next((a for a in self.data["articles"] if a["code"] == code), None)

    def prix_article(self, code_article, code_client, quantite):
        a = self._article(code_article)
        if a is None:
            return {"erreur": f"article inconnu : {code_article}"}
        if a["prix"] is None:
            return {"code_article": code_article, "prix_disponible": False,
                    "message": "Prix non disponible dans Divalto : à traiter par le collaborateur"}
        return {"code_article": code_article, "prix_disponible": True, "prix_unitaire": a["prix"],
                "unite": a["unite"], "quantite": quantite, "source": "divalto"}

    def delai_article(self, code_article, quantite):
        if self._article(code_article) is None:
            return {"erreur": f"article inconnu : {code_article}"}
        return {"code_article": code_article, "delai": None, "message": "Délai non renseigné (simulation)"}


class DivaltoWebServiceClient:
    """Squelette pour les web services Divalto. A COMPLETER avec le contrat d'API réel.

    Configuration par variables d'environnement : DIVALTO_BASE_URL, DIVALTO_TOKEN (ou autre
    schéma d'authentification selon l'installation). Chaque méthode correspond à un web
    service à identifier / à faire exposer par l'intégrateur Divalto (voir README).
    """

    def __init__(self, base_url: str | None = None, token: str | None = None):
        self.base_url = base_url or os.environ.get("DIVALTO_BASE_URL", "")
        self.token = token or os.environ.get("DIVALTO_TOKEN", "")

    def _todo(self, ws: str):
        raise NotImplementedError(f"Web service Divalto à brancher : {ws}")

    def rechercher_client(self, texte="", email="", code=""): self._todo("recherche client")
    def historique_devis(self, code_client, limite=10): self._todo("liste des devis d'un client")
    def lire_devis(self, numero): self._todo("lecture d'un devis")
    def rechercher_article(self, texte="", code=""): self._todo("recherche article")
    def prix_article(self, code_article, code_client, quantite): self._todo("tarification (prix client, remises)")
    def delai_article(self, code_article, quantite): self._todo("stock / délai")
