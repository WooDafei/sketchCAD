"""DOF 分析与冗余约束检测（简化自 solvespace 的秩分析思路）。

- DOF = 参数数 - rank(J)（SVD 数值秩，RANK_TOL 相对最大奇异值）；
- 冗余 = 方程数 - rank(J)：秩亏缺的方程不再提供新信息；
- 矛盾约束（如同一线段 distance 5 + 10）J 满秩但不收敛，由 lm.solve
  的 ok=False 报告——与"冗余/过约束"是不同性质的失败，分别提示。

analyze 每次调用做一次全量 J 的 SVD（快，可进拖动路径）；
detect_redundant 逐条增量定位冗余 id（k 次 SVD，仅约束编辑时调用，
不进每帧路径）。
"""

import numpy as np

from . import constraints as cons_mod
from .lm import numerical_jacobian
from .params import ParamLayout

RANK_TOL = 1e-8

STATUS_UNDER = "under"
STATUS_FULL = "full"
STATUS_OVER = "over"


class DofReport:
    """草图约束状态报告（DOF / 冗余计数 / 状态分类）。"""

    __slots__ = ("n_params", "n_equations", "rank", "dof",
                 "n_redundant", "status")

    def __init__(self, n_params, n_equations, rank, dof,
                 n_redundant, status):
        self.n_params = n_params
        self.n_equations = n_equations
        self.rank = rank
        self.dof = dof
        self.n_redundant = n_redundant
        self.status = status

    def __repr__(self):
        return (f"DofReport(dof={self.dof}/{self.n_params}, "
                f"eqs={self.n_equations}, rank={self.rank}, "
                f"redundant={self.n_redundant}, status={self.status!r})")


def _rank(J, rank_tol):
    if J.size == 0:
        return 0
    s = np.linalg.svd(J, compute_uv=False)
    return int((s > rank_tol * float(s[0])).sum())


def analyze(entities, constraints, rank_tol=RANK_TOL):
    """对当前几何点做 DOF 分析（J 在实体当前坐标处评估）。"""
    layout = ParamLayout(entities)
    x = layout.pack(entities)
    residual = cons_mod.build_residual_fn(entities, constraints, layout)
    r = residual(x)
    m = r.size
    n = layout.size
    if m == 0:
        status = STATUS_UNDER if n > 0 else STATUS_FULL
        return DofReport(n, 0, 0, n, 0, status)
    J = numerical_jacobian(residual, x, r)
    rank = _rank(J, rank_tol)
    dof = n - rank
    redundant = m - rank
    if redundant > 0:
        status = STATUS_OVER
    elif dof > 0:
        status = STATUS_UNDER
    else:
        status = STATUS_FULL
    return DofReport(n, m, rank, dof, redundant, status)


def detect_redundant(entities, constraints, rank_tol=RANK_TOL):
    """增量法逐条定位冗余约束，返回冗余 constraint id 列表。

    按顺序加入启用约束，数值秩不增者即冗余。秩单调不减，重复约束
    （如同一线段两条 horizontal）必然被标记；矛盾但独立的方程秩会
    增长，不会被误标（其失败由求解层报告）。
    """
    layout = ParamLayout(entities)
    x = layout.pack(entities)
    acc = []
    prev_rank = 0
    redundant_ids = []
    for c in cons_mod.active_constraints(constraints):
        trial = acc + [c]
        residual = cons_mod.build_residual_fn(entities, trial, layout)
        r = residual(x)
        if r.size == 0:
            acc = trial
            continue
        rank = _rank(numerical_jacobian(residual, x, r), rank_tol)
        if rank > prev_rank:
            acc = trial
            prev_rank = rank
        else:
            redundant_ids.append(c["id"])
    return redundant_ids