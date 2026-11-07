"""Format adapters: bytes of one input file -> documents (plain dict/list/scalar trees) keyed by application id.

Every format lands in the same tree model, so flatten and compare are format-agnostic. Detection uses content
(magic bytes, first character) first and the file extension only as a tiebreak.
"""

import csv
import gzip
import io
import json

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
    from lxml import etree

    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, load_dtd=False)
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError as e:
        raise FormatError(f"invalid XML: {e}") from e

    def conv(el):
        node = {"@" + k: v for k, v in el.attrib.items()}
        for c in el:
            if not isinstance(c.tag, str):
                continue
            tag = etree.QName(c).localname
            v = conv(c)
            if tag in node:
                prev = node[tag]
                node[tag] = prev + [v] if isinstance(prev, list) else [prev, v]
            else:
                node[tag] = [v] if tag in array_tags else v
        text = (el.text or "").strip()
        if not node:
            return text
        if text:
            node["#text"] = text
        return node

    return {etree.QName(root).localname: conv(root)}


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
            yield stem, yaml.safe_load(data)
        except yaml.YAMLError as e:
            raise FormatError(f"invalid YAML: {e}") from e
    elif fmt in ("csv", "tsv"):
        text = data.decode("utf-8-sig")
        yield from _rows(csv.DictReader(io.StringIO(text), delimiter="\t" if fmt == "tsv" else
                                        csv.Sniffer().sniff(text[:4096], ",;").delimiter), id_field, stem)
    elif fmt == "msgpack":
        import msgpack

        yield stem, msgpack.unpackb(data, raw=False, strict_map_key=False)
    else:
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pq.read_table(io.BytesIO(data)) if fmt == "parquet" else pa.ipc.open_file(io.BytesIO(data)).read_all()
        yield from _rows(table.to_pylist(), id_field, stem)
