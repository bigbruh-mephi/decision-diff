"""Lineage of SPR responses (editable default, params only).

Ordered stages along the decision path; each stage has a role and path globs (`*` one segment, `**` any number).
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
            {"name": "input", "role": "input", "paths": ["appData.**", "characteristics.**", "ext_data.**"]},
            {"name": "reference", "role": "reference", "paths": ["product_catalog.**", "tariff_matrix.**"]},
            {"name": "features", "role": "logic", "paths": ["decisionResult.scoring.features.**"]},
            {"name": "models", "role": "logic",
             "paths": ["decisionResult.scoring.**", "decisionResult.product_trials.scoring.**"]},
            {"name": "cutoff", "role": "logic", "paths": ["decisionResult.product_trials.cutOff.**"]},
            {"name": "rules", "role": "logic", "paths": ["decisionResult.product_trials.rules.**"]},
            {"name": "trial_decision", "role": "outcome",
             "paths": ["decisionResult.product_trials.decision", "decisionResult.product_trials.rejectReason*"]},
            {"name": "decision", "role": "outcome", "paths": ["decisionResult.decision"]},
            {"name": "offers", "role": "outcome",
             "paths": ["decisionResult.available_offers.**", "decisionResult.product_trials.**"]},
        ],
        "decision_path": "decisionResult.decision",
    },
}
