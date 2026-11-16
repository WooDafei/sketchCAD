"""修剪与延伸（SketchCAD.md v2 §6 Trim/Extend）。

纯几何函数，不依赖 bpy：输入 entities 列表（Schema v1 line 实体），
输出新端点坐标或原因说明，实体 id 与约束的级联处理由调用方
（operator / 数据层）完成。

语义约定（对齐专业 CAD 惯例）：
- Trim：点击线段上要**移除**的部分，线段在距点击点最近的**内部交点**
  处剪断、裁除点击侧、保留另一侧；保留侧退化（长度 < _GEOM_EPS）
  视为整段删除。与邻段共享的端点交点（t=0/1 处，折线连接惯例）
  不算修剪边界。
- Extend（两段式交互由 operator 层编排）：延伸端沿所在直线延长，
  终点由用户在延长线上指定。
"""

_T_EPS = 1e-9     # 交点参数视为"内部"的最小 margin（排除端点相接）
_GEOM_EPS = 1e-6  # 几何零长判定，与端点匹配 round6 语义对齐

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