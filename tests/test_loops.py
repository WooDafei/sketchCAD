"""闭合轮廓检测测试（SketchCAD.md v2 Phase 3）。

MVP 边界：全度=2 连通分量识别为简单环；度>2 分叉分量与非闭合
链不识别。
"""

from sketchcad.editing import loops


def mk(eid, p1, p2):
    return {"id": eid, "type": "line", "p1": list(p1), "p2": list(p2)}


def test_triangle():
    ents = [
        mk(1, (0, 0), (4, 0)),
        mk(2, (4, 0), (2, 3)),
        mk(3, (2, 3), (0, 0)),
    ]
    result = loops.detect_closed_loops(ents)
    assert len(result) == 1
    assert sorted(result[0]) == [1, 2, 3]


def test_quad_chain_order():
    ents = [
        mk(1, (0, 0), (4, 0)),
        mk(2, (4, 0), (4, 4)),
        mk(3, (4, 4), (0, 4)),
        mk(4, (0, 4), (0, 0)),
    ]
    result = loops.detect_closed_loops(ents)
    assert len(result) == 1
    # 走链起点为实体 1 的 p1 端点，链按连接顺序
    assert result[0] == [1, 2, 3, 4]


def test_open_chain():
    ents = [mk(1, (0, 0), (4, 0)), mk(2, (4, 0), (4, 4))]
    assert loops.detect_closed_loops(ents) == []


def test_loop_with_tail():
    # 三角形 + 尾巴：尾巴端点度 1 被剪枝，剩余纯环
    ents = [
        mk(1, (0, 0), (4, 0)),
        mk(2, (4, 0), (2, 3)),
        mk(3, (2, 3), (0, 0)),
        mk(4, (4, 0), (8, 0)),
    ]
    result = loops.detect_closed_loops(ents)
    assert len(result) == 1
    assert sorted(result[0]) == [1, 2, 3]


def test_two_loops():
    ents = [
        mk(1, (0, 0), (4, 0)), mk(2, (4, 0), (2, 3)), mk(3, (2, 3), (0, 0)),
        mk(4, (10, 0), (14, 0)), mk(5, (14, 0), (12, 3)), mk(6, (12, 3), (10, 0)),
    ]
    result = loops.detect_closed_loops(ents)
    assert len(result) == 2
    all_ids = sorted(i for loop in result for i in loop)
    assert all_ids == [1, 2, 3, 4, 5, 6]


def test_branch_degree3_not_loop():
    ents = [
        mk(1, (0, 0), (4, 0)),
        mk(2, (4, 0), (8, 0)),
        mk(3, (4, 0), (4, 4)),
    ]
    assert loops.detect_closed_loops(ents) == []


def test_two_triangles_share_vertex_not_loop():
    ents = [
        mk(1, (0, 0), (4, 0)), mk(2, (4, 0), (2, 3)), mk(3, (2, 3), (0, 0)),
        mk(4, (4, 0), (8, 0)), mk(5, (8, 0), (6, 3)), mk(6, (6, 3), (4, 0)),
    ]
    assert loops.detect_closed_loops(ents) == []


def test_two_nodes_two_edges():
    # 退化环：两点之间两条边
    ents = [mk(1, (0, 0), (4, 0)), mk(2, (4, 0), (0, 0))]
    result = loops.detect_closed_loops(ents)
    assert len(result) == 1
    assert sorted(result[0]) == [1, 2]


def test_zero_len_segment_ignored():
    ents = [
        mk(1, (0, 0), (4, 0)), mk(2, (4, 0), (2, 3)), mk(3, (2, 3), (0, 0)),
        mk(9, (5, 5), (5, 5)),
    ]
    result = loops.detect_closed_loops(ents)
    assert len(result) == 1
    assert sorted(result[0]) == [1, 2, 3]


def test_non_line_entity_ignored():
    ents = [
        mk(1, (0, 0), (4, 0)),
        {"id": 2, "type": "spline", "points": [[0, 0]]},
    ]
    assert loops.detect_closed_loops(ents) == []


def circle(eid, center, radius):
    return {"id": eid, "type": "circle",
            "center": list(center), "radius": radius}


def arc(eid, center, radius, a1, a2):
    return {"id": eid, "type": "arc", "center": list(center),
            "radius": radius, "a1": a1, "a2": a2}


def test_circle_standalone_loop():
    # circle 本身是闭合轮廓：独立成单元素环
    ents = [
        mk(1, (0, 0), (4, 0)), mk(2, (4, 0), (4, 4)), mk(3, (4, 4), (0, 0)),
        circle(9, (20, 20), 3.0),
    ]
    result = loops.detect_closed_loops(ents)
    assert len(result) == 2
    assert sorted(i for loop in result for i in loop) == [1, 2, 3, 9]
    assert any(loop == [9] for loop in result)


def test_zero_radius_circle_ignored():
    ents = [circle(1, (0, 0), 0.0)]
    assert loops.detect_closed_loops(ents) == []


def test_arc_joins_line_loop():
    # 半圆弧 + 直径线构成闭合轮廓（拱形）：弧端点进端点图
    import math
    ents = [
        arc(1, (2, 0), 2.0, 0.0, math.pi),   # (4,0) -> (0,0) 经 (2,2)
        mk(2, (0, 0), (4, 0)),               # 直径
    ]
    result = loops.detect_closed_loops(ents)
    assert len(result) == 1
    assert sorted(result[0]) == [1, 2]


def test_arc_open_not_loop():
    import math
    ents = [arc(1, (2, 0), 2.0, 0.0, math.pi)]
    assert loops.detect_closed_loops(ents) == []