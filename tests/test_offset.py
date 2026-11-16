"""偏移算法测试（SketchCAD.md v2 §6 Offset，editing/offset.py）。

偏移侧向由鼠标相对轮廓内外决定（signed area 定向 + 射线法）；
相邻段偏移线求交 miter 连接；圆/弧同心偏移，半径=鼠标到圆心距离。
"""

import math

from sketchcad.editing import offset


def mk_line(eid, p1, p2):
    return {"id": eid, "type": "line", "p1": list(p1), "p2": list(p2)}


def square_ccw():
    """4x4 正方形 CCW：(0,0)->(4,0)->(4,4)->(0,4)->闭合。"""
    ents = {
        1: mk_line(1, (0, 0), (4, 0)),
        2: mk_line(2, (4, 0), (4, 4)),
        3: mk_line(3, (4, 4), (0, 4)),
        4: mk_line(4, (0, 4), (0, 0)),
    }
    return ents, [1, 2, 3, 4]


def pts_of(segs):
    """全部段端点的唯一点集合（miter 相邻段共享端点须去重）。"""
    return sorted({(round(p[0], 6), round(p[1], 6))
                   for seg in segs for p in seg})


def test_signed_area_ccw_and_cw():
    ents, loop = square_ccw()
    chain = offset.orient_closed_chain(ents, loop)
    assert offset.signed_area(chain) > 0
    # 反向遍历（连接顺序倒置）定向后为 CW
    chain_cw = offset.orient_closed_chain(ents, list(reversed(loop)))
    assert offset.signed_area(chain_cw) < 0


def test_point_in_polygon():
    ents, loop = square_ccw()
    chain = offset.orient_closed_chain(ents, loop)
    assert offset.point_in_polygon(chain, (2, 2)) is True
    assert offset.point_in_polygon(chain, (5, 5)) is False
    assert offset.point_in_polygon(chain, (-1, 2)) is False


def test_orient_chain_storage_direction_independent():
    # 实体存储方向与环行进方向无关：orient 按首尾相接重定向
    ents = {
        1: mk_line(1, (0, 0), (4, 0)),
        2: mk_line(2, (4, 4), (4, 0)),   # 存储反向
        3: mk_line(3, (0, 4), (4, 4)),   # 存储反向
        4: mk_line(4, (0, 0), (0, 4)),
    }
    chain = offset.orient_closed_chain(ents, [1, 2, 3, 4])
    assert chain[0] == ((0.0, 0.0), (4.0, 0.0))
    assert chain[1] == ((4.0, 0.0), (4.0, 4.0))
    assert chain[2] == ((4.0, 4.0), (0.0, 4.0))
    assert chain[3] == ((0.0, 4.0), (0.0, 0.0))


def test_offset_profile_square_inward():
    ents, loop = square_ccw()
    chain = offset.orient_closed_chain(ents, loop)
    # 鼠标在内部 (2,2)、dist=1 → 内偏：新正方形 (1,1)-(3,1)-(3,3)-(1,3)
    segs = offset.offset_profile(chain, 1.0, (2, 2))
    assert len(segs) == 4
    assert pts_of(segs) == [(1.0, 1.0), (1.0, 3.0), (3.0, 1.0), (3.0, 3.0)]
    # 端点连续（miter 交点共享）
    for (pa, pb), (qa, _qb) in zip(segs, segs[1:]):
        assert pb == qa


def test_offset_profile_square_outward():
    ents, loop = square_ccw()
    chain = offset.orient_closed_chain(ents, loop)
    # 鼠标在外部 (5,5)、dist=1 → 外偏：(-1,-1)-(5,-1)-(5,5)-(-1,5)
    segs = offset.offset_profile(chain, 1.0, (5, 5))
    assert len(segs) == 4
    assert pts_of(segs) == [(-1.0, -1.0), (-1.0, 5.0),
                            (5.0, -1.0), (5.0, 5.0)]


def test_offset_profile_collinear_neighbors():
    # 底边拆两段（共线相邻）：miter 平行退化 → 直接偏移端点相连
    ents = {
        1: mk_line(1, (0, 0), (2, 0)),
        2: mk_line(2, (2, 0), (4, 0)),
        3: mk_line(3, (4, 0), (4, 4)),
        4: mk_line(4, (4, 4), (0, 4)),
        5: mk_line(5, (0, 4), (0, 0)),
    }
    chain = offset.orient_closed_chain(ents, [1, 2, 3, 4, 5])
    segs = offset.offset_profile(chain, 1.0, (2, 2))
    assert len(segs) == 5
    # 内偏 d=1：底边拆分处连接点 = 偏移起点 (2,1)（无交叉尖点）
    assert pts_of(segs) == [(1.0, 1.0), (1.0, 3.0), (2.0, 1.0),
                            (3.0, 1.0), (3.0, 3.0)]


def test_offset_profile_insufficient_segments():
    # 零长段跳过后有效段不足 3 → 返回空（无法构成轮廓）
    chain = [((0.0, 0.0), (0.0, 0.0)),
             ((0.0, 0.0), (0.0, 0.0)),
             ((0.0, 0.0), (0.0, 0.0))]
    assert offset.offset_profile(chain, 1.0, (1, 1)) == []


def test_offset_circle_mouse_drives_radius():
    assert offset.offset_circle((0, 0), 2.0, (5, 0)) == 5.0
    assert offset.offset_circle((0, 0), 2.0, (1, 0)) == 1.0


def test_offset_circle_degenerate_none():
    assert offset.offset_circle((0, 0), 2.0, (1e-5, 0)) is None


def test_offset_arc_angles_unchanged():
    res = offset.offset_arc((0, 0), 2.0, 0.3, 1.5, (3, 0))
    assert res == (3.0, 0.3, 1.5)


def test_offset_arc_degenerate_none():
    assert offset.offset_arc((0, 0), 2.0, 0.3, 1.5, (0, 0)) is None


def test_distance_to_segment():
    # 鼠标 (5, 1) 到线段 (0,0)-(4,0)：垂足截断到端点 (4,0) → sqrt(1+1)
    d = offset.distance_to_segment((5, 1), (0, 0), (4, 0))
    assert abs(d - 2 ** 0.5) < 1e-9
    # 垂足在段内：(2, 3) 到 (0,0)-(4,0) → 3
    assert offset.distance_to_segment((2, 3), (0, 0), (4, 0)) == 3.0

# ---- 开放链偏移与数值偏移（2026-11-16 实测反馈）----

def test_side_of_segment():
    # (0,0)->(4,0) 行进方向向右：p 在上方为左侧（>0）、下方右侧（<0）
    assert offset.side_of_segment((0, 0), (4, 0), (2, 1)) > 0
    assert offset.side_of_segment((0, 0), (4, 0), (2, -1)) < 0


def test_collect_open_chain_l_shape():
    ents = {1: mk_line(1, (0, 0), (4, 0)), 2: mk_line(2, (4, 0), (4, 3))}
    for eid in (1, 2):  # 从任一端实体出发，链覆盖一致（方向二义）
        chain = offset.collect_open_chain(ents, eid)
        assert chain is not None and len(chain) == 2
        pts = sorted((round(p[0], 6), round(p[1], 6))
                     for seg in chain for p in seg)
        # 共享端点 (4,0) 出现两次
        assert pts == [(0.0, 0.0), (4.0, 0.0), (4.0, 0.0), (4.0, 3.0)]
        for (pa, pb), (qa, _qb) in zip(chain, chain[1:]):
            assert pb == qa  # 首尾相接


def test_collect_open_chain_closed_none():
    ents, loop = square_ccw()
    assert offset.collect_open_chain(ents, loop[0]) is None


def test_collect_open_chain_fork_none():
    ents = {
        1: mk_line(1, (0, 0), (4, 0)),
        2: mk_line(2, (4, 0), (8, 0)),
        3: mk_line(3, (4, 0), (4, 4)),  # (4,0) 分叉（度 3）
    }
    assert offset.collect_open_chain(ents, 1) is None


def test_collect_open_chain_mixed_none():
    # 链中相邻实体为弧 → 不支持：arc 端点 (4,0) 与线 p2 相接
    ents = {
        1: mk_line(1, (0, 0), (4, 0)),
        2: {"id": 2, "type": "arc", "center": [6, 0], "radius": 2.0,
            "a1": math.pi, "a2": 1.5 * math.pi},
    }
    assert offset.collect_open_chain(ents, 1) is None


def test_chain_side_for_entity():
    ents = {1: mk_line(1, (0, 0), (4, 0)), 2: mk_line(2, (4, 0), (4, 3))}
    chain = offset.collect_open_chain(ents, 1)
    # id1 (0,0)->(4,0)：鼠标 (2,1) 在行进方向左侧
    assert offset.chain_side_for_entity(chain, ents[1], (2, 1)) is True
    assert offset.chain_side_for_entity(chain, ents[1], (2, -1)) is False
    # id2 (4,0)->(4,3)（向上）：行进方向左侧为 -x 侧
    assert offset.chain_side_for_entity(chain, ents[2], (3, 1)) is True
    assert offset.chain_side_for_entity(chain, ents[2], (5, 1)) is False


def test_offset_open_chain_l_left():
    chain = [((0.0, 0.0), (4.0, 0.0)), ((4.0, 0.0), (4.0, 3.0))]
    segs = offset.offset_open_chain(chain, 1.0, True)
    # 左偏：段1 (0,1)-(4,1)、段2 (3,0)-(3,3)，miter 交点 (3,1)
    assert segs == [((0.0, 1.0), (3.0, 1.0)), ((3.0, 1.0), (3.0, 3.0))]


def test_offset_open_chain_l_right():
    chain = [((0.0, 0.0), (4.0, 0.0)), ((4.0, 0.0), (4.0, 3.0))]
    segs = offset.offset_open_chain(chain, 1.0, False)
    # 右偏：段1 (0,-1)-(4,-1)、段2 (5,0)-(5,3)，miter 交点 (5,-1)
    assert segs == [((0.0, -1.0), (5.0, -1.0)), ((5.0, -1.0), (5.0, 3.0))]


def test_offset_open_chain_single_segment():
    chain = [((0.0, 0.0), (4.0, 0.0))]
    segs = offset.offset_open_chain(chain, 1.0, True)
    assert segs == [((0.0, 1.0), (4.0, 1.0))]


def test_offset_circle_by_distance():
    assert offset.offset_circle_by_distance((0, 0), 2.0, 1.0, True) == 3.0
    assert offset.offset_circle_by_distance((0, 0), 2.0, 1.0, False) == 1.0
    # 内偏超过半径退化
    assert offset.offset_circle_by_distance(
        (0, 0), 0.5, 1.0, False) is None


def test_offset_arc_by_distance():
    assert offset.offset_arc_by_distance(
        (0, 0), 2.0, 0.3, 1.5, 1.0, True) == (3.0, 0.3, 1.5)
    assert offset.offset_arc_by_distance(
        (0, 0), 2.0, 0.3, 1.5, 1.0, False) == (1.0, 0.3, 1.5)
    assert offset.offset_arc_by_distance(
        (0, 0), 0.5, 0.3, 1.5, 1.0, False) is None