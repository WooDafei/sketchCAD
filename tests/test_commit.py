"""提交段序列与快照 key 测试（Phase 3 替换式提交）。

entity_segments 是 mesh 提交与提交快照的共同来源：GPU 预览
（geom.arc_points）与提交段序列同密度，保证快照与视口所见一致。
"""

import math

from sketchcad.core.geom import ARC_MAX_STEP, TWO_PI, arc_point
from sketchcad.editing.commit import entity_segments, seg_key


def line(eid, p1, p2):
    return {"id": eid, "type": "line", "p1": list(p1), "p2": list(p2)}


def circle(eid, center, radius):
    return {"id": eid, "type": "circle",
            "center": list(center), "radius": radius}


def arc(eid, center, radius, a1, a2):
    return {"id": eid, "type": "arc", "center": list(center),
            "radius": radius, "a1": a1, "a2": a2}


def test_entity_segments_line():
    segs = entity_segments(line(1, (0, 0), (4, 5)))
    assert segs == [((0.0, 0.0), (4.0, 5.0))]


def test_entity_segments_circle_closed():
    segs = entity_segments(circle(1, (0, 0), 2.0))
    # 全圆离散：段数 = ceil(2π/步长)（点数-1，首尾点重合）
    expected_segs = max(1, int(math.ceil(TWO_PI / ARC_MAX_STEP)))
    assert len(segs) == expected_segs
    # 相邻段端点连续：前段终点 == 后段起点
    for (pa, pb), (qa, _qb) in zip(segs, segs[1:]):
        assert pb == qa
    # 首尾闭合（末段终点回到首段起点）
    first, last = segs[0][0], segs[-1][1]
    assert abs(first[0] - last[0]) < 1e-9
    assert abs(first[1] - last[1]) < 1e-9
    # 所有段端点都在半径 2 的圆周上
    for a, b in segs:
        for p in (a, b):
            assert abs(math.hypot(p[0], p[1]) - 2.0) < 1e-6


def test_entity_segments_arc_endpoints():
    a1, a2 = 0.3, 1.5
    segs = entity_segments(arc(1, (5, 5), 2.0, a1, a2))
    assert segs
    # 首段起点 = a1 端点，末段终点 = a2 端点（世界原点 (0,0) 基准）
    start = (segs[0][0][0] - 5.0, segs[0][0][1] - 5.0)
    end = (segs[-1][1][0] - 5.0, segs[-1][1][1] - 5.0)
    ref1 = arc_point((0, 0), 2.0, a1)
    ref2 = arc_point((0, 0), 2.0, a2)
    assert abs(start[0] - ref1[0]) < 1e-9
    assert abs(start[1] - ref1[1]) < 1e-9
    assert abs(end[0] - ref2[0]) < 1e-9
    assert abs(end[1] - ref2[1]) < 1e-9


def test_entity_segments_unknown_type_empty():
    assert entity_segments({"id": 1, "type": "spline"}) == []


def test_seg_key_order_independent():
    a = (1.23456789, 2.34567891, 0.0)
    b = (4.56789123, 5.67891234, 0.0)
    assert seg_key(a, b) == seg_key(b, a)


def test_seg_key_round6_merge():
    # 超过 round6 精度的差异归并（与顶点缓存/端点匹配同语义）
    a = (0.0000001, 0.0, 0.0)
    a2 = (0.0000002, 0.0, 0.0)
    b = (1.0, 1.0, 0.0)
    assert seg_key(a, b) == seg_key(a2, b)


def test_seg_key_2d_and_3d_compat():
    # 快照段为 3D 局部坐标，mesh 顶点 co 也是 3D；2D 输入按 z=0 处理
    assert seg_key((0, 0), (1, 1)) == seg_key((0, 0, 0.0), (1, 1, 0.0))