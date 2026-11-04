"""
Suite de tests de l'application de gestion d'etudiants (v4).

Lancement :
    pytest -v

Couverture :
  * validation des entrees (400, jamais de 500) et codes en double (409)
  * champ "average" (None / float)
  * /generate_report (piece jointe .xlsx)
  * authentification de TOUTES les routes protegees (401) + acces demo
  * pagination et recherche (q, page, per_page)
  * tri (sort / order) : valide, ordre, liste blanche -> 400
  * export CSV : entete, BOM, filtres et tri respectes
  * DELETE /students/<code> : 204, 404, cascade
  * DELETE /students : suppression en masse par codes et par date, cascade
"""

from __future__ import annotations

import csv
import io
import os
import sqlite3
import sys
import tempfile

import pytest

# --------------------------------------------------------------------------- #
# L'application lit DATABASE_PATH a l'import : on prepare l'environnement avant.
# --------------------------------------------------------------------------- #
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMPDIR = tempfile.mkdtemp(prefix="students-tests-")
DB_PATH = os.path.join(_TMPDIR, "test.db")
API_KEY = "test-api-key"

os.environ["DATABASE_PATH"] = DB_PATH
os.environ["STUDENT_API_KEY"] = API_KEY
os.environ["STUDENT_DEMO_MODE"] = "0"

import app as app_module  # noqa: E402  (import volontairement apres l'env)

AUTH = {"X-API-Key": API_KEY}


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def client():
    app_module.app.config.update(TESTING=True)
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def clean_db(client):
    """Vide les tables avant chaque test (ordre enfants -> parent)."""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("DELETE FROM payments")
        conn.execute("DELETE FROM grades")
        conn.execute("DELETE FROM students")
        conn.commit()
    finally:
        conn.close()
    yield


def create_student(client, code="ETU-001", first="Amina", last="Diallo",
                   email="amina@example.com", headers=None):
    return client.post(
        "/students",
        json={"student_code": code, "first_name": first, "last_name": last, "email": email},
        headers=AUTH if headers is None else headers,
    )


def add_grade(client, code, subject="Mathematiques", score=15.5, headers=None):
    return client.post(
        f"/students/{code}/grades",
        json={"subject": subject, "score": score},
        headers=AUTH if headers is None else headers,
    )


def pay(client, code, amount=250, key=API_KEY):
    headers = {"X-API-Key": key} if key is not None else {}
    return client.post("/pay", json={"student_code": code, "amount": amount}, headers=headers)


def db_counts():
    """Retourne (nb_etudiants, nb_notes, nb_paiements)."""
    conn = sqlite3.connect(DB_PATH)
    try:
        return tuple(
            conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("students", "grades", "payments")
        )
    finally:
        conn.close()


def seed_students(client, n=25):
    """Cree n etudiants : ETU-001..ETU-0nn, prenoms/noms varies."""
    firsts = ["Amina", "Awa", "Moussa", "Fatou", "Ibrahim"]
    lasts = ["Diallo", "Traore", "Keita", "Sow", "Camara"]
    for i in range(1, n + 1):
        create_student(
            client,
            code=f"ETU-{i:03d}",
            first=firsts[(i - 1) % len(firsts)],
            last=lasts[(i - 1) % len(lasts)],
            email=f"etu{i:03d}@example.com",
        )


def codes_of(client, query=""):
    body = client.get(f"/students{query}", headers=AUTH).get_json()
    return [s["student_code"] for s in body["items"]]


# --------------------------------------------------------------------------- #
# Sante / base
# --------------------------------------------------------------------------- #
def test_health_is_public(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.get_json()["status"] == "ok"
    assert res.get_json()["demo_mode"] is False


def test_foreign_keys_pragma_enabled(client):
    with app_module.app.app_context():
        value = app_module.get_db().execute("PRAGMA foreign_keys").fetchone()[0]
    assert value == 1


def test_index_is_public(client):
    res = client.get("/")
    assert res.status_code == 200
    assert b"Gestion des etudiants" in res.data


# --------------------------------------------------------------------------- #
# Authentification : toutes les routes sensibles exigent la cle
# --------------------------------------------------------------------------- #
PROTECTED_CALLS = [
    ("get", "/students", {}),
    ("get", "/students/ETU-001", {}),
    ("post", "/students", {"json": {"student_code": "X", "first_name": "A", "last_name": "B"}}),
    ("post", "/students/ETU-001/grades", {"json": {"subject": "Maths", "score": 10}}),
    ("delete", "/students/ETU-001", {}),
    ("delete", "/students", {"json": {"codes": ["ETU-001"]}}),
    ("post", "/pay", {"json": {"student_code": "ETU-001", "amount": 10}}),
    ("get", "/generate_report", {}),
    ("get", "/students/export.csv", {}),
]


@pytest.mark.parametrize("method,url,kwargs", PROTECTED_CALLS)
def test_route_requires_api_key(client, method, url, kwargs):
    """Sans cle : 401 JSON, jamais 200 ni 500."""
    res = getattr(client, method)(url, **kwargs)
    assert res.status_code == 401
    assert res.is_json
    assert res.get_json()["status"] == 401


@pytest.mark.parametrize("method,url,kwargs", PROTECTED_CALLS)
def test_route_accepts_wrong_key_returns_401(client, method, url, kwargs):
    body = dict(kwargs)
    body["headers"] = {"X-API-Key": "mauvaise-cle"}
    res = getattr(client, method)(url, **body)
    assert res.status_code == 401


def test_bearer_token_is_accepted(client):
    create_student(client)
    res = client.get("/students", headers={"Authorization": f"Bearer {API_KEY}"})
    assert res.status_code == 200


def test_demo_mode_injection_grants_session_access(client, monkeypatch):
    """Mode demo : GET / pose un cookie de session, puis les routes passent."""
    monkeypatch.setattr(app_module, "DEMO_MODE", True)

    with app_module.app.test_client() as demo:
        assert demo.get("/students").status_code == 401        # avant le cookie
        assert demo.get("/").status_code == 200                # pose l'acces demo

        res = demo.get("/students")
        assert res.status_code == 200
        assert demo.get("/generate_report").status_code == 200
        assert demo.get("/students/export.csv").status_code == 200


def test_demo_mode_off_does_not_grant_access(client):
    """Sans mode demo, un cookie de session ne suffit pas."""
    with app_module.app.test_client() as anon:
        anon.get("/")
        assert anon.get("/students").status_code == 401


# --------------------------------------------------------------------------- #
# Inscription
# --------------------------------------------------------------------------- #
def test_create_student_201(client):
    res = create_student(client)
    assert res.status_code == 201
    body = res.get_json()
    assert body["student_code"] == "ETU-001"
    assert body["average"] is None
    assert body["grades_count"] == 0


def test_missing_field_returns_400(client):
    res = client.post("/students", json={"student_code": "ETU-002", "first_name": "Awa"}, headers=AUTH)
    assert res.status_code == 400
    assert res.get_json()["details"]["missing_fields"] == ["last_name"]


def test_invalid_json_returns_400(client):
    res = client.post("/students", data="pas du json", content_type="text/plain", headers=AUTH)
    assert res.status_code == 400


def test_duplicate_code_returns_409(client):
    create_student(client)
    res = create_student(client, first="Autre")
    assert res.status_code == 409
    assert "existe deja" in res.get_json()["error"]


# --------------------------------------------------------------------------- #
# Notes / moyenne
# --------------------------------------------------------------------------- #
def test_average_is_none_without_grades(client):
    create_student(client)
    body = client.get("/students/ETU-001", headers=AUTH).get_json()
    assert body["average"] is None


def test_average_is_float_with_grades(client):
    create_student(client)
    add_grade(client, "ETU-001", score=12)
    res = add_grade(client, "ETU-001", subject="Physique", score=16)
    assert res.status_code == 201
    assert res.get_json()["average"] == 14.0
    assert isinstance(res.get_json()["average"], float)


@pytest.mark.parametrize("score", [25, -3])
def test_grade_out_of_range_returns_400(client, score):
    create_student(client)
    assert add_grade(client, "ETU-001", score=score).status_code == 400


def test_grade_non_numeric_returns_400(client):
    create_student(client)
    res = add_grade(client, "ETU-001", score="abc")
    assert res.status_code == 400
    assert "nombre" in res.get_json()["error"]


# --------------------------------------------------------------------------- #
# Paiements
# --------------------------------------------------------------------------- #
def test_pay_without_key_returns_401(client):
    create_student(client)
    assert pay(client, "ETU-001", key=None).status_code == 401


def test_pay_ok_returns_201(client):
    create_student(client)
    res = pay(client, "ETU-001")
    assert res.status_code == 201
    assert res.get_json()["student"]["amount_paid"] == 250.0
    assert res.get_json()["student"]["is_paid"] is True


def test_pay_negative_amount_returns_400(client):
    create_student(client)
    assert pay(client, "ETU-001", amount=-5).status_code == 400


def test_pay_unknown_student_returns_404(client):
    assert pay(client, "INCONNU").status_code == 404


# --------------------------------------------------------------------------- #
# Rapport Excel
# --------------------------------------------------------------------------- #
def test_generate_report_is_an_attachment(client):
    create_student(client)
    add_grade(client, "ETU-001", score=15.5)
    pay(client, "ETU-001", amount=250)

    res = client.get("/generate_report", headers=AUTH)
    assert res.status_code == 200
    disposition = res.headers["Content-Disposition"]
    assert disposition.startswith("attachment;")
    assert "rapport_etudiants_" in disposition and disposition.endswith(".xlsx")
    assert res.data[:2] == b"PK"


# --------------------------------------------------------------------------- #
# Erreurs globales
# --------------------------------------------------------------------------- #
def test_unknown_route_returns_json_404(client):
    res = client.get("/nope")
    assert res.status_code == 404
    assert res.is_json


def test_wrong_method_returns_json_405(client):
    res = client.get("/pay")
    assert res.status_code == 405
    assert res.is_json


# --------------------------------------------------------------------------- #
# Pagination et recherche
# --------------------------------------------------------------------------- #
def test_list_default_pagination(client):
    seed_students(client, 25)
    body = client.get("/students", headers=AUTH).get_json()

    assert {"items", "page", "per_page", "total", "pages",
            "has_next", "has_prev", "sort", "order"} <= set(body)
    assert body["page"] == 1
    assert body["per_page"] == 20
    assert body["total"] == 25
    assert body["pages"] == 2
    assert body["has_next"] is True
    assert body["has_prev"] is False
    assert len(body["items"]) == 20


def test_list_page_2(client):
    seed_students(client, 25)
    body = client.get("/students?page=2", headers=AUTH).get_json()
    assert body["page"] == 2
    assert len(body["items"]) == 5
    assert body["has_next"] is False
    assert body["has_prev"] is True


def test_list_page_beyond_last_is_empty(client):
    seed_students(client, 5)
    body = client.get("/students?page=9", headers=AUTH).get_json()
    assert body["items"] == []
    assert body["total"] == 5
    assert body["has_next"] is False


def test_list_per_page_boundaries(client):
    seed_students(client, 25)

    body = client.get("/students?per_page=1", headers=AUTH).get_json()
    assert body["per_page"] == 1 and body["pages"] == 25 and len(body["items"]) == 1

    body = client.get("/students?per_page=100", headers=AUTH).get_json()
    assert body["per_page"] == 100 and body["pages"] == 1 and len(body["items"]) == 25


def test_list_search_match(client):
    seed_students(client, 25)
    body = client.get("/students?q=diallo", headers=AUTH).get_json()
    assert body["total"] == 5
    assert all("diallo" in s["last_name"].lower() for s in body["items"])


def test_list_search_matches_first_name_too(client):
    seed_students(client, 25)
    body = client.get("/students?q=moussa", headers=AUTH).get_json()
    assert body["total"] == 5


def test_list_search_is_case_insensitive(client):
    seed_students(client, 25)
    lower = client.get("/students?q=diallo", headers=AUTH).get_json()["total"]
    upper = client.get("/students?q=DIALLO", headers=AUTH).get_json()["total"]
    mixed = client.get("/students?q=DiAlLo", headers=AUTH).get_json()["total"]
    assert lower == upper == mixed == 5


def test_list_search_no_match(client):
    seed_students(client, 25)
    body = client.get("/students?q=zzzzz", headers=AUTH).get_json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["pages"] == 0
    assert body["has_next"] is False and body["has_prev"] is False


def test_list_search_wildcard_is_literal(client):
    seed_students(client, 5)
    body = client.get("/students?q=%25", headers=AUTH).get_json()
    assert body["total"] == 0


@pytest.mark.parametrize("query", [
    "page=0", "page=-1", "page=abc", "page=1.5",
    "per_page=0", "per_page=-3", "per_page=101", "per_page=abc",
])
def test_list_invalid_params_return_400(client, query):
    seed_students(client, 3)
    res = client.get(f"/students?{query}", headers=AUTH)
    assert res.status_code == 400
    assert res.is_json
    assert "param" in res.get_json()["details"]


def test_list_total_and_pages_are_correct(client):
    seed_students(client, 23)
    for per_page, expected_pages in ((5, 5), (10, 3), (20, 2), (23, 1), (100, 1)):
        body = client.get(f"/students?per_page={per_page}", headers=AUTH).get_json()
        assert body["total"] == 23
        assert body["pages"] == expected_pages


# --------------------------------------------------------------------------- #
# Tri
# --------------------------------------------------------------------------- #
def test_sort_by_last_name_asc_and_desc(client):
    create_student(client, code="ETU-001", first="Amina", last="Traore")
    create_student(client, code="ETU-002", first="Awa", last="Camara")
    create_student(client, code="ETU-003", first="Moussa", last="Diallo")

    asc = client.get("/students?sort=last_name&order=asc", headers=AUTH).get_json()
    assert [s["last_name"] for s in asc["items"]] == ["Camara", "Diallo", "Traore"]
    assert asc["sort"] == "last_name" and asc["order"] == "asc"

    desc = client.get("/students?sort=last_name&order=desc", headers=AUTH).get_json()
    assert [s["last_name"] for s in desc["items"]] == ["Traore", "Diallo", "Camara"]


def test_sort_by_code_and_first_name(client):
    create_student(client, code="ETU-002", first="Zoe", last="A")
    create_student(client, code="ETU-001", first="Amina", last="B")
    create_student(client, code="ETU-003", first="Moussa", last="C")

    by_code = codes_of(client, "?sort=code&order=asc")
    assert by_code == ["ETU-001", "ETU-002", "ETU-003"]

    by_first = codes_of(client, "?sort=first_name&order=desc")
    assert by_first[0] == "ETU-002"      # Zoe


def test_sort_by_average_puts_students_without_grades_last(client):
    create_student(client, code="ETU-001")
    add_grade(client, "ETU-001", score=18)
    create_student(client, code="ETU-002")          # aucune note
    add_grade(client, "ETU-002", score=8)
    create_student(client, code="ETU-003")          # aucune note

    asc = client.get("/students?sort=average&order=asc", headers=AUTH).get_json()
    assert [s["average"] for s in asc["items"]][:2] == [8.0, 18.0]
    assert asc["items"][-1]["average"] is None


def test_sort_by_paid(client):
    create_student(client, code="ETU-001")
    create_student(client, code="ETU-002")
    pay(client, "ETU-002", amount=500)

    desc = client.get("/students?sort=paid&order=desc", headers=AUTH).get_json()
    assert desc["items"][0]["student_code"] == "ETU-002"
    assert desc["items"][0]["amount_paid"] == 500.0


def test_sort_by_created_at(client):
    create_student(client, code="ETU-001")
    create_student(client, code="ETU-002")

    asc = codes_of(client, "?sort=created_at&order=asc")
    assert asc == ["ETU-001", "ETU-002"]


@pytest.mark.parametrize("query,param", [
    ("sort=password", "sort"),
    ("sort=1%3B+DROP+TABLE+students", "sort"),
    ("sort=students.id", "sort"),
    ("order=up", "order"),
    ("order=ascending", "order"),
])
def test_invalid_sort_returns_400(client, query, param):
    """Toute valeur hors liste blanche est rejetee : pas d'injection SQL."""
    seed_students(client, 3)
    res = client.get(f"/students?{query}", headers=AUTH)

    assert res.status_code == 400
    assert res.is_json
    assert res.get_json()["details"]["param"] == param
    # La table est toujours la apres la tentative d'injection.
    assert db_counts()[0] == 3


def test_sort_combines_with_search_and_pagination(client):
    seed_students(client, 25)
    body = client.get(
        "/students?q=diallo&sort=last_name&order=asc&per_page=2&page=1",
        headers=AUTH,
    ).get_json()
    assert body["total"] == 5
    assert body["pages"] == 3
    assert len(body["items"]) == 2


# --------------------------------------------------------------------------- #
# Export CSV
# --------------------------------------------------------------------------- #
def parse_csv(res):
    text = res.data.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text), delimiter=";"))


def test_export_csv_is_attachment_with_bom(client):
    create_student(client)
    res = client.get("/students/export.csv", headers=AUTH)

    assert res.status_code == 200
    assert res.data.startswith(b"\xef\xbb\xbf")                 # BOM UTF-8 (Excel)
    content_type = res.headers["Content-Type"]
    assert content_type.startswith("text/csv")
    assert content_type.count("charset") == 1                  # pas de doublon
    assert res.headers["Content-Disposition"].startswith("attachment;")
    assert ".csv" in res.headers["Content-Disposition"]


def test_export_csv_content(client):
    create_student(client, code="ETU-001", first="Amina", last="Diallo")
    add_grade(client, "ETU-001", score=15.5)
    pay(client, "ETU-001", amount=250)

    rows = parse_csv(client.get("/students/export.csv", headers=AUTH))
    assert rows[0][0] == "Code etudiant"
    assert rows[1][0] == "ETU-001"
    assert rows[1][1] == "Amina"
    assert rows[1][5] == "15,50"          # virgule decimale pour Excel FR
    assert rows[1][8] == "Paye"


def test_export_csv_respects_search(client):
    seed_students(client, 25)
    rows = parse_csv(client.get("/students/export.csv?q=moussa", headers=AUTH))
    assert len(rows) == 1 + 5                     # entete + 5 resultats
    assert all(r[1] == "Moussa" for r in rows[1:])


def test_export_csv_respects_sort(client):
    create_student(client, code="ETU-001", first="A", last="Traore")
    create_student(client, code="ETU-002", first="B", last="Camara")

    asc = parse_csv(client.get("/students/export.csv?sort=last_name&order=asc", headers=AUTH))
    assert [r[2] for r in asc[1:]] == ["Camara", "Traore"]

    desc = parse_csv(client.get("/students/export.csv?sort=last_name&order=desc", headers=AUTH))
    assert [r[2] for r in desc[1:]] == ["Traore", "Camara"]


def test_export_csv_invalid_sort_returns_400(client):
    res = client.get("/students/export.csv?sort=nope", headers=AUTH)
    assert res.status_code == 400
    assert res.is_json


# --------------------------------------------------------------------------- #
# DELETE /students/<code>
# --------------------------------------------------------------------------- #
def test_delete_student_returns_204(client):
    create_student(client)
    res = client.delete("/students/ETU-001", headers=AUTH)

    assert res.status_code == 204
    assert res.data == b""
    assert res.headers.get("Content-Type") is None
    assert client.get("/students/ETU-001", headers=AUTH).status_code == 404


def test_delete_unknown_student_returns_404(client):
    res = client.delete("/students/INCONNU", headers=AUTH)
    assert res.status_code == 404
    assert res.is_json
    assert res.get_json()["status"] == 404
    assert "introuvable" in res.get_json()["error"]


def test_delete_removes_student_and_cascades(client):
    create_student(client)
    add_grade(client, "ETU-001", score=12)
    add_grade(client, "ETU-001", subject="Physique", score=16)
    pay(client, "ETU-001", amount=250)

    assert db_counts() == (1, 2, 1)
    assert client.delete("/students/ETU-001", headers=AUTH).status_code == 204
    assert db_counts() == (0, 0, 0)


def test_delete_keeps_other_students_intact(client):
    create_student(client, code="ETU-001")
    create_student(client, code="ETU-002", first="Awa")
    add_grade(client, "ETU-001", score=10)
    add_grade(client, "ETU-002", score=14)
    pay(client, "ETU-002", amount=100)

    assert client.delete("/students/ETU-001", headers=AUTH).status_code == 204
    assert db_counts() == (1, 1, 1)


def test_delete_twice_second_call_returns_404(client):
    create_student(client)
    assert client.delete("/students/ETU-001", headers=AUTH).status_code == 204
    assert client.delete("/students/ETU-001", headers=AUTH).status_code == 404


# --------------------------------------------------------------------------- #
# DELETE /students  (suppression en masse)
# --------------------------------------------------------------------------- #
def test_bulk_delete_by_codes(client):
    for i in range(1, 4):
        create_student(client, code=f"ETU-{i:03d}", first=f"P{i}", last=f"N{i}")

    res = client.delete("/students", json={"codes": ["ETU-001", "ETU-003"]}, headers=AUTH)
    assert res.status_code == 200

    body = res.get_json()
    assert body["deleted"] == 2
    assert body["requested"] == 2
    assert body["not_found"] == []
    assert codes_of(client) == ["ETU-002"]


def test_bulk_delete_reports_unknown_codes(client):
    create_student(client, code="ETU-001")
    body = client.delete(
        "/students", json={"codes": ["ETU-001", "GHOST-1", "GHOST-2"]}, headers=AUTH
    ).get_json()

    assert body["deleted"] == 1
    assert body["requested"] == 3
    assert set(body["not_found"]) == {"GHOST-1", "GHOST-2"}


def test_bulk_delete_deduplicates_codes(client):
    create_student(client, code="ETU-001")
    body = client.delete(
        "/students", json={"codes": ["ETU-001", "ETU-001", " ETU-001 "]}, headers=AUTH
    ).get_json()

    assert body["deleted"] == 1
    assert body["requested"] == 1


def test_bulk_delete_cascades_grades_and_payments(client):
    for i in range(1, 4):
        create_student(client, code=f"ETU-{i:03d}", first=f"P{i}", last=f"N{i}")
        add_grade(client, f"ETU-{i:03d}", score=10 + i)
        pay(client, f"ETU-{i:03d}", amount=100)

    assert db_counts() == (3, 3, 3)

    res = client.delete("/students", json={"codes": ["ETU-001", "ETU-002"]}, headers=AUTH)
    assert res.status_code == 200
    assert res.get_json()["deleted"] == 2
    assert db_counts() == (1, 1, 1)          # une seule lignee restante


def test_bulk_delete_by_before_date(client):
    create_student(client, code="ETU-001")
    create_student(client, code="ETU-002")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE students SET created_at = '2020-01-01T00:00:00+00:00' WHERE student_code = 'ETU-001'")
    conn.commit()
    conn.close()

    res = client.delete("/students", json={"before": "2021-01-01"}, headers=AUTH)
    assert res.status_code == 200
    body = res.get_json()
    assert body["deleted"] == 1
    assert body["before"] == "2021-01-01"
    assert codes_of(client) == ["ETU-002"]


def test_bulk_delete_by_before_date_cascades(client):
    create_student(client, code="ETU-001")
    add_grade(client, "ETU-001", score=12)
    pay(client, "ETU-001", amount=50)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE students SET created_at = '2019-05-05T00:00:00+00:00'")
    conn.commit()
    conn.close()

    assert client.delete("/students", json={"before": "2020-01-01"}, headers=AUTH).status_code == 200
    assert db_counts() == (0, 0, 0)


@pytest.mark.parametrize("payload", [
    {},
    {"codes": []},
    {"codes": "ETU-001"},
    {"codes": [1, 2]},
    {"codes": [""]},
    {"before": "17-10-2026"},
    {"before": "2026-13-45"},
    {"before": ""},
    {"codes": ["ETU-001"], "before": "2020-01-01"},
])
def test_bulk_delete_invalid_payload_returns_400(client, payload):
    seed_students(client, 2)
    res = client.delete("/students", json=payload, headers=AUTH)

    assert res.status_code == 400
    assert res.is_json
    assert db_counts()[0] == 2               # rien n'a ete supprime


def test_bulk_delete_requires_json_body(client):
    res = client.delete("/students", data="pas du json", content_type="text/plain", headers=AUTH)
    assert res.status_code == 400
    assert res.is_json
