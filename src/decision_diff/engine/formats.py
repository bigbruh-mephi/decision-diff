"""Format adapters: bytes of one input file -> documents (plain dict/list/scalar trees) keyed by application id.

Every format lands in the same tree model, so flatten and compare are format-agnostic. Detection uses content
(magic bytes, first character) first and the file extension only as a tiebreak.
"""

import csv
import gzip
import io
import json
import sys

import orjson

GZIP, ZSTD, ZIP, PARQUET, ARROW = b"\x1f\x8b", b"\x28\xb5\x2f\xfd", b"PK\x03\x04", b"PAR1", b"ARROW1"
PICKLE_EXT = (".pkl", ".pickle")
LOSSY = {"parquet", "arrow", "csv", "tsv"}


class FormatError(ValueError):
    """Input that cannot be parsed; the message is shown to the user in the stage journal."""


def detect(name, data):
    """Format name of ``data`` (``json``, ``ndjson``, ``xml``, ``yaml``, ``csv``, ``tsv``, ``msgpack``, ``parquet``,
    ``arrow``, ``gzip``, ``zstd``, ``zip``)."""
    low = name.lower()
    if low.endswith(PICKLE_EXT) or (data[:1] == b"\x80" and data[1:2] in (b"\x02", b"\x03", b"\x04", b"\x05")
                                    and not low.endswith((".msgpack", ".mpk"))):
        raise FormatError(f"{name}: pickle is not accepted (it executes code when loaded); export to Parquet or CSV")
    for magic, fmt in ((GZIP, "gzip"), (ZSTD, "zstd"), (ZIP, "zip"), (PARQUET, "parquet"), (ARROW, "arrow")):
        if data.startswith(magic):
            return fmt
    for ext, fmt in ((".msgpack", "msgpack"), (".mpk", "msgpack"), (".yaml", "yaml"), (".yml", "yaml"),
                     (".csv", "csv"), (".tsv", "tsv"), (".ndjson", "ndjson"), (".jsonl", "ndjson")):
        if low.endswith(ext):
            return fmt
    head = data.lstrip(b"\xef\xbb\xbf \t\r\n")[:1]
    if head == b"<":
        return "xml"
    if head in (b"{", b"["):
        first, _, rest = data.strip().partition(b"\n")
        return "ndjson" if rest.strip()[:1] == b"{" and first.rstrip().endswith(b"}") and _is_json(first) else "json"
    return "yaml"


def _is_json(line):
    try:
        orjson.loads(line)
        return True
    except orjson.JSONDecodeError:
        return False


def load_json(data):
    """Parse JSON with orjson (natural nesting limit 1024); integers beyond 64 bits are re-parsed exactly."""
    try:
        return orjson.loads(data)
    except orjson.JSONDecodeError as e:
        if "Integer exceeds 64-bit range" in str(e):
            return json.loads(data)
        if "recursion" in str(e).lower() or "depth" in str(e).lower():
            raise FormatError(f"nesting deeper than 1024 levels is not supported: {e}") from e
        raise FormatError(f"invalid JSON: {e}") from e


def _xml_tree(data, array_tags):
    """XML -> tree with lxml's natural depth limit (``huge_tree``); entities and DTDs stay disabled. The conversion is
    iterative, so it never imposes a lower depth limit than the parser."""
    from lxml import etree

    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True, load_dtd=False)
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError as e:
        if "depth" in str(e).lower():
            raise FormatError(f"XML nesting is deeper than the parser limit (libxml2): {e}") from e
        raise FormatError(f"invalid XML: {e}") from e

    def attrs(el):
        return {"@" + k: v for k, v in el.attrib.items()}

    stack, nodes = [(root, iter(root))], [attrs(root)]
    while True:
        el, children = stack[-1]
        c = next((c for c in children if isinstance(c.tag, str)), None)
        if c is not None:
            stack.append((c, iter(c)))
            nodes.append(attrs(c))
            continue
        stack.pop()
        node, text = nodes.pop(), (el.text or "").strip()
        if node and text:
            node["#text"] = text
        v = node or text
        if not stack:
            return {etree.QName(el).localname: v}
        parent, tag = nodes[-1], etree.QName(el).localname
        if tag in parent:
            prev = parent[tag]
            parent[tag] = prev + [v] if isinstance(prev, list) else [prev, v]
        else:
            parent[tag] = [v] if tag in array_tags else v


def _rows(rows, id_field, name):
    for i, r in enumerate(rows):
        app = r.get(id_field) if id_field else None
        yield (str(app) if app is not None else f"{name}#{i}"), r


def parse(name, data, id_field=None, xml_array_tags=()):
    """Yield ``(app_id, tree)`` from one input file. Single-document formats use the file stem as the id."""
    fmt = detect(name, data)
    stem = name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    if fmt == "gzip":
        yield from parse(name.removesuffix(".gz"), gzip.decompress(data), id_field, xml_array_tags)
    elif fmt == "zstd":
        import zstandard

        yield from parse(name.removesuffix(".zst"), zstandard.ZstdDecompressor().decompress(
            data, max_output_size=1 << 31), id_field, xml_array_tags)
    elif fmt == "zip":
        raise FormatError(f"{name}: nested zip archives are not supported")
    elif fmt == "json":
        yield stem, load_json(data)
    elif fmt == "ndjson":
        yield from _rows((load_json(x) for x in data.splitlines() if x.strip()), id_field, stem)
    elif fmt == "xml":
        yield stem, _xml_tree(data, set(xml_array_tags))
    elif fmt == "yaml":
        import yaml

        try:
            doc = yaml.safe_load(data)
        except yaml.YAMLError as e:
            raise FormatError(f"invalid YAML: {e}") from e
        except RecursionError as e:
            raise FormatError(f"YAML nesting is deeper than the parser limit (PyYAML is recursive: about "
                              f"{sys.getrecursionlimit() // 2} levels)") from e
        yield stem, doc
    elif fmt in ("csv", "tsv"):
        text = data.decode("utf-8-sig")
        yield from _rows(csv.DictReader(io.StringIO(text), delimiter="\t" if fmt == "tsv" else
                                        csv.Sniffer().sniff(text[:4096], ",;").delimiter), id_field, stem)
    elif fmt == "msgpack":
        import msgpack

        try:
            doc = msgpack.unpackb(data, raw=False, strict_map_key=False)
        except msgpack.StackError as e:
            raise FormatError("MessagePack nesting is deeper than the parser limit (msgpack, 1024 levels)") from e
        except ValueError as e:
            raise FormatError(f"invalid MessagePack: {e}") from e
        yield stem, doc
    else:
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pq.read_table(io.BytesIO(data)) if fmt == "parquet" else pa.ipc.open_file(io.BytesIO(data)).read_all()
        yield from _rows(table.to_pylist(), id_field, stem)
