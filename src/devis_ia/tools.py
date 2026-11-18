"""Définition des outils exposés à Claude et exécution côté serveur (lecture seule sur Divalto)."""
from __future__ import annotations

from .divalto import DivaltoClient

LIGNE_SCHEMA = {
    "type": "object",
    "properties": {
        "designation_demandee": {"type": "string", "description": "Texte du client pour cette ligne"},
        "reference_demandee": {"type": ["string", "null"], "description": "Référence citée par le client, si présente"},
        "code_article": {"type": ["string", "null"], "description": "Code article Divalto, uniquement s'il a été renvoyé par rechercher_article"},
        "designation_article": {"type": ["string", "null"]},
        "quantite": {"type": ["number", "null"]},
        "unite": {"type": ["string", "null"], "description": "P (pièce) ou M (mètre) comme dans Divalto"},
        "longueur_mm": {"type": ["number", "null"]},
        "prix_unitaire": {"type": ["number", "null"], "description": "Uniquement la valeur renvoyée par prix_article, sinon null"},
        "delai": {"type": ["string", "null"]},
        "statut": {"type": "string", "enum": ["ok", "a_verifier", "introuvable"]},
        "remarque": {"type": ["string", "null"]},
    },
    "required": ["designation_demandee", "statut"],
}

PROPOSER_DEVIS = {
    "name": "proposer_devis",
    "description": (
        "Rend la proposition finale au collaborateur Mafdel. A appeler une seule fois, quand l'analyse est terminée. "
        "Ne contient jamais de prix inventé : prix_unitaire vient de prix_article ou vaut null."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "intention": {
                "type": "string",
                "enum": ["nouveau_devis", "mise_a_jour_devis", "variantes_devis_existant", "demande_info_ou_distributeur", "autre"],
            },
            "client": {
                "type": "object",
                "properties": {
                    "statut": {"type": "string", "enum": ["trouve", "ambigu", "introuvable"]},
                    "code_client": {"type": ["string", "null"]},
                    "nom_detecte": {"type": ["string", "null"]},
                    "contact": {"type": ["string", "null"]},
                    "email": {"type": ["string", "null"]},
                    "adresse": {"type": ["string", "null"]},
                    "commentaire": {"type": ["string", "null"]},
                },
                "required": ["statut"],
            },
            "reference_client": {"type": ["string", "null"], "description": "N° de demande de prix / référence du client"},
            "devis_source": {"type": ["string", "null"], "description": "N° du devis existant concerné, le cas échéant"},
            "lignes": {"type": "array", "items": LIGNE_SCHEMA},
            "variantes": {
                "type": "array",
                "description": "Pour variantes_devis_existant : une entrée par variante",
                "items": {
                    "type": "object",
                    "properties": {"libelle": {"type": "string"}, "lignes": {"type": "array", "items": LIGNE_SCHEMA}},
                    "required": ["libelle", "lignes"],
                },
            },
            "points_a_verifier": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["intention", "client", "lignes", "points_a_verifier"],
    },
}

DIVALTO_TOOLS = [
    {
        "name": "rechercher_client",
        "description": "Cherche un client dans Divalto par nom/ville, adresse e-mail (ou domaine) ou code. Renvoie les candidats avec un score. Le client réel est souvent dans un message transféré, pas dans l'expéditeur.",
        "input_schema": {"type": "object", "properties": {"texte": {"type": "string"}, "email": {"type": "string"}, "code": {"type": "string"}}},
    },
    {
        "name": "historique_devis",
        "description": "Liste les derniers devis d'un client (numéro, date, référence client).",
        "input_schema": {"type": "object", "properties": {"code_client": {"type": "string"}, "limite": {"type": "integer"}}, "required": ["code_client"]},
    },
    {
        "name": "lire_devis",
        "description": "Lit un devis Divalto existant avec ses lignes (pour le reprendre ou le décliner en variantes).",
        "input_schema": {"type": "object", "properties": {"numero": {"type": "string"}}, "required": ["numero"]},
    },
    {
        "name": "rechercher_article",
        "description": "Cherche des articles Divalto par code exact ou texte libre (famille, couleur, section, revêtement...). Les codes sont structurés (ex. DFVR22SPI0001).",
        "input_schema": {"type": "object", "properties": {"texte": {"type": "string"}, "code": {"type": "string"}}},
    },
    {
        "name": "prix_article",
        "description": "Prix unitaire officiel Divalto d'un article pour un client et une quantité. Seule source de prix autorisée.",
        "input_schema": {"type": "object", "properties": {"code_article": {"type": "string"}, "code_client": {"type": "string"}, "quantite": {"type": "number"}},
                         "required": ["code_article", "code_client", "quantite"]},
    },
    {
        "name": "delai_article",
        "description": "Stock et délai de livraison/fabrication d'un article pour une quantité.",
        "input_schema": {"type": "object", "properties": {"code_article": {"type": "string"}, "quantite": {"type": "number"}}, "required": ["code_article", "quantite"]},
    },
]

ALL_TOOLS = DIVALTO_TOOLS + [PROPOSER_DEVIS]


def execute_tool(divalto: DivaltoClient, name: str, args: dict):
    """Exécute un outil Divalto (lecture seule). Lève ValueError pour un outil inconnu."""
    if name == "rechercher_client":
        return divalto.rechercher_client(args.get("texte", ""), args.get("email", ""), args.get("code", ""))
    if name == "historique_devis":
        return divalto.historique_devis(args["code_client"], int(args.get("limite", 10)))
    if name == "lire_devis":
        return divalto.lire_devis(args["numero"]) or {"erreur": "devis introuvable"}
    if name == "rechercher_article":
        return divalto.rechercher_article(args.get("texte", ""), args.get("code", ""))
    if name == "prix_article":
        return divalto.prix_article(args["code_article"], args["code_client"], float(args["quantite"]))
    if name == "delai_article":
        return divalto.delai_article(args["code_article"], float(args["quantite"]))
    raise ValueError(f"outil inconnu : {name}")
