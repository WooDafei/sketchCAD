"""实体拾取（SketchCAD.md §4 尺寸驱动的选中环节）。

纯几何函数，不依赖 bpy。tol 由 operator 层按视口缩放把像素容差
换算为基准面世界单位后传入（与自动约束推断同一换算语义）。

Phase 3 圆弧：pick_entity 统一命中 line/circle/arc（select 模式
选择）；trim/extend 工具仍用 pick_segment（仅 line，MVP 边界）。
pick_endpoint 扩展 arc 两端点与 circle 圆心（pos=2，拖动圆心用）。
"""

from ..core.geom import arc_point, point_arc_distance

SELECT_PIXEL_TOL = 12.0

# circle 拖动/拾取的"端点"槽位约定：2 = 圆心（0/1 已被 p1/p2 占用）
POS_CENTER = 2


def point_segment_distance(p, a, b):
    """平面内点到线段的最短距离。"""
    ax, ay = a[0], a[1]
    bx, by = b[0], b[1]
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq < 1e-18:
        # 零长段退化为点距
        return ((p[0] - ax) ** 2 + (p[1] - ay) ** 2) ** 0.5
    # 参数 t 截断到 [0, 1]：垂足在延长线上时取最近端点
    t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / length_sq
    t = max(0.0, min(1.0, t))
    cx, cy = ax + t * dx, ay + t * dy
    return ((p[0] - cx) ** 2 + (p[1] - cy) ** 2) ** 0.5


def pick_segment(point, entities, tol):
    """返回容差内距 point 最近的 line 实体 id，无命中返回 None。"""
    best_id = None
    best_dist = tol
    for e in entities:
        if e.get("type") != "line":
            continue
        d = point_segment_distance(point, e["p1"], e["p2"])
        if d <= best_dist:
            best_dist = d
            best_id = e["id"]
    return best_id


def pick_entity(point, entities, tol):
    """容差内距 point 最近的实体 id（line/circle/arc），无命中 None。

    circle 距离 = |dist(p, center) - r|；arc 扫角内取半径差、扫角外
    取较近端点距离（见 geom.point_arc_distance）。
    """
    best_id = None
    best_dist = tol
    for e in entities:
        t = e.get("type")
        if t == "line":
            d = point_segment_distance(point, e["p1"], e["p2"])
        elif t == "circle":
            c, r = e["center"], e["radius"]
            d = abs(((point[0] - c[0]) ** 2 + (point[1] - c[1]) ** 2)
                    ** 0.5 - r)
        elif t == "arc":
            d = point_arc_distance(point, e["center"], e["radius"],
                                   e["a1"], e["a2"])
        else:
            continue
        if d < best_dist or (d <= best_dist and best_id is None):
            # 等距不覆盖（先遍历者命中，与 pick_endpoint 语义一致）
            best_dist = d
            best_id = e["id"]
    return best_id


def pick_endpoint(point, entities, tol):
    """返回容差内距 point 最近的实体端点 (entity_id, pos)，无命中 None。

    拖动拾取优先于实体拾取：端点是更小的操作目标，调用方先试
    端点、未命中再选实体。pos 0/1 = line 或 arc 的端点（arc 端点
    由 center/r/angle 现算），2 = circle 圆心（POS_CENTER）。
    重合端点坐标相同距离相等，固定返回先建实体的端点（等距
    不覆盖，行为可预测）。
    """
    best = None
    best_dist = tol
    for e in entities:
        t = e.get("type")
        if t == "line":
            slots = ((0, e["p1"]), (1, e["p2"]))
        elif t == "arc":
            p1, p2 = arc_point(e["center"], e["radius"], e["a1"]), \
                arc_point(e["center"], e["radius"], e["a2"])
            slots = ((0, p1), (1, p2))
        elif t == "circle":
            slots = ((POS_CENTER, e["center"]),)
        else:
            continue
        for pos, p in slots:
            d = ((point[0] - p[0]) ** 2 + (point[1] - p[1]) ** 2) ** 0.5
            if d <= best_dist and (best is None or d < best_dist):
                best_dist = d
                best = (e["id"], pos)
    return best