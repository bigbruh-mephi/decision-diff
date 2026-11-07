"""Normalization profile for Decision Flow SPR responses (editable default).

Array modes are configuration by array path: by field(s), by value, or by index. Unconfigured arrays are compared by
index. Patterns: `*` one segment, `**` any number of segments.
"""

PROCEDURE = {
    "id": "spr_normalize",
    "label": "Ответы СПР: ключи массивов, технические исключения, бакеты",
    "kind": "normalize",
    "params": {
        "arrays": {
            "decisionResult.available_offers": ["product_id", "type"],
            "decisionResult.product_trials": ["product_id"],
            "decisionResult.product_trials.rules": ["name"],
            "decisionResult.product_trials.scoring.scoreModels": ["modelName"],
            "decisionResult.scoring": ["modelName"],
            "decisionResult.scoring.value": "@index",
            "ext_data": ["body.action"],
            "product_catalog": ["mfo_id", "product_id"],
            "tariff_matrix": ["mfo_id", "product_id"],
            "**.rejectReasons": "@value",
        },
        "exclude": ["appData.request_id", "appData.createDateTime"],
        "buckets": [
            {"pattern": "**", "breaks": [0, 0.3, 0.6, 0.8, 1, 1000, 100000, 1000000]},
        ],
        "rename": {},
        "key_style": None,
    },
}
