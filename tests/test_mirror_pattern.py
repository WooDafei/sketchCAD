"""Mirror/Pattern 纯几何测试（SketchCAD.md §6）。

镜像对称性（轴上点不动、垂距等值反向、斜轴）、arc 端点对调与
扫角不变、线性/圆形阵列的副本数与步距递推、退化输入保护、
add_entity_copy 写入 SketchData 的 id 分配与 construction 保留。
"""

import math

from sketchcad.core.datum import Datum
from sketchcad.core.sketch_data import SketchData, arc_endpoints
from sketchcad.editing import mirror_pattern as mp

EPS = 1e-9


def approx(a, b, eps=EPS):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 < eps ** 2


# ---- 镜像点 ----

def test_mirror_point_on_axis_unchanged():
    assert mp.mirror_point((3.0, 0.0), (0.0, 0.0), (1.0, 0.0)) == (3.0, 0.0)


def test_mirror_point_perpendicular_flip():
    assert approx(mp.mirror_point((1.0, 1.0), (0.0, 0.0), (1.0, 0.0)),
                  (1.0, -1.0))


def test_mirror_point_diagonal_axis():
    # 关于 45° 线（过原点方向 (1,1)）镜像：(1,0) -> (0,1)
    assert approx(mp.mirror_point((1.0, 0.0), (0.0, 0.0), (1.0, 1.0)),
                  (0.0, 1.0))


def test_mirror_point_offset_axis():
    # 轴不过原点：线 x = 2（(2,0)->(2,1)），(0,0) -> (4,0)
    assert approx(mp.mirror_point((0.0, 0.0), (2.0, 0.0), (2.0, 1.0)),
                  (4.0, 0.0))


def test_mirror_point_zero_axis_none():
    assert mp.mirror_point((1.0, 1.0), (2.0, 2.0), (2.0, 2.0)) is None


# ---- 镜像实体 ----

def test_mirror_line():
    m = mp.mirror_entity(
        {"type": "line", "p1": [0, 0], "p2": [1, 0]},
        (0, 0), (0, 1))
    assert m["type"] == "line"
    assert approx(tuple(m["p1"]), (0.0, 0.0))
    assert approx(tuple(m["p2"]), (-1.0, 0.0))


def test_mirror_line_on_axis_self():
    m = mp.mirror_entity(
        {"type": "line", "p1": [0, 0], "p2": [2, 2]},
        (0, 0), (1, 1))
    assert approx(tuple(m["p1"]), (0.0, 0.0))
    assert approx(tuple(m["p2"]), (2.0, 2.0))


def test_mirror_circle_center_radius():
    m = mp.mirror_entity(
        {"type": "circle", "center": [3, 1], "radius": 2.0},
        (0, 0), (1, 0))
    assert m["radius"] == 2.0
    assert approx(tuple(m["center"]), (3.0, -1.0))


def test_mirror_construction_preserved():
    m = mp.mirror_entity(
        {"type": "circle", "center": [3, 1], "radius": 2.0,
         "construction": True},
        (0, 0), (1, 0))
    assert m["construction"] is True


def test_mirror_arc_endpoints_swap_sweep_kept():
    e = {"type": "arc", "center": [0, 0], "radius": 1.0,
         "a1": 0.0, "a2": math.pi / 2.0}
    m = mp.mirror_entity(e, (0, 0), (1, 0))
    assert m["radius"] == 1.0
    # 扫角不变
    assert abs((m["a2"] - m["a1"]) - math.pi / 2.0) < EPS
    # 端点身份对调：新 pos0 = 原终点镜像，新 pos1 = 原起点镜像
    p0, p1 = arc_endpoints(m)
    assert approx(p0, (0.0, -1.0))
    assert approx(p1, (1.0, 0.0))


def test_mirror_arc_diagonal_axis_geometry():
    # 关于 y 轴镜像：a1=π/6, a2=π/3 的弧圆心 (1,0) → (-1,0)
    e = {"type": "arc", "center": [1, 0], "radius": 2.0,
         "a1": math.pi / 6, "a2": math.pi / 3}
    m = mp.mirror_entity(e, (0, 0), (0, 1))
    assert approx(tuple(m["center"]), (-1.0, 0.0))
    assert abs((m["a2"] - m["a1"]) - (math.pi / 3 - math.pi / 6)) < EPS
    # 端点几何：新端点集合 = 原端点的镜像（x 取反）
    p0, p1 = arc_endpoints(e)
    m0, m1 = arc_endpoints(m)
    assert (approx(m0, (p0[0] * -1, p0[1]))
            or approx(m0, (p1[0] * -1, p1[1])))
    assert (approx(m1, (p0[0] * -1, p0[1]))
            or approx(m1, (p1[0] * -1, p1[1])))


def test_mirror_entity_zero_axis_none():
    assert mp.mirror_entity(
        {"type": "line", "p1": [0, 0], "p2": [1, 0]},
        (5, 5), (5, 5)) is None


def test_mirror_entities_batch_and_fail():
    ents = [
        {"type": "line", "p1": [0, 0], "p2": [1, 0]},
        {"type": "circle", "center": [2, 2], "radius": 1.0},
    ]
    out = mp.mirror_entities(ents, (0, 0), (0, 1))
    assert len(out) == 2 and out[0]["type"] == "line"
    # 零长轴整体失败
    assert mp.mirror_entities(ents, (1, 1), (1, 1)) is None


# ---- 平移 / 旋转 ----

def test_translate_entity_line():
    t = mp.translate_entity(
        {"type": "line", "p1": [0, 0], "p2": [1, 2]}, 10.0, -1.0)
    assert t["p1"] == [10.0, -1.0] and t["p2"] == [11.0, 1.0]


def test_translate_entity_arc_sweep_kept():
    t = mp.translate_entity(
        {"type": "arc", "center": [1, 1], "radius": 2.0,
         "a1": 0.3, "a2": 1.2}, 5.0, 5.0)
    assert t["center"] == [6.0, 6.0]
    assert t["a1"] == 0.3 and t["a2"] == 1.2


def test_rotate_point_quarter():
    assert approx(mp.rotate_point((1.0, 0.0), (0.0, 0.0), math.pi / 2),
                  (0.0, 1.0))
    # 绕非原点中心转 180°：(1,0) 绕 (2,0) -> (3,0)
    assert approx(mp.rotate_point((1.0, 0.0), (2.0, 0.0), math.pi),
                  (3.0, 0.0))


def test_rotate_entity_arc():
    m = mp.rotate_entity(
        {"type": "arc", "center": [0, 0], "radius": 1.0,
         "a1": 0.0, "a2": 1.0}, (2.0, 0.0), math.pi / 2)
    # 圆心 (0,0) 相对中心 (2,0) 的向量 (-2,0) 旋 90° CCW -> (0,-2)
    assert approx(tuple(m["center"]), (2.0, -2.0))
    assert abs(m["a1"] - math.pi / 2) < EPS
    assert abs(m["a2"] - (1.0 + math.pi / 2)) < EPS


def test_rotate_entity_circle():
    m = mp.rotate_entity(
        {"type": "circle", "center": [3, 0], "radius": 1.0},
        (0, 0), math.pi / 2)
    assert approx(tuple(m["center"]), (0.0, 3.0))
    assert m["radius"] == 1.0


# ---- 线性阵列 ----

def test_linear_pattern_count_and_positions():
    e = {"type": "line", "p1": [0, 0], "p2": [1, 0]}
    copies = mp.linear_pattern(e, (1, 0), 2.0, 3)
    assert len(copies) == 2
    assert approx(tuple(copies[0]["p1"]), (2.0, 0.0))
    assert approx(tuple(copies[0]["p2"]), (3.0, 0.0))
    assert approx(tuple(copies[1]["p1"]), (4.0, 0.0))


def test_linear_pattern_diagonal_direction_normalized():
    e = {"type": "circle", "center": [0, 0], "radius": 1.0}
    copies = mp.linear_pattern(e, (3, 0), 2.0, 2)
    # 方向 (3,0) 归一化后仍沿 +x，步距不受方向向量长度影响
    assert approx(tuple(copies[0]["center"]), (2.0, 0.0))


def test_linear_pattern_negative_step():
    e = {"type": "line", "p1": [0, 0], "p2": [1, 0]}
    copies = mp.linear_pattern(e, (1, 0), -1.5, 2)
    assert approx(tuple(copies[0]["p1"]), (-1.5, 0.0))


def test_linear_pattern_invalid_returns_empty():
    e = {"type": "line", "p1": [0, 0], "p2": [1, 0]}
    assert mp.linear_pattern(e, (1, 0), 2.0, 1) == []
    assert mp.linear_pattern(e, (1, 0), 0.0, 3) == []
    assert mp.linear_pattern(e, (0, 0), 2.0, 3) == []


# ---- 圆形阵列 ----

def test_circular_pattern_count_and_rotation():
    e = {"type": "line", "p1": [1, 0], "p2": [2, 0]}
    copies = mp.circular_pattern(e, (0, 0), math.pi / 2, 3)
    assert len(copies) == 2
    assert approx(tuple(copies[0]["p1"]), (0.0, 1.0))
    assert approx(tuple(copies[1]["p1"]), (-1.0, 0.0))


def test_circular_pattern_negative_angle():
    e = {"type": "circle", "center": [2, 0], "radius": 1.0}
    copies = mp.circular_pattern(e, (0, 0), -math.pi / 2, 2)
    assert approx(tuple(copies[0]["center"]), (0.0, -2.0))


def test_circular_pattern_invalid_returns_empty():
    e = {"type": "line", "p1": [0, 0], "p2": [1, 0]}
    assert mp.circular_pattern(e, (0, 0), 0.0, 3) == []
    assert mp.circular_pattern(e, (0, 0), 0.5, 1) == []


# ---- 写入 SketchData ----

def _sketch():
    return SketchData(Datum((0.0, 0.0, 0.0), (0.0, 0.0, 1.0)))


def test_add_entity_copy_line_and_ids():
    s = _sketch()
    id1 = mp.add_entity_copy(
        s, {"type": "line", "p1": [0, 0], "p2": [1, 0]})
    id2 = mp.add_entity_copy(
        s, {"type": "circle", "center": [2, 2], "radius": 1.0})
    assert id1 == 1 and id2 == 2
    assert len(s.entities) == 2
    assert s.entities[0]["p1"] == [0, 0]
    assert s.entities[1]["construction"] is False


def test_add_entity_copy_arc_and_construction():
    s = _sketch()
    eid = mp.add_entity_copy(
        s, {"type": "arc", "center": [0, 0], "radius": 1.0,
            "a1": 0.5, "a2": 1.5, "construction": True})
    ent = next(e for e in s.entities if e["id"] == eid)
    assert ent["type"] == "arc" and ent["construction"] is True
    assert ent["a1"] == 0.5 and ent["a2"] == 1.5


def test_add_entity_copy_unknown_type_none():
    s = _sketch()
    assert mp.add_entity_copy(s, {"type": "point", "x": 0}) is None
    assert len(s.entities) == 0