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

## Tester en local (sans Outlook, sans certificat)

```bash
pip install -e ".[dev]"
python -m pytest                     # tests sans réseau (faux client)
```

**1. CLI sur des `.eml`** (vrai appel Claude, Divalto simulé) :
```bash
export ANTHROPIC_API_KEY=...
devis-ia samples/*.eml --divalto mock --out out
```
Les mails réels vont dans `samples/` (ignoré par git : données clients). Modèle : `DEVIS_IA_MODEL` (défaut `claude-opus-5-5`). Repli serveur en cas de refus : actif par défaut, désactivable avec `DEVIS_IA_FALLBACKS=0`.

**2. Panneau dans un navigateur** (HTTP local, un mail d'exemple intégré remplace Outlook) :
```bash
DEVIS_IA_TOKEN=secret uvicorn devis_ia.api:app --port 8000
# puis http://localhost:8000/addin/taskpane.html (jeton à saisir dans Réglages)
```

**3. Mode démo** (sans clé Claude ni Divalto) : `DEVIS_IA_DEMO=1` rejoue un scénario préenregistré à travers la vraie boucle et les vrais garde-fous.
```bash
DEVIS_IA_DEMO=1 DEVIS_IA_TOKEN=secret uvicorn devis_ia.api:app --port 8000
```

## API interne (appelée par le panneau)

| Route | Rôle |
|---|---|
| `POST /analyse` | reçoit le mail (objet, expéditeur, corps, pièces jointes PDF en base64), renvoie `session_id`, la proposition, les alertes |
| `POST /sessions/{id}/corriger` | message libre du collaborateur + proposition éditée dans le panneau ; renvoie la proposition mise à jour |
| `POST /sessions/{id}/creer-devis` | création dans Divalto **après validation** ; refuse (422) s'il manque client, code article ou quantité ; n'envoie aucun prix |
| `GET /sante` | contrôle |
| `GET /manifest.xml` | manifeste du complément (déploiement uniquement) |

Authentification : en-tête `X-API-Key` = `DEVIS_IA_TOKEN` (POC ; à remplacer par l'authentification Windows/Entra pour le pilote). Sessions en mémoire, un seul processus.

Le panneau (`src/devis_ia/static/`) est en JavaScript simple, servi par l'API elle-même (`/addin/...`) : un seul service, pas de CORS. Il affiche le client, les lignes (éditables), les points à vérifier, le chat de correction et le bouton de création (confirmation en deux clics). Les prix sont en lecture seule ; modifier un article ou une quantité invalide le prix, à recalculer via le chat.

## Déploiement (complément Outlook)

Tout ce qui suit n'est nécessaire que pour le pilote, pas pour les tests ci-dessus.

### VM Windows interne (collaborateurs en réseau interne)
```powershell
py -3.12 -m venv .venv ; .venv\Scripts\pip install .
$env:ANTHROPIC_API_KEY="..." ; $env:DEVIS_IA_TOKEN="..." ; $env:DEVIS_IA_DIVALTO="ws"
$env:DEVIS_IA_PUBLIC_URL="https://<nom-du-serveur>:8443"
.venv\Scripts\uvicorn devis_ia.api:app --host 0.0.0.0 --port 8443 --ssl-keyfile cle.pem --ssl-certfile cert.pem
```
- Outlook exige du **HTTPS** : certificat serveur émis par l'autorité interne de Mafdel, de confiance sur les postes (GPO), dont le nom correspond à celui de l'URL du manifeste.
- A installer comme service Windows (ex. NSSM) avec redémarrage automatique ; secrets hors du code.
- Accès sortant autorisé uniquement vers `api.anthropic.com` ; accès aux web services Divalto en interne avec un compte technique dédié.

### Installation du complément sur un poste (Microsoft 365)
1. Télécharger `https://<nom-du-serveur>:8443/manifest.xml`.
2. Outlook : Obtenir des compléments > Mes compléments > Compléments personnalisés > Ajouter à partir d'un fichier > choisir le manifeste. (Intitulés à confirmer sur votre version.) Bouton **Devis IA** dans le ruban de lecture d'un mail.
3. Désinstallation : même écran, menu « … » du complément > Supprimer. Rien n'est installé sur le poste.
4. Le poste doit pouvoir charger `https://appsforoffice.microsoft.com/lib/1/hosted/office.js` (Office.js est requis par Outlook).
5. Au premier usage, saisir le jeton dans Réglages (POC).

**Non testé dans un vrai Outlook** : le manifeste, la lecture des pièces jointes et le chargement du panneau restent à valider sur un poste Windows. Le panneau lui-même a été vérifié dans Chromium. Limites actuelles : le panneau n'est pas épinglé (il se ferme en changeant de mail) ; la création de devis ne porte que sur les lignes principales, pas sur les variantes.

## À faire pour passer du POC au pilote
1. **Brancher les web services Divalto** dans `DivaltoWebServiceClient` : recherche client, devis d'un client / lecture d'un devis, recherche article, tarification (prix client et remises), stock/délai, et **création de devis** (non implémentée ici volontairement : uniquement après validation humaine). Contrat d'API à obtenir auprès de l'intégrateur.
2. Remplacer `data/mock_divalto.json` par les vraies données pour la mesure de qualité.
3. Constituer 100 à 200 anciens mails avec le devis réellement émis (vérité terrain) et mesurer : client retrouvé, articles, quantités/unités, intention.
4. Complément Outlook (Office.js) + panneau de correction + chat ; la clé API reste côté backend.
5. RGPD : accord de traitement et rétention des données de l'API ; authentification du backend.

Note : les prix et codes articles de `data/mock_divalto.json` proviennent uniquement des PDF d'exemple ; ils ne constituent pas un référentiel.
