"""core.geom 椭圆辅助测试：规范化 / 参数方程点 / 离散折线。"""

import math

from sketchcad.core import geom


def test_ellipse_normalize_swaps_when_b_major():
    # b > a：交换两轴，rot 转 90°（长轴方向同步换到另一轴）
    A, B, R = geom.ellipse_normalize(2.0, 5.0, 0.3)
    assert A == 5.0 and B == 2.0
    assert math.isclose(R, 0.3 + math.pi / 2)


def test_ellipse_normalize_keeps_ordered():
    # 已有序（a >= b）原样返回
    A, B, R = geom.ellipse_normalize(5.0, 2.0, 0.3)
    assert (A, B, R) == (5.0, 2.0, 0.3)
    # 圆退化（a == b）rot 保留
    A, B, R = geom.ellipse_normalize(3.0, 3.0, 1.0)
    assert (A, B, R) == (3.0, 3.0, 1.0)


def test_ellipse_point_axis_aligned():
    # rot=0：参数角 0 → 长轴正端 (cx+a, cy)；π/2 → 短轴正端 (cx, cy+b)
    p0 = geom.ellipse_point((1.0, 2.0), 3.0, 2.0, 0.0, 0.0)
    assert math.isclose(p0[0], 4.0) and math.isclose(p0[1], 2.0)
    p1 = geom.ellipse_point((1.0, 2.0), 3.0, 2.0, 0.0, math.pi / 2)
    assert math.isclose(p1[0], 1.0) and math.isclose(p1[1], 4.0)


def test_ellipse_point_rotated():
    # rot=π/2：长轴方向转到世界 Y——参数角 0 的点在 (cx, cy+a)
    p = geom.ellipse_point((0.0, 0.0), 3.0, 1.0, math.pi / 2, 0.0)
    assert math.isclose(p[0], 0.0, abs_tol=1e-9)
    assert math.isclose(p[1], 3.0, abs_tol=1e-9)


def test_ellipse_points_closed_and_dense():
    pts = geom.ellipse_points((0.0, 0.0), 3.0, 2.0, 0.0)
    # 首尾重合（闭合约定与全圆一致）
    assert math.isclose(pts[0][0], pts[-1][0], abs_tol=1e-9)
    assert math.isclose(pts[0][1], pts[-1][1], abs_tol=1e-9)
    # 密度：步长上限 0.035 rad → 至少 ~180 点
    assert len(pts) >= 180
    # 所有点到两焦点距离和恒等于 2a（椭圆定义验证）
    c = (-1.0, 0.0)
    f1, f2 = (math.sqrt(9.0 - 4.0), 0.0), (-math.sqrt(9.0 - 4.0), 0.0)
    for x, y in pts[::20]:
        s = (math.hypot(x - f1[0], y - f1[1])
             + math.hypot(x - f2[0], y - f2[1]))
        assert math.isclose(s, 6.0, abs_tol=1e-6)