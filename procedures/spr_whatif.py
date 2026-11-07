"""What-if over SPR responses (editable default).

The strategy re-derived from the datamart: a product trial approves when no enabled rule hit and the target score
passes the cut-off (``pd >= cut``); an application approves when any trial approves. Calibration: with default
params the procedure must reproduce the slice's own decision for every application (the app reports fidelity).
"""

import numpy as np


def evaluate(data, params):
    """Return the outcome index per application (0 = APPROVE, 1 = REJECT), vectorised over all product trials."""
    cut = data["cut"].copy()
    for name, value in params["cutoffs"].items():
        cut[data["cut_name"] == name] = value
    cut += params["cutoff_shift"]
    pd = data["pd"]
    if params["target_model"]:
        alt = data["models"].get(params["target_model"])
        pd = pd if alt is None else np.where(np.isnan(alt), pd, alt)
    hit = np.zeros(len(cut), bool)
    for rule, fired in data["hit"].items():
        if rule not in params["rules_off"]:
            hit |= fired
    ok = ~hit & (pd >= cut)
    approve = np.bincount(data["unit_app"], weights=ok, minlength=data["n_apps"]) > 0
    return np.where(approve, 0, 1).astype(np.uint8)


PROCEDURE = {
    "id": "spr_whatif_cutoff_rules",
    "label": "SPR: cut-offs, stop-factors and target model what-if",
    "kind": "whatif",
    "unit_depth": 1,
    "inputs": {
        "pd": {"path": "decisionResult.product_trials.scoring.pd.value", "type": "float"},
        "cut": {"path": "decisionResult.product_trials.cutOff.value", "type": "float"},
        "cut_name": {"path": "decisionResult.product_trials.cutOff.name", "type": "str"},
        "hit": {"path": "decisionResult.product_trials.rules.hit", "type": "bool"},
        "models": {"path": "decisionResult.product_trials.scoring.scoreModels.value", "type": "float"},
    },
    "outcome": {"field": "DECISION", "values": ["APPROVE", "REJECT"]},
    "params": {
        "slice": {"type": "enum", "values": ["AFTER", "BEFORE"], "default": "AFTER", "label": "Data and logic of"},
        "cutoff_shift": {"type": "float", "min": -0.3, "max": 0.3, "step": 0.01, "default": 0.0,
                         "label": "Shift of every cut-off"},
        "cutoffs": {"type": "float_map", "keys_from": "cut_name", "min": 0.0, "max": 1.0, "step": 0.01,
                    "default": {}, "label": "Cut-off value by name (empty = as in the slice)"},
        "rules_off": {"type": "multi", "values_from": "hit", "default": [], "label": "Disabled stop-factors"},
        "target_model": {"type": "enum", "values_from": "models", "default": "", "label": "Target score model"},
    },
    "entrypoint": evaluate,
}
