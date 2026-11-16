"""偏移边线（SketchCAD.md v2 §6 Offset）。

纯几何函数，不依赖 bpy。交互遵循显式三段式：select 模式 O 键激活 →
点击选中实体 → 移动鼠标实时预览 → 再次点击确认生成偏移实体
（与原轮廓独立，MVP 不加偏移约束——参数化关联为远期）。

语义与 MVP 边界：
- circle/arc：单实体同心偏移——鼠标驱动（新半径 = 鼠标到圆心距离）
  或数值输入（输入为偏移距离，鼠标在圆内/外决定增减）；扫角不变，
  新半径退化（< min_radius）返回 None；
- 闭合轮廓：纯 line 环（detect_closed_loops 的输出）。偏移侧向由
  鼠标相对轮廓内外决定（射线法 + 有向面积定向），各段沿统一法线
  平移后相邻偏移线求交 miter 连接；平行相邻段（共线）直接端点相连；
- 开放链（2026-11-16 实测反馈：未闭合线段也要能偏移）：从选中段
  沿共享端点收集所属连通链（纯线、无分叉），侧向由鼠标相对选中段
  的左右侧决定，miter 连接，两端平端帽；
- 混合直/弧链不支持（圆/弧可单独偏移）；数值输入由 operator 层
  整合（预览按输入距离实时更新，回车确认）。

已知 MVP 局限：内偏距离超过轮廓内切半径时几何自交不检测（与
Sketcher 一致，由预览直观呈现）；锐角 miter 尖点过长不裁剪。
"""

from math import hypot

from ..core.geom import arc_point
from ..drawing.picking import point_segment_distance

_GEOM_EPS = 1e-6
_KEY_EPS = 6  # 端点匹配小数位（与 round6 语义一致）

# 供 operator 层复用的最小半径（圆/弧偏移退化判定）
MIN_OFFSET_RADIUS = 1e-4


def _key(p):
    return (round(p[0], _KEY_EPS), round(p[1], _KEY_EPS))


def signed_area(chain):
    """有向闭合折线（[(p_from, p_to), ...]）的 signed area。

    CCW 环为正（内部在每段行进方向的左侧），CW 环为负。
    """
    total = 0.0
    for a, b in chain:
        total += a[0] * b[1] - b[0] * a[1]
    return 0.5 * total


def point_in_polygon(chain, p):
    """射线法判定 p 是否在闭合折线内部（边界归属不作保证）。"""
    inside = False
    for a, b in chain:
        x1, y1 = a[0], a[1]
        x2, y2 = b[0], b[1]
        if (y1 > p[1]) != (y2 > p[1]):
            x_cross = (x2 - x1) * (p[1] - y1) / (y2 - y1) + x1
            if p[0] < x_cross:
                inside = not inside
    return inside


def orient_closed_chain(entities_by_id, loop_ids):
    """按环序定向闭合链：返回 [(p_from, p_to), ...]。

    loop_ids 为 detect_closed_loops 输出的连接顺序实体 id；每条
    line 的 p1/p2 存储方向与环行进方向无关，按首尾相接（round6）
    逐段确定实际方向。
    """
    chain = []
    prev_end = None
    for eid in loop_ids:
        e = entities_by_id[eid]
        p1, p2 = tuple(e["p1"]), tuple(e["p2"])
        if prev_end is None:
            a, b = p1, p2
        elif _key(prev_end) == _key(p1):
            a, b = p1, p2
        else:
            a, b = p2, p1
        chain.append((a, b))
        prev_end = b
    return chain


def _offset_normal(a, b, area, inside):
    """段 a->b 的偏移法线（单位向量）。

    内法线：CCW 环取左法线 (-dy, dx)，CW 环取右法线 (dy, -dx)；
    外偏时取反。
    """
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = hypot(dx, dy)
    if length < _GEOM_EPS:
        return None
    if area > 0:
        n = (-dy / length, dx / length)
    else:
        n = (dy / length, -dx / length)
    if not inside:
        n = (-n[0], -n[1])
    return n


def _line_intersect(p1, p2, p3, p4):
    """两条无限直线交点；平行（含共线）返回 None。"""
    x1, y1 = p1[0], p1[1]
    dx1, dy1 = p2[0] - x1, p2[1] - y1
    x3, y3 = p3[0], p3[1]
    dx2, dy2 = p4[0] - x3, p4[1] - y3
    denom = dx1 * dy2 - dy1 * dx2
    if abs(denom) < _GEOM_EPS:
        return None
    ex, ey = x3 - x1, y3 - y1
    u = (ex * dy1 - ey * dx1) / denom
    return (x3 + u * dx2, y3 + u * dy2)


def offset_profile(chain, dist, side_point):
    """闭合纯线轮廓偏移：返回 [(p1, p2), ...] 段序列（miter 连接）。

    dist 为偏移距离（正数）；侧向由 side_point 相对轮廓内外决定
    （内部 → 向内偏，外部 → 向外偏）。零长段跳过（段数减少时
    miter 仍按相邻有效段连接）。
    """
    area = signed_area(chain)
    inside = point_in_polygon(chain, side_point)
    # 1. 各段沿统一法线平移
    off = []
    for a, b in chain:
        n = _offset_normal(a, b, area, inside)
        if n is None:
            continue
        off.append(((a[0] + dist * n[0], a[1] + dist * n[1]),
                    (b[0] + dist * n[0], b[1] + dist * n[1])))
    m = len(off)
    if m < 3:
        return []  # 有效段不足以构成轮廓
    # 2. miter 连接：每个连接点 = 前段偏移直线 ∩ 本段偏移直线；
    #    平行（相邻段共线）直接取本段偏移起点
    starts = []
    for i in range(m):
        prev_a, prev_b = off[i - 1]
        cur_a, cur_b = off[i]
        ip = _line_intersect(prev_a, prev_b, cur_a, cur_b)
        starts.append(ip if ip is not None else cur_a)
    return [(starts[i], starts[(i + 1) % m]) for i in range(m)]


def offset_circle(center, radius, side_point, min_radius=MIN_OFFSET_RADIUS):
    """圆偏移：新半径 = side_point 到圆心的距离；退化返回 None。"""
    d = hypot(side_point[0] - center[0], side_point[1] - center[1])
    if d < min_radius:
        return None
    return d


def offset_arc(center, radius, a1, a2, side_point,
               min_radius=MIN_OFFSET_RADIUS):
    """弧偏移：同心，新半径 = 鼠标到圆心距离，扫角 (a1, a2) 不变。

    返回 (new_radius, a1, a2) 或 None（新半径退化）。
    """
    d = hypot(side_point[0] - center[0], side_point[1] - center[1])
    if d < min_radius:
        return None
    return (d, a1, a2)


def distance_to_segment(side_point, a, b):
    """side_point 到线段 a-b 的最短距离（偏移距离取自选中线段）。"""
    return point_segment_distance(side_point, a, b)

# ---- 开放链偏移（2026-11-16 实测反馈：未闭合线段也要能偏移）----

def side_of_segment(a, b, p):
    """p 相对段 a->b 行进方向的有向侧：>0 左侧，<0 右侧。"""
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def collect_open_chain(entities_by_id, start_eid):
    """从选中线段出发收集所属连通链（定向）；不适配返回 None。

    沿共享端点（round6）遍历相邻 line 段；要求连通分量全部为
    line 且无分叉（节点度 <= 2）；分量闭合（环）返回 None
    （闭合轮廓走 offset_profile）。返回按连接顺序定向的
    [(p_from, p_to), ...]，起点取分量中某个度 = 1 端点
    （开放链方向二义，功能上无影响——侧向判定按段自身方向）。
    """
    adj = {}
    for eid, e in entities_by_id.items():
        t = e.get("type")
        if t == "line":
            k1, k2 = _key(e["p1"]), _key(e["p2"])
        elif t == "arc":
            # 弧端点现算进邻接表：混合链检测依赖"相邻遇弧"可触发
            c, r = e["center"], e["radius"]
            k1 = _key(arc_point(c, r, e["a1"]))
            k2 = _key(arc_point(c, r, e["a2"]))
        else:
            continue  # circle 无端点，不参与链
        if k1 == k2:
            continue
        adj.setdefault(k1, []).append((eid, k2))
        adj.setdefault(k2, []).append((eid, k1))
    e0 = entities_by_id[start_eid]
    visited_eids = {start_eid}
    visited_keys = {_key(e0["p1"]), _key(e0["p2"])}
    queue = list(visited_keys)
    while queue:
        k = queue.pop()
        for eid, ok in adj.get(k, []):
            if eid in visited_eids:
                continue
            if entities_by_id[eid].get("type") != "line":
                return None  # 混合链（含非 line）不支持
            visited_eids.add(eid)
            if ok not in visited_keys:
                visited_keys.add(ok)
                queue.append(ok)

    def _deg(k):
        return sum(1 for eid, _ok in adj.get(k, [])
                   if eid in visited_eids)

    if any(_deg(k) > 2 for k in visited_keys):
        return None  # 分叉节点不支持
    if all(_deg(k) == 2 for k in visited_keys):
        return None  # 闭合环走 offset_profile
    cur = next(k for k in visited_keys if _deg(k) == 1)
    used = set()
    chain = []
    while True:
        nxt = None
        for eid, _ok in adj.get(cur, []):
            if eid not in used:
                nxt = eid
                break
        if nxt is None:
            break
        used.add(nxt)
        e = entities_by_id[nxt]
        p1, p2 = tuple(e["p1"]), tuple(e["p2"])
        if _key(p1) == cur:
            chain.append((p1, p2))
            cur = _key(p2)
        else:
            chain.append((p2, p1))
            cur = _key(p1)
    return chain


def chain_side_for_entity(chain, e, side_point):
    """side_point 相对链中实体 e 的有向侧（按存储端点匹配、方向自适应）。

    返回 True = 行进方向左侧；实体不在链中（理论不可达）返回 True。
    """
    p1, p2 = tuple(e["p1"]), tuple(e["p2"])
    for a, b in chain:
        if _key(a) == _key(p1) and _key(b) == _key(p2):
            return side_of_segment(a, b, side_point) > 0
        if _key(a) == _key(p2) and _key(b) == _key(p1):
            return side_of_segment(b, a, side_point) > 0
    return True


def offset_open_chain(chain, dist, side_is_left):
    """开放链偏移（butt 平端帽）：返回 [(p1, p2), ...] 段序列。

    side_is_left：取链各段行进方向的左法线（False 取右）；
    相邻段偏移线求交 miter 连接（平行退化取偏移起点），链两端
    直接用偏移端点（平端帽，无延伸）。
    """
    off = []
    for a, b in chain:
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = hypot(dx, dy)
        if length < _GEOM_EPS:
            continue
        nx, ny = -dy / length, dx / length
        if not side_is_left:
            nx, ny = -nx, -ny
        off.append(((a[0] + dist * nx, a[1] + dist * ny),
                    (b[0] + dist * nx, b[1] + dist * ny)))
    m = len(off)
    if m == 0:
        return []
    if m == 1:
        return [off[0]]
    joints = [off[0][0]]
    for i in range(1, m):
        ip = _line_intersect(off[i - 1][0], off[i - 1][1],
                             off[i][0], off[i][1])
        joints.append(ip if ip is not None else off[i][0])
    joints.append(off[-1][1])
    return [(joints[i], joints[i + 1]) for i in range(m)]


def offset_circle_by_distance(center, radius, dist, outward,
                              min_radius=MIN_OFFSET_RADIUS):
    """圆按输入距离偏移：外偏半径增大、内偏减小；退化返回 None。"""
    new_r = radius + dist if outward else radius - dist
    if new_r < min_radius:
        return None
    return new_r


def offset_arc_by_distance(center, radius, a1, a2, dist, outward,
                           min_radius=MIN_OFFSET_RADIUS):
    """弧按输入距离偏移：返回 (new_radius, a1, a2) 或 None。"""
    new_r = offset_circle_by_distance(center, radius, dist, outward,
                                      min_radius)
    if new_r is None:
        return None
    return (new_r, a1, a2)