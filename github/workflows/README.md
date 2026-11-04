# Gestion des etudiants — Flask + SQLite

Application complete de gestion d'etudiants : inscription, notes, paiements, exports CSV/Excel.
API REST JSON, interface web responsive (mobile / tablette / ordinateur) et **PWA installable**.

Version 4.0 — derniere mise a jour : 17 octobre 2026

> **Applications natives (.exe Windows / .apk Android)** : voir `BUILD.md`.

---

## 1. Contenu du projet

```
student-management-flask_v4/
├── app.py                     API Flask + persistance SQLite
├── requirements.txt           dependances Python
├── templates/
│   └── index.html             interface web (mobile-first, PWA)
├── static/
│   ├── manifest.json          manifeste PWA
│   ├── sw.js                  service worker
│   └── icons/                 icones 192/512/maskable/apple-touch
├── tests/
│   └── test_app.py            95 tests pytest
├── screenshots/               captures 390x844 / 820x1180 / 1440x900
├── Dockerfile
├── docker-compose.yml
├── .dockerignore
└── README.md
```

---

## 2. Installation locale

Prerequis : **Python 3.10 ou superieur**.

```bash
unzip student-management-flask_v4.zip
cd student-management-flask_v4

python3 -m venv .venv
source .venv/bin/activate            # Windows : .venv\Scripts\activate

pip install -r requirements.txt
```

### Lancement

```bash
export STUDENT_API_KEY="une-cle-longue-et-secrete"    # Windows : set STUDENT_API_KEY=...
python app.py
```

L'application ecoute sur `http://127.0.0.1:5000`.
La base SQLite `students.db` est creee automatiquement au premier demarrage.

Pour la rendre accessible depuis un telephone sur le meme reseau :

```bash
HOST=0.0.0.0 PORT=5000 python app.py
# puis http://<ip-locale-de-la-machine>:5000
```

### Variables d'environnement

| Variable | Defaut | Role |
|---|---|---|
| `STUDENT_API_KEY` | `dev-secret-key-change-me` | Cle attendue dans `X-API-Key` ou `Authorization: Bearer`. **A changer en production.** |
| `STUDENT_DEMO_MODE` | `0` | `1` = l'interface web recoit un acces session sans saisie de cle (demonstration uniquement). |
| `SECRET_KEY` | valeur de dev | Signature du cookie de session (utilisee par le mode demo). |
| `DATABASE_PATH` | `./students.db` | Chemin du fichier SQLite. |
| `HOST` / `PORT` | `127.0.0.1` / `5000` | Interface et port d'ecoute. |
| `FLASK_DEBUG` | `0` | `1` = mode debug Flask (desactive par defaut). |

---

## 3. Deploiement Docker

```bash
# 1. Creez un fichier .env a cote du docker-compose.yml
cat > .env <<'EOF'
STUDENT_API_KEY=une-cle-longue-et-secrete
SECRET_KEY=une-autre-cle-pour-les-sessions
STUDENT_DEMO_MODE=0
EOF

# 2. Construisez et demarrez
docker compose up -d --build

# 3. Verifiez
curl http://localhost:5000/health
```

La base est conservee dans le volume nomme `students-data` (monte sur `/data`) :
elle survit aux `docker compose down`, aux reconstructions d'image et aux mises a jour.

```bash
docker compose logs -f          # journaux
docker compose down             # arret (les donnees restent)
docker compose down -v          # arret + suppression du volume (donnees perdues)
```

L'image est basee sur `python:3.11-slim`, tourne avec l'utilisateur non privilegie `appuser`,
expose un `HEALTHCHECK` sur `/health` et lance **gunicorn** (2 workers, 4 threads).

---

## 4. Utilisation sur mobile, tablette et ordinateur

L'interface est **mobile-first**, sans aucune largeur fixe :

- **Telephone (390 px et moins)** : le tableau se transforme en **cartes empilees**, chaque valeur
  etant prefixee par son libelle ; les boutons font au moins 44 px de haut.
- **Tablette (700 px et plus)** : formulaires sur 2 colonnes, barre d'outils sur une ligne.
- **Ordinateur (980 px et plus)** : tableau classique avec **tri par clic sur les en-tetes**.

Details techniques :

- `meta viewport` avec `viewport-fit=cover` + `env(safe-area-inset-*)` : pas de contenu
  sous l'encoche ou la barre gestuelle iOS.
- Champs de formulaire a `font-size: 16px` : iOS ne declenche pas de zoom au focus.
- `inputmode="decimal"` / `email` / `search` : claviers adaptes sur mobile.

### Installation comme application (PWA)

**Android (Chrome)** : ouvrir l'URL → menu ⋮ → *Ajouter a l'ecran d'accueil* → *Installer*.

**iPhone / iPad (Safari)** : ouvrir l'URL → bouton Partager → *Sur l'ecran d'accueil*.

L'application s'ouvre alors en plein ecran, avec son icone, sans barre d'adresse.
Le service worker ne met en cache que la coque de l'interface : **les donnees
metier ne sont jamais servies depuis le cache** (pas de donnees perimees).

---

## 5. Routes de l'API

Legende auth : **oui** = en-tete `X-API-Key: <cle>` ou `Authorization: Bearer <cle>`
(ou session demo si `STUDENT_DEMO_MODE=1`).

| Methode | Route | Auth | Description |
|---|---|:---:|---|
| GET | `/` | non | Interface web (PWA) |
| GET | `/health` | non | Etat du service |
| GET | `/manifest.json` | non | Manifeste PWA |
| GET | `/sw.js` | non | Service worker |
| POST | `/students` | **oui** | Inscrire un etudiant → 201 / 400 / 409 |
| GET | `/students` | **oui** | Liste paginee, filtree, triee → 200 / 400 |
| GET | `/students/export.csv` | **oui** | Export CSV des resultats filtres (piece jointe) |
| GET | `/students/<code>` | **oui** | Detail + notes → 200 / 404 |
| POST | `/students/<code>/grades` | **oui** | Ajouter une note → 201 / 400 / 404 |
| DELETE | `/students/<code>` | **oui** | Supprimer un etudiant + cascade → 204 / 404 |
| DELETE | `/students` | **oui** | Suppression en masse → 200 / 400 |
| POST | `/pay` | **oui** | Enregistrer un paiement → 201 / 400 / 401 / 404 |
| GET | `/generate_report` | **oui** | Rapport Excel `.xlsx` en piece jointe |

### Parametres de `GET /students`

| Parametre | Defaut | Valeurs | Erreur si invalide |
|---|---|---|---|
| `q` | vide | texte libre (nom **ou** prenom, insensible a la casse, partiel) | — |
| `page` | `1` | entier >= 1 | 400 |
| `per_page` | `20` | entier entre 1 et 100 | 400 |
| `sort` | `created_at` | `last_name`, `first_name`, `code`, `average`, `paid`, `created_at` | 400 |
| `order` | `asc` | `asc`, `desc` | 400 |

Reponse :

```json
{
  "items": [ { "student_code": "ETU-001", "average": 16.17, "...": "..." } ],
  "page": 1, "per_page": 20, "total": 12, "pages": 1,
  "has_next": false, "has_prev": false,
  "sort": "created_at", "order": "asc"
}
```

`average` vaut `null` lorsqu'aucune note n'existe — jamais `""` ni une chaine.
`sort` et `order` sont valides contre une **liste blanche** : aucune valeur utilisateur
n'est concatenee au SQL.

---

## 6. Exemples curl

```bash
export API="http://127.0.0.1:5000"
export KEY="une-cle-longue-et-secrete"

# Inscrire un etudiant
curl -i -X POST "$API/students" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"student_code":"ETU-100","first_name":"Amina","last_name":"Diallo","email":"amina@example.com"}'

# Ajouter une note
curl -i -X POST "$API/students/ETU-100/grades" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"subject":"Mathematiques","score":15.5}'

# Lister : recherche + tri + pagination
curl "$API/students?q=diallo&sort=average&order=desc&page=1&per_page=10" \
  -H "X-API-Key: $KEY"

# Paiement (route protegee)
curl -i -X POST "$API/pay" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"student_code":"ETU-100","amount":250}'

# Telecharger le rapport Excel
curl -OJ "$API/generate_report" -H "X-API-Key: $KEY"

# Export CSV (respecte q + tri)
curl -OJ "$API/students/export.csv?q=diallo&sort=last_name&order=asc" \
  -H "X-API-Key: $KEY"

# Supprimer un etudiant (204)
curl -i -X DELETE "$API/students/ETU-100" -H "X-API-Key: $KEY"

# Suppression en masse par codes
curl -i -X DELETE "$API/students" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"codes":["ETU-101","ETU-102"]}'

# Suppression en masse par date d'inscription
curl -i -X DELETE "$API/students" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"before":"2026-01-01"}'
```

Variante Bearer :

```bash
curl "$API/students" -H "Authorization: Bearer $KEY"
```

---

## 7. Format des erreurs

Toutes les erreurs sont du JSON, jamais une page HTML et jamais un 500 sur une
requete mal formee :

```json
{ "error": "Champs obligatoires manquants.", "status": 400,
  "details": { "missing_fields": ["last_name"] } }
```

| Code | Cas |
|---|---|
| 400 | champ manquant, JSON invalide, note hors 0–20, montant <= 0, parametre ou tri invalide, corps de suppression invalide |
| 401 | cle API absente ou incorrecte |
| 404 | code etudiant inconnu, route inconnue |
| 405 | methode non autorisee sur la route |
| 409 | code etudiant deja utilise |
| 500 | filet de securite uniquement — journalise, jamais attendu en usage normal |

---

## 8. Tests

```bash
pip install pytest
pytest -v
```

Resultat attendu : **95 tests passes**, couvrant validation, doublons, auth sur
**chaque** route protegee, mode demo, pagination, recherche, tri (valide / invalide /
liste blanche), export CSV, suppression unitaire, suppression en masse et cascade.

---

## 9. Notes de securite

- La cle API est comparee avec `secrets.compare_digest` (temps constant).
- Toutes les requetes SQL sont parametrees ; le tri passe par une liste blanche.
- `PRAGMA foreign_keys = ON` est active sur **chaque** connexion : la cascade
  `ON DELETE CASCADE` est donc reellement appliquee.
- Le mode demo est **desactive par defaut** ; ne l'activez jamais sur une instance
  contenant des donnees reelles.
- En production : definissez un `STUDENT_API_KEY` long et aleatoire, un `SECRET_KEY`
  distinct, et servez l'application derriere HTTPS (reverse proxy).
