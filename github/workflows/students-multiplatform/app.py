"""
Gestion d'etudiants - API Flask + SQLite (v4)
=============================================

Fonctionnalites
---------------
  * Persistance SQLite (aucune liste en memoire)
  * Validation stricte des entrees -> 400 JSON, jamais de 500
  * Detection des codes etudiants en double (409)
  * Authentification par cle API sur toutes les routes sensibles
  * Mode demo : la cle est injectee cote serveur (cookie de session signe)
    pour que l'interface web fonctionne sans saisie
  * Pagination + recherche (q, page, per_page)
  * Tri serveur (sort, order) avec liste blanche stricte
  * Export CSV (UTF-8 + BOM) respectant recherche et tri
  * Suppression unitaire et suppression en masse (codes ou before)
  * /generate_report renvoie un vrai fichier .xlsx telechargeable
  * Champ "average" propre : None si aucune note, sinon float arrondi
  * Gestionnaire d'erreurs global (reponses JSON homogenes)
  * debug desactive par defaut, activable via FLASK_DEBUG=1

Lancement
---------
    pip install -r requirements.txt
    export STUDENT_API_KEY="une-cle-secrete"
    python app.py

Variables d'environnement
-------------------------
    STUDENT_API_KEY    cle attendue dans X-API-Key / Authorization: Bearer
    STUDENT_DEMO_MODE  1 = l'interface web recoit un acces session (demo)
    SECRET_KEY         cle de signature du cookie de session
    DATABASE_PATH      chemin du fichier SQLite
    HOST / PORT        interface et port d'ecoute (defaut 127.0.0.1:5000)
    FLASK_DEBUG        1 = mode debug (desactive par defaut)
"""

from __future__ import annotations

import csv
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from functools import wraps
from io import BytesIO, StringIO

from flask import (
    Flask,
    Response,
    g,
    jsonify,
    render_template,
    request,
    send_file,
    session,
)
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from werkzeug.exceptions import HTTPException


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
def _env_flag(name: str, default: bool = False) -> bool:
    """Lit un booleen d'environnement ('1', 'true', 'yes', 'on')."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE = os.environ.get("DATABASE_PATH", os.path.join(BASE_DIR, "students.db"))
API_KEY = os.environ.get("STUDENT_API_KEY", "dev-secret-key-change-me")

# Mode demo : la cle est injectee cote serveur sous forme d'un cookie de
# session signe, emis uniquement par GET / lorsque ce drapeau est actif.
# Desactive par defaut : en production, chaque appel doit porter la cle.
DEMO_MODE = _env_flag("STUDENT_DEMO_MODE", default=False)

DEBUG = _env_flag("FLASK_DEBUG", default=False)

GRADE_MIN, GRADE_MAX = 0.0, 20.0
DEFAULT_PER_PAGE = 20
MAX_PER_PAGE = 100

# Liste blanche du tri : la cle utilisateur n'est JAMAIS concatenee au SQL,
# elle sert seulement a choisir une expression figee dans ce dictionnaire.
SORT_COLUMNS = {
    "last_name": "last_name COLLATE NOCASE",
    "first_name": "first_name COLLATE NOCASE",
    "code": "student_code COLLATE NOCASE",
    "created_at": "created_at",
    "average": (
        "(SELECT AVG(gr.score) FROM grades gr WHERE gr.student_id = students.id)"
    ),
    "paid": (
        "(SELECT COALESCE(SUM(CASE WHEN pa.paid = 1 THEN pa.amount ELSE 0 END), 0) "
        " FROM payments pa WHERE pa.student_id = students.id)"
    ),
}
DEFAULT_SORT = "created_at"
SORT_ORDER = ("asc", "desc")

app = Flask(__name__)
app.json.sort_keys = False
app.config["PROPAGATE_EXCEPTIONS"] = False  # garantit que le handler 500 s'execute
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-session-secret-change-me")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 Mo suffit pour du JSON


# --------------------------------------------------------------------------- #
# Schema SQLite
# --------------------------------------------------------------------------- #
SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    student_code TEXT    NOT NULL UNIQUE,
    first_name   TEXT    NOT NULL,
    last_name    TEXT    NOT NULL,
    email        TEXT,
    created_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS grades (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    subject    TEXT    NOT NULL,
    score      REAL    NOT NULL,
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    amount     REAL    NOT NULL,
    paid       INTEGER NOT NULL DEFAULT 0,
    paid_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_grades_student    ON grades(student_id);
CREATE INDEX IF NOT EXISTS idx_payments_student  ON payments(student_id);
CREATE INDEX IF NOT EXISTS idx_students_lastname ON students(last_name);
CREATE INDEX IF NOT EXISTS idx_students_created  ON students(created_at);
"""


# --------------------------------------------------------------------------- #
# Erreur metier -> reponse JSON propre
# --------------------------------------------------------------------------- #
class ApiError(Exception):
    """Erreur applicative renvoyee proprement en JSON (jamais un 500)."""

    def __init__(self, message: str, status_code: int = 400, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details or {}


# --------------------------------------------------------------------------- #
# Acces base de donnees
# --------------------------------------------------------------------------- #
def get_db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(DATABASE)
        conn.row_factory = sqlite3.Row
        # Indispensable pour que ON DELETE CASCADE soit reellement applique.
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    conn = sqlite3.connect(DATABASE)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Helpers de validation
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def json_body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("Corps de requete JSON invalide ou manquant.", 400)
    return data


def require_fields(data: dict, fields: list[str]) -> None:
    missing = [f for f in fields if data.get(f) in (None, "", [])]
    if missing:
        raise ApiError(
            "Champs obligatoires manquants.",
            400,
            {"missing_fields": missing},
        )


def parse_float(value, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ApiError(
            f"Le champ '{field}' doit etre un nombre.",
            400,
            {"field": field},
        )


def parse_int_param(
    name: str,
    default: int,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Lit un parametre de requete entier et le valide (400 si invalide)."""
    raw = request.args.get(name)
    if raw is None or raw.strip() == "":
        return default

    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        raise ApiError(
            f"Le parametre '{name}' doit etre un entier.",
            400,
            {"param": name, "value": raw},
        )

    if minimum is not None and value < minimum:
        raise ApiError(
            f"Le parametre '{name}' doit etre superieur ou egal a {minimum}.",
            400,
            {"param": name, "value": value},
        )
    if maximum is not None and value > maximum:
        raise ApiError(
            f"Le parametre '{name}' doit etre inferieur ou egal a {maximum}.",
            400,
            {"param": name, "value": value},
        )
    return value


def parse_sort_params() -> tuple[str, str]:
    """Valide `sort` et `order` contre une liste blanche (400 sinon)."""
    sort = (request.args.get("sort") or DEFAULT_SORT).strip().lower()
    order = (request.args.get("order") or "asc").strip().lower()

    if sort not in SORT_COLUMNS:
        raise ApiError(
            f"Le parametre 'sort' doit valoir : {', '.join(SORT_COLUMNS)}.",
            400,
            {"param": "sort", "value": sort, "allowed": list(SORT_COLUMNS)},
        )
    if order not in SORT_ORDER:
        raise ApiError(
            "Le parametre 'order' doit valoir 'asc' ou 'desc'.",
            400,
            {"param": "order", "value": order, "allowed": list(SORT_ORDER)},
        )
    return sort, order


def like_pattern(term: str) -> str:
    """Motif LIKE '%...%' en neutralisant les jokers % et _."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def build_filters() -> tuple[str, list, str]:
    """Construit la clause WHERE (parametree), ses params et l'ORDER BY.

    `q` est lie en parametre ; `sort`/`order` passent par une liste blanche,
    donc aucune valeur utilisateur n'atteint le SQL sous forme de texte.
    """
    q = (request.args.get("q") or "").strip()
    sort, order = parse_sort_params()

    where_sql, where_params = "", []
    if q:
        pattern = like_pattern(q.lower())
        where_sql = (
            "WHERE LOWER(first_name) LIKE ? ESCAPE '\\' "
            "   OR LOWER(last_name)  LIKE ? ESCAPE '\\'"
        )
        where_params = [pattern, pattern]

    expr = SORT_COLUMNS[sort]
    direction = "DESC" if order == "desc" else "ASC"
    # NULLS LAST portable + second critere (id) pour une pagination stable.
    order_sql = f"ORDER BY ({expr} IS NULL) ASC, {expr} {direction}, id {direction}"
    return where_sql, where_params, order_sql


def get_student_or_404(db: sqlite3.Connection, student_code: str) -> sqlite3.Row:
    row = db.execute(
        "SELECT * FROM students WHERE student_code = ?", (student_code,)
    ).fetchone()
    if row is None:
        raise ApiError(f"Etudiant '{student_code}' introuvable.", 404)
    return row


def serialize_student(
    row: sqlite3.Row, db: sqlite3.Connection, include_grades: bool = False
) -> dict:
    """Serialise un etudiant. 'average' vaut None s'il n'y a aucune note."""
    student_id = row["id"]

    agg = db.execute(
        "SELECT AVG(score) AS avg, COUNT(*) AS n FROM grades WHERE student_id = ?",
        (student_id,),
    ).fetchone()
    grades_count = int(agg["n"] or 0)
    average = round(float(agg["avg"]), 2) if grades_count else None

    pay = db.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total, "
        "       COALESCE(SUM(CASE WHEN paid = 1 THEN amount ELSE 0 END), 0) AS paid "
        "FROM payments WHERE student_id = ?",
        (student_id,),
    ).fetchone()
    total = round(float(pay["total"]), 2)
    paid = round(float(pay["paid"]), 2)

    data = {
        "id": student_id,
        "student_code": row["student_code"],
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "email": row["email"],
        "created_at": row["created_at"],
        "grades_count": grades_count,
        "average": average,          # None si aucune note -> pas de melange float/str
        "amount_due": total,
        "amount_paid": paid,
        "is_paid": bool(total > 0 and paid >= total),
    }

    if include_grades:
        grades = db.execute(
            "SELECT id, subject, score, created_at FROM grades "
            "WHERE student_id = ? ORDER BY id",
            (student_id,),
        ).fetchall()
        data["grades"] = [dict(gr) for gr in grades]

    return data


# --------------------------------------------------------------------------- #
# Authentification
# --------------------------------------------------------------------------- #
def _request_has_access() -> bool:
    """Cle API fournie, ou acces demo accorde a cette session navigateur."""
    provided = request.headers.get("X-API-Key")
    if not provided:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            provided = auth[7:].strip()

    if provided and secrets.compare_digest(provided, API_KEY):
        return True

    # Mode demo : GET / a pose un cookie de session signe.
    return bool(DEMO_MODE and session.get("demo_access"))


def require_api_key(view):
    """Protege une route : 401 JSON si la cle (ou l'acces demo) est absente."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not _request_has_access():
            raise ApiError(
                "Authentification requise : cle API invalide ou absente.",
                401,
                {"how_to": "En-tete 'X-API-Key' ou 'Authorization: Bearer <cle>'."},
            )
        return view(*args, **kwargs)

    return wrapper


# --------------------------------------------------------------------------- #
# Routes publiques
# --------------------------------------------------------------------------- #
@app.get("/")
def index():
    """Interface web. En mode demo, delivre l'acces session au navigateur."""
    if DEMO_MODE:
        session["demo_access"] = True
    return render_template("index.html", demo_mode=DEMO_MODE)


@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "database": os.path.basename(DATABASE),
        "demo_mode": DEMO_MODE,
    })


@app.get("/manifest.json")
def manifest():
    """Manifeste PWA (sert depuis / pour garder un scope racine)."""
    response = app.send_static_file("manifest.json")
    response.headers["Content-Type"] = "application/manifest+json"
    return response


@app.get("/sw.js")
def service_worker():
    """Service worker servi a la racine pour couvrir tout le site."""
    response = app.send_static_file("sw.js")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


# --------------------------------------------------------------------------- #
# Etudiants
# --------------------------------------------------------------------------- #
@app.post("/students")
@require_api_key
def create_student():
    data = json_body()
    require_fields(data, ["student_code", "first_name", "last_name"])

    code = str(data["student_code"]).strip()
    first = str(data["first_name"]).strip()
    last = str(data["last_name"]).strip()
    email = str(data["email"]).strip() if data.get("email") else None

    if not code:
        raise ApiError("Le champ 'student_code' ne peut pas etre vide.", 400)

    db = get_db()
    if db.execute("SELECT 1 FROM students WHERE student_code = ?", (code,)).fetchone():
        raise ApiError(
            f"Le code etudiant '{code}' existe deja.",
            409,
            {"student_code": code},
        )

    cur = db.execute(
        "INSERT INTO students (student_code, first_name, last_name, email, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (code, first, last, email, now_iso()),
    )
    db.commit()
    row = db.execute("SELECT * FROM students WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(serialize_student(row, db)), 201


@app.get("/students")
@require_api_key
def list_students():
    """Liste paginee, filtrable et triable.

    Parametres : ``q``, ``page``, ``per_page``, ``sort``, ``order``.
    Reponse : ``{items, page, per_page, total, pages, has_next, has_prev,
    sort, order}``.
    """
    page = parse_int_param("page", 1, minimum=1)
    per_page = parse_int_param("per_page", DEFAULT_PER_PAGE, minimum=1, maximum=MAX_PER_PAGE)
    where_sql, where_params, order_sql = build_filters()
    sort = (request.args.get("sort") or DEFAULT_SORT).strip().lower()
    order = (request.args.get("order") or "asc").strip().lower()

    db = get_db()
    total = int(
        db.execute(f"SELECT COUNT(*) FROM students {where_sql}", where_params).fetchone()[0]
    )
    pages = (total + per_page - 1) // per_page  # 0 si aucun resultat
    offset = (page - 1) * per_page

    rows = db.execute(
        f"SELECT * FROM students {where_sql} {order_sql} LIMIT ? OFFSET ?",
        [*where_params, per_page, offset],
    ).fetchall()

    return jsonify({
        "items": [serialize_student(r, db) for r in rows],
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
        "has_next": page < pages,
        "has_prev": page > 1,
        "sort": sort,
        "order": order,
    })


@app.get("/students/export.csv")
@require_api_key
def export_students_csv():
    """Export CSV de tous les resultats filtres (recherche + tri courants).

    Le fichier est en UTF-8 avec BOM et separe par des points-virgules, ce qui
    l'ouvre directement dans Excel (y compris en locale francaise) sans
    manipulation d'encodage.
    """
    where_sql, where_params, order_sql = build_filters()

    db = get_db()
    rows = db.execute(
        f"SELECT * FROM students {where_sql} {order_sql}", where_params
    ).fetchall()
    records = [serialize_student(r, db) for r in rows]

    buffer = StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow([
        "Code etudiant", "Prenom", "Nom", "Email", "Nombre de notes",
        "Moyenne", "Montant du", "Montant paye", "Statut paiement", "Inscrit le",
    ])
    for s in records:
        writer.writerow([
            s["student_code"],
            s["first_name"],
            s["last_name"],
            s["email"] or "",
            s["grades_count"],
            "" if s["average"] is None else f"{s['average']:.2f}".replace(".", ","),
            f"{s['amount_due']:.2f}".replace(".", ","),
            f"{s['amount_paid']:.2f}".replace(".", ","),
            "Paye" if s["is_paid"] else "Impaye",
            s["created_at"],
        ])

    payload = ("\ufeff" + buffer.getvalue()).encode("utf-8")
    filename = f"etudiants_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return send_file(
        BytesIO(payload),
        as_attachment=True,
        download_name=filename,
        mimetype="text/csv",           # Flask ajoute lui-meme '; charset=utf-8'
    )


@app.get("/students/<student_code>")
@require_api_key
def get_student(student_code: str):
    db = get_db()
    row = get_student_or_404(db, student_code)
    return jsonify(serialize_student(row, db, include_grades=True))


@app.post("/students/<student_code>/grades")
@require_api_key
def add_grade(student_code: str):
    data = json_body()
    require_fields(data, ["subject", "score"])

    score = parse_float(data["score"], "score")
    if not (GRADE_MIN <= score <= GRADE_MAX):
        raise ApiError(
            f"La note doit etre comprise entre {GRADE_MIN:g} et {GRADE_MAX:g}.",
            400,
            {"field": "score"},
        )

    db = get_db()
    row = get_student_or_404(db, student_code)
    db.execute(
        "INSERT INTO grades (student_id, subject, score, created_at) VALUES (?, ?, ?, ?)",
        (row["id"], str(data["subject"]).strip(), score, now_iso()),
    )
    db.commit()
    return jsonify(serialize_student(row, db, include_grades=True)), 201


@app.delete("/students/<student_code>")
@require_api_key
def delete_student(student_code: str):
    """Supprime un etudiant et toutes ses donnees liees (notes + paiements).

    La cascade est assuree par ``ON DELETE CASCADE`` et par
    ``PRAGMA foreign_keys = ON`` active sur chaque connexion.

    204 No Content si supprime, 404 JSON si le code est inconnu.
    """
    db = get_db()
    row = get_student_or_404(db, student_code)

    db.execute("DELETE FROM students WHERE id = ?", (row["id"],))
    db.commit()

    return Response(status=204)


@app.delete("/students")
@require_api_key
def bulk_delete_students():
    """Suppression en masse : ``{"codes": [...]}`` ou ``{"before": "AAAA-MM-JJ"}``.

    Repond ``{deleted, requested, not_found}``. La cascade s'applique aux notes
    et aux paiements de chaque etudiant supprime.
    """
    data = json_body()
    has_codes = "codes" in data and data.get("codes") is not None
    has_before = "before" in data and data.get("before") is not None

    if has_codes and has_before:
        raise ApiError(
            "Fournissez soit 'codes', soit 'before' (pas les deux).", 400
        )
    if not has_codes and not has_before:
        raise ApiError(
            "Corps attendu : {\"codes\": [...]} ou {\"before\": \"AAAA-MM-JJ\"}.",
            400,
        )

    db = get_db()

    if has_codes:
        codes = data["codes"]
        if not isinstance(codes, list) or not codes:
            raise ApiError(
                "Le champ 'codes' doit etre une liste non vide de codes etudiants.",
                400,
                {"field": "codes"},
            )
        if any(not isinstance(c, str) for c in codes):
            raise ApiError(
                "Chaque element de 'codes' doit etre une chaine de caracteres.",
                400,
                {"field": "codes"},
            )

        # Deduplication en conservant l'ordre, puis suppression par paquets.
        unique_codes = list(dict.fromkeys(c.strip() for c in codes if c.strip()))
        if not unique_codes:
            raise ApiError("Le champ 'codes' ne contient aucun code valide.", 400)

        placeholders = ",".join("?" * len(unique_codes))
        found = db.execute(
            f"SELECT student_code FROM students WHERE student_code IN ({placeholders})",
            unique_codes,
        ).fetchall()
        found_codes = [r["student_code"] for r in found]

        deleted = 0
        if found_codes:
            # Un placeholder par code REELLEMENT trouve (pas un par code demande).
            found_placeholders = ",".join("?" * len(found_codes))
            cur = db.execute(
                f"DELETE FROM students WHERE student_code IN ({found_placeholders})",
                found_codes,
            )
            deleted = cur.rowcount
        db.commit()

        missing = [c for c in unique_codes if c not in set(found_codes)]
        return jsonify({
            "deleted": deleted,
            "requested": len(unique_codes),
            "not_found": missing,
            "message": f"{deleted} etudiant(s) supprime(s) avec notes et paiements.",
        })

    # -- suppression par date ------------------------------------------------
    before = str(data["before"]).strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", before):
        raise ApiError(
            "Le champ 'before' doit etre une date au format AAAA-MM-JJ.",
            400,
            {"field": "before", "value": before},
        )
    try:
        datetime.strptime(before, "%Y-%m-%d")
    except ValueError:
        raise ApiError(
            f"La date '{before}' n'existe pas dans le calendrier.",
            400,
            {"field": "before", "value": before},
        )

    matched = db.execute(
        "SELECT COUNT(*) FROM students WHERE date(created_at) < date(?)", (before,)
    ).fetchone()[0]
    cur = db.execute(
        "DELETE FROM students WHERE date(created_at) < date(?)", (before,)
    )
    db.commit()

    return jsonify({
        "deleted": cur.rowcount,
        "requested": int(matched),
        "before": before,
        "message": f"{cur.rowcount} etudiant(s) inscrit(s) avant le {before} supprime(s).",
    })


# --------------------------------------------------------------------------- #
# Paiements
# --------------------------------------------------------------------------- #
@app.post("/pay")
@require_api_key
def pay():
    """Marque un paiement. Protege par cle API (ou session demo)."""
    data = json_body()
    require_fields(data, ["student_code", "amount"])

    amount = parse_float(data["amount"], "amount")
    if amount <= 0:
        raise ApiError("Le montant doit etre strictement positif.", 400, {"field": "amount"})

    db = get_db()
    row = get_student_or_404(db, str(data["student_code"]).strip())
    db.execute(
        "INSERT INTO payments (student_id, amount, paid, paid_at) VALUES (?, ?, 1, ?)",
        (row["id"], amount, now_iso()),
    )
    db.commit()
    return jsonify({
        "message": f"Paiement de {amount:.2f} enregistre pour {row['student_code']}.",
        "student": serialize_student(row, db),
    }), 201


# --------------------------------------------------------------------------- #
# Rapport Excel
# --------------------------------------------------------------------------- #
@app.get("/generate_report")
@require_api_key
def generate_report():
    """Genere le rapport Excel et le renvoie en piece jointe telechargeable."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM students ORDER BY last_name COLLATE NOCASE, first_name COLLATE NOCASE"
    ).fetchall()

    wb = Workbook()
    ws = wb.active
    ws.title = "Etudiants"

    headers = [
        "Code etudiant", "Prenom", "Nom", "Email",
        "Nombre de notes", "Moyenne", "Montant du", "Montant paye", "Statut paiement",
    ]
    ws.append(headers)

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F4E79")
    for col in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for r in rows:
        s = serialize_student(r, db)
        ws.append([
            s["student_code"],
            s["first_name"],
            s["last_name"],
            s["email"] or "",
            s["grades_count"],
            s["average"] if s["average"] is not None else "",
            s["amount_due"],
            s["amount_paid"],
            "Paye" if s["is_paid"] else "Impaye",
        ])

    for col in range(1, len(headers) + 1):
        letter = get_column_letter(col)
        width = max(
            [len(str(headers[col - 1]))]
            + [len(str(ws.cell(row=i, column=col).value or "")) for i in range(2, ws.max_row + 1)]
        )
        ws.column_dimensions[letter].width = min(width + 4, 40)

    ws.freeze_panes = "A2"

    stream = BytesIO()
    wb.save(stream)
    stream.seek(0)

    filename = f"rapport_etudiants_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return send_file(
        stream,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# --------------------------------------------------------------------------- #
# Gestionnaire d'erreurs global
# --------------------------------------------------------------------------- #
@app.errorhandler(ApiError)
def handle_api_error(err: ApiError):
    payload = {"error": err.message, "status": err.status_code}
    if err.details:
        payload["details"] = err.details
    return jsonify(payload), err.status_code


@app.errorhandler(HTTPException)
def handle_http_exception(err: HTTPException):
    return jsonify({"error": err.description, "status": err.code}), err.code


@app.errorhandler(sqlite3.IntegrityError)
def handle_integrity_error(err: sqlite3.IntegrityError):
    # Filet de securite : une contrainte SQLite ne doit jamais produire un 500.
    return jsonify({
        "error": "Conflit en base : contrainte d'integrite violee.",
        "status": 409,
        "details": {"sqlite": str(err)},
    }), 409


@app.errorhandler(Exception)
def handle_unexpected(err: Exception):
    app.logger.exception("Erreur non geree")
    return jsonify({"error": "Erreur interne du serveur.", "status": 500}), 500


@app.after_request
def add_response_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    # Une 204 ne doit porter aucune entite (Werkzeug ajoute sinon un
    # Content-Type text/html par defaut).
    if response.status_code == 204:
        response.headers.pop("Content-Type", None)
    return response


# --------------------------------------------------------------------------- #
# Point d'entree
# --------------------------------------------------------------------------- #
init_db()

if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    # debug desactive par defaut : activable uniquement via FLASK_DEBUG=1
    app.run(host=host, port=port, debug=DEBUG)
