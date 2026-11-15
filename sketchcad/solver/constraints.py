"""约束残差库：每类型约束一个残差函数（纯 numpy，禁止 bpy / core 依赖）。

残差为 0 表示约束满足；方程数由 EQ_COUNTS 声明（DOF 分析用）。
- coincident: 2 方程 (dx, dy)，端点定位存于 "endpoints"（0=p1, 1=p2），
  默认 [1, 0]（前段终点接后段起点，折线连接惯例）；
- horizontal / vertical: 1 方程；
- distance: 1 方程，残差取 dx²+dy²-value² 光滑形式（避免 |d|-v 在
  零长处导数奇异，且多项式形式便于差分 Jacobian）。
"""

import numpy as np

EQ_COUNTS = {
    "coincident": 2,
    "horizontal": 1,
    "vertical": 1,
    "distance": 1,
}

_DEFAULT_ENDPOINTS = (1, 0)


class UnknownConstraintTypeError(ValueError):
    pass


def _line_point(x, layout, eid, pos):
    off = layout.offsets[eid]
    return x[off + 2 * pos], x[off + 2 * pos + 1]


def _res_coincident(x, layout, con):
    ids = con["targets"]
    ep = con.get("endpoints", _DEFAULT_ENDPOINTS)
    ax, ay = _line_point(x, layout, ids[0], ep[0])
    bx, by = _line_point(x, layout, ids[1], ep[1])
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


RESIDUALS = {
    "coincident": _res_coincident,
    "horizontal": _res_horizontal,
    "vertical": _res_vertical,
    "distance": _res_distance,
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
    明确提示。
    """
    problems = []
    entity_ids = {e["id"] for e in entities}
    for c in active_constraints(constraints):
        cid = c.get("id")
        t = c.get("type")
        if t not in RESIDUALS:
            problems.append(f"约束 {cid}: 未知类型 {t!r}")
            continue
        targets = c.get("targets") or []
        for tid in targets:
            if tid not in entity_ids:
                problems.append(f"约束 {cid}: target 实体 {tid} 不存在")
        if t == "coincident":
            if len(targets) != 2:
                problems.append(f"约束 {cid}: coincident 需要 2 个 target")
            ep = c.get("endpoints")
            if ep is not None and (
                    len(ep) != 2 or any(v not in (0, 1) for v in ep)):
                problems.append(f"约束 {cid}: endpoints 需为两个 0/1 值")
        elif len(targets) != 1:
            problems.append(f"约束 {cid}: {t} 需要 1 个 target")
        if t == "distance" and c.get("value") is None:
            problems.append(f"约束 {cid}: distance 缺少 value")
    return problems