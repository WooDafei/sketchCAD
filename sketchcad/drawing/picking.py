"""线段拾取（SketchCAD.md §4 尺寸驱动的选中环节）。

纯几何函数，不依赖 bpy。tol 由 operator 层按视口缩放把像素容差
换算为基准面世界单位后传入（与自动约束推断同一换算语义）。
"""

SELECT_PIXEL_TOL = 12.0


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

def pick_endpoint(point, entities, tol):
    """返回容差内距 point 最近的实体端点 (entity_id, pos)，无命中 None。

    拖动拾取优先于线段拾取：端点是更小的操作目标，调用方先试
    端点、未命中再选线段。pos 0=p1 / 1=p2。重合端点（如 coincident
    约束的两端）坐标相同距离相等，固定返回先建实体的端点（等距
    不覆盖，行为可预测）。
    """
    best = None
    best_dist = tol
    for e in entities:
        if e.get("type") != "line":
            continue
        for pos, p in ((0, e["p1"]), (1, e["p2"])):
            d = ((point[0] - p[0]) ** 2 + (point[1] - p[1]) ** 2) ** 0.5
            if d <= best_dist and (best is None or d < best_dist):
                best_dist = d
                best = (e["id"], pos)
    return best