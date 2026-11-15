"""solver.constraints 测试：残差零点 / 方程数 / 数据层校验。"""

import numpy as np
import pytest

from sketchcad.solver.constraints import (
    UnknownConstraintTypeError,
    build_residual_fn,
    collect_problems,
    equations_count,
)
from sketchcad.solver.params import ParamLayout


def line(eid, p1, p2):
    return {"id": eid, "type": "line", "p1": list(p1), "p2": list(p2)}


def residual_of(ents, cons):
    layout = ParamLayout(ents)
    fn = build_residual_fn(ents, cons, layout)
    return fn(layout.pack(ents))


def test_coincident_default_endpoints_satisfied():
    ents = [line(1, (0, 0), (3, 0)), line(2, (3, 0), (5, 2))]
    con = {"id": 10, "type": "coincident", "targets": [1, 2], "enabled": True}
    assert np.allclose(residual_of(ents, [con]), 0.0)


def test_coincident_violated_and_explicit_endpoints():
    ents = [line(1, (0, 0), (3, 0)), line(2, (4, 1), (5, 2))]
    con = {"id": 10, "type": "coincident", "targets": [1, 2], "enabled": True}
    assert np.allclose(residual_of(ents, [con]), [-1.0, -1.0])
    con_b = dict(con, endpoints=[0, 1])  # A.p1 vs B.p2
    assert np.allclose(residual_of(ents, [con_b]), [-5.0, -2.0])


def test_horizontal_and_vertical_residuals():
    ents = [line(1, (0, 1), (4, 1))]
    h = {"id": 10, "type": "horizontal", "targets": [1], "enabled": True}
    v = {"id": 11, "type": "vertical", "targets": [1], "enabled": True}
    assert np.allclose(residual_of(ents, [h]), 0.0)
    assert np.allclose(residual_of(ents, [v]), 4.0)


def test_distance_quadratic_form():
    ents = [line(1, (0, 0), (3, 4))]
    con = {"id": 10, "type": "distance", "targets": [1], "value": 5.0,
           "enabled": True}
    assert np.allclose(residual_of(ents, [con]), 0.0)  # 3-4-5 直角边
    con2 = dict(con, value=10.0)
    assert np.allclose(residual_of(ents, [con2]), 25.0 - 100.0)


def test_disabled_constraints_skipped():
    ents = [line(1, (0, 1), (4, 1))]
    h = {"id": 10, "type": "horizontal", "targets": [1], "enabled": False}
    assert residual_of(ents, [h]).size == 0
    assert equations_count([h]) == 0


def test_equations_count():
    cons = [
        {"id": 1, "type": "coincident", "targets": [1, 2], "enabled": True},
        {"id": 2, "type": "horizontal", "targets": [1], "enabled": True},
        {"id": 3, "type": "vertical", "targets": [1], "enabled": True},
        {"id": 4, "type": "distance", "targets": [1], "value": 1.0,
         "enabled": True},
    ]
    assert equations_count(cons) == 5


def test_unknown_type_raises():
    ents = [line(1, (0, 0), (1, 1))]
    with pytest.raises(UnknownConstraintTypeError):
        residual_of(ents, [{"id": 9, "type": "symmetry", "targets": [1],
                            "enabled": True}])


def test_collect_problems_reports_data_layer_issues():
    ents = [line(1, (0, 0), (1, 1))]
    assert collect_problems(ents, []) == []
    bad = [
        {"id": 10, "type": "distance", "targets": [1], "enabled": True},
        {"id": 11, "type": "horizontal", "targets": [2], "enabled": True},
        {"id": 12, "type": "coincident", "targets": [1], "enabled": True},
        {"id": 13, "type": "parallel", "targets": [1], "enabled": True},
        {"id": 14, "type": "horizontal", "targets": [1], "enabled": False},
    ]
    problems = collect_problems(ents, bad)
    assert len(problems) == 4  # 禁用约束不校验