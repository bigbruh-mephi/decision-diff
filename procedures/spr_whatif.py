"""What-if over SPR responses (editable default).

The strategy re-derived from the datamart: a product trial approves when no enabled rule hit and the target score
passes the cut-off (``pd >= cut``); an application approves when any trial approves. Calibration: with default
params the procedure must reproduce the slice's own decision for every application (the app reports fidelity).

The target score is the trial's ``pd.modelName`` model read from ``scoreModels`` (``pd`` when that model has no
score). Factors for the cause of a decision flip, each restored from BEFORE while the rest stays AFTER: the cut-offs,
the target model (which model is the target; scores stay AFTER), the input data (scores of the same models) and the
stop-factor rules (which rules exist and fire).
"""

import numpy as np


def evaluate(data, params):
    """Return the outcome index per application (0 = APPROVE, 1 = REJECT), vectorised over all product trials."""
    cut = data["cut"].copy()
    for name, value in params["cutoffs"].items():
        cut[data["cut_name"] == name] = value
    cut += params["cutoff_shift"]
    target = np.full(len(cut), params["target_model"]) if params["target_model"] else data["target"]
    pd = np.array(data["pd"])
    for model, score in data["models"].items():
        pick = (target == model) & ~np.isnan(score)
        pd[pick] = score[pick]
    hit = np.zeros(len(cut), bool)
    for rule, fired in data["hit"].items():
        if rule not in params["rules_off"]:
            hit |= fired
    ok = ~hit & (pd >= cut)
    approve = np.bincount(data["unit_app"], weights=ok, minlength=data["n_apps"]) > 0
    return np.where(approve, 0, 1).astype(np.uint8)


PROCEDURE = {
    "id": "spr_whatif_cutoff_rules",
    "label": "СПР: что если поменять пороги, стоп-факторы и целевую модель",
    "kind": "whatif",
    "unit_depth": 1,
    "inputs": {
        "pd": {"path": "decisionResult.product_trials.scoring.pd.value", "type": "float"},
        "target": {"path": "decisionResult.product_trials.scoring.pd.modelName", "type": "str"},
        "cut": {"path": "decisionResult.product_trials.cutOff.value", "type": "float"},
        "cut_name": {"path": "decisionResult.product_trials.cutOff.name", "type": "str"},
        "hit": {"path": "decisionResult.product_trials.rules.hit", "type": "bool"},
        "models": {"path": "decisionResult.product_trials.scoring.scoreModels.value", "type": "float"},
    },
    "outcome": {"field": "DECISION", "values": ["APPROVE", "REJECT"]},
    "params": {
        "slice": {"type": "enum", "values": ["AFTER", "BEFORE"], "default": "AFTER", "label": "Данные и логика среза"},
        "cutoff_shift": {"type": "float", "min": -0.3, "max": 0.3, "step": 0.01, "default": 0.0,
                         "label": "Сдвиг всех порогов"},
        "cutoffs": {"type": "float_map", "keys_from": "cut_name", "min": 0.0, "max": 1.0, "step": 0.01,
                    "default": {}, "label": "Порог по имени (пустое поле - как в срезе)"},
        "rules_off": {"type": "multi", "values_from": "hit", "default": [], "label": "Отключённые стоп-факторы"},
        "target_model": {"type": "enum", "values_from": "models", "default": "", "label": "Целевая модель скоринга"},
    },
    "factors": {
        "cutoff": {"label": "порог", "inputs": ["cut", "cut_name"]},
        "model": {"label": "модель", "inputs": ["target", "pd"]},
        "data": {"label": "входные данные", "inputs": ["models"]},
        "rules": {"label": "стоп-факторы", "inputs": ["hit"]},
    },
    "entrypoint": evaluate,
}
