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


def analyser_mail(mail: ParsedMail, divalto: DivaltoClient, client=None, model: str = DEFAULT_MODEL,
                  max_tokens: int = 16000) -> Resultat:
    client = client or anthropic.Anthropic()
    messages: list[dict] = [{"role": "user", "content": to_user_content(mail)}]
    res = Resultat(proposition=None)
    prix_vus: dict[str, float] = {}
    articles_vus: set[str] = set()
    tokens_in = tokens_out = 0

    for _ in range(MAX_ITERATIONS):
        r = _create(client, model, messages, max_tokens)
        tokens_in += getattr(r.usage, "input_tokens", 0) or 0
        tokens_out += getattr(r.usage, "output_tokens", 0) or 0
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
        messages.append({"role": "assistant", "content": r.content})
        results = []
        final = None
        for u in uses:
            if u.name == "proposer_devis":
                final = u.input
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": "enregistré"})
                continue
            try:
                out = execute_tool(divalto, u.name, u.input)
                is_err = False
            except Exception as exc:  # erreur outil renvoyée au modèle, pas fatale
                out, is_err = {"erreur": str(exc)}, True
            res.trace.append({"outil": u.name, "entree": u.input, "sortie": out})
            if u.name == "prix_article" and isinstance(out, dict) and out.get("prix_disponible"):
                prix_vus[out["code_article"]] = out["prix_unitaire"]
            if u.name == "rechercher_article" and isinstance(out, list):
                articles_vus.update(a["code"] for a in out)
            if u.name == "lire_devis" and isinstance(out, dict):
                articles_vus.update(l["code_article"] for l in out.get("lignes", []))
            results.append({"type": "tool_result", "tool_use_id": u.id,
                            "content": json.dumps(out, ensure_ascii=False), **({"is_error": True} if is_err else {})})
        if final is not None:
            res.proposition = final
            res.alertes += verifier_proposition(final, prix_vus, articles_vus)
            break
        messages.append({"role": "user", "content": results})
    else:
        res.alertes.append("Nombre maximal d'itérations atteint.")

    res.usage = {"input_tokens": tokens_in, "output_tokens": tokens_out}
    return res
