"""drawing.polygon 正多边形顶点生成器测试。"""

import math

from sketchcad.drawing import polygon


def test_polygon_vertices_square():
    # n=4、r=1、起始角 0：顶点 (1,0) (0,1) (-1,0) (0,-1)（CCW）
    verts = polygon.polygon_vertices((0.0, 0.0), 1.0, 0.0, 4)
    assert len(verts) == 4
    expect = [(1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0)]
    for (x, y), (ex, ey) in zip(verts, expect):
        assert math.isclose(x, ex, abs_tol=1e-9)
        assert math.isclose(y, ey, abs_tol=1e-9)


def test_polygon_vertices_offset_center_and_angle():
    # 平移 + 起始角偏移：n=3 时第二个顶点 = 中心 + r*(cos(120°+30°), sin(...))
    verts = polygon.polygon_vertices((5.0, 5.0), 2.0, math.pi / 6, 3)
    assert len(verts) == 3
    ang = math.pi / 6 + 2.0 * math.pi / 3
    ex = 5.0 + 2.0 * math.cos(ang)
    ey = 5.0 + 2.0 * math.sin(ang)
    assert math.isclose(verts[1][0], ex, abs_tol=1e-9)
    assert math.isclose(verts[1][1], ey, abs_tol=1e-9)


def test_polygon_vertices_on_circumcircle():
    # 所有顶点到中心距离一致（外接圆语义）
    verts = polygon.polygon_vertices((1.0, -2.0), 7.0, 0.7, 6)
    for x, y in verts:
        assert math.isclose(math.hypot(x - 1.0, y + 2.0), 7.0,
                            abs_tol=1e-9)


def test_polygon_vertices_invalid_inputs():
    assert polygon.polygon_vertices((0, 0), 1.0, 0.0, 2) is None
    assert polygon.polygon_vertices((0, 0), 1e-12, 0.0, 6) is None


def test_polygon_sides_bounds():
    assert polygon.MIN_SIDES == 3
    assert polygon.MAX_SIDES == 32
    assert polygon.DEFAULT_SIDES == 6