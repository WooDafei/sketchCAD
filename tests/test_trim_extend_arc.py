"""Trim/Extend 圆弧适配测试（SketchCAD.md §6 Phase 3）。

直线-圆 / 圆-圆交点参数、弧扫角过滤、trim_arc 点击侧裁除与端点
margin 排除、extend_arc_result 延伸端选择与扫角内无效判定。
"""

import math

from sketchcad.editing import trim_extend as te

EPS = 1e-9


def approx(a, b, eps=EPS):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 < eps ** 2


# ---- 直线-圆交点 ----

def test_line_circle_two_points():
    pts = te.line_circle_params((-2, 0), (2, 0), (0, 0), 1.0)
    assert len(pts) == 2
    ts = sorted(t for t, _pt in pts)
    assert abs(ts[0] - 0.25) < EPS       # t 相对 (-2,0)->(2,0)
    assert abs(ts[1] - 0.75) < EPS
    for _t, pt in pts:
        assert abs((pt[0] ** 2 + pt[1] ** 2) - 1.0) < EPS


def test_line_circle_tangent_one_point():
    pts = te.line_circle_params((-2, 1), (2, 1), (0, 0), 1.0)
    assert len(pts) == 1
    assert approx(pts[0][1], (0.0, 1.0))


def test_line_circle_miss():
    assert te.line_circle_params((-2, 2), (2, 2), (0, 0), 1.0) == []


# ---- 圆-圆交点 ----

def test_circle_circle_two_points():
    pts = te.circle_circle_params((0, 0), 2.0, (3, 0), 2.0)
    assert len(pts) == 2
    for x, _y in pts:
        assert abs(x - 1.5) < EPS        # 对称轴 x = 1.5


def test_circle_circle_tangent_one_point():
    pts = te.circle_circle_params((0, 0), 1.0, (2, 0), 1.0)
    assert len(pts) == 1
    assert approx(pts[0], (1.0, 0.0))


def test_circle_circle_no_intersection():
    assert te.circle_circle_params((0, 0), 1.0, (5, 0), 1.0) == []
    # 内含：大圆 r=2 覆盖小圆 r=1（圆心距 0.2 < 2-1）→ 无交点
    assert te.circle_circle_params((0, 0), 2.0, (0.2, 0), 1.0) == []


# ---- 弧交点收集 ----

def test_collect_arc_intersections_line_sweep_filter():
    # 四分之一弧 a1=0 -> a2=π/2；竖直线 x=0.5 与弧交点在扫角内
    arc = {"id": 1, "type": "arc", "center": [0, 0], "radius": 1.0,
           "a1": 0.0, "a2": math.pi / 2}
    line = {"id": 2, "type": "line", "p1": [0.5, -2], "p2": [0.5, 2]}
    cuts = te.collect_arc_intersections(arc, [line], exclude_id=1)
    assert len(cuts) == 1
    ang, eid = cuts[0]
    assert eid == 2
    assert abs(ang - math.acos(0.5)) < EPS   # 交点角 = π/3


def test_collect_arc_intersections_sweep_excluded():
    # 直线 x=-0.5 与整圆的两交点（±120°）都落在弧扫角 [0, π/2] 外
    arc = {"id": 1, "type": "arc", "center": [0, 0], "radius": 1.0,
           "a1": 0.0, "a2": math.pi / 2}
    line = {"id": 2, "type": "line", "p1": [-0.5, -2], "p2": [-0.5, 2]}
    assert te.collect_arc_intersections(arc, [line], exclude_id=1) == []


def test_collect_arc_intersections_arc_arc():
    # 两四分之一弧同圆心不同半径：无交；半径同但扫角互补 → 端点相接
    arc1 = {"id": 1, "type": "arc", "center": [0, 0], "radius": 1.0,
            "a1": 0.0, "a2": math.pi / 2}
    arc2 = {"id": 2, "type": "arc", "center": [0, 0], "radius": 2.0,
            "a1": 0.0, "a2": math.pi / 2}
    assert te.collect_arc_intersections(arc1, [arc2], 1) == []
    arc3 = {"id": 3, "type": "arc", "center": [0, 0], "radius": 1.0,
            "a1": math.pi / 2, "a2": math.pi}
    cuts = te.collect_arc_intersections(arc1, [arc3], 1)
    # 共享圆：整圆交点退化（同圆心同半径），无有效交点返回
    assert cuts == []


def test_collect_arc_intersections_crossing_arcs():
    # 同圆心同半径的两个互补弧扫角，整圆交点无数 → 端点相接不算；
    # 用不同圆心的两弧相交验证双向扫角过滤
    arc1 = {"id": 1, "type": "arc", "center": [0, 0], "radius": 1.0,
            "a1": 0.0, "a2": math.pi}         # 上半圆
    arc2 = {"id": 2, "type": "arc", "center": [1, 0], "radius": 1.0,
            "a1": math.pi / 2, "a2": 3 * math.pi / 2}  # 左半圆
    cuts = te.collect_arc_intersections(arc1, [arc2], 1)
    assert len(cuts) == 1
    ang, _eid = cuts[0]
    pt = (math.cos(ang), math.sin(ang))
    assert abs((pt[0] - 1) ** 2 + pt[1] ** 2 - 1.0) < EPS  # 在 arc2 圆上


# ---- trim_arc ----

def _arc_entities():
    return [
        {"id": 1, "type": "arc", "center": [0, 0], "radius": 1.0,
         "a1": 0.0, "a2": math.pi},
        {"id": 2, "type": "line", "p1": [0.5, -2], "p2": [0.5, 2]},
    ]


def test_trim_arc_click_lower_side():
    ents = _arc_entities()
    ok, *rest = te.trim_arc(ents, 1, (0.5, 0.2))   # 点击角 0.38（a1 侧段）
    assert ok
    changed_pos, new_a1, new_a2 = rest
    # 点击角 < 交点角（π/3）：点击在 [a1, 交点] 段 → 裁除该段，
    # 保留 [交点, a2]，a1 端被移到交点（与 trim_line 语义一致）
    assert changed_pos == 0
    assert abs(new_a1 - math.pi / 3) < EPS
    assert abs(new_a2 - math.pi) < EPS


def test_trim_arc_click_upper_side():
    ents = _arc_entities()
    ok, *rest = te.trim_arc(ents, 1, (0.5, 0.9))   # 点击角 1.06（交点→a2 段）
    assert ok
    changed_pos, new_a1, new_a2 = rest
    # 点击角 > 交点角（π/3）：裁除 [交点, a2]，保留 [a1, 交点]
    assert changed_pos == 1
    assert abs(new_a1 - 0.0) < EPS
    assert abs(new_a2 - math.pi / 3) < EPS


def test_trim_arc_no_intersection():
    ents = _arc_entities()[:1]                     # 只剩弧自己
    ok, reason = te.trim_arc(ents, 1, (0.5, 0.5))
    assert not ok and reason == "no-intersection"


def test_trim_arc_not_found():
    ok, reason = te.trim_arc(_arc_entities(), 99, (0, 0))
    assert not ok and reason == "not-found"


def test_trim_arc_endpoint_touch_not_boundary():
    # 直线穿过弧端点 (1, 0)：端点交点不算修剪边界 → no-intersection
    ents = [
        {"id": 1, "type": "arc", "center": [0, 0], "radius": 1.0,
         "a1": 0.0, "a2": math.pi},
        {"id": 2, "type": "line", "p1": [1, -2], "p2": [1, 2]},
    ]
    ok, reason = te.trim_arc(ents, 1, (0.99, 0.1))
    assert not ok and reason == "no-intersection"


# ---- extend_arc_result ----

def test_extend_arc_ccw_from_a2():
    arc = {"type": "arc", "center": [0, 0], "radius": 1.0,
           "a1": 0.0, "a2": math.pi / 2}
    # 鼠标方位角精确 π（在 a2 的 CCW 侧，较近端为 a2）
    res = te.extend_arc_result(arc, (-1.0, 0.0))
    assert res is not None
    pos, delta = res
    assert pos == 1
    assert abs(delta - (math.pi - math.pi / 2)) < EPS


def test_extend_arc_cw_from_a1():
    arc = {"type": "arc", "center": [0, 0], "radius": 1.0,
           "a1": 0.0, "a2": math.pi / 2}
    # 鼠标方位角精确 -π/2（a1 的 CW 侧，较近端为 a1）
    res = te.extend_arc_result(arc, (0.0, -1.0))
    assert res is not None
    pos, delta = res
    assert pos == 0
    assert abs(delta - math.pi / 2) < EPS


def test_extend_arc_inside_sweep_none():
    arc = {"type": "arc", "center": [0, 0], "radius": 1.0,
           "a1": 0.0, "a2": math.pi / 2}
    assert te.extend_arc_result(arc, (0.7071, 0.7071)) is None  # π/4 扫角内


def test_extend_arc_at_endpoint_none():
    arc = {"type": "arc", "center": [0, 0], "radius": 1.0,
           "a1": 0.0, "a2": math.pi / 2}
    assert te.extend_arc_result(arc, (1.0, 0.0)) is None   # 恰在 a1 端


def test_extend_arc_far_side_nearest_end():
    # 鼠标在 a1 顺时针远端（-3π/4）：d0 = 3π/4 < d1 = 2π - 3π/4 - π/2
    arc = {"type": "arc", "center": [0, 0], "radius": 1.0,
           "a1": 0.0, "a2": math.pi / 2}
    res = te.extend_arc_result(arc, (-0.7071, -0.7071))
    assert res is not None
    pos, delta = res
    assert pos == 0
    assert abs(delta - 3 * math.pi / 4) < EPS