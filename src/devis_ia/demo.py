"""Faux client Claude pour le mode démo (DEVIS_IA_DEMO=1) : aucun appel réseau, scénario préenregistré.

Il passe par la vraie boucle (SessionDevis) : outils Divalto simulés et garde-fous réellement exécutés.
Sert à développer et montrer le complément Outlook avant d'avoir clé API et web services Divalto.
"""
from __future__ import annotations

import re
from itertools import count
from types import SimpleNamespace as NS

_ids = count(1)


def _use(name: str, **inp):
    return NS(type="tool_use", id=f"demo{next(_ids)}", name=name, input=inp)


def _proposition(quantite: float, note: str | None) -> dict:
    return {
        "intention": "nouveau_devis",
        "client": {"statut": "trouve", "code_client": "CSEFIS14", "nom_detecte": "SEFI SAS AGENCE DE LORIENT",
                   "contact": "Mélan LE LAY", "email": "mlelay@sefitransmission.fr",
                   "commentaire": "[DEMO] Client fixe : scénario préenregistré."},
        "reference_client": "DDP DEMO",
        "lignes": [
            {"designation_demandee": "Fer à souder MC50", "reference_demandee": "MC50", "code_article": "MC50",
             "designation_article": "Fer à souder MC50 avec régulation électronique (230V)", "quantite": quantite, "unite": "P",
             "prix_unitaire": 238.07, "delai": None, "statut": "ok", "remarque": note},
            {"designation_demandee": "Courroie sans fin 175 mm", "reference_demandee": None, "code_article": None,
             "designation_article": None, "quantite": 30, "unite": "P", "longueur_mm": 175, "prix_unitaire": None,
             "statut": "introuvable", "remarque": "[DEMO] Plusieurs articles possibles : à choisir."},
        ],
        "points_a_verifier": ["[DEMO] Réponse simulée : aucune analyse réelle du mail n'a été faite.",
                              "Confirmer le délai avec la production."],
    }


class DemoClient:
    def __init__(self):
        self.beta = NS(messages=NS(create=self.create))
        self.messages = NS(create=self.create)

    def create(self, **kw):
        msgs = kw["messages"]
        last = msgs[-1]["content"]
        texts = [b["text"] for b in last if isinstance(b, dict) and b.get("type") == "text"]
        usage = NS(input_tokens=0, output_tokens=0)
        if texts:  # nouveau tour utilisateur
            t = texts[-1]
            deja_propose = any(b.name == "proposer_devis" for m in msgs if m["role"] == "assistant" for b in m["content"] if b.type == "tool_use")
            if deja_propose:  # correction du collaborateur
                m = re.search(r"\d+", t.split("Demande :", 1)[-1])
                q = float(m.group()) if m else 1
                content = [_use("proposer_devis", **_proposition(q, f"[DEMO] Quantité ajustée sur demande : {q:g}"))]
                return NS(content=content, stop_reason="tool_use", usage=usage)
            content = [_use("rechercher_client", email="mlelay@sefitransmission.fr")]
        else:
            vus = [b["tool_use_id"] for b in last if isinstance(b, dict) and b.get("type") == "tool_result"]
            names = [b.name for m in msgs if m["role"] == "assistant" for b in m["content"] if b.type == "tool_use"]
            n = len([x for x in names if x != "proposer_devis"])
            if n == 1:
                content = [_use("rechercher_article", texte="fer à souder MC50")]
            elif n == 2:
                content = [_use("prix_article", code_article="MC50", code_client="CSEFIS14", quantite=1)]
            else:
                content = [_use("proposer_devis", **_proposition(1, None))]
            assert vus is not None
        return NS(content=content, stop_reason="tool_use", usage=usage)
