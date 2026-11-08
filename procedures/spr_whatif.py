"""What-if over SPR responses (editable default).

The strategy re-derived from the datamart: a product trial approves when no enabled rule hit and the target score
passes the cut-off (``pd >= cut``); an application approves when any trial approves. Cut-offs shift for all
products or per product (the trial's ``product_name``); stop factors and the target model are not user params (only the
cut-off is configurable), the cause attribution still restores them internally. The report breaks the result down by
``by_segment``. Calibration: with default
params the procedure must reproduce the slice's own decision for every application (the app reports fidelity).

The target score is the trial's ``pd.modelName`` model read from ``scoreModels`` (``pd`` when that model has no
score). Factors for the cause of a decision flip, each restored from BEFORE while the rest stays AFTER: the cut-offs,
the target model (which model is the target; scores stay AFTER) and the stop-factor rules (which rules exist and
fire); the input data (scores of the same models) is ``strategy: False``, never a cause by itself.
"""

import numpy as np


def evaluate(data, params):
    """Return the outcome index per application (0 = APPROVE, 1 = REJECT), vectorised over all product trials."""
    cut = data["cut"] + params["cutoff_shift"]
    for product, shift in params["product_shift"].items():
        cut[data["product"] == product] += shift
    target = np.full(len(cut), params["target_model"]) if params.get("target_model") else data["target"]
    pd = np.array(data["pd"])
    for model, score in data["models"].items():
        pick = (target == model) & ~np.isnan(score)
        pd[pick] = score[pick]
    hit = np.zeros(len(cut), bool)
    for rule, fired in data["hit"].items():
        if rule not in params.get("rules_off", ()):
            hit |= fired
    ok = ~hit & (pd >= cut)
    approve = np.bincount(data["unit_app"], weights=ok, minlength=data["n_apps"]) > 0
    return np.where(approve, 0, 1).astype(np.uint8)


PROCEDURE = {
    "id": "spr_whatif_cutoff_rules",
    "label": "СПР: что если сдвинуть пороги отсечения",
    "description": "Упрощенная модель решения: по ней система ищет причину каждой смены решения (порог, модель, правила или входные данные).",
    "kind": "whatif",
    "unit_depth": 1,
    "inputs": {
        "pd": {"path": "decisionResult.product_trials.scoring.pd.value", "type": "float"},
        "target": {"path": "decisionResult.product_trials.scoring.pd.modelName", "type": "str"},
        "cut": {"path": "decisionResult.product_trials.cutOff.value", "type": "float"},
        "cut_name": {"path": "decisionResult.product_trials.cutOff.name", "type": "str"},
        "hit": {"path": "decisionResult.product_trials.rules.hit", "type": "bool"},
        "models": {"path": "decisionResult.product_trials.scoring.scoreModels.value", "type": "float"},
        "product": {"path": "decisionResult.product_trials.product_name", "type": "str"},
    },
    "by_segment": "PRODUCTS",
    "outcome": {"field": "DECISION", "values": ["APPROVE", "REJECT"]},
    "params": {
        "cutoff_shift": {"type": "float", "min": -0.3, "max": 0.3, "step": 0.01, "default": 0.0,
                         "label": "Сдвиг всех порогов"},
        "product_shift": {"type": "float_map", "keys_from": "product", "min": -0.3, "max": 0.3, "step": 0.01,
                          "default": {}, "label": "Сдвиг порога по продукту"},
    },
    "factors": {
        "cutoff": {"label": "порог", "inputs": ["cut", "cut_name"],
                   "hint": "Если вернуть пороги BEFORE, а все остальное оставить AFTER, решение возвращается"},
        "model": {"label": "целевая модель", "inputs": ["target", "pd"],
                  "hint": "Смена целевой модели - решение стратегии, как и порог: если вернуть модель BEFORE, "
                          "а все остальное оставить AFTER, решение возвращается"},
        "data": {"label": "входные данные", "inputs": ["models"], "strategy": False},
        "rules": {"label": "стоп-факторы", "inputs": ["hit"],
                  "hint": "Если вернуть стоп-факторы BEFORE (какие правила есть и срабатывают), решение возвращается"},
    },
    "entrypoint": evaluate,
}
