"""圆/弧纯几何辅助（Phase 3 圆弧实体支持）。

零 bpy / mathutils 依赖（solver、snapping、picking、loops、gpu 与
operator 各层共用，venv pytest 可直接导入）。

方向约定（schema 与全部下游一致）：弧从角 a1 **逆时针（CCW）**扫到
角 a2；端点 pos0 = center + r*(cos a1, sin a1)，pos1 = center +
r*(cos a2, sin a2)。整圆用 circle 实体表达，arc 实体的扫角为
(0, 2π) 开区间（绘制交互保证三点弧起终点不重合、不共线）。
"""

import math

TWO_PI = 2.0 * math.pi

# 弧离散折线的最大角步长（弧度，约 2°）：GPU 绘制与 mesh 提交共用
ARC_MAX_STEP = 0.035

_DEGENERATE_EPS = 1e-9


def arc_point(center, radius, angle):
    """弧上角度 angle 处的坐标点。"""
    return (center[0] + radius * math.cos(angle),
            center[1] + radius * math.sin(angle))


def arc_points(center, radius, a1, a2, max_step=ARC_MAX_STEP):
    """弧 CCW 离散为折线点序列（含两端点；全圆时首尾点重合闭合）。"""
    sweep = (a2 - a1) % TWO_PI
    if sweep < _DEGENERATE_EPS:
        sweep = TWO_PI
    n = max(1, int(math.ceil(sweep / max_step)))
    return [arc_point(center, radius, a1 + sweep * i / n)
            for i in range(n + 1)]


def circle_from_3_points(p1, p2, p3):
    """三点定圆，返回 (center, radius)；共线/重合退化返回 None。"""
    ax, ay = p1[0], p1[1]
    bx, by = p2[0], p2[1]
    cx, cy = p3[0], p3[1]
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < _DEGENERATE_EPS:
        return None
    a2 = ax * ax + ay * ay
    b2 = bx * bx + by * by
    c2 = cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    if r < _DEGENERATE_EPS:
        return None
    return (ux, uy), r


def _in_ccw(angle, a_from, a_to):
    return ((angle - a_from) % TWO_PI) <= ((a_to - a_from) % TWO_PI)


def arc_from_3_points(p1, p2, p3):
    """三点定弧（p2 为弧上中间点）。

    返回 (center, radius, a1, a2)：弧从 p1 经 p2 扫到 p3；方向由
    p2 相对 p1->p3 的位置判定，与存储约定（a1->a2 为 CCW）不符时
    交换起点与终点。退化（三点共线/重合）返回 None。
    """
    res = circle_from_3_points(p1, p2, p3)
    if res is None:
        return None
    center, r = res
    a1 = math.atan2(p1[1] - center[1], p1[0] - center[0])
    a2 = math.atan2(p3[1] - center[1], p3[0] - center[0])
    am = math.atan2(p2[1] - center[1], p2[0] - center[0])
    if _in_ccw(am, a1, a2):
        return center, r, a1, a2
    return center, r, a2, a1


def arc_angle_in(a1, a2, angle):
    """angle 是否落在弧 CCW 扫角 [a1, a2] 内（含端点）。"""
    return ((angle - a1) % TWO_PI) <= ((a2 - a1) % TWO_PI)


def arc_mid_angle(a1, a2):
    """弧 CCW 中点角（捕捉与标注用）。"""
    return a1 + ((a2 - a1) % TWO_PI) * 0.5


def point_arc_distance(p, center, radius, a1, a2):
    """点到弧的最短距离：扫角内取半径差，扫角外取较近端点的距离。"""
    dist_c = math.hypot(p[0] - center[0], p[1] - center[1])
    if arc_angle_in(a1, a2, math.atan2(p[1] - center[1],
                                       p[0] - center[0])):
        return abs(dist_c - radius)
    e1 = arc_point(center, radius, a1)
    e2 = arc_point(center, radius, a2)
    d1 = math.hypot(p[0] - e1[0], p[1] - e1[1])
    d2 = math.hypot(p[0] - e2[0], p[1] - e2[1])
    return min(d1, d2)