# itp-mafdel-devismail

POC : proposer automatiquement un devis Divalto à partir d'un e-mail client (Mafdel), avec **validation humaine obligatoire** et **prix issus de Divalto uniquement**. Cible finale : complément Outlook qui déclenche l'analyse, panneau de correction, échange avec Claude, création du devis via les web services Divalto.

## Architecture

```
Outlook (complément Office.js)  ->  Backend  ->  API Claude (tool use)
                                       |             |
                                       |             +-- outils lecture seule --> web services Divalto
                                       +-- création du devis (après clic de validation) --> web service Divalto
```

Ce dépôt contient le coeur du backend (étape POC, sans Outlook) :

| Fichier | Rôle |
|---|---|
| `src/devis_ia/eml_parser.py` | lit un `.eml` : corps nettoyé, PDF joints (transmis tels quels à Claude) |
| `src/devis_ia/tools.py` | outils Divalto exposés à Claude + schéma de la proposition (`proposer_devis`) |
| `src/devis_ia/divalto.py` | interface `DivaltoClient`, `MockDivalto` (tests), `DivaltoWebServiceClient` (**à brancher**) |
| `src/devis_ia/agent.py` | boucle agent + garde-fous |
| `src/devis_ia/prompts.py` | consignes à Claude |
| `eval/attendus.json` | résultats attendus pour les 12 mails d'exemple |

### Garde-fous (dans le code, pas dans le prompt)
- un `code_article` qui n'a pas été renvoyé par Divalto pendant l'analyse est retiré ;
- un `prix_unitaire` différent de celui renvoyé par `prix_article` est retiré ;
- les lignes concernées passent en `a_verifier`, et la proposition porte la liste des `alertes`.

## Utilisation

```bash
pip install -e ".[dev]"
python -m pytest                     # tests sans réseau (faux client)
export ANTHROPIC_API_KEY=...         # nécessaire pour les vrais appels
devis-ia samples/*.eml --divalto mock --out out
```

Les mails réels vont dans `samples/` (ignoré par git : données clients). Modèle : `DEVIS_IA_MODEL` (défaut `claude-opus-5-5`). Repli serveur en cas de refus : actif par défaut, désactivable avec `DEVIS_IA_FALLBACKS=0`.

## API interne (appelée par le complément Outlook)

| Route | Rôle |
|---|---|
| `POST /analyse` | reçoit le mail (objet, expéditeur, corps, pièces jointes PDF en base64), renvoie `session_id`, la proposition, les alertes |
| `POST /sessions/{id}/corriger` | message libre du collaborateur + proposition éditée dans le panneau ; renvoie la proposition mise à jour |
| `POST /sessions/{id}/creer-devis` | création dans Divalto **après validation** ; refuse (422) s'il manque client, code article ou quantité ; n'envoie aucun prix |
| `GET /sante` | contrôle |

Authentification : en-tête `X-API-Key` = `DEVIS_IA_TOKEN` (POC ; à remplacer par l'authentification Windows/Entra pour le pilote). Sessions en mémoire, un seul processus.

### Déploiement retenu : VM Windows interne (collaborateurs en réseau interne)
```powershell
py -3.12 -m venv .venv ; .venv\Scripts\pip install .
$env:ANTHROPIC_API_KEY="..." ; $env:DEVIS_IA_TOKEN="..." ; $env:DEVIS_IA_DIVALTO="ws"
.venv\Scripts\uvicorn devis_ia.api:app --host 0.0.0.0 --port 8443 --ssl-keyfile cle.pem --ssl-certfile cert.pem
```
- Le complément Outlook exige du **HTTPS** : certificat émis par l'autorité interne de Mafdel, de confiance sur les postes (GPO).
- A installer comme service Windows (ex. NSSM) avec redémarrage automatique ; secrets hors du code.
- Accès sortant autorisé uniquement vers `api.anthropic.com` ; accès aux web services Divalto en interne avec un compte technique dédié.

## À faire pour passer du POC au pilote
1. **Brancher les web services Divalto** dans `DivaltoWebServiceClient` : recherche client, devis d'un client / lecture d'un devis, recherche article, tarification (prix client et remises), stock/délai, et **création de devis** (non implémentée ici volontairement : uniquement après validation humaine). Contrat d'API à obtenir auprès de l'intégrateur.
2. Remplacer `data/mock_divalto.json` par les vraies données pour la mesure de qualité.
3. Constituer 100 à 200 anciens mails avec le devis réellement émis (vérité terrain) et mesurer : client retrouvé, articles, quantités/unités, intention.
4. Complément Outlook (Office.js) + panneau de correction + chat ; la clé API reste côté backend.
5. RGPD : accord de traitement et rétention des données de l'API ; authentification du backend.

Note : les prix et codes articles de `data/mock_divalto.json` proviennent uniquement des PDF d'exemple ; ils ne constituent pas un référentiel.
