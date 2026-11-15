"""Levenberg-Marquardt 求解核心（纯 numpy）。

实现 Madsen-Nielsen 标准阻尼策略：增益比 rho 自适应调节 lambda，
接受步按 max(1/3, 1-(2rho-1)^3) 缩减，拒绝步 lambda *= nu（nu 翻倍）。
不用裸牛顿法（无阻尼极易发散，文档 §4 明确排除）。

Jacobian 默认前向差分（h ≈ 1e-7，一阶截断与舍入误差的平衡点）。
20 实体 / 30 约束规模（n=80 参数）构造一次 J 约 <10ms，满足
SketchCAD.md §10 "拖动尺寸实时求解 <50ms/帧" 的预算；解析 Jacobian
可经 jac 参数注入（接口预留，MVP 用差分）。

矛盾约束（如同一线段 distance 5 + 10）会收敛到驻点但残差不为零，
由 ok=False + message 报告——求解器不崩溃（Phase 2 验收项）。
"""

from time import perf_counter

import numpy as np

_FD_EPS = 1e-7
_MAX_DAMP_TRIES = 50


class SolveResult:
    """求解结果。ok=True 表示最终残差范数已降到 success_tol 以下。"""

    __slots__ = ("ok", "x", "residual_norm", "iters", "elapsed_ms", "message")

    def __init__(self, ok, x, residual_norm, iters, elapsed_ms, message):
        self.ok = ok
        self.x = x
        self.residual_norm = residual_norm
        self.iters = iters
        self.elapsed_ms = elapsed_ms
        self.message = message

    @property
    def cost(self):
        return 0.5 * float(self.residual_norm) ** 2

    def __repr__(self):
        return (f"SolveResult(ok={self.ok}, |r|={self.residual_norm:.3e}, "
                f"iters={self.iters}, {self.elapsed_ms:.1f}ms, "
                f"{self.message})")


def numerical_jacobian(residual, x, r0):
    """前向差分 Jacobian（xp 原位恢复，residual 不持有 xp 引用）。"""
    m = r0.size
    n = x.size
    J = np.zeros((m, n))
    xp = x.copy()
    for j in range(n):
        h = _FD_EPS * max(1.0, abs(x[j]))
        orig = xp[j]
        xp[j] = orig + h
        J[:, j] = (residual(xp) - r0) / h
        xp[j] = orig
    return J


def solve(x0, residual, jac=None, tol=1e-10, max_iter=200,
          lam0=1e-3, success_tol=1e-6):
    """求解 min ½‖residual(x)‖²。

    - tol: 完全收敛阈值（残差/梯度/步长范数）；
    - success_tol: "解出"判据——低于它才 ok=True；矛盾约束收敛到
      驻点但残差大，ok=False；
    - message: converged_residual / converged_gradient / converged_step /
      converged (迭代后) / max_iter / stalled_damping / empty_problem。
    """
    t0 = perf_counter()
    x = np.array(x0, dtype=float)
    r = residual(x)
    rn = float(np.linalg.norm(r))

    def finish(ok, iters, message):
        return SolveResult(ok, x, float(np.linalg.norm(r)), iters,
                           (perf_counter() - t0) * 1000.0, message)

    if r.size == 0:
        return finish(True, 0, "empty_problem")
    if rn < tol:
        return finish(True, 0, "converged_residual")

    lam = float(lam0)
    nu = 2.0
    F = 0.5 * rn * rn
    iters = 0
    for it in range(max_iter):
        iters = it + 1
        J = jac(x, r) if jac is not None else numerical_jacobian(residual, x, r)
        A = J.T @ J
        g = J.T @ r
        if float(np.abs(g).max()) < tol:
            return finish(rn < success_tol, iters, "converged_gradient")

        accepted = False
        for _ in range(_MAX_DAMP_TRIES):
            diag = np.maximum(np.diag(A), _FD_EPS)
            try:
                delta = np.linalg.solve(A + lam * np.diag(diag), -g)
            except np.linalg.LinAlgError:
                lam *= nu
                nu *= 2.0
                continue
            if float(np.linalg.norm(delta)) <= tol * (
                    float(np.linalg.norm(x)) + tol):
                return finish(rn < success_tol, iters, "converged_step")
            x_new = x + delta
            r_new = residual(x_new)
            rn_new = float(np.linalg.norm(r_new))
            F_new = 0.5 * rn_new * rn_new
            pred = 0.5 * float(delta @ (lam * delta - g))
            rho = (F - F_new) / pred if pred > 0 else -np.inf
            if rho > 0:
                x = x_new
                r = r_new
                rn = rn_new
                F = F_new
                lam *= max(1.0 / 3.0, 1.0 - (2.0 * rho - 1.0) ** 3)
                nu = 2.0
                accepted = True
                break
            lam *= nu
            nu *= 2.0
        if not accepted:
            return finish(rn < success_tol, iters, "stalled_damping")
        if rn < tol:
            return finish(True, iters, "converged_residual")

    return finish(rn < success_tol, iters, "max_iter")