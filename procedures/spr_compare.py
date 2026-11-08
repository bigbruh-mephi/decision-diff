"""Comparison rules for SPR responses (editable default).

`equivalent` declares different values equal for matching paths. Empty by default: `hit` null -> false is reported as
a change, because "rule was not evaluated" and "rule did not fire" are different strategy behaviours.
Example to treat them as equal: {"pattern": "**.rules.hit", "values": [["NULL", "false"]]}.
"""

PROCEDURE = {
    "id": "spr_compare",
    "label": "Ответы СПР: эквивалентные значения",
    "description": "Какие разные значения считать одинаковыми при сравнении версий (например, пусто и false).",
    "kind": "compare",
    "params": {"equivalent": []},
}
