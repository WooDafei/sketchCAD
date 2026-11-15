"""drawing.auto_constraints 测试：推断 / 吸附 / 端点命中 / DOF 集成。"""

from sketchcad.drawing import auto_constraints as ac
from sketchcad.solver.dof import STATUS_UNDER, analyze


def line(eid, p1, p2):
    return {"id": eid, "type": "line", "p1": list(p1), "p2": list(p2)}


def test_classify_horizontal():
    assert ac.classify_segment((0, 1), (5, 1.003), 0.01) == ac.HORIZONTAL


def test_classify_vertical():
    assert ac.classify_segment((2, 0), (2.02, 7), 0.05) == ac.VERTICAL


def test_classify_free_returns_none():
    assert ac.classify_segment((0, 0), (3, 4), 0.01) is None


def test_classify_zero_length_returns_none():
    assert ac.classify_segment((1, 1), (1, 1), 0.01) is None


def test_classify_boundary_inclusive():
    # 恰好等于阈值视为满足（<=），与捕捉容差语义一致
    assert ac.classify_segment((0, 0), (5, 0.01), 0.01) == ac.HORIZONTAL


def test_snapped_segment_horizontal():
    p1, p2 = ac.snapped_segment((1, 2), (5, 2.4), ac.HORIZONTAL)
    assert p1 == (1, 2)
    assert p2 == (5, 2)


def test_snapped_segment_vertical():
    p1, p2 = ac.snapped_segment((1, 2), (0.7, 9), ac.VERTICAL)
    assert p1 == (1, 2)
    assert p2 == (1, 9)


def test_snapped_segment_none_passthrough():
    assert ac.snapped_segment((0, 0), (3, 4), None) == ((0, 0), (3, 4))


def test_find_endpoint_hit():
    ents = [line(1, (0, 0), (3, 0)), line(2, (3, 0), (3, 2))]
    assert ac.find_endpoint_hit((3, 0), ents) == (1, 1)
    assert ac.find_endpoint_hit((2.999999999, 0.0), ents) == (1, 1)
    assert ac.find_endpoint_hit((9, 9), ents) is None


def test_find_endpoint_hit_exclude_self():
    ents = [line(1, (0, 0), (3, 0)), line(2, (3, 0), (3, 2))]
    # 排除自身后，刚建的段端点只能匹配到其他实体
    assert ac.find_endpoint_hit((0, 0), ents, exclude_id=1) is None
    assert ac.find_endpoint_hit((3, 0), ents, exclude_id=1) == (2, 0)


def test_inferred_constraints_dof_integration():
    # 集成：两段折线（连续段 + 吸附水平）→ coincident + horizontal
    # 方程 3（2+1），参数 8，独立 → dof=5 under，无冗余
    ents = [line(1, (0, 0), (3, 0)), line(2, (3, 0), (6, 0.01))]
    cons = [
        {"id": 10, "type": ac.COINCIDENT, "targets": [1, 2],
         "endpoints": [1, 0], "enabled": True},
        {"id": 11, "type": ac.HORIZONTAL, "targets": [2], "enabled": True},
    ]
    rep = analyze(ents, cons)
    assert rep.status == STATUS_UNDER
    assert rep.dof == 5
    assert rep.n_redundant == 0