"""Example parser: text files of ``key=value`` lines -> one nested document per file.

A dotted key builds nesting (``client.address.city=Omsk`` -> ``{"client": {"address": {"city": "Omsk"}}}``),
lines starting with the comment mark and empty lines are skipped, values stay strings unless ``numbers`` is on.
The file name without extension is the application id, so BEFORE and AFTER files pair by name as usual.
Select it in "Разбор формата" of a new run; without a parser the built-in auto-detection is used.
"""

PARAMS = {"separator": "=", "comment": "#", "encoding": "utf-8", "numbers": True}


def _value(text, numbers):
    if numbers:
        for cast in (int, float):
            try:
                return cast(text)
            except ValueError:
                pass
    return text


def parse(name, data, params):
    """Yield ``(app_id, document)`` for one input file; ``data`` is the raw bytes of the file."""
    p = {**PARAMS, **(params or {})}
    doc = {}
    for no, line in enumerate(data.decode(p["encoding"]).splitlines(), 1):
        line = line.strip()
        if not line or line.startswith(p["comment"]):
            continue
        key, sep, value = line.partition(p["separator"])
        if not sep or not key.strip():
            raise ValueError(f"строка {no}: ожидается ключ{p['separator']}значение")
        *parents, leaf = key.strip().split(".")
        node = doc
        for part in parents:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise TypeError(f"строка {no}: {part!r} уже задан значением, а не разделом")
        node[leaf] = _value(value.strip(), p["numbers"])
    yield name.rsplit("/", 1)[-1].rsplit(".", 1)[0], doc


PROCEDURE = {
    "id": "kv_lines",
    "label": "Разбор строк ключ=значение (пример)",
    "description": "Читает текстовые файлы из строк ключ=значение и собирает из них документ: точка в ключе задает вложенность.",
    "kind": "parser",
    "params": PARAMS,
    "entrypoint": parse,
}
