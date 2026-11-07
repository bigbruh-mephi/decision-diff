"""Explicit-stack flattener of a document tree into datamart leaves.

A run-wide :class:`Paths` trie interns object paths (array levels are segment ``None``) and evaluates the profile rules
(array keys, exclusions, buckets, renames, key style) once per distinct node, so the per-leaf cost does not grow with
depth. Element keys of arrays are interned in a per-application :class:`Trie` shared by the BEFORE and AFTER documents,
so comparison works on integer keys and ARRAY_KEY strings are built once per distinct key.
"""

import re

MISSING = object()
SCALARS = (str, int, float, bool, type(None))
INT64 = (-9.3e18, 1.9e19)


def esc(s, chars):
    """Backslash-escape ``chars`` (and the backslash itself) in ``s``."""
    s = s.replace("\\", "\\\\")
    for c in chars:
        s = s.replace(c, "\\" + c)
    return s


def split_esc(s, sep):
    """Split ``s`` on unescaped ``sep``; inverse of :func:`esc`."""
    out, cur, it = [], [], iter(s)
    for c in it:
        if c == "\\":
            cur.append(next(it, ""))
        elif c == sep:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(c)
    out.append("".join(cur))
    return out


def scalar(v):
    """JSON-style text of a scalar: ``true``/``false``/``NULL``, numbers via ``str``."""
    return "NULL" if v is None else "true" if v is True else "false" if v is False else str(v)


def vtype(v):
    """One-letter JSON type tag: z null, b bool, s string, n number."""
    return "z" if v is None else "b" if type(v) is bool else "s" if type(v) is str else "n"


_CAMEL = re.compile(r"(?<=[a-z0-9])([A-Z])|(?<=[A-Z])([A-Z])(?=[a-z])")
KEY_STYLES = {
    "snake": lambda k: _CAMEL.sub(lambda m: "_" + (m.group(1) or m.group(2)), k).lower(),
    "camel": lambda k: re.sub(r"_+([a-zA-Z0-9])", lambda m: m.group(1).upper(), k[:1].lower() + k[1:]),
    "lower": str.lower,
}


class Trie:
    """Interned ``(parent, segment) -> id`` nodes; node 0 is the root."""

    def __init__(self):
        self.ids, self.parent, self.seg, self.memo = {}, [-1], [None], {0: ""}

    def child(self, p, s):
        i = self.ids.get((p, s))
        if i is None:
            i = self.ids[p, s] = len(self.seg)
            self.parent.append(p)
            self.seg.append(s)
        return i

    def segs(self, i):
        out = []
        while i:
            out.append(self.seg[i])
            i = self.parent[i]
        return out[::-1]

    def akey(self, i):
        """Contract ARRAY_KEY of node ``i``: per-array components joined by ``|``."""
        s = self.memo.get(i)
        if s is None:
            s = self.memo[i] = "|".join(self.segs(i))
        return s


def _field_spec(spec):
    if isinstance(spec, str):
        return spec
    return tuple(tuple(split_esc(f, ".")) for f in spec)


class Paths(Trie):
    """Path trie with incremental glob matching of profile rules.

    Patterns are dotted object paths (array levels are transparent); ``*`` matches one segment, ``**`` any number.
    Rules: ``arrays`` pattern -> ``"@index" | "@value" | [fields] | {"key": ..., "element_name": ...}``,
    ``exclude`` patterns, ``buckets`` list of ``{"pattern", "breaks"}``, ``equivalent`` list of
    ``{"pattern", "values": [[a, b], ...]}``. First matching rule of a kind wins; unconfigured arrays are by index.
    """

    def __init__(self, arrays=None, exclude=(), buckets=(), rename=None, key_style=None, equivalent=()):
        super().__init__()
        rules = []
        for pat, spec in (arrays or {}).items():
            elem = ""
            if isinstance(spec, dict):
                elem, spec = spec.get("element_name", ""), spec["key"]
            rules.append((pat, "array", (_field_spec(spec), elem)))
        rules += [(p, "exclude", True) for p in exclude]
        rules += [(b["pattern"], "bucket", tuple(sorted(float(x) for x in b["breaks"]))) for b in buckets]
        rules += [(e["pattern"], "equiv", frozenset(frozenset(map(str, pair)) for pair in e["values"]))
                  for e in equivalent]
        self.rules = [(split_esc(p, "."), kind, payload) for p, kind, payload in rules]
        self.steps = {}
        self.state = [self._closure((i, 0) for i in range(len(self.rules)))]
        self.rule = [{}]
        self.rename = rename or {}
        self.style = KEY_STYLES[key_style] if key_style else None
        self.keymap = {}
        self.transforms = bool(self.rename or self.style)

    def _closure(self, st):
        st = set(st)
        todo = list(st)
        while todo:
            i, pos = todo.pop()
            pat = self.rules[i][0]
            if pos < len(pat) and pat[pos] == "**" and (i, pos + 1) not in st:
                st.add((i, pos + 1))
                todo.append((i, pos + 1))
        return frozenset(st)

    def child(self, p, s):
        n = len(self.seg)
        i = super().child(p, s)
        if i == n:
            st = self.state[p]
            if s is not None:
                key = (st, s)
                nxt = self.steps.get(key)
                if nxt is None:
                    nxt = self.steps[key] = self._closure(
                        (j, pos + (self.rules[j][0][pos] != "**")) for j, pos in st
                        if pos < len(self.rules[j][0]) and self.rules[j][0][pos] in ("**", "*", s))
                st = nxt
            done = {}
            for j in sorted(j for j, pos in st if pos == len(self.rules[j][0])):
                done.setdefault(self.rules[j][1], self.rules[j][2])
            self.state.append(st)
            self.rule.append(done)
        return i

    def key(self, p, k):
        """Child node for object key ``k`` under ``p`` after renames and key style."""
        r = self.keymap.get((p, k))
        if r is None:
            name = None
            if self.rename:
                name = self.rename.get(self.info(p)[1] + "." + esc(k, ".") if p else esc(k, "."))
            if name is None:
                name = self.style(k) if self.style else k
            r = self.keymap[p, k] = self.child(p, name)
        return r

    def array_spec(self, arr):
        return self.rule[arr].get("array", ("@index", ""))

    def excluded(self, node):
        return "exclude" in self.rule[node]

    def breaks(self, node):
        return self.rule[node].get("bucket")

    def equivalent(self, key, b, a):
        """True if the compare rules declare values ``b`` and ``a`` of leaf ``key`` equal."""
        pairs = self.rule[key[0]].get("equiv")
        return bool(pairs) and frozenset((b, a)) in pairs

    def info(self, i):
        """``(jsonpath, PATH, PREFIX, CLASS, SUFFIX, KEY)`` of node ``i``, memoized.

        A leaf that is an array element has an empty KEY; the array rule's ``element_name`` (if any) becomes the last
        path segment, as in the task example ``company.rejectReasons`` -> SUFFIX ``reason``, KEY ``''``.
        """
        r = self.memo.get(i)
        if not r:
            segs = self.segs(i)
            jp = "$" + "".join("[*]" if s is None else "['" + re.sub(
                r"[\\'\x00-\x1f]", lambda m: "\\" + m.group() if m.group() in "\\'" else f"\\u{ord(m.group()):04x}",
                s) + "']" for s in segs)
            n = [esc(s, ".") for s in segs if s is not None] or [""]
            if segs and segs[-1] is None:
                elem = self.array_spec(i)[1]
                n = n + [esc(elem, ".")] if elem else n
                key, mid = "", n[2:]
            else:
                key, mid = n[-1], n[2:-1]
            r = self.memo[i] = (jp, ".".join(n), n[0], n[1] if len(n) > 1 else "", ".".join(mid), key)
        return r


def element_key(spec, i, x, issues, where):
    """Key component of array element ``x`` at index ``i`` under ``spec``; problems go to ``issues``."""
    if spec == "@index":
        return str(i)
    if spec == "@value":
        if type(x) in SCALARS:
            return esc(scalar(x), "|+")
        issues.append(("container_in_value_array", where, i))
        return "\\@" + str(i)
    if type(x) is not dict:
        issues.append(("non_object_in_keyed_array", where, i))
        return "\\@" + str(i)
    parts = []
    for field in spec:
        o = x
        for s in field:
            o = o.get(s, MISSING) if type(o) is dict else MISSING
        if o is MISSING or type(o) not in SCALARS:
            issues.append(("missing_key_field", where, i, ".".join(field)))
            parts.append("\\?")
        else:
            parts.append(esc(scalar(o), "|+"))
    return "+".join(parts)


def flatten(doc, P, A):
    """Flatten one document into ``{(path_node, akey_node): (type, value)}`` plus a list of issues.

    ``P`` is the run-wide :class:`Paths`, ``A`` the per-application :class:`Trie` shared by both slices.
    Duplicate element keys inside one array are suffixed ``\\#n`` and reported, never merged.
    """
    out, issues = {}, []
    stack = [(0, 0, doc)]
    pop, push, ids, key, achild = stack.pop, stack.append, P.keymap.get, P.key, A.child
    transforms = P.transforms
    while stack:
        node, ak, v = pop()
        t = type(v)
        if t is dict:
            seen = set() if transforms else None
            for k, x in v.items():
                c = ids((node, k)) or key(node, k)
                if seen is not None:
                    if c in seen:
                        issues.append(("key_collision", node, k))
                        continue
                    seen.add(c)
                push((c, ak, x))
        elif t is list:
            arr = P.child(node, None)
            spec = P.array_spec(arr)[0]
            dup = {}
            for i, x in enumerate(v):
                comp = element_key(spec, i, x, issues, arr)
                n = dup[comp] = dup.get(comp, 0) + 1
                if n > 1:
                    issues.append(("duplicate_key", arr, comp, n))
                    comp += "\\#" + str(n)
                push((arr, achild(ak, comp), x))
        elif not P.excluded(node):
            if t is float and not INT64[0] < v < INT64[1]:
                issues.append(("float_beyond_int64", node))
            out[node, ak] = (vtype(v), scalar(v))
    return out, issues
