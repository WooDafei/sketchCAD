"""drawing.picking 测试：点到线段距离与拾取选择。"""

from sketchcad.drawing import picking


def line(eid, p1, p2):
    return {"id": eid, "type": "line", "p1": list(p1), "p2": list(p2)}


def test_point_segment_distance_foot_inside():
    # 垂足在段内：p=(2.5,1) 到 (0,0)-(5,0) 距离即 |1|
    assert abs(picking.point_segment_distance(
        (2.5, 1.0), (0, 0), (5, 0)) - 1.0) < 1e-9


def test_point_segment_distance_foot_beyond_end():
    # 垂足在延长线上：取最近端点 (0,0)，距离即 3
    assert abs(picking.point_segment_distance(
        (-3.0, 0.0), (0, 0), (5, 0)) - 3.0) < 1e-9


def test_point_segment_distance_on_endpoint():
    assert picking.point_segment_distance((0, 0), (0, 0), (5, 0)) < 1e-12


def test_point_segment_distance_degenerate_segment():
    # 零长段退化为点距：p=(4,5) 到 (1,1) → sqrt(9+16)=5
    assert abs(picking.point_segment_distance(
        (4, 5), (1, 1), (1, 1)) - 5.0) < 1e-9


def test_pick_segment_nearest_wins():
    ents = [line(1, (0, 0), (10, 0)), line(2, (0, 5), (10, 5))]
    # 点 (5, 0.6) 距线 1 为 0.6、距线 2 为 4.4 → 命中线 1
    assert picking.pick_segment((5, 0.6), ents, 1.0) == 1
    # 点 (5, 4.4) 距线 2 更近 → 命中线 2
    assert picking.pick_segment((5, 4.4), ents, 1.0) == 2


def test_pick_segment_outside_tol():
    ents = [line(1, (0, 0), (10, 0))]
    assert picking.pick_segment((5, 2.0), ents, 1.0) is None


def test_pick_segment_boundary_inclusive():
    ents = [line(1, (0, 0), (10, 0))]
    # 恰好等于容差视为命中（<=，与捕捉/推断语义一致）
    assert picking.pick_segment((5, 1.0), ents, 1.0) == 1


def test_pick_segment_skips_non_line_entities():
    ents = [line(1, (0, 0), (10, 0)),
            {"id": 2, "type": "circle", "center": [5, 0], "radius": 1}]
    assert picking.pick_segment((5, 0.5), ents, 1.0) == 1

def test_pick_endpoint_nearest_wins():
    ents = [line(1, (0, 0), (3, 0)), line(2, (3, 0), (3, 2))]
    # 点 (3.1, 0.1)：距 (3,0) 端点最近——该点同时是线 1 的 p2 与线 2 的 p1，
    # 先遍历者命中 → 线 1 pos 1
    assert picking.pick_endpoint((3.1, 0.1), ents, 0.5) == (1, 1)
    # 明确靠近线 2 的 p2
    assert picking.pick_endpoint((2.9, 1.9), ents, 0.5) == (2, 1)


def test_pick_endpoint_outside_tol():
    ents = [line(1, (0, 0), (3, 0))]
    assert picking.pick_endpoint((3.0, 2.0), ents, 1.0) is None


def test_pick_endpoint_boundary_inclusive():
    ents = [line(1, (0, 0), (3, 0))]
    assert picking.pick_endpoint((3.0, 0.5), ents, 0.5) == (1, 1)

def circle(eid, center, radius):
    return {"id": eid, "type": "circle",
            "center": list(center), "radius": radius}


def arc(eid, center, radius, a1, a2):
    return {"id": eid, "type": "arc", "center": list(center),
            "radius": radius, "a1": a1, "a2": a2}


def test_pick_entity_circle_radial_distance():
    ents = [circle(1, (0, 0), 3.0)]
    # 圆周上：|4 - 3| = 1（径向距离，非到圆心距离）
    assert picking.pick_entity((4, 0), ents, 1.0) == 1
    assert picking.pick_entity((4.0, 0.0), ents, 0.9) is None


def test_pick_entity_circle_nearest_wins():
    ents = [circle(1, (0, 0), 3.0), circle(2, (8, 0), 3.0)]
    # 点 (4, 0)：距圆 1 圆周 1.0，距圆 2 圆周 1.0 → 先遍历者命中
    assert picking.pick_entity((4, 0), ents, 1.5) == 1
    # 点 (4.5, 0)：距圆 2 圆周 0.5 → 命中圆 2
    assert picking.pick_entity((4.5, 0), ents, 1.5) == 2


def test_pick_entity_arc_inside_sweep():
    import math
    ents = [arc(1, (0, 0), 2.0, 0.0, math.pi)]
    # 点 (0, 3)：角度 π/2 在扫角 [0, π] 内，距离 |3 - 2| = 1
    assert picking.pick_entity((0, 3), ents, 1.0) == 1


def test_pick_entity_arc_outside_sweep_takes_endpoint():
    import math
    ents = [arc(1, (0, 0), 2.0, 0.0, math.pi)]
    # 点 (3, -0.5)：角度 < 0 不在扫角内 → 取较近端点 (2, 0) 的距离
    d = picking.point_segment_distance((3, -0.5), (2, 0), (2, 0))
    assert abs(d - 1.118033988749895) < 1e-9  # sqrt(1 + 0.25)
    assert picking.pick_entity((3, -0.5), ents, 1.2) == 1
    assert picking.pick_entity((3, -0.5), ents, 1.0) is None


def test_pick_endpoint_arc_endpoints():
    import math
    ents = [arc(1, (0, 0), 2.0, 0.0, math.pi)]
    # a1 端点 (2, 0) → (1, 0)；a2 端点 (-2, 0) → (1, 1)
    assert picking.pick_endpoint((2.1, 0.1), ents, 0.5) == (1, 0)
    assert picking.pick_endpoint((-2.1, -0.1), ents, 0.5) == (1, 1)


def test_pick_endpoint_circle_center_pos2():
    ents = [circle(1, (5, 5), 3.0)]
    # 圆心命中 pos=2（POS_CENTER）；圆周附近不是端点
    assert picking.pick_endpoint((5.2, 4.9), ents, 0.5) == (1, 2)
    assert picking.pick_endpoint((8.0, 5.0), ents, 0.5) is None


def test_pick_endpoint_line_and_arc_shared_point():
    import math
    ents = [line(1, (0, 0), (4, 0)), arc(2, (2, 0), 2.0, 0.0, math.pi)]
    # 共享端点 (4, 0)：线 1 p2 与弧 2 a1 端 → 先遍历者命中
    assert picking.pick_endpoint((4.05, 0.05), ents, 0.5) == (1, 1)