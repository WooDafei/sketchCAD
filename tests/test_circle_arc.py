"""圆/弧实体支持测试（Phase 3 混合草图）：geom 几何、参数化、残差、
数据层校验与 LM 求解收敛。

弧方向约定（core/geom.py）：弧从 a1 逆时针（CCW）扫到 a2。
"""

import math

import pytest

from sketchcad.core import geom
from sketchcad.core.datum import Datum
from sketchcad.core.sketch_data import SketchData
from sketchcad.solver import apply as solver_apply
from sketchcad.solver import dof as solver_dof
from sketchcad.solver.constraints import (
    EQ_COUNTS,
    collect_problems,
    equations_count,
)
from sketchcad.solver.params import ParamLayout, UnknownEntityTypeError


# ---- geom：三点定圆/弧 ----

def test_circle_from_3_points():
    res = geom.circle_from_3_points((2, 0), (0, 2), (-2, 0))
    assert res is not None
    (cx, cy), r = res
    assert abs(cx) < 1e-9 and abs(cy) < 1e-9
    assert abs(r - 2.0) < 1e-9


def test_circle_from_3_points_collinear_none():
    assert geom.circle_from_3_points((0, 0), (1, 1), (2, 2)) is None


def test_circle_from_3_points_degenerate_none():
    assert geom.circle_from_3_points((1, 1), (1, 1), (1, 1)) is None


def test_arc_from_3_points_ccw_direction_kept():
    # p1=(2,0) -> p2=(0,2) -> p3=(-2,0)：p2 在 CCW 扫角 [0, π] 内
    res = geom.arc_from_3_points((2, 0), (0, 2), (-2, 0))
    assert res is not None
    (cx, cy), r, a1, a2 = res
    assert abs(cx) < 1e-9 and abs(cy) < 1e-9
    assert abs(r - 2.0) < 1e-9
    assert abs(a1 - 0.0) < 1e-9
    assert abs(a2 - math.pi) < 1e-9


def test_arc_from_3_points_cw_swaps_endpoints():
    # p1=(-2,0) -> p2=(0,2) -> p3=(2,0)：p2 不在 CCW [π, 2π) 内，
    # 交换后弧从 0（p3=(2,0) 端）CCW 到 π（p1 端）
    res = geom.arc_from_3_points((-2, 0), (0, 2), (2, 0))
    assert res is not None
    _center, r, a1, a2 = res
    assert abs(r - 2.0) < 1e-9
    assert abs(a1 - 0.0) < 1e-9
    assert abs(a2 - math.pi) < 1e-9


def test_arc_from_3_points_collinear_none():
    assert geom.arc_from_3_points((0, 0), (1, 1), (2, 2)) is None


def test_arc_points_full_circle_closed():
    pts = geom.arc_points((0, 0), 1.0, 0.0, geom.TWO_PI)
    # 首尾重合闭合；圆周长度近似 2πr
    assert abs(pts[0][0] - pts[-1][0]) < 1e-9
    assert abs(pts[0][1] - pts[-1][1]) < 1e-9


def test_point_arc_distance_inside_and_outside_sweep():
    a1, a2 = 0.0, math.pi
    # 扫角内：径向距离
    assert abs(geom.point_arc_distance(
        (0, 3), (0, 0), 2.0, a1, a2) - 1.0) < 1e-9
    # 扫角外：取较近端点 (2, 0) 距离
    assert abs(geom.point_arc_distance(
        (5, 0), (0, 0), 2.0, a1, a2) - 3.0) < 1e-9


# ---- params：参数化 ----

def test_layout_dims():
    ents = [
        {"id": 1, "type": "line", "p1": [0, 0], "p2": [1, 1]},
        {"id": 2, "type": "circle", "center": [3, 3], "radius": 2},
        {"id": 3, "type": "arc", "center": [5, 5], "radius": 1,
         "a1": 0.1, "a2": 1.2},
    ]
    layout = ParamLayout(ents)
    assert layout.size == 4 + 3 + 5
    assert layout.types == {1: "line", 2: "circle", 3: "arc"}


def test_layout_unknown_type_raises():
    with pytest.raises(UnknownEntityTypeError):
        ParamLayout([{"id": 1, "type": "spline"}])


def test_pack_apply_roundtrip_curve_entities():
    ents = [
        {"id": 2, "type": "circle", "center": [3, 4], "radius": 2.5},
        {"id": 3, "type": "arc", "center": [5, 6], "radius": 1.5,
         "a1": 0.25, "a2": 2.75},
    ]
    layout = ParamLayout(ents)
    x = layout.pack(ents)
    assert x[0] == 3.0 and x[1] == 4.0 and x[2] == 2.5
    assert x[3] == 5.0 and x[4] == 6.0 and x[5] == 1.5
    assert x[6] == 0.25 and x[7] == 2.75
    ents[0]["center"] = [0, 0]
    ents[0]["radius"] = 0.0
    ents[1]["a1"] = ents[1]["a2"] = 0.0
    layout.apply(x, ents)
    assert ents[0]["center"] == [3.0, 4.0]
    assert ents[0]["radius"] == 2.5
    assert ents[1]["center"] == [5.0, 6.0]
    assert ents[1]["radius"] == 1.5
    assert ents[1]["a1"] == 0.25 and ents[1]["a2"] == 2.75


# ---- constraints：残差与校验 ----

def test_radius_residual_zero():
    ents = [{"id": 1, "type": "circle", "center": [0, 0], "radius": 3}]
    layout = ParamLayout(ents)
    con = {"id": 10, "type": "radius", "targets": [1], "value": 3.0}
    x = layout.pack(ents)
    from sketchcad.solver.constraints import RESIDUALS
    assert abs(RESIDUALS["radius"](x, layout, con)[0]) < 1e-12


def test_concentric_residual_zero():
    ents = [
        {"id": 1, "type": "circle", "center": [2, 3], "radius": 1},
        {"id": 2, "type": "arc", "center": [2, 3], "radius": 4,
         "a1": 0.2, "a2": 1.0},
    ]
    layout = ParamLayout(ents)
    con = {"id": 10, "type": "concentric", "targets": [1, 2]}
    x = layout.pack(ents)
    from sketchcad.solver.constraints import RESIDUALS
    r = RESIDUALS["concentric"](x, layout, con)
    assert abs(r[0]) < 1e-12 and abs(r[1]) < 1e-12


def test_tangent_residual_zero_and_sign():
    # line (0,0)-(10,0)，circle (5,3) r=3：恰在上方相切
    ents = [
        {"id": 1, "type": "line", "p1": [0, 0], "p2": [10, 0]},
        {"id": 2, "type": "circle", "center": [5, 3], "radius": 3},
    ]
    layout = ParamLayout(ents)
    con = {"id": 10, "type": "tangent", "targets": [1, 2]}
    x = layout.pack(ents)
    from sketchcad.solver.constraints import RESIDUALS
    assert abs(RESIDUALS["tangent"](x, layout, con)[0]) < 1e-9
    # 上移圆心 → dist² - r² > 0
    ents[1]["center"] = [5, 5]
    x = layout.pack(ents)
    assert RESIDUALS["tangent"](x, layout, con)[0] > 0


def test_arc_endpoint_coincident_residual_zero():
    # arc 端点由 center/r/angle 现算：pos0 = (2cos0.3, 2sin0.3)
    ents = [
        {"id": 1, "type": "line", "p1": [2 * math.cos(0.3),
                                         2 * math.sin(0.3)],
         "p2": [6, 0]},
        {"id": 2, "type": "arc", "center": [0, 0], "radius": 2,
         "a1": 0.3, "a2": 1.5},
    ]
    layout = ParamLayout(ents)
    con = {"id": 10, "type": "coincident", "targets": [1, 2],
           "endpoints": [0, 0]}
    x = layout.pack(ents)
    from sketchcad.solver.constraints import RESIDUALS
    r = RESIDUALS["coincident"](x, layout, con)
    assert abs(r[0]) < 1e-12 and abs(r[1]) < 1e-12


def test_eq_counts_curve_constraints():
    assert EQ_COUNTS["radius"] == 1
    assert EQ_COUNTS["concentric"] == 2
    assert EQ_COUNTS["tangent"] == 1
    ents = [{"id": 1, "type": "circle", "center": [0, 0], "radius": 1}]
    cons = [
        {"id": 10, "type": "radius", "targets": [1], "value": 2.0,
         "enabled": True},
        {"id": 11, "type": "concentric", "targets": [1, 1],
         "enabled": False},  # 禁用不计入
    ]
    assert equations_count(cons) == 1


def test_collect_problems_type_validation():
    ents = [
        {"id": 1, "type": "line", "p1": [0, 0], "p2": [1, 1]},
        {"id": 2, "type": "circle", "center": [3, 3], "radius": 1},
    ]
    # radius 作用于 line → 报错
    problems = collect_problems(ents, [
        {"id": 10, "type": "radius", "targets": [1], "value": 2.0}])
    assert any("radius" in p for p in problems)
    # radius 缺 value → 报错
    problems = collect_problems(ents, [
        {"id": 10, "type": "radius", "targets": [2]}])
    assert any("value" in p for p in problems)
    # tangent 须 [line, circle/arc]：顺序颠倒报错
    problems = collect_problems(ents, [
        {"id": 10, "type": "tangent", "targets": [2, 1]}])
    assert any("tangent" in p for p in problems)
    # concentric 须两个圆类实体：line 参与报错
    problems = collect_problems(ents, [
        {"id": 10, "type": "concentric", "targets": [1, 2]}])
    assert any("concentric" in p for p in problems)
    # coincident 一端是 circle（无端点）报错
    problems = collect_problems(ents, [
        {"id": 10, "type": "coincident", "targets": [1, 2],
         "endpoints": [0, 0]}])
    assert any("coincident" in p for p in problems)


# ---- 求解收敛（全链路 apply.solve_and_apply）----

def make_sketch(entities, constraints):
    s = SketchData(Datum((0, 0, 0), (0, 0, 1)))
    s.entities = entities
    s.constraints = constraints
    return s


def test_solve_radius_drive_circle():
    ents = [{"id": 1, "type": "circle", "center": [0, 0], "radius": 1.0}]
    cons = [{"id": 10, "type": "radius", "targets": [1], "value": 5.0}]
    ok, _msg = solver_apply.solve_and_apply(make_sketch(ents, cons))
    assert ok
    assert abs(ents[0]["radius"] - 5.0) < 1e-6


def test_solve_tangent_line_circle():
    # 7 参数 1 方程（欠约束），LM 最小范数步允许 line 与 circle 同时移动，
    # 故断言语义为"圆心到 line 所在直线的距离 = 半径"（残差满足）。
    ents = [
        {"id": 1, "type": "line", "p1": [0, 0], "p2": [10, 0]},
        {"id": 2, "type": "circle", "center": [5, 5], "radius": 3.0},
    ]
    cons = [{"id": 10, "type": "tangent", "targets": [1, 2]}]
    ok, _msg = solver_apply.solve_and_apply(make_sketch(ents, cons))
    assert ok
    p1, p2 = ents[0]["p1"], ents[0]["p2"]
    cx, cy = ents[1]["center"]
    r = ents[1]["radius"]
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    norm = (dx * dx + dy * dy) ** 0.5
    dist = abs((cx - p1[0]) * dy - (cy - p1[1]) * dx) / norm
    assert abs(dist - r) < 1e-4


def test_solve_arc_endpoint_glued_to_line():
    # 弧 a1 端固接到线 p1：9 参数 2 方程（欠约束），LM 最小范数步允许
    # line 端点也移动，断言语义为"arc 端点 = line p1 当前值"（残差满足）。
    ents = [
        {"id": 1, "type": "line", "p1": [2, 0], "p2": [6, 0]},
        {"id": 2, "type": "arc", "center": [0, 0], "radius": 2.0,
         "a1": 0.3, "a2": 1.5},
    ]
    cons = [{"id": 10, "type": "coincident", "targets": [1, 2],
             "endpoints": [0, 0]}]
    ok, _msg = solver_apply.solve_and_apply(make_sketch(ents, cons))
    assert ok
    e = ents[1]
    px = e["center"][0] + e["radius"] * math.cos(e["a1"])
    py = e["center"][1] + e["radius"] * math.sin(e["a1"])
    lp1 = ents[0]["p1"]
    assert abs(px - lp1[0]) < 1e-4 and abs(py - lp1[1]) < 1e-4


def test_dof_report_circle_radius():
    # circle 3 参数，radius 1 方程 → dof = 2（圆心自由）
    ents = [{"id": 1, "type": "circle", "center": [0, 0], "radius": 1.0}]
    cons = [{"id": 10, "type": "radius", "targets": [1], "value": 5.0}]
    rep = solver_dof.analyze(ents, cons)
    assert rep.n_params == 3
    assert rep.n_equations == 1
    assert rep.dof == 2
    assert rep.status == solver_dof.STATUS_UNDER


# ---- 数据模型：circle/arc JSON 往返 ----

def test_sketch_data_circle_arc_roundtrip():
    s = SketchData(Datum((0, 0, 0), (0, 0, 1)))
    s.add_line((0, 0), (4, 0))
    s.add_circle((8, 8), 2.5)
    s.add_arc((20, 20), 3.0, 0.25, 2.75)
    s.add_constraint("radius", [2], value=2.5)
    s.add_constraint("tangent", [1, 3])
    s2 = SketchData.from_dict(__import__("json").loads(s.to_json()))
    assert len(s2.entities) == 3
    circle = next(e for e in s2.entities if e["type"] == "circle")
    assert circle["center"] == [8.0, 8.0]
    assert circle["radius"] == 2.5
    arc = next(e for e in s2.entities if e["type"] == "arc")
    assert arc["a1"] == 0.25 and arc["a2"] == 2.75
    # 实体与约束共用 id 空间：实体 1-3 + 约束 4-5 → 下一 id 取最大 +1
    assert s2._next_id() == 6


def test_sketch_data_arc_endpoints_helper():
    from sketchcad.core.sketch_data import arc_endpoints
    e = {"type": "arc", "center": [0, 0], "radius": 2.0,
         "a1": 0.0, "a2": math.pi}
    p0, p1 = arc_endpoints(e)
    assert abs(p0[0] - 2.0) < 1e-9 and abs(p0[1]) < 1e-9
    assert abs(p1[0] + 2.0) < 1e-9 and abs(p1[1]) < 1e-9