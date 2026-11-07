import pytest
from fastapi.testclient import TestClient

from decision_diff import api, auth, errors, whatif


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setitem(api.STATE, "pool", object())
    return TestClient(api.app, base_url="https://testserver", raise_server_exceptions=False)


def test_validation_error_is_russian_with_field(client):
    r = client.post("/api/auth/login", json={"login": "a", "password": "12345678"})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation_error" and body["field"] == "login"
    assert body["detail"] == "«Логин»: слишком короткое значение: не меньше 2 символов"
    assert "string_too_short" in body["debug"] and "12345678" not in r.text


def test_uuid_and_missing_field_messages():
    msg = errors._validation_message({"type": "uuid_parsing", "loc": ("path", "run_id"), "msg": "x"})
    assert msg.startswith("«Прогон»: неверный идентификатор")
    assert errors._validation_message({"type": "missing", "loc": ("body", "procedures", "normalize"), "msg": "x"}) \
        == "«Процедура нормализации»: обязательное поле не заполнено"


def test_http_errors_share_one_shape(client):
    r = client.get("/api/me")
    assert r.status_code == 401
    assert r.json() == {"detail": "Вы не вошли в систему: войдите, чтобы продолжить.", "code": "not_signed_in",
                        "field": None, "debug": None}
    r = client.get("/api/nothing-here")
    assert (r.status_code, r.json()["code"]) == (404, "not_found")
    r = client.post("/api/auth/logout", headers={"sec-fetch-site": "cross-site"})
    assert (r.status_code, r.json()["code"]) == (403, "csrf")


def test_unexpected_error_does_not_leak_internals(client):
    client.cookies.set(auth.COOKIE, "token")
    r = client.get("/api/me")
    assert r.status_code == 500
    body = r.json()
    assert body["code"] == "internal_error" and "код инцидента" in body["detail"]
    assert "AttributeError" not in r.text and "connection" not in r.text and body["debug"] is None


@pytest.mark.parametrize("raw, expect", [
    ("SandboxError: timeout after 300 s", "дольше 300 с"),
    ("SandboxError: MemoryError: \nTraceback (most recent call last):\n  File \"cell\", line 2", "не хватило памяти"),
    ("SandboxError: sandbox died (signal SIGKILL)", "сигнал SIGKILL): обычно это нехватка памяти"),
    ("NameError: name 'x' is not defined\nTraceback:\n  File \"cell\", line 3, in <module>", "в строке 3: NameError"),
    ("ProcedureError: <workspace>: ZeroDivisionError: division by zero", "ZeroDivisionError - деление на ноль"),
    ("SyntaxError: invalid syntax (cell, line 7)", "в строке 7: SyntaxError"),
    ("FormatError: файл BEFORE/A1.json: некорректный JSON: unexpected end", "Файл BEFORE/A1.json: некорректный JSON"),
    ("ingest: RUN-1: не найдена папка среза BEFORE (ожидается RESULTS/BEFORE*)", "RUN-1: не найдена папка среза BEFORE"),
    ("something odd", "Операция завершилась с ошибкой"),
])
def test_stored_errors_get_russian_headlines(raw, expect):
    assert expect in errors.explain(raw)
    assert errors.humanize({"error": raw}) == {"error": errors.explain(raw), "error_debug": raw}


def test_whatif_validation_names_the_param_and_key():
    decl = {"params": {"cutoffs": {"type": "float_map", "keys_from": "cut_name", "min": 0.0, "max": 1.0, "default": {},
                                   "label": "Порог по имени"}}}
    choices = {"cut_name": ["MFO_ZAIMER_2026", "MFO_A"]}
    with pytest.raises(ValueError) as e:
        whatif.validate(decl, choices, {"cutoffs": {"MFO_ZAIMER_2026": 100}})
    assert str(e.value) == "«Порог по имени», MFO_ZAIMER_2026: значение 100 вне допустимого диапазона, нужно число от 0 до 1"
    assert errors.explain(f"ValueError: {e.value}") == str(e.value)
    with pytest.raises(ValueError, match="неизвестное имя 'X', допустимы: MFO_ZAIMER_2026, MFO_A"):
        whatif.validate(decl, choices, {"cutoffs": {"X": 0.5}})
    assert whatif.validate(decl, choices, {"cutoffs": {"MFO_A": 0.5}}) == {"cutoffs": {"MFO_A": 0.5}}


def test_invalid_upload_is_explained(client, tmp_path, monkeypatch):
    import dataclasses

    monkeypatch.setattr(api, "settings", dataclasses.replace(api.settings, datasets_dir=tmp_path))
    api.app.dependency_overrides[api.user] = lambda: {"user_id": "u1", "login": "a", "run_id": None}
    try:
        r = client.post("/api/datasets", files={"file": ("responses.zip", b"not a zip", "application/zip")})
    finally:
        api.app.dependency_overrides.clear()
    assert (r.status_code, r.json()["code"], r.json()["field"]) == (400, "bad_zip", "file")
    assert r.json()["detail"].startswith("Файл не является ZIP-архивом") and not any((tmp_path / "uploads").rglob("*.*"))
