"""solver.dof 测试：DOF 序列 / 冗余检测 / FULL 与 OVER 状态。"""

import numpy as np

from sketchcad.solver.constraints import EQ_COUNTS, RESIDUALS
from sketchcad.solver.dof import (
    STATUS_FULL,
    STATUS_OVER,
    STATUS_UNDER,
    analyze,
    detect_redundant,
)


def line(eid, p1, p2):
    return {"id": eid, "type": "line", "p1": list(p1), "p2": list(p2)}


def coincident(cid, a, b):
    return {"id": cid, "type": "coincident", "targets": [a, b],
            "enabled": True}


def square_entities():
    return [
        line(1, (0, 0), (2, 0)),
        line(2, (2, 0), (2, 2)),
        line(3, (2, 2), (0, 2)),
        line(4, (0, 2), (0, 0)),
    ]


def square_chain():
    return [coincident(10, 1, 2), coincident(11, 2, 3),
            coincident(12, 3, 4), coincident(13, 4, 1)]


def rect_constraints():
    return [
        {"id": 20, "type": "horizontal", "targets": [1], "enabled": True},
        {"id": 21, "type": "vertical", "targets": [2], "enabled": True},
        {"id": 22, "type": "horizontal", "targets": [3], "enabled": True},
        {"id": 23, "type": "vertical", "targets": [4], "enabled": True},
    ]


def test_free_line_under():
    rep = analyze([line(1, (0, 0), (1, 2))], [])
    assert rep.n_params == 4
    assert rep.n_equations == 0
    assert rep.dof == 4
    assert rep.status == STATUS_UNDER


def test_horizontal_reduces_dof():
    ents = [line(1, (0, 0), (3, 1))]
    rep = analyze(ents, [{"id": 10, "type": "horizontal", "targets": [1],
                          "enabled": True}])
    assert rep.dof == 3


def test_square_chain_dof():
    rep = analyze(square_entities(), square_chain())
    assert rep.n_equations == 8
    assert rep.dof == 8
    assert rep.status == STATUS_UNDER


def test_square_rectangular_dof():
    cons = square_chain() + rect_constraints()
    rep = analyze(square_entities(), cons)
    assert rep.dof == 4  # 平移 2 + 宽 1 + 高 1


def test_square_dimensioned_translation_only():
    cons = square_chain() + rect_constraints() + [
        {"id": 24, "type": "distance", "targets": [1], "value": 2.0,
         "enabled": True},
        {"id": 25, "type": "distance", "targets": [2], "value": 2.0,
         "enabled": True},
    ]
    rep = analyze(square_entities(), cons)
    assert rep.dof == 2
    assert rep.status == STATUS_UNDER


def test_duplicate_constraint_redundant_over():
    ents = [line(1, (0, 0), (3, 0))]
    cons = [
        {"id": 10, "type": "horizontal", "targets": [1], "enabled": True},
        {"id": 11, "type": "horizontal", "targets": [1], "enabled": True},
    ]
    rep = analyze(ents, cons)
    assert rep.n_equations == 2
    assert rep.rank == 1
    assert rep.n_redundant == 1
    assert rep.status == STATUS_OVER
    assert detect_redundant(ents, cons) == [11]


def _fix_residual(x, layout, con):
    eid = con["targets"][0]
    pos = con.get("endpoints", [0])[0]
    off = layout.offsets[eid]
    return np.array([x[off + 2 * pos] - con["at"][0],
                     x[off + 2 * pos + 1] - con["at"][1]])


def test_fully_constrained_square_full():
    # 测试内临时注册 fix 约束（MVP 约束库之外，验证 dof=0 满约束路径）。
    # 单 fix 点（2 方程）：chain 8 + rect 4 + distance 2 + fix 2 = 16 方程，
    # 恰好 16 参数满秩；再加第二个 fix 会直接过约束。
    RESIDUALS["__fix_test"] = _fix_residual
    EQ_COUNTS["__fix_test"] = 2
    try:
        cons = (square_chain() + rect_constraints() + [
            {"id": 24, "type": "distance", "targets": [1], "value": 2.0,
             "enabled": True},
            {"id": 25, "type": "distance", "targets": [2], "value": 2.0,
             "enabled": True},
            {"id": 30, "type": "__fix_test", "targets": [1],
             "endpoints": [0], "at": [0.0, 0.0], "enabled": True},
        ])
        rep = analyze(square_entities(), cons)
        assert rep.n_equations == 16
        assert rep.dof == 0
        assert rep.n_redundant == 0
        assert rep.status == STATUS_FULL
    finally:
        RESIDUALS.pop("__fix_test", None)
        EQ_COUNTS.pop("__fix_test", None)


def test_over_square_extra_horizontal():
    RESIDUALS["__fix_test"] = _fix_residual
    EQ_COUNTS["__fix_test"] = 2
    try:
        cons = (square_chain() + rect_constraints() + [
            {"id": 24, "type": "distance", "targets": [1], "value": 2.0,
             "enabled": True},
            {"id": 25, "type": "distance", "targets": [2], "value": 2.0,
             "enabled": True},
            {"id": 30, "type": "__fix_test", "targets": [1],
             "endpoints": [0], "at": [0.0, 0.0], "enabled": True},
            {"id": 40, "type": "horizontal", "targets": [3],
             "enabled": True},
        ])
        ents = square_entities()
        rep = analyze(ents, cons)
        assert rep.n_equations == 17
        assert rep.status == STATUS_OVER
        assert rep.n_redundant == 1
        assert detect_redundant(ents, cons) == [40]
    finally:
        RESIDUALS.pop("__fix_test", None)
        EQ_COUNTS.pop("__fix_test", None)