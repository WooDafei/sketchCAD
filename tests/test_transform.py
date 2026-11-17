"""editing.transform 变换函数测试：移动 / 旋转 / 缩放副本语义。"""

import math

from sketchcad.editing import transform as tr


def line(eid=1, p1=(0, 0), p2=(3, 0)):
    return {"id": eid, "type": "line", "p1": list(p1), "p2": list(p2)}


def circle(eid=1, center=(0, 0), radius=2.0):
    return {"id": eid, "type": "circle", "center": list(center),
            "radius": radius}


def arc(eid=1, center=(0, 0), radius=2.0, a1=0.0, a2=1.5):
    return {"id": eid, "type": "arc", "center": list(center),
            "radius": radius, "a1": a1, "a2": a2}


def ellipse(eid=1, center=(0, 0), a=3.0, b=2.0, rot=0.5):
    return {"id": eid, "type": "ellipse", "center": list(center),
            "a": a, "b": b, "rot": rot}


# ---- moved_entity ----

def test_moved_line_circle_arc_ellipse():
    d = tr.moved_entity(line(p1=(0, 0), p2=(3, 0)), 10.0, -5.0)
    assert d["p1"] == [10.0, -5.0] and d["p2"] == [13.0, -5.0]
    d = tr.moved_entity(circle(center=(1, 1)), 2.0, 3.0)
    assert d["center"] == [3.0, 4.0] and d["radius"] == 2.0
    d = tr.moved_entity(arc(a1=0.5, a2=1.5), 1.0, 1.0)
    assert d["center"] == [1.0, 1.0]
    assert d["a1"] == 0.5 and d["a2"] == 1.5  # 平移扫角不变
    d = tr.moved_entity(ellipse(rot=0.5), 1.0, 2.0)
    assert d["center"] == [1.0, 2.0]
    assert d["a"] == 3.0 and d["b"] == 2.0 and d["rot"] == 0.5


def test_moved_unknown_type_returns_none():
    assert tr.moved_entity({"id": 1, "type": "spline"}, 1, 1) is None


def test_moved_preserves_construction():
    e = line()
    e["construction"] = True
    assert tr.moved_entity(e, 1, 1)["construction"] is True


# ---- rotated_entity ----

def test_rotated_line_90deg_around_origin():
    # (0,0)-(3,0) 绕原点转 90°（CCW）：p1 不动，p2 → (0, 3)
    d = tr.rotated_entity(line(p1=(0, 0), p2=(3, 0)),
                          (0.0, 0.0), math.pi / 2)
    assert math.isclose(d["p1"][0], 0.0, abs_tol=1e-9)
    assert math.isclose(d["p1"][1], 0.0, abs_tol=1e-9)
    assert math.isclose(d["p2"][0], 0.0, abs_tol=1e-9)
    assert math.isclose(d["p2"][1], 3.0, abs_tol=1e-9)


def test_rotated_circle_center_moves_radius_keeps():
    d = tr.rotated_entity(circle(center=(5, 0)), (0.0, 0.0),
                          math.pi / 2)
    assert math.isclose(d["center"][0], 0.0, abs_tol=1e-9)
    assert math.isclose(d["center"][1], 5.0, abs_tol=1e-9)
    assert d["radius"] == 2.0


def test_rotated_arc_sweep_rotates():
    # 弧自身朝向同步旋转（a1/a2 加同一角度）
    d = tr.rotated_entity(arc(a1=0.5, a2=1.5), (0, 0), 0.25)
    assert math.isclose(d["a1"], 0.75)
    assert math.isclose(d["a2"], 1.75)


def test_rotated_ellipse_rot_synchronizes():
    # 长轴方向 rot 同步旋转；center 位置旋转
    d = tr.rotated_entity(ellipse(center=(2, 0), rot=0.5),
                          (0.0, 0.0), math.pi / 2)
    assert math.isclose(d["rot"], 0.5 + math.pi / 2)
    assert math.isclose(d["center"][0], 0.0, abs_tol=1e-9)
    assert math.isclose(d["center"][1], 2.0, abs_tol=1e-9)
    assert d["a"] == 3.0 and d["b"] == 2.0


def test_rotated_unknown_type_returns_none():
    assert tr.rotated_entity({"id": 1, "type": "spline"},
                             (0, 0), 1.0) is None


# ---- scaled_entity ----

def test_scaled_line_from_center():
    # (0,0)-(3,0) 以 (0,0) 为基准放大 2 倍：(0,0)-(6,0)
    d = tr.scaled_entity(line(p1=(0, 0), p2=(3, 0)),
                         (0.0, 0.0), 2.0)
    assert d["p1"] == [0.0, 0.0] and d["p2"] == [6.0, 0.0]


def test_scaled_circle_radius_and_center():
    d = tr.scaled_entity(circle(center=(4, 0), radius=2.0),
                         (0.0, 0.0), 0.5)
    assert d["center"] == [2.0, 0.0]
    assert d["radius"] == 1.0


def test_scaled_arc_radius_scales_sweep_keeps():
    d = tr.scaled_entity(arc(center=(2, 0), radius=2.0,
                             a1=0.3, a2=1.2), (0, 0), 1.5)
    assert d["center"] == [3.0, 0.0]
    assert d["radius"] == 3.0
    assert d["a1"] == 0.3 and d["a2"] == 1.2


def test_scaled_ellipse_axes_scale_rot_keeps():
    d = tr.scaled_entity(ellipse(a=3.0, b=2.0, rot=0.5),
                         (0, 0), 2.0)
    assert d["a"] == 6.0 and d["b"] == 4.0 and d["rot"] == 0.5


def test_scaled_invalid_factor_returns_none():
    e = line()
    assert tr.scaled_entity(e, (0, 0), 0.0) is None
    assert tr.scaled_entity(e, (0, 0), -1.0) is None
    assert tr.scaled_entity(e, (0, 0), None) is None


def test_scaled_unknown_type_returns_none():
    assert tr.scaled_entity({"id": 1, "type": "spline"},
                            (0, 0), 2.0) is None


# ---- transform_batch / apply_geometry ----

def test_transform_batch_all_or_nothing():
    ents = [line(), line(eid=2)]
    out = tr.transform_batch(ents, lambda e: tr.moved_entity(e, 1, 1))
    assert len(out) == 2
    ents_bad = [line(), {"id": 2, "type": "spline"}]
    assert tr.transform_batch(ents_bad,
                              lambda e: tr.moved_entity(e, 1, 1)) is None


def test_apply_geometry_preserves_identity():
    orig = line(eid=7)
    orig["construction"] = True
    new = tr.moved_entity(orig, 1.0, 2.0)
    new["id"] = 999  # 副本不携带 id；apply 时身份字段不覆盖
    tr.apply_geometry(orig, new)
    assert orig["p1"] == [1.0, 2.0]
    assert orig["id"] == 7 and orig["construction"] is True
    assert orig["type"] == "line"