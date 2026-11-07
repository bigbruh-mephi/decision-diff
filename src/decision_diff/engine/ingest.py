"""Run folder ingest: pair BEFORE/AFTER files by application id and cut them into chunks of raw bytes.

A run folder holds ``manifest.json`` (optional) with ``srez.{BEFORE,AFTER}.dir`` and
``technical_fields_always_differ``; without a manifest the slices are ``RESULTS/BEFORE*`` and ``RESULTS/AFTER*``.
"""

import io
import zipfile
from pathlib import Path

import orjson

from .formats import parse


def read_manifest(run_dir):
    p = Path(run_dir) / "manifest.json"
    return orjson.loads(p.read_bytes()) if p.exists() else {}


def slice_dirs(run_dir):
    """``(before_dir, after_dir)`` of a run folder."""
    root = Path(run_dir)
    srez = read_manifest(root).get("srez") or {}
    found = {}
    for side in ("BEFORE", "AFTER"):
        d = (srez.get(side) or {}).get("dir")
        if d:
            found[side] = root / d
        else:
            hits = sorted((root / "RESULTS").glob(f"{side}*")) or sorted(root.glob(f"{side}*"))
            if not hits:
                raise FileNotFoundError(f"{root}: no {side} slice directory")
            found[side] = hits[0]
    return found["BEFORE"], found["AFTER"]


def pairs(run_dir):
    """Sorted ``(app_id, before_path | None, after_path | None)`` of a run folder, paired by file stem."""
    b, a = slice_dirs(run_dir)
    fb = {p.stem: p for p in b.iterdir() if p.is_file()}
    fa = {p.stem: p for p in a.iterdir() if p.is_file()}
    return [(k, fb.get(k), fa.get(k)) for k in sorted(fb.keys() | fa.keys())]


def write_chunk_zip(path, items):
    """Store ``items`` = ``[(app_id, before_path, after_path)]`` as one zip of raw files (``BEFORE/x``, ``AFTER/x``)."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        for _, b, a in items:
            for side, p in (("BEFORE", b), ("AFTER", a)):
                if p is not None:
                    z.write(p, f"{side}/{p.name}")


def read_chunk_zip(data, id_field=None, xml_array_tags=()):
    """Yield ``(app_id, before_doc, after_doc)`` from chunk zip bytes; every member is parsed by its format."""
    docs = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            side, _, name = info.filename.partition("/")
            if info.is_dir() or side not in ("BEFORE", "AFTER"):
                continue
            for app_id, tree in parse(name, z.read(info), id_field, xml_array_tags):
                docs.setdefault(app_id, {})[side] = tree
    for app_id in sorted(docs):
        d = docs[app_id]
        yield app_id, d.get("BEFORE"), d.get("AFTER")
