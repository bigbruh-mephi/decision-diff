"""User-facing errors in Russian: one JSON shape for every API error and headlines for stored technical errors.

API errors are ``{"detail": <Russian text>, "code": <machine code>, "field": <field or null>, "debug": <technical
detail or null>}`` with the original status code. Errors stored on runs, chunks, stages, checks, cells, what-if and AI
results stay raw (the analyst needs the traceback of their own code); :func:`explain` gives the Russian headline that
the API serves in ``error`` while the raw text moves to ``error_debug``.
"""

import logging
import re
import uuid

import psycopg
from fastapi import HTTPException as ApiHTTPException
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from .settings import settings

log = logging.getLogger("dd.api")


class Problem(ApiHTTPException):
    """HTTP error with a Russian ``detail``, a stable machine ``code`` and optionally the offending ``field``."""

    def __init__(self, status, code, detail, field=None):
        super().__init__(status, detail)
        self.code, self.field = code, field


STATUS = {400: ("bad_request", "Неверный запрос"), 401: ("unauthorized", "Нужно войти в систему"),
          403: ("forbidden", "Нет доступа"), 404: ("not_found", "Адрес не найден"),
          405: ("method_not_allowed", "Этот метод запроса не поддерживается"), 409: ("conflict", "Конфликт состояния"),
          413: ("too_large", "Слишком большой запрос"), 422: ("invalid", "Неверные данные запроса"),
          429: ("too_many", "Слишком много запросов: повторите позже")}

FIELDS = {"login": "Логин", "password": "Пароль", "name": "Название", "code": "Код", "dataset": "Датасет",
          "procedures": "Процедуры", "normalize": "Процедура нормализации", "compare": "Процедура сравнения",
          "segments": "Процедура разрезов", "lineage": "Цепочка решения", "checks": "Автопроверки",
          "params": "Параметры", "procedure_id": "Процедура", "config_id": "Конфигурация", "run_id": "Прогон",
          "pid": "Процедура", "cid": "Конфигурация", "cell_id": "Запуск ячейки", "exec_id": "Расчет «что если»",
          "rows": "Строки", "cols": "Колонки", "filters": "Фильтры", "offset": "Смещение", "limit": "Лимит",
          "file": "Файл", "values": "Значения параметров", "scope": "Срез", "run": "Прогон", "seal": "Завершение приема"}

UUID = "неверный идентификатор (ожидается UUID, например 3f2b...); проверьте ссылку"
PYDANTIC = {
    "missing": "обязательное поле не заполнено",
    "uuid_parsing": UUID, "uuid_type": UUID,
    "string_too_short": "слишком короткое значение: не меньше {min_length} символов",
    "string_too_long": "слишком длинное значение: не больше {max_length} символов",
    "string_pattern_mismatch": "недопустимые символы: разрешены латинские буквы, цифры и . _ @ -",
    "too_short": "нужно не меньше {min_length} элементов",
    "too_long": "не больше {max_length} элементов",
    "greater_than_equal": "значение должно быть не меньше {ge}",
    "less_than_equal": "значение должно быть не больше {le}",
    "greater_than": "значение должно быть больше {gt}",
    "less_than": "значение должно быть меньше {lt}",
    "int_parsing": "нужно целое число", "int_type": "нужно целое число", "int_from_float": "нужно целое число",
    "float_parsing": "нужно число", "float_type": "нужно число",
    "bool_parsing": "нужно true или false", "bool_type": "нужно true или false",
    "string_type": "нужна строка", "list_type": "нужен список", "dict_type": "нужен объект {...}",
    "model_attributes_type": "нужен объект {...}",
    "json_invalid": "тело запроса не является корректным JSON",
}


def _field(loc):
    parts = [str(x) for x in loc if x not in ("body", "query", "path", "header", "cookie")]
    return ".".join(parts) or None


def _validation_message(err):
    """``«Логин»: слишком короткое значение: не меньше 2 символов`` for one pydantic error."""
    name = _field(err["loc"])
    label = FIELDS.get(str(err["loc"][-1]), name) if name else None
    text = PYDANTIC.get(err["type"], "неверное значение")
    try:
        text = text.format(**err.get("ctx") or {})
    except (KeyError, IndexError):
        pass
    return f"«{label}»: {text}" if label else text[:1].upper() + text[1:]


def body(status, detail, code=None, field=None, debug=None):
    return {"detail": detail, "code": code or STATUS.get(status, ("error",))[0], "field": field, "debug": debug}


async def on_http(request: Request, exc: HTTPException):
    if isinstance(exc, Problem):
        data = body(exc.status_code, exc.detail, exc.code, exc.field)
    else:
        code, text = STATUS.get(exc.status_code, ("error", "Ошибка запроса"))
        custom = isinstance(exc.detail, str) and re.search("[а-яА-Я]", exc.detail)
        data = body(exc.status_code, exc.detail if custom else text, code,
                    debug=None if custom else str(exc.detail))
    return JSONResponse(data, exc.status_code, headers=getattr(exc, "headers", None))


async def on_validation(request: Request, exc: RequestValidationError):
    errs = exc.errors()
    messages = [_validation_message(e) for e in errs]
    debug = "\n".join(f"{'.'.join(map(str, e['loc']))}: {e['type']}: {e['msg']}" for e in errs)
    data = body(422, "; ".join(dict.fromkeys(messages)), "validation_error", _field(errs[0]["loc"]) if errs else None,
                debug)
    data["errors"] = [{"field": _field(e["loc"]), "message": m} for e, m in zip(errs, messages)]
    return JSONResponse(data, 422)


async def on_unexpected(request: Request, exc: Exception):
    """500 that never leaks internals; the incident id links the user's report to the server log."""
    incident = uuid.uuid4().hex[:8]
    log.error("incident %s: %s %s", incident, request.method, request.url.path, exc_info=exc)
    if isinstance(exc, (psycopg.OperationalError, ConnectionError, TimeoutError)):
        return JSONResponse(body(503, "Сервис временно недоступен: нет связи с базой данных или очередью задач. "
                                      f"Повторите через минуту (код инцидента {incident}).", "unavailable"), 503)
    return JSONResponse(body(500, "Внутренняя ошибка сервера. Повторите действие; если ошибка повторяется, сообщите "
                                  f"администратору код инцидента {incident}.", "internal_error"), 500)


def install(app):
    app.add_exception_handler(HTTPException, on_http)
    app.add_exception_handler(RequestValidationError, on_validation)
    app.add_exception_handler(Exception, on_unexpected)


GLOSS = {"NameError": "неизвестное имя (переменная или функция не определена)", "KeyError": "нет такого ключа",
         "TypeError": "неверный тип значения", "ValueError": "недопустимое значение",
         "IndexError": "индекс за пределами списка", "AttributeError": "у объекта нет такого атрибута",
         "ZeroDivisionError": "деление на ноль", "SyntaxError": "синтаксическая ошибка",
         "IndentationError": "ошибка отступов", "TabError": "смешаны табуляции и пробелы в отступах",
         "ModuleNotFoundError": "модуль недоступен в песочнице", "ImportError": "модуль недоступен в песочнице",
         "PermissionError": "песочница запретила доступ (сеть и файлы вне витрины недоступны)",
         "RecursionError": "слишком глубокая рекурсия", "AssertionError": "не выполнилось условие assert",
         "UnicodeDecodeError": "не удалось декодировать текст", "OverflowError": "число слишком большое",
         "StopIteration": "итератор закончился", "NotImplementedError": "функция не реализована"}


WRAPPERS = {"SandboxError", "ProcedureError", "FormatError"}


def _line(raw):
    """Line of the user's code where the error happened: the last traceback frame outside the platform code."""
    frames = [(f, n) for f, n in re.findall(r'File "([^"]+)", line (\d+)', raw)
              if "site-packages" not in f and "/decision_diff/" not in f and not f.startswith("/usr/")]
    if frames:
        return frames[-1][1]
    m = re.search(r"\(\S+, line (\d+)\)|^[^\s:]+:(\d+): ", raw, re.MULTILINE)
    return m and (m.group(1) or m.group(2))


def explain(raw):
    """Russian headline of a stored technical error (``None`` stays ``None``); the raw text is the detail."""
    if not raw:
        return raw
    mem, wall = settings.sandbox_mem_mb, settings.sandbox_wall_s
    if m := re.search(r"timeout after (\d+) s", raw):
        return (f"Процедура выполнялась дольше {m.group(1)} с и была остановлена. Проверьте циклы (нет ли "
                "бесконечного) и объем обрабатываемых данных.")
    if re.search(r"MemoryError|Unable to allocate|Out of Memory|Cannot allocate memory|std::bad_alloc", raw):
        return (f"Процедуре не хватило памяти (лимит {mem} МБ). Обрабатывайте данные частями или сократите выборку: "
                "фильтры mart.rows(...), агрегаты вместо полных списков.")
    if m := re.search(r"sandbox died \(signal (\w+)\)", raw):
        why = {"SIGKILL": "обычно это нехватка памяти", "SIGXCPU": f"превышен лимит процессорного времени ({wall} с)",
               "SIGSEGV": "сбой внутри библиотеки"}.get(m.group(1), "процесс аварийно завершился")
        return f"Процесс процедуры был остановлен системой (сигнал {m.group(1)}): {why}."
    if "sandbox died" in raw:
        return "Процедура завершилась, не вернув результат. Повторите запуск; если повторится, упростите код."
    if m := re.search(r"result larger than (\d+) MB", raw):
        return (f"Результат процедуры больше {m.group(1)} МБ. Возвращайте меньше данных: агрегаты, первые строки, "
                "идентификаторы вместо полных записей.")
    if "not JSON-serializable" in raw:
        return ("Процедура вернула значение, которое нельзя передать в отчет: поддерживаются числа, строки, списки и "
                "словари из них.")
    if "Landlock or libseccomp" in raw:
        return "Песочница недоступна на этом сервере, пользовательский код не запускается. Обратитесь к администратору."
    if "multi-threaded" in raw:
        return "Внутренняя ошибка песочницы. Повторите запуск; если ошибка повторяется, сообщите администратору."
    if "delivery limit exceeded" in raw:
        return "Порцию не удалось обработать после нескольких попыток (сбой воркера). Запустите прогон заново."
    if "no chunk finished successfully" in raw:
        return "Ни одна порция не обработалась успешно, поэтому отчет не собран. Причины - в журнале порций."
    if m := re.search(r"(\d+) duplicate datamart keys", raw):
        return (f"В витрине {m.group(1)} повторяющихся ключей (APP_ID, PATH, ARRAY_KEY, SOURCE): уточните правила "
                "ключей массивов в процедуре нормализации.")
    if m := re.search(r"LLMError: endpoint answered HTTP (\d+)", raw):
        hint = " Проверьте ключ DD_LLM_API_KEY." if m.group(1) in ("401", "403") else ""
        return f"Языковая модель ответила ошибкой HTTP {m.group(1)}.{hint}"
    if "LLMError: endpoint unavailable" in raw:
        return "Языковая модель недоступна: сервер не отвечает. Проверьте DD_LLM_BASE_URL и что сервер модели запущен."
    if "cut at the output token limit" in raw:
        return ("Ответ языковой модели обрезан лимитом токенов на ответ: модель не успела дописать вывод. Попробуйте "
                "модель с большим лимитом или без рассуждений.")
    if "LLMError" in raw:
        return "Языковая модель ответила не по формату, мнение не получено. Повторите запрос или смените модель."
    if "evaluate must return" in raw or "sandbox returned an invalid result" in raw:
        return ("Процедура «что если» вернула результат не по контракту: нужен массив с одним целым индексом исхода "
                "на каждую заявку.")
    if "does not fit" in raw and "tokens" in raw:
        return "Сводка прогона не помещается в контекст языковой модели. Нужна модель с большим контекстом."
    head = raw.strip().splitlines()[0]
    ours = re.sub(r"^(?:(?:ingest|finalize): )?(?:SandboxError: )?(?:(?:ProcedureError|FormatError|ValueError|"
                  r"FileNotFoundError): )?", "", head)
    if re.search("[а-яА-Я]", ours):
        return ours[:1].upper() + ours[1:]
    kinds = [k for k in re.findall(r"\b([A-Z]\w*(?:Error|Exception|Interrupt)|StopIteration)\b", head)
             if k not in WRAPPERS]
    if kinds:
        line = _line(raw)
        where = f" в строке {line}" if line else ""
        return (f"Ошибка в коде процедуры{where}: {kinds[-1]} - {GLOSS.get(kinds[-1], 'исключение Python')}. "
                "Исправьте код; текст ошибки и трассировка - в подробностях.")
    return "Операция завершилась с ошибкой. Технические подробности ниже."


def humanize(row, key="error"):
    """``row`` with the Russian headline in ``key`` and the raw text in ``key + "_debug"``."""
    if not isinstance(row, dict) or not row.get(key):
        return row
    return {**row, key: explain(row[key]), f"{key}_debug": row[key]}
