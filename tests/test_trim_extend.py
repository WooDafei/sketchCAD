"""trim/extend 纯几何测试（SketchCAD.md v2 §6）。

Trim 语义（2026-11-16 实测修正）：点击线段上要**移除**的部分，
在最近内部交点处剪断、裁除点击侧、保留另一侧。
"""

from sketchcad.editing import trim_extend as te


def mk(eid, p1, p2):
    return {"id": eid, "type": "line", "p1": list(p1), "p2": list(p2)}


# ---- line_line_params / 投影工具 ----

def test_line_line_params_basic():
    t, u = te.line_line_params((0, 0), (10, 0), (5, -5), (5, 5))
    assert abs(t - 0.5) < 1e-9
    assert abs(u - 0.5) < 1e-9


def test_line_line_params_parallel():
    assert te.line_line_params((0, 0), (1, 0), (0, 1), (1, 1)) is None


def test_line_line_params_collinear():
    assert te.line_line_params((0, 0), (1, 0), (2, 0), (3, 0)) is None


def test_collect_intersections_sorted_and_excluded():
    ents = [mk(1, (0, 0), (10, 0)),
            mk(2, (3, -3), (3, 3)),
            mk(3, (7, -3), (7, 3))]
    cuts = te.collect_intersections((0, 0), (10, 0), ents, exclude_id=1)
    assert [eid for _t, eid in cuts] == [2, 3]
    assert abs(cuts[0][0] - 0.3) < 1e-9
    assert abs(cuts[1][0] - 0.7) < 1e-9


def test_project_param_unclamped():
    assert abs(te.project_param((0, 0), (10, 0), (4, 3)) - 0.4) < 1e-9
    assert abs(te.project_param((0, 0), (10, 0), (15, 0)) - 1.5) < 1e-9
    assert abs(te.project_param((0, 0), (10, 0), (-5, 0)) + 0.5) < 1e-9


def test_point_at():
    x, y = te.point_at((0, 0), (10, 5), 0.4)
    assert abs(x - 4.0) < 1e-9 and abs(y - 2.0) < 1e-9


# ---- trim：裁除点击侧 ----

def test_trim_removes_clicked_p1_side():
    # 点击 p1 侧（x=2）：裁除 p1→交点，保留交点→p2
    ents = [mk(1, (0, 0), (10, 0)), mk(2, (5, -5), (5, 5))]
    ok, changed, p1, p2 = te.trim_line(ents, 1, (2, 0))
    assert ok and changed == 0
    assert abs(p1[0] - 5) < 1e-9 and p1[1] == 0
    assert p2 == [10, 0]


def test_trim_removes_clicked_p2_side():
    # 点击 p2 侧（x=8）：裁除交点→p2，保留 p1→交点
    ents = [mk(1, (0, 0), (10, 0)), mk(2, (5, -5), (5, 5))]
    ok, changed, p1, p2 = te.trim_line(ents, 1, (8, 0))
    assert ok and changed == 1
    assert p1 == [0, 0]
    assert abs(p2[0] - 5) < 1e-9 and p2[1] == 0


def test_trim_nearest_cut():
    ents = [mk(1, (0, 0), (10, 0)),
            mk(2, (3, -3), (3, 3)),
            mk(3, (7, -3), (7, 3))]
    ok, changed, _p1, p2 = te.trim_line(ents, 1, (8, 0))
    assert ok and changed == 1
    assert abs(p2[0] - 7) < 1e-9


def test_trim_endpoint_touch_not_boundary():
    # T 形相接：竖线端点落在横线 t=0 处，共享端点不算修剪边界
    ents = [mk(1, (0, 0), (10, 0)), mk(2, (0, 0), (0, 5))]
    ok, reason = te.trim_line(ents, 1, (2, 0))
    assert not ok and reason == "no-intersection"


def test_trim_removed_degenerate():
    # 交点紧贴 p1（保留侧 < _GEOM_EPS），点击 p2 侧 → 保留 [p1, cut] 退化
    ents = [mk(1, (0, 0), (10, 0)), mk(2, (5e-7, -5), (5e-7, 5))]
    ok, reason = te.trim_line(ents, 1, (10, 0))
    assert not ok and reason == "removed"


def test_trim_no_intersection():
    ents = [mk(1, (0, 0), (10, 0)), mk(2, (0, 5), (5, 5))]
    ok, reason = te.trim_line(ents, 1, (2, 0))
    assert not ok and reason == "no-intersection"


def test_trim_parallel_others_ignored():
    ents = [mk(1, (0, 0), (10, 0)), mk(2, (0, 1), (10, 1))]
    ok, reason = te.trim_line(ents, 1, (5, 0))
    assert not ok and reason == "no-intersection"


def test_trim_not_found():
    ok, reason = te.trim_line([], 99, (0, 0))
    assert not ok and reason == "not-found"


# ---- extend：单发几何函数（operator 走两段式交互，用户定终点）----

def test_extend_p2_side():
    ents = [mk(1, (0, 0), (10, 0)), mk(2, (15, -5), (15, 5))]
    ok, changed, p1, p2 = te.extend_line(ents, 1, (8, 0))
    assert ok and changed == 1
    assert p1 == [0, 0]
    assert abs(p2[0] - 15) < 1e-9 and p2[1] == 0


def test_extend_p1_side():
    ents = [mk(1, (0, 0), (10, 0)), mk(2, (-5, -5), (-5, 5))]
    ok, changed, p1, p2 = te.extend_line(ents, 1, (2, 0))
    assert ok and changed == 0
    assert abs(p1[0] + 5) < 1e-9
    assert p2 == [10, 0]


def test_extend_nearest_target():
    ents = [mk(1, (0, 0), (10, 0)),
            mk(2, (15, -5), (15, 5)),
            mk(3, (20, -5), (20, 5))]
    ok, _changed, _p1, p2 = te.extend_line(ents, 1, (8, 0))
    assert ok and abs(p2[0] - 15) < 1e-9


def test_extend_no_target():
    ents = [mk(1, (0, 0), (10, 0)), mk(2, (0, 5), (5, 5))]
    ok, reason = te.extend_line(ents, 1, (8, 0))
    assert not ok and reason == "no-target"


def test_extend_not_found():
    ok, reason = te.extend_line([], 99, (0, 0))
    assert not ok and reason == "not-found"