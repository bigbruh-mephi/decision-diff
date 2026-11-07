"""Segments layer for SPR responses (editable default): one row per application, fields from the datamart schema."""


def _trials(doc):
    return (doc.get("decisionResult") or {}).get("product_trials") or []


def _first(values):
    return next((v for v in values if v not in (None, "")), None)


def _top_offer(doc):
    offers = (doc.get("decisionResult") or {}).get("available_offers") or []
    best = min(offers, key=lambda o: o.get("rank") if isinstance(o.get("rank"), (int, float)) else 1e9, default=None)
    return best.get("product_name") if best else None


def _side(doc):
    trials = _trials(doc)
    return {
        "DECISION": (doc.get("decisionResult") or {}).get("decision"),
        "TOP_OFFER": _top_offer(doc),
        "PD_MODEL": _first(((t.get("scoring") or {}).get("pd") or {}).get("modelName") for t in trials),
        "CUTOFF": _first((t.get("cutOff") or {}).get("name") for t in trials),
        "REJECT_REASON": _first(t.get("rejectReason") for t in trials),
        "SCORE_OKB": (doc.get("characteristics") or {}).get("score_okb"),
    }


def run(before, after, params):
    b, a = _side(before), _side(after)
    trials = _trials(before) + _trials(after)
    row = {f"{k}_{s}": v for s, d in (("BEFORE", b), ("AFTER", a)) for k, v in d.items()}
    row["DECISION_CHANGED"] = int(b["DECISION"] != a["DECISION"])
    row["PRODUCTS"] = ";".join(sorted({str(t.get("product_name")) for t in trials if t.get("product_name")}))
    row["PARTNER_TYPE"] = ";".join(sorted({str(t.get("partner_type")) for t in trials if t.get("partner_type")}))
    row["ROUTING_METHOD"] = ";".join(sorted({str(t.get("routing_method")) for t in trials if t.get("routing_method")}))
    return row


PROCEDURE = {
    "id": "spr_segments",
    "label": "SPR responses: segments layer (decision, products, target model, cut-off, external score)",
    "kind": "segments",
    "params": {"multi_valued": {"PRODUCTS": ";", "PARTNER_TYPE": ";", "ROUTING_METHOD": ";"},
               "decision_field": "DECISION",
               "positive_decision": "APPROVE",
               "input_paths": ["characteristics.**", "ext_data.**", "appData.**"]},
    "entrypoint": run,
}
