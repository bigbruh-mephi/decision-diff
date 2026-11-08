const rnd = () => Math.random().toString(36).slice(2, 7);

const check = (id: string) => `# %% Новая автопроверка: контракт CHECK = {id, label, params, entrypoint}
# entrypoint(mart, params) -> список строк с полями LABEL, SOURCE, ARGUMENT, CNT, TOTAL, METRIC, APP_IDS, RESULT, CONFIDENCE.
# mart: rows(**filters), count(**filters), app_ids(**filters), segments(), total_apps().
DEFAULTS = {"min_share": 0.05, "low_confidence_cnt": 5, "app_ids_limit": 20}


def run(mart, params=None):
    """Доля заявок, у которых различия есть, но решение не изменилось."""
    p = {**DEFAULTS, **(params or {})}
    total = mart.total_apps()
    changed = set(mart.app_ids(diff_flag=1))
    flipped = {s["APP_ID"] for s in mart.segments() if s.get("DECISION_CHANGED")}
    passed_by = sorted(changed - flipped)
    share = len(passed_by) / total if total else 0.0
    return [{
        "LABEL": CHECK["label"], "SOURCE": "AFTER", "ARGUMENT": "",
        "CNT": len(passed_by), "TOTAL": total, "METRIC": round(share, 4),
        "APP_IDS": passed_by[:p["app_ids_limit"]],
        "RESULT": "FAIL" if share > p["min_share"] else "PASS",
        "CONFIDENCE": "LOW" if len(passed_by) < p["low_confidence_cnt"] else "HIGH",
    }]


CHECK = {"id": "${id}", "label": "Различия есть, решение то же", "params": DEFAULTS, "entrypoint": run}

# %% Попробовать на прогоне: последнее выражение ячейки показывается таблицей
run(mart)
`;

const parser = (id: string) => `# %% Разбор своего формата: kind "parser"
# entrypoint(name, data, params) получает имя и байты одного входного файла и возвращает пары (app_id, документ).
# Документ - обычное дерево dict/list/значений: дальше работают приведение и сравнение как для JSON.
# Срезы BEFORE и AFTER сопоставляются по app_id. Исполняется только в песочнице воркера.
PARAMS = {"separator": ";", "encoding": "utf-8"}


def parse(name, data, params):
    """Пример: CSV без заголовка "app_id;раздел.поле;значение", несколько заявок в одном файле."""
    p = {**PARAMS, **(params or {})}
    docs = {}
    for line in data.decode(p["encoding"]).splitlines():
        if not line.strip():
            continue
        app_id, path, value = line.split(p["separator"], 2)
        node = docs.setdefault(app_id, {})
        *parents, leaf = path.split(".")
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = value
    yield from docs.items()


PROCEDURE = {"id": "${id}", "label": "Мой формат: строки app_id;путь;значение", "kind": "parser",
             "params": PARAMS, "entrypoint": parse}

# %% Проба на образце файла (без прогона)
list(parse("sample.txt", """A1;order.status;new
A1;order.amount;150
A2;order.status;paid
""".encode(), {}))
`;

const step = (id: string) => `# %% Шаг подготовки документа: kind "step" (этап до приведения)
# entrypoint(doc, params) -> doc получает документ одной заявки (BEFORE и AFTER по отдельности) и возвращает новый.
# Шаги выполняются по порядку, заданному в конфигурации, в песочнице воркера, до приведения и сравнения.
PARAMS = {"drop": ["meta.trace"], "lower": ["client.email"]}


def run(doc, params):
    """Удаляет служебные поля и приводит указанные значения к нижнему регистру."""
    p = {**PARAMS, **(params or {})}
    for path in p["drop"]:
        *parents, leaf = path.split(".")
        node = doc
        for part in parents:
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, dict):
            node.pop(leaf, None)
    for path in p["lower"]:
        *parents, leaf = path.split(".")
        node = doc
        for part in parents:
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, dict) and isinstance(node.get(leaf), str):
            node[leaf] = node[leaf].lower()
    return doc


PROCEDURE = {"id": "${id}", "label": "Убрать служебные поля", "kind": "step",
             "description": "Удаляет служебные поля и выравнивает регистр перед сравнением.",
             "params": PARAMS, "entrypoint": run}

# %% Проба на документе
run({"meta": {"trace": "x"}, "client": {"email": "A@B.RU"}}, {})
`;

const normalize = (id: string) => `# %% Приведение (нормализация): kind "normalize"
# params - профиль разворота документа в строки витрины. Пути пишутся через точку без индексов,
# "*" - один сегмент, "**" - любое число сегментов.
PROCEDURE = {
    "id": "${id}",
    "label": "Мое приведение",
    "kind": "normalize",
    "params": {
        # массив -> ключ элемента: ["поле", ...] (составной ключ), "@index" (по позиции), "@value" (массив значений)
        "arrays": {"items": ["sku"], "**.tags": "@value"},
        # технические поля, которые всегда различаются
        "exclude": ["meta.request_id", "meta.created_at"],
        # бакеты числовых значений для сводной (ADJUSTED_VALUE)
        "buckets": [{"pattern": "**.score", "breaks": [0.2, 0.4, 0.6, 0.8]}],
        # переименования между версиями контракта: старый путь -> новый
        "rename": {},
        # "snake" - привести camelCase к snake_case во всем срезе, None - как есть
        "key_style": None,
        # поле с номером заявки для форматов "много заявок в файле" (NDJSON, CSV)
        "id_field": None,
    },
    # необязательно: entrypoint(doc, params) -> doc меняет документ до разворота
}
`;

const compare = (id: string) => `# %% Сравнение: kind "compare"
# equivalent - значения, которые считаются равными для путей по шаблону (например null и false).
PROCEDURE = {
    "id": "${id}",
    "label": "Мои правила сравнения",
    "kind": "compare",
    "params": {"equivalent": [{"pattern": "**.flag", "values": [["NULL", "false"]]}]},
}
`;

const segments = (id: string) => `# %% Разрезы отчета: kind "segments"
# entrypoint(before, after, params) -> одна строка слоя разрезов на заявку (словарь "колонка": значение).
# Колонки становятся фильтрами и измерениями сводной. decision_field - имя разреза с решением:
# из него строятся колонки <поле>_BEFORE, <поле>_AFTER, <поле>_CHANGED.


def run(before, after, params):
    """Решение и категория заявки в обоих срезах."""
    status = lambda d: (d.get("order") or {}).get("status")
    return {
        "DECISION_BEFORE": status(before), "DECISION_AFTER": status(after),
        "DECISION_CHANGED": int(status(before) != status(after)),
        "CATEGORY": ";".join(sorted({i.get("category", "") for i in after.get("items") or []})),
    }


PROCEDURE = {"id": "${id}", "label": "Мои разрезы", "kind": "segments", "entrypoint": run,
             "params": {"decision_field": "DECISION", "positive_decision": "paid",
                        "multi_valued": {"CATEGORY": ";"}, "pii_paths": ["client.email"]}}

# %% Проба на двух документах
run({"order": {"status": "new"}}, {"order": {"status": "paid"}, "items": [{"category": "books"}]}, {})
`;

const lineage = (id: string) => `# %% Этапы цепочки решения: kind "lineage" (только параметры)
# Этапы по порядку от входа к результату; путь относится к первому этапу, чей шаблон подошел, остальные - "other".
# role: input - данные извне, reference - справочники, logic - то, что вычисляет стратегия, outcome - результат.
# Используется для атрибуции "логика или входные данные" и для проверок с параметром "@lineage".
PROCEDURE = {
    "id": "${id}",
    "label": "Мои этапы цепочки решения",
    "kind": "lineage",
    "params": {
        "stages": [
            {"name": "input", "label": "входные данные", "role": "input", "paths": ["client.**", "request.**"]},
            {"name": "reference", "label": "справочники", "role": "reference", "paths": ["catalog.**"]},
            {"name": "scoring", "label": "оценка", "role": "logic", "paths": ["result.scores.**"]},
            {"name": "rules", "label": "правила", "role": "logic", "paths": ["result.rules.**"]},
            {"name": "decision", "label": "решение", "role": "outcome", "paths": ["result.decision"]},
        ],
        "decision_path": "result.decision",
    },
}
`;

const whatif = (id: string) => `# %% Модель решения (по ней система находит причину каждой смены решения): kind "whatif"
# Все, кроме entrypoint, - литералы: система читает объявление без исполнения кода.
# inputs: имя -> путь витрины и тип (float | str | bool); unit_depth - сколько уровней массива образуют единицу расчета.
# entrypoint(data, params) -> индекс исхода (outcome.values) для каждой заявки. Полный пример - файл spr_whatif.py.
import numpy as np


def evaluate(data, params):
    """Одобрение, если оценка не ниже порога."""
    return np.where(data["score"] >= params["threshold"], 0, 1)


PROCEDURE = {
    "id": "${id}",
    "label": "Модель решения по порогу",
    "kind": "whatif",
    "unit_depth": 0,
    "inputs": {"score": {"path": "result.score", "type": "float"}},
    "outcome": {"field": "DECISION", "values": ["APPROVE", "REJECT"]},
    "params": {"threshold": {"type": "float", "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.5,
                             "label": "Порог"}},
    "entrypoint": evaluate,
}
`;

const aiPrompt = (id: string) => `# %% Запрос к ИИ-рецензенту: kind "ai_prompt" (только текст, код не исполняется)
# Тексты, которые не заданы здесь, берутся из procedures/ai_prompt.py. Подстановки системы:
# system: {lang}; call1: {schema} и {pack} обязательны, {max_h}, {n_apps}; call2: {schema} и {derived} обязательны, {max_f}.
# Фигурная скобка как текст пишется двойной: {{ и }}.
PROCEDURE = {
    "id": "${id}",
    "label": "Мой запрос к ИИ",
    "kind": "ai_prompt",
    "params": {
        "system": """You are an independent senior reviewer of a change of an automated decision process.
Two versions (BEFORE = production, AFTER = candidate) ran on the same cases; the system computed every count.
Find what the report and the checks do not say. Use only the provided items, cite item ids,
write user-facing text in {lang}. Answer with one JSON object only.""",
    },
}
`;

export const TEMPLATES: { kind: string; title: string; make: (id: string) => string }[] = [
  { kind: "check", title: "Автопроверка", make: check },
  { kind: "parser", title: "Разбор формата", make: parser },
  { kind: "step", title: "Шаг подготовки", make: step },
  { kind: "normalize", title: "Приведение", make: normalize },
  { kind: "compare", title: "Сравнение", make: compare },
  { kind: "segments", title: "Разрезы отчета", make: segments },
  { kind: "lineage", title: "Этапы цепочки решения", make: lineage },
  { kind: "whatif", title: "Модель решения", make: whatif },
  { kind: "ai_prompt", title: "Запрос к ИИ", make: aiPrompt },
];

export const template = (kind: string) => (TEMPLATES.find((t) => t.kind === kind) ?? TEMPLATES[0]).make(`my_${kind}_${rnd()}`);
