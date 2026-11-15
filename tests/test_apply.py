"""solver.apply 测试：会话级求解回写（走 conftest mathutils stub 链）。"""

from sketchcad.core.datum import Datum
from sketchcad.core.sketch_data import SketchData
from sketchcad.solver.apply import solve_and_apply


def make_sketch(p1, p2):
    s = SketchData(Datum((0, 0, 0), (0, 0, 1)))
    s.add_line(p1, p2)
    return s


def test_solve_and_apply_success():
    s = make_sketch((0, 0), (3, 0))
    s.add_constraint("distance", [1], value=5.0)
    ok, _ = solve_and_apply(s)
    assert ok
    p1, p2 = s.entities[0]["p1"], s.entities[0]["p2"]
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    assert abs(dx * dx + dy * dy - 25.0) < 1e-8


def test_solve_and_apply_data_problem_keeps_geometry():
    s = make_sketch((0, 0), (1, 0))
    s.add_constraint("horizontal", [99])  # target 不存在
    ok, message = solve_and_apply(s)
    assert not ok
    assert "99" in message
    assert s.entities[0]["p2"] == [1.0, 0.0]  # 几何未动


def test_solve_and_apply_contradiction_no_crash():
    s = make_sketch((0, 0), (1, 0))
    s.add_constraint("distance", [1], value=5.0)
    s.add_constraint("distance", [1], value=10.0)
    ok, _ = solve_and_apply(s)
    assert not ok
    # 求解失败时不回写，几何保持原状
    assert s.entities[0]["p1"] == [0.0, 0.0]
    assert s.entities[0]["p2"] == [1.0, 0.0]