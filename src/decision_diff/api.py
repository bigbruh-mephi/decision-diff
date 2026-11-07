"""HTTP API: auth, datasets, procedures, configurations, runs with live progress (SSE), report, checks, cells.

The API never executes user code: procedures are stored after an AST-only inspection and executed by workers in
the sandbox. Finished runs are immutable, so report payloads are serialized once and cached with strong ETags.
"""

import asyncio
import hashlib
import hmac
import os
import shutil
import time
import urllib.parse
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

import orjson
from fastapi import Depends, FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import auth, catalog, db, errors, export, queue
from .engine import procedures
from .report import ReportCache
from .settings import settings

REQUESTS = Counter("dd_http_requests_total", "HTTP requests", ["route", "status"])
LATENCY = Histogram("dd_http_request_seconds", "HTTP latency", ["route"])
MAX_UPLOAD = 2 << 30
MAX_ENTRIES = 300_000
MIN_FREE = 1 << 30  # below this the broker stops accepting tasks, and runs hang
STATE = {}


@asynccontextmanager
async def lifespan(app):
    Path(os.environ.get("TMPDIR", "/tmp")).mkdir(parents=True, exist_ok=True)
    pool = await db.pool(max_size=20)
    await db.migrate(pool)
    async with pool.connection() as c:
        await catalog.sync_changed(c, settings.procedures_dir)
        admin, password = os.environ.get("DD_ADMIN_LOGIN"), os.environ.get("DD_ADMIN_PASSWORD")
        if admin and password:
            await c.execute("""INSERT INTO users (login, password_hash) VALUES (%s, %s)
                               ON CONFLICT (login) DO UPDATE SET password_hash = excluded.password_hash""",
                            (admin, auth.hash_password(password)))
    conn, ch = await queue.connect()
    STATE.update(pool=pool, conn=conn, ch=ch, reports=ReportCache(), payloads={})
    yield
    await conn.close()
    await pool.close()


_DOCS = os.environ.get("DD_API_DOCS", "0") in ("1", "true", "yes")
app = FastAPI(title="decision-diff", lifespan=lifespan, docs_url="/api/docs" if _DOCS else None,
              openapi_url="/api/openapi.json" if _DOCS else None)
errors.install(app)


@app.middleware("http")
async def guard(request: Request, call_next):
    t0 = time.perf_counter()
    if request.url.path.startswith("/api/") and not auth.csrf_ok(request):
        return JSONResponse(errors.body(403, "Запрос с другого сайта отклонен (защита от подделки запросов). Откройте "
                                             "приложение по его адресу и повторите.", "csrf"), 403)
    response = await call_next(request)
    route = request.scope.get("route")
    name = route.path if route else "unmatched"
    REQUESTS.labels(name, response.status_code).inc()
    LATENCY.labels(name).observe(time.perf_counter() - t0)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response


def pool():
    return STATE["pool"]


async def session(request: Request):
    return await auth.current(request, pool())


async def user(s=Depends(session)):
    if s["user_id"] is None:
        raise errors.Problem(403, "read_only", "Ссылка на отчет дает доступ только на просмотр. Для этого действия войдите "
                                                 "под своей учетной записью.")
    return s


def j(data, status=200, headers=None):
    return Response(orjson.dumps(data, default=str), status, headers, media_type="application/json")


class Credentials(BaseModel):
    login: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_.@-]+$")
    password: str = Field(min_length=8, max_length=256)


class LoginBody(Credentials):
    # Length is a sign-up rule; the admin password comes from .env and is not bound by it
    password: str = Field(min_length=1, max_length=256)


_HASH_SLOTS = asyncio.Semaphore(4)


async def _argon(fn, *args):
    """argon2 off the event loop, at most 4 at once: bounds CPU and memory (19 MiB each) under login spraying."""
    async with _HASH_SLOTS:
        return await asyncio.to_thread(fn, *args)


@app.post("/api/auth/register")
async def register(body: Credentials, request: Request, response: Response):
    async with pool().connection() as c:
        n = (await (await c.execute("SELECT count(*) AS n FROM users")).fetchone())["n"]
        if n and not settings_flag("DD_OPEN_REGISTRATION"):
            raise errors.Problem(403, "registration_closed", "Регистрация закрыта: учетную запись выдает администратор.")
        try:
            row = await (await c.execute("INSERT INTO users (login, password_hash) VALUES (%s, %s) RETURNING id",
                                         (body.login, await _argon(auth.hash_password, body.password)))).fetchone()
        except Exception as e:
            raise errors.Problem(409, "login_taken", "Этот логин уже занят: выберите другой.", "login") from e
        await c.execute("INSERT INTO audit (user_id, action) VALUES (%s, 'register')", (row["id"],))
        return await _login(c, row["id"], body.login, request, response)


def settings_flag(name):
    return os.environ.get(name, "0") in ("1", "true", "yes")


async def _login(c, user_id, login, request, response, run_id=None, days=auth.SESSION_DAYS):
    token = auth.new_token()
    await c.execute("INSERT INTO sessions (token_hash, user_id, run_id, expires_at) "
                    "VALUES (%s, %s, %s, now() + make_interval(days => %s))",
                    (auth.token_hash(token), user_id, run_id, days))
    name, secure = auth.session_cookie(request)
    response.set_cookie(name, token, max_age=days * 86400, httponly=True, secure=secure, samesite="lax", path="/")
    return {"user_id": str(user_id) if user_id else None, "login": login, "run_id": str(run_id) if run_id else None}


@app.post("/api/auth/login")
async def login(body: LoginBody, request: Request, response: Response):
    ip = request.client.host if request.client else ""
    async with pool().connection() as c:
        failed = await (await c.execute("""SELECT count(*) FILTER (WHERE target = %s) AS login,
                                                  count(*) FILTER (WHERE data->>'ip' = %s) AS ip
                                           FROM audit WHERE action = 'login.failed'
                                           AND ts > now() - interval '15 minutes'""", (body.login, ip))).fetchone()
        if failed["login"] >= 10 or failed["ip"] >= 100:
            raise errors.Problem(429, "too_many_attempts", "Слишком много неудачных попыток входа. Повторите через 15 минут.")
        row = await (await c.execute("SELECT id, password_hash FROM users WHERE login = %s",
                                     (body.login,))).fetchone()
        ok = await _argon(auth.verify_password, row["password_hash"] if row else auth.DUMMY_HASH, body.password)
        if not (row and ok):
            await c.execute("INSERT INTO audit (action, target, data) VALUES ('login.failed', %s, %s)",
                            (body.login, Jsonb({"ip": ip})))
            await c.commit()
            raise errors.Problem(401, "wrong_credentials", "Неверный логин или пароль.")
        await c.execute("INSERT INTO audit (user_id, action) VALUES (%s, 'login')", (row["id"],))
        return await _login(c, row["id"], body.login, request, response)


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    token = auth.session_token(request)
    if token:
        async with pool().connection() as c:
            await c.execute("DELETE FROM sessions WHERE token_hash = %s", (auth.token_hash(token),))
    name, secure = auth.session_cookie(request)
    response.delete_cookie(name, path="/", secure=secure)
    return {"ok": True}


@app.get("/api/me")
async def me(s=Depends(session)):
    return {"user_id": str(s["user_id"]) if s["user_id"] else None, "login": s["login"],
            "run_id": str(s["run_id"]) if s["run_id"] else None}


def _dataset_info(path):
    from .engine import ingest

    try:
        b, a = ingest.slice_dirs(path)
    except FileNotFoundError:
        return None
    man = ingest.read_manifest(path)
    base = settings.data_dir if path.is_relative_to(settings.data_dir / "uploads") else settings.datasets_dir
    return {"path": str(path.relative_to(base)), "run_id": man.get("run_id", path.name),
            "applications": man.get("applications"), "before": len(os.listdir(b)),
            "after": len(os.listdir(a)), "excluded": man.get("technical_fields_always_differ", [])}


def _visible(rel, s):
    """Uploads are private to their owner; admin-placed datasets are shared."""
    parts = Path(rel).parts
    return not (parts and parts[0] == "uploads") or (len(parts) > 1 and parts[1] == str(s["user_id"]))


@app.get("/api/datasets")
async def datasets(s=Depends(user)):
    return await asyncio.to_thread(_find_datasets, s)


def _dataset_root(rel):
    """Uploads live on the data volume (writable by the API); placed datasets in the read-only datasets folder."""
    return settings.data_dir if Path(rel).parts[:1] == ("uploads",) else settings.datasets_dir


def _find_datasets(s):
    """Placed datasets plus the user's own uploads; the walk does not descend into a found dataset (its files)."""
    found = []
    for root, skip in ((settings.datasets_dir, settings.datasets_dir / "uploads"),
                       (settings.data_dir / "uploads" / str(s["user_id"]), None)):
        for d, dirs, files in os.walk(root):
            m = Path(d)
            if m == skip:
                dirs.clear()
            elif m != root and ("manifest.json" in files or "RESULTS" in dirs):
                dirs.clear()
                if info := _dataset_info(m):
                    found.append(info)
            else:
                dirs.sort()
    return sorted(found, key=lambda x: x["path"])


def _resolve_dataset(rel, s):
    """Dataset folder under the datasets root; ``..``, absolute paths and other users' uploads are rejected."""
    parts = Path(rel).parts
    p = _dataset_root(rel) / rel
    if not rel or Path(rel).is_absolute() or ".." in parts or not p.is_dir() or not _visible(rel, s):
        raise errors.Problem(400, "unknown_dataset", "Датасет не найден: выберите его из списка или загрузите архив заново.",
                             "dataset")
    return p.absolute()


def _safe_extract(zip_path, dest):
    """Extract after checking every member: inside ``dest``, at most MAX_ENTRIES files and MAX_UPLOAD bytes in total
    (declared sizes; zipfile stops reading a member at its declared size), and leaving MIN_FREE on the volume."""
    with zipfile.ZipFile(zip_path) as z:
        members = z.infolist()
        if len(members) > MAX_ENTRIES:
            raise errors.Problem(413, "archive_too_many_files", f"В архиве больше {MAX_ENTRIES} файлов. Разделите данные на "
                                                                 "несколько архивов и догрузите их порциями.")
        total = 0
        for info in members:
            target = (dest / info.filename).resolve()
            if not target.is_relative_to(dest.resolve()):
                raise errors.Problem(400, "archive_unsafe_path", f"Недопустимый путь в архиве: {info.filename}. Соберите "
                                                                "архив без абсолютных путей и «..».")
            total += info.file_size
        disk = shutil.disk_usage(dest)
        if total > MAX_UPLOAD or total > disk.free - MIN_FREE:
            raise errors.Problem(413, "archive_too_large", f"После распаковки архив больше {MAX_UPLOAD >> 30} ГБ или "
                                                          "не помещается на диск датасетов.")
        z.extractall(dest)


@app.post("/api/datasets")
async def upload_dataset(file: UploadFile, s=Depends(user)):
    """Upload a zip of a run folder (manifest.json + RESULTS/BEFORE*/ + RESULTS/AFTER*/)."""
    dest = settings.data_dir / "uploads" / str(s["user_id"]) / uuid.uuid4().hex[:12]
    try:
        dest.mkdir(parents=True)
    except OSError as e:
        raise errors.Problem(500, "datasets_not_writable", "Сервер не может сохранить архив: папка данных недоступна "
                                                           "для записи. Сообщите администратору.") from e
    tmp = dest.with_suffix(".zip")
    size = 0
    with tmp.open("wb") as f:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD:
                tmp.unlink()
                shutil.rmtree(dest)
                raise errors.Problem(413, "upload_too_large", f"Файл больше {MAX_UPLOAD >> 30} ГБ. Разделите данные на "
                                                             "несколько архивов.", "file")
            f.write(chunk)
    try:
        await asyncio.to_thread(_safe_extract, tmp, dest)
    except (zipfile.BadZipFile, HTTPException) as e:
        shutil.rmtree(dest)
        if isinstance(e, HTTPException):
            raise
        raise errors.Problem(400, "bad_zip", "Файл не является ZIP-архивом или поврежден. Загрузите .zip с папкой "
                                             "прогона.", "file") from e
    finally:
        tmp.unlink(missing_ok=True)
    roots = [m for m in [dest, *dest.rglob("*")] if m.is_dir() and ((m / "manifest.json").exists()
                                                                     or (m / "RESULTS").is_dir())]
    if not roots:
        shutil.rmtree(dest)
        raise errors.Problem(400, "no_run_folder", "В архиве нет папки прогона: нужен manifest.json или папка "
                                                   "RESULTS/ с подпапками BEFORE* и AFTER*.", "file")
    info = await asyncio.to_thread(_dataset_info, roots[0])
    if info is None:
        shutil.rmtree(dest)
        raise errors.Problem(400, "no_slices", "В папке прогона нет срезов: нужны подпапки BEFORE* и AFTER* (в RESULTS/ "
                                               "или по путям из manifest.json).", "file")
    return info


@app.get("/api/procedures")
async def list_procedures(s=Depends(user)):
    async with pool().connection() as c:
        await catalog.sync_changed(c, settings.procedures_dir)
        rows = await (await c.execute("""
            SELECT id, kind, name, origin, path, sha, updated_at, owner_id = %s AS mine FROM procedures
            WHERE origin = 'file' OR owner_id = %s ORDER BY kind, origin, name""",
                                      (s["user_id"], s["user_id"]))).fetchall()
    return j(rows)


@app.get("/api/procedures/{pid}")
async def get_procedure(pid: uuid.UUID, s=Depends(user)):
    async with pool().connection() as c:
        row = await (await c.execute("""
            SELECT p.id, p.kind, p.name, p.origin, p.path, p.sha, p.updated_at, v.code FROM procedures p
            JOIN code_versions v ON v.sha = p.sha WHERE p.id = %s AND (p.origin = 'file' OR p.owner_id = %s)""",
                                     (pid, s["user_id"]))).fetchone()
    if not row:
        raise errors.Problem(404, "procedure_not_found", "Процедура не найдена: возможно, она удалена или принадлежит "
                                                         "другому пользователю.")
    try:
        declared = procedures.declaration(row["code"])[1].get("params")
    except procedures.ProcedureError:
        declared = None
    return j({**row, "params": declared if isinstance(declared, dict) else {}})


class Code(BaseModel):
    code: str = Field(max_length=512_000)


@app.post("/api/procedures")
async def create_procedure(body: Code, s=Depends(user)):
    return await _save_procedure(body.code, s, None)


@app.put("/api/procedures/{pid}")
async def update_procedure(pid: uuid.UUID, body: Code, s=Depends(user)):
    return await _save_procedure(body.code, s, pid)


async def _save_procedure(code, s, pid):
    try:
        async with pool().connection() as c:
            new_id = await catalog.save_workspace(c, s["user_id"], code, pid)
            if not new_id:
                raise errors.Problem(404, "procedure_not_found", "Процедура рабочей области не найдена: сохраните код как "
                                                                 "новую процедуру.")
    except procedures.ProcedureError as e:
        raise errors.Problem(422, "procedure_invalid", str(e), "code") from e
    return await get_procedure(new_id, s)


@app.post("/api/procedures/inspect")
async def inspect_procedure(body: Code, _=Depends(user)):
    """Syntax and contract check without execution (editor diagnostics)."""
    try:
        return procedures.inspect_source(body.code)
    except procedures.ProcedureError as e:
        raise errors.Problem(422, "procedure_invalid", str(e), "code") from e


@app.get("/api/code/{sha}")
async def code_version(sha: str, s=Depends(user)):
    """Source of a code version the user can reach: a shipped file procedure, their own procedure, or code that ran
    in their run, cell or what-if."""
    async with pool().connection() as c:
        row = await (await c.execute("""
            SELECT v.sha, v.kind, v.name, v.code, v.created_at FROM code_versions v WHERE v.sha = %(sha)s AND (
              EXISTS (SELECT 1 FROM procedures p WHERE p.sha = v.sha AND (p.origin = 'file' OR p.owner_id = %(u)s))
              OR EXISTS (SELECT 1 FROM runs r JOIN stage_runs x ON x.run_id = r.id
                         WHERE r.owner_id = %(u)s AND x.code_sha = v.sha)
              OR EXISTS (SELECT 1 FROM runs r JOIN check_results x ON x.run_id = r.id
                         WHERE r.owner_id = %(u)s AND x.check_sha = v.sha)
              OR EXISTS (SELECT 1 FROM cell_runs x WHERE x.user_id = %(u)s AND x.code_sha = v.sha)
              OR EXISTS (SELECT 1 FROM runs r JOIN whatif_runs x ON x.run_id = r.id
                         WHERE r.owner_id = %(u)s AND x.code_sha = v.sha))""",
                                     {"sha": sha, "u": s["user_id"]})).fetchone()
    if not row:
        raise errors.Problem(404, "code_not_found", "Эта версия кода не найдена или недоступна.")
    return j(row)


class Selection(BaseModel):
    normalize: uuid.UUID
    compare: uuid.UUID
    segments: uuid.UUID
    lineage: uuid.UUID | None = None
    whatif: uuid.UUID | None = None
    checks: list[uuid.UUID] = []


class ConfigBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    dataset: str | None = None
    procedures: Selection
    params: dict = {}


@app.get("/api/configs")
async def list_configs(s=Depends(user)):
    async with pool().connection() as c:
        rows = await (await c.execute("SELECT * FROM run_configs WHERE owner_id = %s ORDER BY updated_at DESC",
                                      (s["user_id"],))).fetchall()
    return j(rows)


@app.post("/api/configs")
async def create_config(body: ConfigBody, s=Depends(user)):
    async with pool().connection() as c:
        row = await (await c.execute("""
            INSERT INTO run_configs (owner_id, name, dataset, procedure_ids, params) VALUES (%s, %s, %s, %s, %s)
            RETURNING *""", (s["user_id"], body.name, body.dataset, Jsonb(body.procedures.model_dump(mode="json")),
                             Jsonb(body.params)))).fetchone()
    return j(row)


@app.put("/api/configs/{cid}")
async def update_config(cid: uuid.UUID, body: ConfigBody, s=Depends(user)):
    async with pool().connection() as c:
        row = await (await c.execute("""
            UPDATE run_configs SET name = %s, dataset = %s, procedure_ids = %s, params = %s, updated_at = now()
            WHERE id = %s AND owner_id = %s RETURNING *""",
                                     (body.name, body.dataset, Jsonb(body.procedures.model_dump(mode="json")),
                                      Jsonb(body.params), cid, s["user_id"]))).fetchone()
    if not row:
        raise errors.Problem(404, "config_not_found", "Конфигурация не найдена: возможно, ее удалили.")
    return j(row)


@app.delete("/api/configs/{cid}")
async def delete_config(cid: uuid.UUID, s=Depends(user)):
    async with pool().connection() as c:
        await c.execute("UPDATE runs SET config_id = NULL WHERE config_id = %s AND owner_id = %s", (cid, s["user_id"]))
        await c.execute("DELETE FROM run_configs WHERE id = %s AND owner_id = %s", (cid, s["user_id"]))
    return {"ok": True}


class RunBody(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    dataset: str | None = None
    config_id: uuid.UUID | None = None
    procedures: Selection | None = None
    params: dict = {}
    seal: bool = True


@app.post("/api/runs")
async def create_run(body: RunBody, s=Depends(user)):
    """Create a run and submit its first batch; ``seal=false`` keeps it open for more batches."""
    async with pool().connection() as c:
        sel, params, dataset = body.procedures, body.params, body.dataset
        if body.config_id:
            cfg = await (await c.execute("SELECT * FROM run_configs WHERE id = %s AND owner_id = %s",
                                         (body.config_id, s["user_id"]))).fetchone()
            if not cfg:
                raise errors.Problem(404, "config_not_found", "Конфигурация не найдена: возможно, ее удалили.",
                                     "config_id")
            sel = sel or Selection(**cfg["procedure_ids"])
            params = {**cfg["params"], **params}
            dataset = dataset or cfg["dataset"]
        if not sel or not dataset:
            raise errors.Problem(422, "run_incomplete", "Для запуска нужны датасет и процедуры (или сохраненная "
                                                     "конфигурация).", "dataset" if sel else "procedures")
        sample = params.get("sample_apps")
        if sample is not None and (type(sample) is not int or sample < 1):
            raise errors.Problem(422, "bad_sample", "Размер выборки должен быть положительным целым числом.", "sample_apps")
        if sample and not body.seal:
            raise errors.Problem(422, "sample_with_batches", "Выборку нельзя совмещать с догрузкой порций.", "sample_apps")
        source = _resolve_dataset(dataset, s)
        try:
            snap = await catalog.snapshot(c, sel.model_dump(mode="json"), params, s["user_id"])
        except procedures.ProcedureError as e:
            raise errors.Problem(422, "procedure_invalid", str(e), "procedures") from e
        run_id = await db.create_run(c, s["user_id"], body.name or Path(dataset).name, snap, params, body.config_id)
        if body.seal:
            await c.execute("UPDATE runs SET seal_requested = true WHERE id = %s", (run_id,))
        await db.open_batch(c, run_id, str(source))
        await c.execute("INSERT INTO audit (user_id, action, target) VALUES (%s, 'run.create', %s)",
                        (s["user_id"], str(run_id)))
    await queue.publish(STATE["ch"], queue.WORK, {"kind": "ingest", "run_id": str(run_id),
                                                  "user_id": str(s["user_id"]), "source": str(source),
                                                  "seal": body.seal, "sample": sample})
    return await get_run(run_id, s)


class BatchBody(BaseModel):
    dataset: str
    seal: bool = False


@app.post("/api/runs/{run_id}/batches")
async def add_batch(run_id: uuid.UUID, body: BatchBody, s=Depends(user)):
    """Add another batch of responses to an open run; processing starts immediately."""
    await _own_run(run_id, s, states=("receiving",))
    source = _resolve_dataset(body.dataset, s)
    async with pool().connection() as c:
        await db.open_batch(c, run_id, str(source))
    await queue.publish(STATE["ch"], queue.WORK, {"kind": "ingest", "run_id": str(run_id),
                                                  "user_id": str(s["user_id"]), "source": str(source),
                                                  "seal": body.seal})
    return {"ok": True}


@app.post("/api/runs/{run_id}/seal")
async def seal_run(run_id: uuid.UUID, s=Depends(user)):
    await _own_run(run_id, s, states=("receiving",))
    async with pool().connection() as c:
        flip = await db.seal(c, run_id)
    if flip:
        await queue.publish(STATE["ch"], queue.FINALIZE, {"kind": "finalize", "run_id": str(run_id),
                                                          "user_id": str(s["user_id"])})
    return {"ok": True}


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: uuid.UUID, s=Depends(user)):
    await _own_run(run_id, s)
    async with pool().connection() as c:
        await c.execute("UPDATE runs SET state = 'cancelled', finished_at = now() WHERE id = %s "
                        "AND state NOT IN ('done', 'failed', 'cancelled')", (run_id,))
        await c.execute("UPDATE chunks SET state = 'cancelled' WHERE run_id = %s AND state IN ('ready', 'queued')",
                        (run_id,))
        await db.event(c, run_id, "run.cancelled")
    return {"ok": True}


async def _interactive_slot(c, table, user_id, limit=4):
    """At most ``limit`` unfinished interactive executions per user, so one analyst cannot fill the shared queue."""
    row = await (await c.execute(f"""SELECT count(*) AS n FROM {table} WHERE user_id = %s
                                     AND state IN ('queued', 'running')
                                     AND created_at > now() - interval '10 minutes'""", (user_id,))).fetchone()
    if row["n"] >= limit:
        raise errors.Problem(429, "too_many_executions", f"Одновременно можно выполнять не больше {limit} запусков: "
                                                        "дождитесь завершения предыдущих и повторите.")


RUN_STATE = {"receiving": "прием данных", "sealed": "обработка", "finalizing": "сборка отчета", "done": "готов",
             "failed": "завершился с ошибкой", "cancelled": "отменен"}


async def _own_run(run_id, s, states=None):
    async with pool().connection() as c:
        row = await (await c.execute("SELECT * FROM runs WHERE id = %s", (run_id,))).fetchone()
    allowed = row and (row["owner_id"] == s["user_id"] or (s["run_id"] and s["run_id"] == row["id"]))
    if not allowed:
        raise errors.Problem(404, "run_not_found", "Прогон не найден: возможно, он удален или ссылка устарела.",
                             "run_id")
    if states and row["state"] not in states:
        need = "завершенного прогона" if "done" in states else "прогона, который еще принимает данные"
        raise errors.Problem(409, "run_state", f"Действие доступно только для {need}. Сейчас: "
                                               f"{RUN_STATE.get(row['state'], row['state'])}.")
    return row


@app.get("/api/runs")
async def list_runs(s=Depends(user)):
    async with pool().connection() as c:
        rows = await (await c.execute("""
            SELECT id, name, state, apps, total_chunks, done_chunks, failed_chunks, stats, error, created_at,
                   finished_at, config_id FROM runs WHERE owner_id = %s ORDER BY created_at DESC LIMIT 200""",
                                      (s["user_id"],))).fetchall()
    return j([errors.humanize(r) for r in rows])


@app.get("/api/runs/{run_id}")
async def get_run(run_id: uuid.UUID, s=Depends(session)):
    row = await _own_run(run_id, s)
    async with pool().connection() as c:
        chunks = await (await c.execute("""
            SELECT state, count(*) AS n, coalesce(sum(apps), 0) AS apps,
                   coalesce(sum((stats->>'changed_apps')::int), 0) AS changed FROM chunks WHERE run_id = %s
            GROUP BY state""", (run_id,))).fetchall()
    return j({**errors.humanize({k: row[k] for k in ("id", "name", "state", "apps", "total_chunks", "done_chunks",
                                                    "failed_chunks", "stats", "error", "created_at", "sealed_at",
                                                    "finished_at", "config_id", "params", "seal_requested")}),
              "procedures": row["procedures"], "chunks": chunks})


@app.get("/api/runs/{run_id}/journal")
async def journal(run_id: uuid.UUID, s=Depends(session)):
    """Stage journal: per stage totals, timings, rows, errors and the exact code version executed."""
    await _own_run(run_id, s)
    async with pool().connection() as c:
        stages = await (await c.execute("""
            SELECT stage, code_sha, count(*) AS executions, count(*) FILTER (WHERE status <> 'done') AS failed,
                   min(started_at) AS started_at, max(started_at + make_interval(secs => duration_ms / 1000)) AS ended_at,
                   sum(duration_ms) AS cpu_ms, sum(rows_in) AS rows_in, sum(rows_out) AS rows_out,
                   (array_agg(error) FILTER (WHERE error IS NOT NULL))[1] AS error
            FROM stage_runs WHERE run_id = %s GROUP BY stage, code_sha ORDER BY min(started_at)""",
                                        (run_id,))).fetchall()
        events = await (await c.execute("SELECT id, ts, type, data FROM run_events WHERE run_id = %s ORDER BY id "
                                        "DESC LIMIT 200", (run_id,))).fetchall()
        chunks = await (await c.execute("""SELECT chunk_no, state, apps, attempts, started_at, finished_at, worker,
                                           error FROM chunks WHERE run_id = %s ORDER BY chunk_no""",
                                        (run_id,))).fetchall()
    return j({"stages": [errors.humanize(x) for x in stages], "chunks": [errors.humanize(x) for x in chunks],
              "events": [{**e, "data": errors.humanize(e["data"])} for e in events]})


@app.get("/api/events")
async def events(request: Request, run: list[uuid.UUID] | None = None, s=Depends(session)):
    """Server-Sent Events of run progress; resumes after ``Last-Event-ID`` so progress survives a page reload."""
    if run:
        for r in run:
            await _own_run(r, s)
    raw = request.headers.get("last-event-id") or request.query_params.get("after") or "0"
    last = int(raw) if raw.isdigit() else 0
    if not run and not last:
        async with pool().connection() as c:
            last = (await (await c.execute("SELECT coalesce(max(id), 0) AS m FROM run_events")).fetchone())["m"]

    async def stream():
        nonlocal last
        yield "retry: 2000\n\n"
        idle = 0
        while not await request.is_disconnected():
            async with pool().connection() as c:
                if run:
                    rows = await (await c.execute("""
                        SELECT e.id, e.run_id, e.type, e.data FROM run_events e WHERE e.run_id = ANY(%s) AND e.id > %s
                        ORDER BY e.id LIMIT 500""", (run, last))).fetchall()
                else:
                    rows = await (await c.execute("""
                        SELECT e.id, e.run_id, e.type, e.data FROM run_events e JOIN runs r ON r.id = e.run_id
                        WHERE r.owner_id = %s AND e.id > %s ORDER BY e.id LIMIT 500""",
                                                  (s["user_id"], last))).fetchall()
            for r in rows:
                last = r["id"]
                data = orjson.dumps({"run_id": r["run_id"], **errors.humanize(r["data"])}, default=str).decode()
                yield f"id: {r['id']}\nevent: {r['type']}\ndata: {data}\n\n"
            idle = 0 if rows else idle + 1
            if idle and idle % 15 == 0:
                yield ": keep-alive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _report(run_id, s):
    row = await _own_run(run_id, s)
    if row["state"] != "done":
        raise errors.Problem(409, "run_not_ready", "Отчет откроется после завершения обработки прогона.")
    path = settings.run_dir(run_id) / "run.duckdb"
    return await asyncio.to_thread(STATE["reports"].get, str(run_id), path, row["report_params"] or {}), row


BAD_SCOPE = "Срез отчета в адресе поврежден: откройте отчет заново из списка прогонов."


def _scope(request: Request):
    raw = request.query_params.get("scope")
    if not raw:
        return {}
    try:
        spec = orjson.loads(raw)
    except orjson.JSONDecodeError as e:
        raise errors.Problem(400, "bad_scope", BAD_SCOPE, "scope") from e
    if not isinstance(spec, dict):
        raise errors.Problem(400, "bad_scope", BAD_SCOPE, "scope")
    return spec


def _cached(request, run_id, name, build):
    """Immutable run payloads: serialized once, served with a strong ETag and 304 revalidation."""
    key = (str(run_id), name, request.url.query)
    hit = STATE["payloads"].get(key)
    if hit is None:
        body = orjson.dumps(build(), default=str)
        hit = (body, '"' + hashlib.sha256(body).hexdigest()[:20] + '"')
        if len(STATE["payloads"]) > 2000:
            STATE["payloads"].clear()
        STATE["payloads"][key] = hit
    body, etag = hit
    headers = {"ETag": etag, "Cache-Control": "private, max-age=0, must-revalidate"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type="application/json", headers=headers)


@app.get("/api/runs/{run_id}/summary")
async def summary(run_id: uuid.UUID, request: Request, s=Depends(session)):
    rep, row = await _report(run_id, s)
    spec = _scope(request)
    return _cached(request, run_id, "summary", lambda: {
        **rep.summary(spec), "run": {"id": row["id"], "name": row["name"], "stats": row["stats"]},
        "structure": rep.structure[:50], "clusters": rep.cluster_rows(spec)})


@app.get("/api/runs/{run_id}/clusters")
async def clusters(run_id: uuid.UUID, request: Request, s=Depends(session)):
    rep, _ = await _report(run_id, s)
    return _cached(request, run_id, "clusters", lambda: rep.cluster_rows(_scope(request)))


@app.get("/api/runs/{run_id}/attributes")
async def attributes(run_id: uuid.UUID, request: Request, s=Depends(session)):
    rep, _ = await _report(run_id, s)
    return _cached(request, run_id, "attributes", lambda: rep.attributes(_scope(request)))


@app.get("/api/runs/{run_id}/segments")
async def segments(run_id: uuid.UUID, request: Request, s=Depends(session)):
    rep, _ = await _report(run_id, s)
    return _cached(request, run_id, "segments", lambda: rep.segment_rows(_scope(request)))


@app.get("/api/runs/{run_id}/fields")
async def fields(run_id: uuid.UUID, request: Request, s=Depends(session)):
    """Pivot dimensions with their distinct values (fact fields, segment fields, CLUSTER)."""
    rep, _ = await _report(run_id, s)

    def build():
        from .report import FACT_DIMS

        facts = {d: sorted({str(rep._fact_value(f, d)) for f, _ in rep.facts})[:500] for d in FACT_DIMS}
        segs = {k: sorted(v)[:500] for k, v in rep.seg.items()}
        return {"fact": facts, "segment": segs, "cluster": [str(c["cid"]) for c in rep.clusters] + ["none"]}

    return _cached(request, run_id, "fields", build)


class PivotBody(BaseModel):
    rows: list[str] = Field(default=[], max_length=8)
    cols: list[str] = Field(default=[], max_length=4)
    filters: dict[str, list[str]] = {}
    scope: dict = {}


@app.post("/api/runs/{run_id}/pivot")
async def pivot(run_id: uuid.UUID, body: PivotBody, s=Depends(session)):
    rep, _ = await _report(run_id, s)
    t0 = time.perf_counter()
    res = await asyncio.to_thread(rep.pivot, body.rows, body.cols, body.filters, body.scope)
    return j({**res, "ms": round((time.perf_counter() - t0) * 1000, 2)})


class CellBody(BaseModel):
    cell: dict[str, str] = {}
    filters: dict[str, list[str]] = {}
    scope: dict = {}
    transition: dict | None = None
    offset: int = Field(0, ge=0)
    limit: int = Field(100, ge=1, le=1000)


@app.post("/api/runs/{run_id}/apps")
async def app_list(run_id: uuid.UUID, body: CellBody, s=Depends(session)):
    """Applications of a pivot cell, a transition or a scope (the one list every drill-down lands in)."""
    rep, _ = await _report(run_id, s)

    def build():
        bm = rep.cell_apps(body.cell, body.filters, body.scope) if body.cell or body.filters else rep.scope(body.scope)
        if body.transition:
            bm &= rep.transition_apps(int(body.transition["path_id"]), body.transition["before"],
                                      body.transition["after"])
        return rep.app_list(bm, body.offset, body.limit)

    return j(await asyncio.to_thread(build))


@app.get("/api/runs/{run_id}/apps/{app_id}")
async def app_detail(run_id: uuid.UUID, app_id: str, s=Depends(session)):
    rep, _ = await _report(run_id, s)
    res = await asyncio.to_thread(rep.app, app_id)
    if res is None:
        raise errors.Problem(404, "app_not_found", f"Заявки {app_id} нет в этом прогоне.")
    async with pool().connection() as c:
        results = await (await c.execute("""SELECT DISTINCT ON (check_id) check_id, check_sha, label, rows
                                            FROM check_results WHERE run_id = %s ORDER BY check_id, created_at DESC""",
                                         (run_id,))).fetchall()
    return j({**res, "checks": await asyncio.to_thread(rep.app_checks, app_id, results)})


@app.get("/api/runs/{run_id}/checks")
async def checks(run_id: uuid.UUID, s=Depends(session)):
    """All check rows of the run in one table (PASS/FAIL, confidence), newest code version per check."""
    await _own_run(run_id, s)
    async with pool().connection() as c:
        rows = await (await c.execute("""
            SELECT DISTINCT ON (check_id) check_id, check_sha, label, rows, duration_ms, error, created_at
            FROM check_results WHERE run_id = %s ORDER BY check_id, created_at DESC""", (run_id,))).fetchall()
    return j([errors.humanize(r) for r in rows])


class CheckRunBody(BaseModel):
    procedure_id: uuid.UUID
    params: dict = {}


@app.post("/api/runs/{run_id}/checks")
async def run_check(run_id: uuid.UUID, body: CheckRunBody, s=Depends(user)):
    """Execute a (new or edited) check on a finished run; its rows join the same report."""
    await _own_run(run_id, s, states=("done",))
    async with pool().connection() as c:
        row = await (await c.execute("SELECT sha, kind FROM procedures WHERE id = %s AND (origin = 'file' OR "
                                     "owner_id = %s)", (body.procedure_id, s["user_id"]))).fetchone()
        if not row or row["kind"] != "check":
            raise errors.Problem(422, "not_a_check", "Выбранная процедура не является автопроверкой (нужен CHECK).",
                                 "procedure_id")
        await db.event(c, run_id, "check.queued", {"sha": row["sha"]})
    await queue.publish(STATE["ch"], queue.INTERACTIVE, {"kind": "check", "run_id": str(run_id),
                                                         "user_id": str(s["user_id"]), "sha": row["sha"],
                                                         "params": body.params})
    STATE["payloads"].clear()
    return {"queued": True, "sha": row["sha"]}


@app.get("/api/runs/{run_id}/ai")
async def ai_opinion(run_id: uuid.UUID, s=Depends(session)):
    """AI second opinion: ``{state: disabled|pending|done|failed, model, findings, ...}``; 404 when never requested."""
    await _own_run(run_id, s)
    if not settings.llm_enabled:
        return j({"state": "disabled"})
    async with pool().connection() as c:
        row = await (await c.execute("SELECT state, model, result FROM ai_opinions WHERE run_id = %s",
                                     (run_id,))).fetchone()
    if not row:
        raise errors.Problem(404, "ai_not_requested", "Мнение ИИ для этого прогона еще не запрашивалось.")
    return j(errors.humanize(row["result"]) or {"state": row["state"], "model": row["model"]})


@app.post("/api/runs/{run_id}/ai")
async def rerun_ai(run_id: uuid.UUID, s=Depends(user)):
    """Queue a fresh AI second opinion of a finished run."""
    await _own_run(run_id, s, states=("done",))
    if not settings.llm_enabled:
        raise errors.Problem(409, "llm_disabled", "Языковая модель не подключена: задайте DD_LLM_BASE_URL, "
                                                  "DD_LLM_API_KEY и DD_LLM_MODEL в .env.")
    async with pool().connection() as c:
        row = await (await c.execute("""
            INSERT INTO ai_opinions (run_id, state, model) VALUES (%s, 'pending', %s)
            ON CONFLICT (run_id) DO UPDATE SET state = 'pending', model = excluded.model, result = NULL,
                created_at = now(), finished_at = NULL
            WHERE ai_opinions.state <> 'pending' OR ai_opinions.created_at < now() - interval '15 minutes'
            RETURNING run_id""",
                                     (run_id, settings.llm_model))).fetchone()
    if row:
        await queue.publish(STATE["ch"], queue.INTERACTIVE, {"kind": "ai", "run_id": str(run_id)})
    return {"queued": bool(row)}


class CellRun(BaseModel):
    code: str = Field(max_length=512_000)
    run_id: uuid.UUID | None = None


@app.post("/api/cells")
async def run_cell(body: CellRun, s=Depends(user)):
    """Run workspace code in the sandbox against a finished run's datamart (``mart``); poll ``GET /api/cells/{id}``."""
    if body.run_id:
        await _own_run(body.run_id, s, states=("done",))
    sha = hashlib.sha256(body.code.encode()).hexdigest()[:16]
    async with pool().connection() as c:
        await _interactive_slot(c, "cell_runs", s["user_id"])
        await db.store_code(c, "cell", "cell", body.code, sha)
        row = await (await c.execute("INSERT INTO cell_runs (user_id, run_id, code_sha) VALUES (%s, %s, %s) "
                                     "RETURNING id", (s["user_id"], body.run_id, sha))).fetchone()
    await queue.publish(STATE["ch"], queue.INTERACTIVE, {"kind": "cell", "exec_id": str(row["id"]),
                                                         "user_id": str(s["user_id"]),
                                                         "run_id": str(body.run_id) if body.run_id else None,
                                                         "sha": sha})
    return {"id": str(row["id"])}


@app.get("/api/cells/{cell_id}")
async def get_cell(cell_id: uuid.UUID, s=Depends(user)):
    async with pool().connection() as c:
        row = await (await c.execute("SELECT * FROM cell_runs WHERE id = %s AND user_id = %s",
                                     (cell_id, s["user_id"]))).fetchone()
    if not row:
        raise errors.Problem(404, "cell_not_found", "Запуск ячейки не найден.")
    res = row["result"]
    if res and res.get("error"):
        raw = "\n".join([res["error"], *(res.get("traceback") or [])])
        res = errors.humanize({**res, "error": raw})
    return j({**row, "result": res})


@app.post("/api/runs/{run_id}/share")
async def share(run_id: uuid.UUID, request: Request, s=Depends(user)):
    """Create a revocable read-only link to this run's report (valid 30 days)."""
    await _own_run(run_id, s)
    token = auth.new_token()
    async with pool().connection() as c:
        await c.execute("INSERT INTO shares (token_hash, run_id, created_by, expires_at) "
                        "VALUES (%s, %s, %s, now() + interval '30 days')", (auth.token_hash(token), run_id,
                                                                           s["user_id"]))
    return {"url": str(request.base_url).rstrip("/") + f"/s/{token}"}


@app.delete("/api/runs/{run_id}/share", status_code=204)
async def revoke_shares(run_id: uuid.UUID, s=Depends(user)):
    """Revoke every share link of this run and end the sessions opened through them."""
    await _own_run(run_id, s)
    async with pool().connection() as c:
        await c.execute("DELETE FROM shares WHERE run_id = %s", (run_id,))
        await c.execute("DELETE FROM sessions WHERE run_id = %s AND user_id IS NULL", (run_id,))


@app.get("/s/{token}")
async def open_share(token: str, request: Request):
    """Open a share link; a signed-in user keeps their own session instead of being switched to the read-only one."""
    async with pool().connection() as c:
        row = await (await c.execute("SELECT run_id FROM shares WHERE token_hash = %s AND expires_at > now()",
                                     (auth.token_hash(token),))).fetchone()
        if not row:
            raise errors.Problem(404, "share_expired", "Ссылка на отчет истекла или отозвана: попросите автора прогона "
                                                       "прислать новую.")
        resp = RedirectResponse(f"/runs/{row['run_id']}", 303)
        try:
            signed_in = (await auth.current(request, pool()))["user_id"] is not None
        except HTTPException:
            signed_in = False
        if not signed_in:
            await _login(c, None, None, request, resp, run_id=row["run_id"], days=1)
    return resp


@app.get("/api/runs/{run_id}/export.html")
async def export_html(run_id: uuid.UUID, request: Request, s=Depends(session)):
    """Self-contained HTML report (checks + verdict + clusters) with links back into the application."""
    rep, row = await _report(run_id, s)
    async with pool().connection() as c:
        rows = await (await c.execute("""SELECT DISTINCT ON (check_id) check_id, check_sha, label, rows, error
                                         FROM check_results
                                         WHERE run_id = %s ORDER BY check_id, created_at DESC""", (run_id,))).fetchall()
    base = str(request.base_url).rstrip("/")
    html = await asyncio.to_thread(export.render, rep, row, rows, base)
    fname = urllib.parse.quote(f"report-{row['name']}.html")
    return HTMLResponse(html, headers={"Content-Disposition": f"attachment; filename=\"report.html\"; filename*=UTF-8''{fname}",
                                       "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                                                                  "img-src data:"})


@app.get("/metrics")
async def metrics(request: Request):
    """Prometheus metrics behind ``DD_METRICS_TOKEN`` (Bearer); disabled while the token is not set."""
    token = os.environ.get("DD_METRICS_TOKEN")
    if not token:
        raise errors.Problem(404, "metrics_disabled", "Метрики отключены: задайте DD_METRICS_TOKEN.")
    if not hmac.compare_digest(request.headers.get("authorization", "").encode(), f"Bearer {token}".encode()):
        raise errors.Problem(401, "metrics_token", "Нужен токен метрик в заголовке Authorization: Bearer.")
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/healthz")
async def healthz():
    async with pool().connection() as c:
        await c.execute("SELECT 1")
    return {"ok": True}


from .routes_whatif import router as whatif_router

app.include_router(whatif_router)

WEB = Path(__file__).resolve().parent / "web"


@app.get("/{path:path}", include_in_schema=False)
async def spa(path: str):
    """Built frontend; unknown paths fall back to index.html for client-side routing (never for ``/api/``)."""
    if path.startswith("api/"):
        raise errors.Problem(404, "not_found", "Такого адреса API нет.")
    target = (WEB / path).resolve()
    if path and target.is_relative_to(WEB) and target.is_file():
        headers = {"Cache-Control": "public, max-age=31536000, immutable"} if path.startswith("assets/") else {}
        return FileResponse(target, headers=headers)
    index = WEB / "index.html"
    if not index.exists():
        return HTMLResponse("<h1>decision-diff API</h1><p>Frontend is not built. See <a href='/api/docs'>API docs</a>.",
                            200)
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
