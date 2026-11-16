"""修剪与延伸（SketchCAD.md v2 §6 Trim/Extend）。

纯几何函数，不依赖 bpy：输入 entities 列表（Schema v1 line/circle/arc
实体），输出新端点坐标或原因说明，实体 id 与约束的级联处理由调用方
（operator / 数据层）完成。

语义约定（对齐专业 CAD 惯例）：
- Trim：点击实体上要**移除**的部分，实体在距点击点最近的**内部交点**
  处剪断、裁除点击侧、保留另一侧；保留侧退化（< _GEOM_EPS）
  视为整段删除。与邻段共享的端点交点（line 参数 t=0/1、弧端点
  margin 内）不算修剪边界。Phase 3 圆弧适配：line 与 arc 均可修剪；
  circle 整圆不参与（无端点语义，主流 CAD 同此边界）。
- Extend（两段式交互由 operator 层编排）：line 沿所在直线延长、
  终点由用户指定；arc 沿圆周延长（延伸端取距鼠标方位角较近端，
  a1 端向顺时针扩展、a2 端向逆时针扩展），鼠标方位角落在扫角内时
  延伸无意义（返回 None）。
"""

from math import atan2, hypot, sqrt

from ..core.geom import TWO_PI

_T_EPS = 1e-9     # 交点参数视为"内部"的最小 margin（排除端点相接）
_GEOM_EPS = 1e-6  # 几何零长判定，与端点匹配 round6 语义对齐

# 弧端点交点 margin（弧度）：距 a1/a2 过近的交点视为共享端点，
# 不算修剪/延伸边界（半径 1 时弦长 1e-6，与 round6 端点匹配同量级）
_ARC_END_EPS = 1e-6

# 供 operator 层复用的最小参数 margin（延伸有效区判定）
PARAM_EPS = _T_EPS


def line_line_params(p1, p2, p3, p4):
    """两条无限直线的交点参数 (t, u)；平行（含共线）返回 None。

    p1/p2、p3/p4 为两条直线上各取两点：p = p1 + t*(p2-p1) = p3 + u*(p4-p3)。
    """
    x1, y1 = p1[0], p1[1]
    dx1, dy1 = p2[0] - x1, p2[1] - y1
    x3, y3 = p3[0], p3[1]
    dx2, dy2 = p4[0] - x3, p4[1] - y3
    denom = dx1 * dy2 - dy1 * dx2
    if abs(denom) < _T_EPS:
        return None
    ex, ey = x3 - x1, y3 - y1
    t = (ex * dy2 - ey * dx2) / denom
    u = (ex * dy1 - ey * dx1) / denom
    return t, u


def collect_intersections(seg_p1, seg_p2, entities, exclude_id):
    """seg 所在直线与其他 line 实体所在直线的交点。

    返回 [(t, entity_id), ...] 按 t 升序（t 为 seg 参数，u 无调用方）。
    """
    out = []
    for e in entities:
        if e.get("type") != "line" or e.get("id") == exclude_id:
            continue
        r = line_line_params(seg_p1, seg_p2, e["p1"], e["p2"])
        if r is None:
            continue
        out.append((r[0], e["id"]))
    out.sort(key=lambda x: x[0])
    return out


def _find_line(entities, seg_id):
    for e in entities:
        if e.get("id") == seg_id and e.get("type") == "line":
            return e
    return None


def _param_on_segment(p1, p2, p):
    """p 在线段上的投影参数（截断到 [0, 1]）。"""
    return max(0.0, min(1.0, project_param(p1, p2, p)))


def project_param(p1, p2, p):
    """p 在 p1->p2 所在直线上的投影参数（不截断）。"""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    length_sq = dx * dx + dy * dy
    if length_sq < _T_EPS * _T_EPS:
        return 0.0
    return ((p[0] - p1[0]) * dx + (p[1] - p1[1]) * dy) / length_sq


def point_at(p1, p2, t):
    """直线上参数 t 处的坐标点。"""
    return (p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1]))


def trim_line(entities, seg_id, click_point):
    """修剪线段：在距点击点最近的内部交点处剪断，裁除点击侧。

    返回 (True, changed_pos, new_p1, new_p2)——changed_pos 指位置发生
    变化的端点（0=p1 / 1=p2，调用方据此级联清理该端点上的固接类
    约束）；或 (False, reason)：
    - "not-found"：seg_id 不存在或非 line 实体
    - "no-intersection"：无内部交点（修剪边界不存在）
    - "removed"：保留侧退化（< _GEOM_EPS），调用方应删除整个实体
    """
    ent = _find_line(entities, seg_id)
    if ent is None:
        return False, "not-found"
    p1, p2 = ent["p1"], ent["p2"]
    cuts = [t for t, _eid in collect_intersections(p1, p2, entities, seg_id)
            if _T_EPS < t < 1.0 - _T_EPS]
    if not cuts:
        return False, "no-intersection"
    t_click = _param_on_segment(p1, p2, click_point)
    t_cut = min(cuts, key=lambda t: abs(t - t_click))
    if t_click < t_cut:
        # 点击点在交点 p1 侧：裁除 p1→交点，保留交点→p2
        a, b = point_at(p1, p2, t_cut), p2
        changed_pos = 0
    else:
        # 点击点在交点 p2 侧：裁除交点→p2，保留 p1→交点
        a, b = p1, point_at(p1, p2, t_cut)
        changed_pos = 1
    if (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 < _GEOM_EPS * _GEOM_EPS:
        return False, "removed"
    return True, changed_pos, [a[0], a[1]], [b[0], b[1]]


def extend_line(entities, seg_id, click_point):
    """延伸线段的近点击端至最近的外部交点（沿所在直线延长）。

    返回 (True, changed_pos, new_p1, new_p2) 或 (False, reason)：
    - "not-found"：seg_id 不存在或非 line 实体
    - "no-target"：该端延长方向上无交点目标
    """
    ent = _find_line(entities, seg_id)
    if ent is None:
        return False, "not-found"
    p1, p2 = ent["p1"], ent["p2"]
    t_click = _param_on_segment(p1, p2, click_point)
    all_cuts = collect_intersections(p1, p2, entities, seg_id)
    if t_click < 0.5:
        pos = 0
        cands = [t for t, _eid in all_cuts if t < -_T_EPS]
        t_ext = max(cands) if cands else None   # 最靠近 p1 的外部交点
    else:
        pos = 1
        cands = [t for t, _eid in all_cuts if t > 1.0 + _T_EPS]
        t_ext = min(cands) if cands else None   # 最靠近 p2 的外部交点
    if t_ext is None:
        return False, "no-target"
    pt = point_at(p1, p2, t_ext)
    if pos == 0:
        return True, 0, [pt[0], pt[1]], list(p2)
    return True, 1, list(p1), [pt[0], pt[1]]

# ---- 圆弧适配（Phase 3：Trim/Extend 支持 arc 实体）----

def _angle_in_sweep(angle, a1, a2, margin=0.0):
    """角度是否在 CCW 扫角 (a1, a2) 内部（margin 内缩排除端点）。"""
    sweep = (a2 - a1) % TWO_PI
    off = (angle - a1) % TWO_PI
    return margin < off < sweep - margin


def line_circle_params(p1, p2, center, radius):
    """无限直线与圆的交点 [(t, point)]（0/1/2 个，t 为直线参数）。"""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    length_sq = dx * dx + dy * dy
    if length_sq < _T_EPS * _T_EPS:
        return []
    fx, fy = center[0] - p1[0], center[1] - p1[1]
    a = (fx * dx + fy * dy) / length_sq          # 垂足参数
    dist_sq = fx * fx + fy * fy - a * a * length_sq
    if dist_sq > radius * radius:
        return []
    h = sqrt(max(0.0, radius * radius - dist_sq)) / sqrt(length_sq)
    if h < _T_EPS:
        # 相切：单交点（避免返回两个重合点）
        return [(a, point_at(p1, p2, a))]
    return [(a - h, point_at(p1, p2, a - h)),
            (a + h, point_at(p1, p2, a + h))]


def circle_circle_params(c1, r1, c2, r2):
    """圆与圆的交点坐标列表（0/1/2 个）。"""
    dx, dy = c2[0] - c1[0], c2[1] - c1[1]
    d = hypot(dx, dy)
    if d < _T_EPS or d > r1 + r2 or d < abs(r1 - r2):
        return []
    a = (d * d + r1 * r1 - r2 * r2) / (2.0 * d)
    h_sq = r1 * r1 - a * a
    h = sqrt(max(0.0, h_sq))
    ux, uy = dx / d, dy / d
    bx, by = c1[0] + a * ux, c1[1] + a * uy
    if h < _T_EPS:
        return [(bx, by)]
    return [(bx - h * uy, by + h * ux), (bx + h * uy, by - h * ux)]


def _arc_of_point(center, radius, pt):
    return atan2(pt[1] - center[1], pt[0] - center[0])


def collect_line_intersections_all(p1, p2, entities, exclude_id):
    """line 所在无限直线与 line/circle/arc 的交点参数 [(t, eid)]。

    arc 边界按扫角过滤（交点须落在弧的 (a1, a2) 内）；circle 整圆
    交点全部有效。按 t 升序。
    """
    out = []
    for e in entities:
        if e.get("id") == exclude_id:
            continue
        t_e = e.get("type")
        if t_e == "line":
            r = line_line_params(p1, p2, e["p1"], e["p2"])
            if r is not None:
                out.append((r[0], e["id"]))
        elif t_e in ("circle", "arc"):
            for _t, pt in line_circle_params(p1, p2, e["center"],
                                             e["radius"]):
                if (t_e == "arc" and not _angle_in_sweep(
                        _arc_of_point(e["center"], e["radius"], pt),
                        e["a1"], e["a2"])):
                    continue
                out.append((project_param(p1, p2, pt), e["id"]))
    out.sort(key=lambda x: x[0])
    return out


def collect_arc_intersections(arc, entities, exclude_id):
    """arc 与其他实体的交点角度 [(angle, eid)]（角度在弧扫角内）。

    对方 line：无限直线与弧所属整圆的交点按弧扫角过滤；
    对方 circle：整圆与整圆交点按弧扫角过滤；
    对方 arc：双向扫角过滤（交点须同时落在双方扫角内）。
    按沿扫角的偏移 (angle - a1) % 2π 升序。
    """
    c, r = arc["center"], arc["radius"]
    out = []
    for e in entities:
        if e.get("id") == exclude_id:
            continue
        t_e = e.get("type")
        pts = []
        if t_e == "line":
            pts = [pt for _t, pt in line_circle_params(
                e["p1"], e["p2"], c, r)]
        elif t_e == "circle":
            pts = circle_circle_params(c, r, e["center"], e["radius"])
        elif t_e == "arc":
            pts = circle_circle_params(c, r, e["center"], e["radius"])
        for pt in pts:
            ang = _arc_of_point(c, r, pt)
            if not _angle_in_sweep(ang, arc["a1"], arc["a2"]):
                continue
            if (t_e == "arc" and not _angle_in_sweep(
                    _arc_of_point(e["center"], e["radius"], pt),
                    e["a1"], e["a2"])):
                continue
            out.append((ang, e["id"]))
    out.sort(key=lambda x: (x[0] - arc["a1"]) % TWO_PI)
    return out


def _find_arc(entities, arc_id):
    for e in entities:
        if e.get("id") == arc_id and e.get("type") == "arc":
            return e
    return None


def trim_arc(entities, arc_id, click_point):
    """修剪弧：距点击角最近的内部交点处剪断，裁除点击侧。

    返回 (True, changed_pos, new_a1, new_a2)——changed_pos 指位置
    发生变化的端（0=a1 端 / 1=a2 端，调用方据此级联清理该端固接
    约束）；或 (False, reason)：
    - "not-found"：arc_id 不存在或非 arc 实体
    - "no-intersection"：无内部交点（修剪边界不存在）
    - "removed"：保留侧退化，调用方应删除整个实体
    端点 margin 内的交点（与邻段共享端点）不算边界。
    """
    arc = _find_arc(entities, arc_id)
    if arc is None:
        return False, "not-found"
    c, r = arc["center"], arc["radius"]
    a1, a2 = arc["a1"], arc["a2"]
    sweep = (a2 - a1) % TWO_PI
    if sweep < _GEOM_EPS:
        return False, "not-found"
    cuts = [ang for ang, _eid
            in collect_arc_intersections(arc, entities, arc_id)
            if _angle_in_sweep(ang, a1, a2, margin=_ARC_END_EPS)]
    if not cuts:
        return False, "no-intersection"
    a_click = _arc_of_point(c, r, click_point)
    # 点击角投影回扫角内（点击在弧附近；扫角外取距较近端的端点值）
    off_click = (a_click - a1) % TWO_PI
    if off_click > sweep:
        d_a2 = off_click - sweep
        d_a1 = TWO_PI - off_click
        off_click = sweep if d_a2 <= d_a1 else 0.0
    off_cut = min(((ang - a1) % TWO_PI for ang in cuts),
                  key=lambda off: abs(off - off_click))
    if off_click < off_cut:
        # 点击在 a1→交点侧：裁除该段，保留 [交点, a2]
        new_a1, new_a2, changed_pos = a1 + off_cut, a2, 0
    else:
        # 点击在交点→a2 侧：裁除该段，保留 [a1, 交点]
        new_a1, new_a2, changed_pos = a1, a1 + off_cut, 1
    if (new_a2 - new_a1) < _GEOM_EPS:
        return False, "removed"
    return True, changed_pos, new_a1, new_a2


def extend_arc_result(arc, mouse_point):
    """弧沿圆周延伸的计算：返回 (pos, delta) 或 None。

    延伸端取距鼠标方位角较近的端：pos 0 = a1 端向顺时针扩展
    delta（新 a1 = a1 - delta），pos 1 = a2 端向逆时针扩展 delta
    （新 a2 = a2 + delta）；扫角相应变大。鼠标方位角落在扫角内
    （或贴端点 margin 内）时延伸无意义，返回 None。
    """
    c, r = arc["center"], arc["radius"]
    a1, a2 = arc["a1"], arc["a2"]
    sweep = (a2 - a1) % TWO_PI
    if sweep < _GEOM_EPS:
        return None
    ang = _arc_of_point(c, r, mouse_point)
    d0 = (a1 - ang) % TWO_PI   # a1 端到鼠标的顺时针角距
    d1 = (ang - a2) % TWO_PI   # a2 端到鼠标的逆时针角距
    if d0 <= d1:
        pos, delta = 0, d0
    else:
        pos, delta = 1, d1
    if not (_ARC_END_EPS < delta
            < TWO_PI - sweep - _ARC_END_EPS):
        return None
    return pos, delta