"""solver.lm 测试：尺寸驱动 / 折线闭合 / 矛盾约束失败报告 / 差分 J 正确性。"""

import numpy as np

from sketchcad.solver.constraints import build_residual_fn
from sketchcad.solver.lm import numerical_jacobian, solve
from sketchcad.solver.params import ParamLayout


def line(eid, p1, p2):
    return {"id": eid, "type": "line", "p1": list(p1), "p2": list(p2)}


def solve_entities(ents, cons, **kw):
    layout = ParamLayout(ents)
    residual = build_residual_fn(ents, cons, layout)
    result = solve(layout.pack(ents), residual, **kw)
    if result.ok:
        layout.apply(result.x, ents)
    return result


def test_distance_drive():
    ents = [line(1, (0, 0), (3, 0))]
    cons = [{"id": 10, "type": "distance", "targets": [1], "value": 5.0,
             "enabled": True}]
    result = solve_entities(ents, cons)
    assert result.ok
    dx = ents[0]["p2"][0] - ents[0]["p1"][0]
    dy = ents[0]["p2"][1] - ents[0]["p1"][1]
    assert abs(dx * dx + dy * dy - 25.0) < 1e-8


def test_drag_refine_few_iters():
    # 已在解附近改 value 再解（拖动尺寸的每帧语义）→ 极少迭代
    ents = [line(1, (0, 0), (5, 0))]
    cons = [{"id": 10, "type": "distance", "targets": [1], "value": 5.0,
             "enabled": True}]
    solve_entities(ents, cons)
    cons[0]["value"] = 5.2
    result = solve_entities(ents, cons)
    assert result.ok
    assert result.iters <= 5


def test_polyline_close_two_coincident():
    ents = [line(1, (0, 0), (3, 0)), line(2, (3, 1), (0, 1))]
    cons = [
        {"id": 10, "type": "coincident", "targets": [1, 2], "enabled": True},
        {"id": 11, "type": "coincident", "targets": [1, 2],
         "endpoints": [0, 1], "enabled": True},
    ]
    result = solve_entities(ents, cons)
    assert result.ok
    assert np.allclose(ents[0]["p2"], ents[1]["p1"], atol=1e-8)
    assert np.allclose(ents[1]["p2"], ents[0]["p1"], atol=1e-8)


def test_horizontal_from_vertical_initial():
    # 竖直初值要求水平（90° 大转动场景，考察阻尼稳健性）
    ents = [line(1, (0, 0), (0, 3))]
    cons = [{"id": 10, "type": "horizontal", "targets": [1],
             "enabled": True}]
    result = solve_entities(ents, cons)
    assert result.ok
    assert abs(ents[0]["p2"][1] - ents[0]["p1"][1]) < 1e-8


def test_contradiction_reports_failure_not_crash():
    ents = [line(1, (0, 0), (1, 0))]
    cons = [
        {"id": 10, "type": "distance", "targets": [1], "value": 5.0,
         "enabled": True},
        {"id": 11, "type": "distance", "targets": [1], "value": 10.0,
         "enabled": True},
    ]
    result = solve_entities(ents, cons)
    assert not result.ok
    assert result.message in (
        "converged_gradient", "converged_step", "max_iter",
        "stalled_damping")


def test_numerical_jacobian_matches_analytic():
    ents = [line(1, (0.3, 0.2), (2.0, 1.1))]
    cons = [
        {"id": 10, "type": "distance", "targets": [1], "value": 2.5,
         "enabled": True},
        {"id": 11, "type": "horizontal", "targets": [1], "enabled": True},
    ]
    layout = ParamLayout(ents)
    residual = build_residual_fn(ents, cons, layout)
    x = layout.pack(ents)
    J_num = numerical_jacobian(residual, x, residual(x))

    dx = x[2] - x[0]
    dy = x[3] - x[1]
    J_ana = np.zeros((2, 4))
    J_ana[0] = [-2 * dx, -2 * dy, 2 * dx, 2 * dy]
    J_ana[1] = [0.0, -1.0, 0.0, 1.0]
    assert np.allclose(J_num, J_ana, rtol=1e-4, atol=1e-6)


def test_empty_constraints_immediate_ok():
    ents = [line(1, (0, 0), (1, 1))]
    result = solve_entities(ents, [])
    assert result.ok
    assert result.message == "empty_problem"