"""Normalization profile for Decision Flow SPR responses (editable default).

Array modes are configuration by array path: by field(s), by value, or by index. Unconfigured arrays are compared by
index. Patterns: `*` one segment, `**` any number of segments.
"""

PROCEDURE = {
    "id": "spr_normalize",
    "label": "Ответы СПР: ключи массивов, технические исключения, бакеты",
    "description": "Как разложить ответ на атрибуты: ключи массивов (как сопоставлять элементы списков между версиями), технические поля, которые не сравниваются, и интервалы (бакеты) для чисел.",
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
            {"pattern": "**.score_okb", "breaks": [500, 550, 600, 650, 700, 750, 800]},
            {"pattern": "**.score_equ", "breaks": [500, 550, 600, 650, 700, 750, 800]},
            {"pattern": "ext_data.body.params.data", "breaks": [500, 550, 600, 650, 700, 750, 800]},
            {"pattern": "**.pd.value", "breaks": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]},
            {"pattern": "**.scoreModels.value", "breaks": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]},
            {"pattern": "decisionResult.scoring.value", "breaks": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]},
            {"pattern": "**.features.age", "breaks": [20, 24, 34, 44, 54, 64]},
            {"pattern": "**.features.sumActiveDeals", "breaks": [0, 50000, 100000, 250000, 500000]},
            {"pattern": "**.features.monthsSinceLastLoan", "breaks": [0, 3, 6, 12, 24]},
        ],
        "rename": {},
        "key_style": None,
    },
}
