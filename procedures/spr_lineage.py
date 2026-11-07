"""Lineage of SPR responses (editable default, params only).

Ordered stages along the decision path; each stage has a role, path globs and an optional display label (`*` one segment, `**` any number).
First matching stage wins, unmatched paths fall into stage "other". Roles:
input = data that comes from outside the strategy, reference = catalogs the strategy reads,
logic = values the strategy computes, outcome = what the client gets.
"""

PROCEDURE = {
    "id": "spr_lineage",
    "label": "Ответы СПР: цепочка решения (вход -> признаки -> модели -> порог -> правила -> решение -> офферы)",
    "kind": "lineage",
    "params": {
        "stages": [
            {"name": "input", "label": "входные данные", "role": "input",
             "paths": ["appData.**", "characteristics.**", "ext_data.**"]},
            {"name": "reference", "label": "справочники", "role": "reference",
             "paths": ["product_catalog.**", "tariff_matrix.**"]},
            {"name": "features", "label": "признаки", "role": "logic", "paths": ["decisionResult.scoring.features.**"]},
            {"name": "models", "label": "скоринговые модели", "role": "logic",
             "paths": ["decisionResult.scoring.**", "decisionResult.product_trials.scoring.**"]},
            {"name": "cutoff", "label": "пороги", "role": "logic",
             "paths": ["decisionResult.product_trials.cutOff.**"]},
            {"name": "rules", "label": "стоп-факторы", "role": "logic",
             "paths": ["decisionResult.product_trials.rules.**"]},
            {"name": "trial_decision", "label": "решение по продукту", "role": "outcome",
             "paths": ["decisionResult.product_trials.decision", "decisionResult.product_trials.rejectReason*"]},
            {"name": "decision", "label": "решение", "role": "outcome", "paths": ["decisionResult.decision"]},
            {"name": "offers", "label": "офферы", "role": "outcome",
             "paths": ["decisionResult.available_offers.**", "decisionResult.product_trials.**"]},
        ],
        "decision_path": "decisionResult.decision",
    },
}
