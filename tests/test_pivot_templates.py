import pytest
from pydantic import ValidationError

from decision_diff.api import Layout, PivotBody, TemplateBody


def test_layout_accepts_a_normal_layout_and_drops_unknown_keys():
    t = TemplateBody(name="  Значения до и после ", layout={"rows": ["PATH", "ADJUSTED_VALUE"], "cols": ["SOURCE"],
                                                            "filters": {"DIFF_FLAG": ["1"]}, "mode": "row", "x": 1})
    assert t.name == "Значения до и после"
    assert t.layout.model_dump() == {"rows": ["PATH", "ADJUSTED_VALUE"], "cols": ["SOURCE"],
                                     "filters": {"DIFF_FLAG": ["1"]}, "mode": "row"}


@pytest.mark.parametrize("layout", [
    {"rows": [f"F{i}" for i in range(9)]},
    {"cols": [f"F{i}" for i in range(5)]},
    {"rows": ["PATH"], "cols": ["PATH"]},
    {"rows": [""]},
    {"rows": ["x" * 201]},
    {"filters": {f"F{i}": [] for i in range(33)}},
    {"filters": {"F": [str(i) for i in range(501)]}},
    {"filters": {"F": ["x" * 1001]}},
    {"mode": "sum"},
])
def test_layout_rejects_oversized_or_ambiguous_layouts(layout):
    with pytest.raises(ValidationError):
        Layout(**layout)
    with pytest.raises(ValidationError):
        PivotBody(**layout)


def test_template_name_is_required():
    with pytest.raises(ValidationError):
        TemplateBody(name="   ", layout={})
