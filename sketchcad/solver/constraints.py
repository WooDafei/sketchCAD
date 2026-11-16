"""约束残差库：每类型约束一个残差函数（纯 numpy，禁止 bpy / core 依赖）。

残差为 0 表示约束满足；方程数由 EQ_COUNTS 声明（DOF 分析用）。
- coincident: 2 方程 (dx, dy)，端点定位存于 "endpoints"（0=p1, 1=p2），
  默认 [1, 0]（前段终点接后段起点，折线连接惯例）；端点对 line 取
  p1/p2，对 arc 由 center/r/angle 现算（pos0=a1 端、pos1=a2 端）；
- horizontal / vertical: 1 方程（仅 line）；
- distance: 1 方程，残差取 dx²+dy²-value² 光滑形式（避免 |d|-v 在
  零长处导数奇异，且多项式形式便于差分 Jacobian）（仅 line）；
- point_on_line: 1 方程，targets=[点实体, 线实体]，endpoints=[pos, None]
  指定点实体的端点（默认 [0, None]）；残差为点到线实体所在直线的
  归一化距离（叉积 / |dir|，线长无关的残差尺度），零长线退化为
  常数 0（不施加约束，避免除零）；
- radius: 1 方程，targets=[circle/arc]，残差 r - value（线性光滑）；
- concentric: 2 方程，targets=[圆类实体, 圆类实体]，圆心重合；
- tangent: 1 方程，targets=[line, 圆类实体]，残差 dist²(center,直线)
  - r²（cross²/len² 光滑形式；零长线退化为常数 0）。
"""

import numpy as np

EQ_COUNTS = {
    "coincident": 2,
    "horizontal": 1,
    "vertical": 1,
    "distance": 1,
    "point_on_line": 1,
    "radius": 1,
    "concentric": 2,
    "tangent": 1,
}

_DEFAULT_ENDPOINTS = (1, 0)

_CURVE_TYPES = ("circle", "arc")   # 携带 center/radius 的实体
_POINTED_TYPES = ("line", "arc")   # 携带端点（pos 0/1）的实体


class UnknownConstraintTypeError(ValueError):
    pass


def _point(x, layout, eid, pos):
    """实体端点坐标：line 取 p1/p2；arc 由 center/r/angle 现算。"""
    off = layout.offsets[eid]
    if layout.types.get(eid) == "arc":
        cx, cy, r = x[off], x[off + 1], x[off + 2]
        ang = x[off + 3 + pos]
        return cx + r * np.cos(ang), cy + r * np.sin(ang)
    return x[off + 2 * pos], x[off + 2 * pos + 1]


def _res_coincident(x, layout, con):
    ids = con["targets"]
    ep = con.get("endpoints", _DEFAULT_ENDPOINTS)
    ax, ay = _point(x, layout, ids[0], ep[0])
    bx, by = _point(x, layout, ids[1], ep[1])
    return np.array([ax - bx, ay - by])


def _res_horizontal(x, layout, con):
    off = layout.offsets[con["targets"][0]]
    return np.array([x[off + 3] - x[off + 1]])


def _res_vertical(x, layout, con):
    off = layout.offsets[con["targets"][0]]
    return np.array([x[off + 2] - x[off + 0]])


def _res_distance(x, layout, con):
    off = layout.offsets[con["targets"][0]]
    dx = x[off + 2] - x[off + 0]
    dy = x[off + 3] - x[off + 1]
    return np.array([dx * dx + dy * dy - con["value"] ** 2])


def _res_point_on_line(x, layout, con):
    ids = con["targets"]
    ep = con.get("endpoints") or (0, None)
    px, py = _point(x, layout, ids[0], ep[0])
    off = layout.offsets[ids[1]]
    x1, y1 = x[off], x[off + 1]
    dx = x[off + 2] - x1
    dy = x[off + 3] - y1
    norm = (dx * dx + dy * dy) ** 0.5
    if norm < 1e-12:
        # 零长线退化：返回常数 0 残差（不施加约束，方程数保持一致）
        return np.array([0.0])
    return np.array([((px - x1) * dy - (py - y1) * dx) / norm])


def _res_radius(x, layout, con):
    off = layout.offsets[con["targets"][0]]
    return np.array([x[off + 2] - con["value"]])


def _res_concentric(x, layout, con):
    ids = con["targets"]
    o1 = layout.offsets[ids[0]]
    o2 = layout.offsets[ids[1]]
    return np.array([x[o1] - x[o2], x[o1 + 1] - x[o2 + 1]])


def _res_tangent(x, layout, con):
    ids = con["targets"]
    loff = layout.offsets[ids[0]]
    coff = layout.offsets[ids[1]]
    x1, y1 = x[loff], x[loff + 1]
    dx = x[loff + 2] - x1
    dy = x[loff + 3] - y1
    len_sq = dx * dx + dy * dy
    if len_sq < 1e-12:
        # 零长线退化：返回常数 0 残差（不施加约束，方程数保持一致）
        return np.array([0.0])
    cx, cy, r = x[coff], x[coff + 1], x[coff + 2]
    cross = (cx - x1) * dy - (cy - y1) * dx
    dist_sq = cross * cross / len_sq
    return np.array([dist_sq - r * r])


RESIDUALS = {
    "coincident": _res_coincident,
    "horizontal": _res_horizontal,
    "vertical": _res_vertical,
    "distance": _res_distance,
    "point_on_line": _res_point_on_line,
    "radius": _res_radius,
    "concentric": _res_concentric,
    "tangent": _res_tangent,
}


def active_constraints(constraints):
    return [c for c in constraints if c.get("enabled", True)]


def build_residual_fn(entities, constraints, layout):
    """返回 residual(x) -> r：启用约束残差按顺序拼接。

    约束 dict 以引用捕获（闭包持 con），拖动中修改 con["value"] 即时
    生效，无需重建闭包。
    """
    pairs = []
    for c in active_constraints(constraints):
        t = c.get("type")
        if t not in RESIDUALS:
            raise UnknownConstraintTypeError(f"未知约束类型: {t!r}")
        pairs.append((RESIDUALS[t], c))

    def residual(x):
        if not pairs:
            return np.zeros(0)
        return np.concatenate([fn(x, layout, c) for fn, c in pairs])

    return residual


def equations_count(constraints):
    """启用约束的方程总数（未知类型直接报错，与残差库保持一致）。"""
    total = 0
    for c in active_constraints(constraints):
        t = c.get("type")
        if t not in EQ_COUNTS:
            raise UnknownConstraintTypeError(f"未知约束类型: {t!r}")
        total += EQ_COUNTS[t]
    return total


def collect_problems(entities, constraints):
    """数据层校验（非数值层）：返回问题描述列表，空列表即合法。

    与求解失败（数值层，矛盾约束）区分：数据层问题在求解前就能给出
    明确提示。除 target 存在性与数量外，还校验约束与实体类型的匹配
    （端点类约束要求 line/arc；圆类约束要求 circle/arc）。
    """
    problems = []
    entity_types = {e["id"]: e.get("type") for e in entities}
    for c in active_constraints(constraints):
        cid = c.get("id")
        t = c.get("type")
        if t not in RESIDUALS:
            problems.append(f"约束 {cid}: 未知类型 {t!r}")
            continue
        targets = c.get("targets") or []
        for tid in targets:
            if tid not in entity_types:
                problems.append(f"约束 {cid}: target 实体 {tid} 不存在")
        missing = [tid for tid in targets if tid not in entity_types]
        if missing:
            continue
        types = [entity_types[tid] for tid in targets]
        if t == "coincident":
            if len(targets) != 2:
                problems.append(f"约束 {cid}: coincident 需要 2 个 target")
            elif any(v not in _POINTED_TYPES for v in types):
                problems.append(
                    f"约束 {cid}: coincident 两端实体须有端点（line/arc）")
            ep = c.get("endpoints")
            if ep is not None and (
                    len(ep) != 2 or any(v not in (0, 1) for v in ep)):
                problems.append(f"约束 {cid}: endpoints 需为两个 0/1 值")
        elif t == "point_on_line":
            if len(targets) != 2:
                problems.append(
                    f"约束 {cid}: point_on_line 需要 [点实体, 线实体] 2 个 target")
            elif (types[0] not in _POINTED_TYPES or types[1] != "line"):
                problems.append(
                    f"约束 {cid}: point_on_line 须为 [line/arc 点实体, line]")
            ep = c.get("endpoints")
            if ep is not None and (
                    len(ep) != 2 or ep[0] not in (0, 1)):
                problems.append(f"约束 {cid}: endpoints 需为 [0/1, None]")
        elif t == "radius":
            if len(targets) != 1 or types[0] not in _CURVE_TYPES:
                problems.append(
                    f"约束 {cid}: radius 需 1 个圆类 target（circle/arc）")
            if c.get("value") is None:
                problems.append(f"约束 {cid}: radius 缺少 value")
        elif t == "concentric":
            if len(targets) != 2 or any(v not in _CURVE_TYPES for v in types):
                problems.append(
                    f"约束 {cid}: concentric 需 2 个圆类 target（circle/arc）")
        elif t == "tangent":
            if (len(targets) != 2 or types[0] != "line"
                    or types[1] not in _CURVE_TYPES):
                problems.append(
                    f"约束 {cid}: tangent 需 [line, circle/arc] 2 个 target")
        elif len(targets) != 1:
            problems.append(f"约束 {cid}: {t} 需要 1 个 target")
        if t == "distance" and c.get("value") is None:
            problems.append(f"约束 {cid}: distance 缺少 value")
    return problems