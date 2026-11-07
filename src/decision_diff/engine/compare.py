"""Comparison of two flattened slices and bucketing of values into ADJUSTED_VALUE."""

import bisect

SAME, CHANGED, NEW, LOST = "SAME", "VALUE_CHANGED", "ATTRIBUTE_NEW", "ATTRIBUTE_LOST"


def compare(before, after, equivalent=None):
    """``{key: (type, value)}`` x2 -> ``{key: (CHANGE_TYPE, type_changed)}``.

    DIFF_FLAG is ``CHANGE_TYPE != SAME`` and is decided by VALUE only; a type change with equal text (``1`` vs ``"1"``)
    stays SAME and is only marked. ``equivalent(key, value_before, value_after) -> bool`` lets the compare procedure
    declare different values equal (e.g. ``NULL`` == ``false`` for a rule hit).
    """
    res = {}
    for k in before.keys() | after.keys():
        b, a = before.get(k), after.get(k)
        if b is None:
            res[k] = (NEW, 0)
        elif a is None:
            res[k] = (LOST, 0)
        elif b[1] == a[1] or (equivalent and equivalent(k, b[1], a[1])):
            res[k] = (SAME, int(b[0] != a[0]))
        else:
            res[k] = (CHANGED, int(b[0] != a[0]))
    return res


def _num(x):
    a = abs(x)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            v = x / div
            return (f"{v:.0f}" if v == int(v) else f"{v:g}") + suf
    return f"{x:g}"


def bucket_label(breaks, text):
    """Interval label ``(lo – hi]`` of a numeric value over sorted ``breaks``; non-numbers pass through."""
    try:
        x = float(text)
    except ValueError:
        return text
    i = bisect.bisect_left(breaks, x)
    if i == 0:
        return f"(-inf – {_num(breaks[0])}]"
    if i == len(breaks):
        return f"({_num(breaks[-1])} – +inf)"
    return f"({_num(breaks[i - 1])} – {_num(breaks[i])}]"
