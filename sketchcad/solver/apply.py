"""会话级求解回写（纯数据操作，operator 与 N-Panel 共用）。

输入是 SketchData（schema dict），不依赖 bpy；负责数据层预检、
LM 求解与坐标回写，并返回 (ok, message) 供调用方 report/提示。
"""

from .constraints import build_residual_fn, collect_problems
from .lm import solve
from .params import ParamLayout


def solve_and_apply(sketch, success_tol=1e-6):
    """对 SketchData 执行预检 + 求解 + 坐标回写。

    返回 (ok, message)：失败时几何保持不变，message 说明原因
    （数据层问题 / 求解失败），供调用方展示。
    """
    problems = collect_problems(sketch.entities, sketch.constraints)
    if problems:
        return False, "约束数据存在问题：" + "; ".join(problems[:3])
    layout = ParamLayout(sketch.entities)
    residual = build_residual_fn(sketch.entities, sketch.constraints, layout)
    result = solve(layout.pack(sketch.entities), residual,
                   success_tol=success_tol)
    if result.ok:
        layout.apply(result.x, sketch.entities)
        return True, f"求解成功（残差 {result.residual_norm:.2e}）"
    return False, (f"求解失败（残差 {result.residual_norm:.2e}）——"
                   "可能存在矛盾约束，几何未改动")