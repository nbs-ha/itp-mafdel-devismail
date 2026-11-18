"""Boucle agent : mail -> appels d'outils Divalto -> proposition structurée -> garde-fous."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import anthropic

from .divalto import DivaltoClient
from .eml_parser import ParsedMail, to_user_content
from .prompts import SYSTEM_PROMPT
from .tools import ALL_TOOLS, execute_tool

DEFAULT_MODEL = os.environ.get("DEVIS_IA_MODEL", "claude-opus-5-5")
MAX_ITERATIONS = 20


@dataclass
class Resultat:
    proposition: dict | None
    alertes: list[str] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)


def _create(client, model: str, messages: list[dict], max_tokens: int):
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        system=SYSTEM_PROMPT,
        tools=ALL_TOOLS,
        messages=messages,
        thinking={"type": "adaptive"},
        # Pas de tool_choice forcé : non supporté par certains modèles ; le prompt impose proposer_devis.
    )
    if os.environ.get("DEVIS_IA_FALLBACKS", "1") == "1":
        # Repli serveur en cas de refus par les classifieurs de sécurité (voir doc API).
        return client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
    return client.messages.create(**kwargs)


def verifier_proposition(proposition: dict, prix_vus: dict[str, float], articles_vus: set[str]) -> list[str]:
    """Garde-fous : un prix ou un code que Divalto n'a pas renvoyé est supprimé et signalé."""
    alertes: list[str] = []

    def check(lignes: list[dict], prefixe: str):
        for i, l in enumerate(lignes, 1):
            code = l.get("code_article")
            if code and code not in articles_vus:
                alertes.append(f"{prefixe}ligne {i}: code article {code} non issu de Divalto, retiré")
                l["code_article"] = None
                l["statut"] = "a_verifier"
            p = l.get("prix_unitaire")
            if p is not None:
                if l.get("code_article") is None or abs(prix_vus.get(l["code_article"], float("nan")) - p) > 1e-9:
                    alertes.append(f"{prefixe}ligne {i}: prix {p} non confirmé par Divalto, retiré")
                    l["prix_unitaire"] = None
                    l["statut"] = "a_verifier"

    check(proposition.get("lignes", []), "")
    for v in proposition.get("variantes", []) or []:
        check(v.get("lignes", []), f"[{v.get('libelle')}] ")
    return alertes


class SessionDevis:
    """Une conversation d'analyse : première analyse du mail, puis corrections du collaborateur."""

    def __init__(self, divalto: DivaltoClient, client=None, model: str = DEFAULT_MODEL, max_tokens: int = 16000):
        self.divalto = divalto
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens
        self.messages: list[dict] = []
        self.prix_vus: dict[str, float] = {}
        self.articles_vus: set[str] = set()
        self._pending: list[dict] = []  # tool_result de proposer_devis, à joindre au tour suivant
        self.tokens_in = self.tokens_out = 0

    def analyser(self, mail: ParsedMail) -> Resultat:
        return self._run(to_user_content(mail))

    def corriger(self, message: str, proposition_actuelle: dict | None = None) -> Resultat:
        """Message libre du collaborateur. proposition_actuelle = état édité dans le panneau (source de vérité)."""
        texte = message
        if proposition_actuelle is not None:
            texte = ("Voici la proposition actuelle telle que le collaborateur l'a modifiée (elle fait foi) :\n"
                     + json.dumps(proposition_actuelle, ensure_ascii=False) + "\n\nDemande : " + message)
        return self._run([{"type": "text", "text": texte}])

    def _run(self, content: list[dict]) -> Resultat:
        self.messages.append({"role": "user", "content": self._pending + content})
        self._pending = []
        res = Resultat(proposition=None)
        for _ in range(MAX_ITERATIONS):
            r = _create(self.client, self.model, self.messages, self.max_tokens)
            self.tokens_in += getattr(r.usage, "input_tokens", 0) or 0
            self.tokens_out += getattr(r.usage, "output_tokens", 0) or 0
            if r.stop_reason == "refusal":
                res.alertes.append("Le modèle a refusé la requête : traitement manuel.")
                break
            if r.stop_reason == "max_tokens":
                res.alertes.append("Réponse tronquée (max_tokens) : traitement manuel.")
                break
            uses = [b for b in r.content if b.type == "tool_use"]
            if not uses:
                res.alertes.append("Le modèle n'a pas appelé proposer_devis.")
                break
            self.messages.append({"role": "assistant", "content": r.content})
            results, final = [], None
            for u in uses:
                if u.name == "proposer_devis":
                    final = u.input
                    results.append({"type": "tool_result", "tool_use_id": u.id, "content": "enregistré"})
                    continue
                try:
                    out, is_err = execute_tool(self.divalto, u.name, u.input), False
                except Exception as exc:  # erreur outil renvoyée au modèle, pas fatale
                    out, is_err = {"erreur": str(exc)}, True
                res.trace.append({"outil": u.name, "entree": u.input, "sortie": out})
                self._memoriser(u.name, out)
                results.append({"type": "tool_result", "tool_use_id": u.id,
                                "content": json.dumps(out, ensure_ascii=False), **({"is_error": True} if is_err else {})})
            if final is not None:
                res.proposition = final
                res.alertes += verifier_proposition(final, self.prix_vus, self.articles_vus)
                self._pending = results  # sera envoyé avec le prochain message
                break
            self.messages.append({"role": "user", "content": results})
        else:
            res.alertes.append("Nombre maximal d'itérations atteint.")
        res.usage = {"input_tokens": self.tokens_in, "output_tokens": self.tokens_out}
        return res

    def _memoriser(self, nom: str, out) -> None:
        if nom == "prix_article" and isinstance(out, dict) and out.get("prix_disponible"):
            self.prix_vus[out["code_article"]] = out["prix_unitaire"]
        elif nom == "rechercher_article" and isinstance(out, list):
            self.articles_vus.update(a["code"] for a in out)
        elif nom == "lire_devis" and isinstance(out, dict):
            self.articles_vus.update(l["code_article"] for l in out.get("lignes", []))


def analyser_mail(mail: ParsedMail, divalto: DivaltoClient, client=None, model: str = DEFAULT_MODEL,
                  max_tokens: int = 16000) -> Resultat:
    return SessionDevis(divalto, client, model, max_tokens).analyser(mail)
