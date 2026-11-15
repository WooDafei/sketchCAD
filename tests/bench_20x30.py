"""Phase 2 验收基准：20 元素 / 30 约束（SketchCAD.md §10/§11）。

直接运行（不经 pytest）：
  E:/autoprogram/envs/python3.14/.venv/Scripts/python.exe tests/bench_20x30.py

场景：
  A 全量求解：扰动初值 -> 收敛（模拟添加尺寸后的首次求解）；
  B 拖动求解：每步修改全部 distance 值 0.05，从上一解继续
    （模拟拖动尺寸的每帧实时求解，20 步取中位）。
回归线 200ms 为硬断言（防性能崩坏）；50ms/帧 为文档预算对照线，
仅打印 PASS/FAIL 提示不判失败。
"""

import sys
import time
import types
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 假包注册：绕过含 import bpy 的真实 sketchcad/__init__.py
_pkg = types.ModuleType("sketchcad")
_pkg.__path__ = [str(Path(__file__).resolve().parents[1] / "sketchcad")]
sys.modules.setdefault("sketchcad", _pkg)

from sketchcad.solver.constraints import build_residual_fn  # noqa: E402
from sketchcad.solver.dof import analyze  # noqa: E402
from sketchcad.solver.lm import solve  # noqa: E402
from sketchcad.solver.params import ParamLayout  # noqa: E402

BUDGET_MS = 50.0    # SketchCAD.md §10 拖动预算（对照提示）
REGRESSION_MS = 200.0  # 硬性回归线


def build_case():
    entities = []
    for i in range(10):  # 水平线
        entities.append({"id": i + 1, "type": "line",
                         "p1": [0.0, float(i)], "p2": [2.0, float(i)]})
    for i in range(10):  # 竖直线
        entities.append({"id": 11 + i, "type": "line",
                         "p1": [float(i), 0.0], "p2": [float(i), 1.5]})
    constraints = []
    cid = 100
    for i in range(10):
        constraints.append({"id": cid, "type": "horizontal",
                            "targets": [i + 1], "enabled": True})
        cid += 1
    for i in range(10):
        constraints.append({"id": cid, "type": "vertical",
                            "targets": [11 + i], "enabled": True})
        cid += 1
    for i in range(10):
        target = (i + 1) if i < 5 else (11 + i - 5)
        constraints.append({"id": cid, "type": "distance",
                            "targets": [target], "value": 2.0 + 0.1 * i,
                            "enabled": True})
        cid += 1
    return entities, constraints


def main():
    rng = np.random.default_rng(42)
    ents, cons = build_case()
    layout = ParamLayout(ents)
    residual = build_residual_fn(ents, cons, layout)
    rep = analyze(ents, cons)

    # 场景 A：扰动初值全量求解
    x0 = layout.pack(ents) + rng.uniform(-0.05, 0.05, layout.size)
    t0 = time.perf_counter()
    res_a = solve(x0, residual)
    t_a = (time.perf_counter() - t0) * 1000.0
    if not res_a.ok:
        print(f"FAIL: 场景 A 未解出（{res_a}）")
        return 1

    # 场景 B：拖动语义，每帧 value 微调后从上一解继续
    x_cur = res_a.x
    times_b = []
    for _ in range(20):
        for c in cons:
            if c["type"] == "distance":
                c["value"] += 0.05
        t0 = time.perf_counter()
        res_b = solve(x_cur, residual)
        times_b.append((time.perf_counter() - t0) * 1000.0)
        if not res_b.ok:
            print(f"FAIL: 场景 B 未解出（{res_b}）")
            return 1
        x_cur = res_b.x

    med_b = float(np.median(times_b))
    mean_b = float(np.mean(times_b))
    max_b = float(np.max(times_b))

    print("=== SketchCAD Phase 2 求解器基准（20 元素 / 30 约束）===")
    print(f"DOF: {rep}")
    print(f"场景 A 全量求解（扰动初值）   : {t_a:7.2f} ms"
          f"  [iters={res_a.iters}, |r|={res_a.residual_norm:.2e}]")
    print(f"场景 B 拖动再求解（20 次中位）: {med_b:7.2f} ms/帧"
          f"  [均值 {mean_b:.2f} ms, 最大 {max_b:.2f} ms]")
    ok_budget = med_b < BUDGET_MS
    print(f"预算 {BUDGET_MS:.0f} ms/帧   : {'PASS' if ok_budget else 'FAIL'}"
          f"（对照线，不判失败）")
    if t_a >= REGRESSION_MS or med_b >= REGRESSION_MS:
        print(f"FAIL: 超过硬性回归线 {REGRESSION_MS:.0f} ms")
        return 1
    print(f"回归线 {REGRESSION_MS:.0f} ms : PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())