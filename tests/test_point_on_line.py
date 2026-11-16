"""point_on_line 约束测试：残差 / 方程数 / 数据层校验 / 求解收敛 / DOF。"""

import numpy as np

from sketchcad.core.datum import Datum
from sketchcad.core.sketch_data import SketchData
from sketchcad.solver import apply as solver_apply
from sketchcad.solver import dof as solver_dof
from sketchcad.solver.constraints import (
    build_residual_fn,
    collect_problems,
    equations_count,
)
from sketchcad.solver.params import ParamLayout


def line(eid, p1, p2):
    return {"id": eid, "type": "line", "p1": list(p1), "p2": list(p2)}


def residual_of(ents, cons):
    layout = ParamLayout(ents)
    fn = build_residual_fn(ents, cons, layout)
    return fn(layout.pack(ents))


def con(eid=10, point_eid=1, line_eid=2, pos=1, enabled=True):
    return {"id": eid, "type": "point_on_line",
            "targets": [point_eid, line_eid],
            "endpoints": [pos, None], "enabled": enabled}


def test_residual_zero_when_point_on_line():
    ents = [line(1, (0, 0), (4, 5)), line(2, (0, 5), (8, 5))]
    assert np.allclose(residual_of(ents, [con()]), 0.0)


def test_residual_violated():
    ents = [line(1, (0, 0), (4, 2)), line(2, (0, 5), (8, 5))]
    assert np.allclose(residual_of(ents, [con()]), [3.0])


def test_residual_scale_independent():
    # 残差归一化（叉积 / |dir|）：同一偏差下目标线长度不同残差一致
    ents_a = [line(1, (0, 0), (4, 2)), line(2, (0, 5), (4, 5))]
    ents_b = [line(1, (0, 0), (4, 2)), line(2, (0, 5), (8, 5))]
    assert np.allclose(residual_of(ents_a, [con()]),
                       residual_of(ents_b, [con()]))


def test_default_endpoints_pos0():
    # 缺省 endpoints=[0, None]：约束 p1 端
    ents = [line(1, (3, 2), (4, 5)), line(2, (0, 5), (8, 5))]
    c = {"id": 10, "type": "point_on_line", "targets": [1, 2],
         "enabled": True}
    assert np.allclose(residual_of(ents, [c]), [3.0])


def test_zero_length_target_line_no_crash():
    ents = [line(1, (0, 0), (4, 5)), line(2, (3, 3), (3, 3))]
    r = residual_of(ents, [con()])
    assert r.size == 1 and r[0] == 0.0


def test_equations_count_and_disabled():
    assert equations_count([con()]) == 1
    assert equations_count([dict(con(), enabled=False)]) == 0


def test_collect_problems():
    ents = [line(1, (0, 0), (4, 5)), line(2, (0, 5), (8, 5))]
    assert collect_problems(ents, [con()]) == []
    problems = collect_problems(ents, [
        {"id": 11, "type": "point_on_line", "targets": [1],
         "enabled": True},
        {"id": 12, "type": "point_on_line", "targets": [1, 2],
         "endpoints": [2, None], "enabled": True},
    ])
    assert len(problems) == 2


def test_solve_satisfies_point_on_line():
    # point_on_line 只约束"点在线上"，目标线本身自由——LM 最小范数
    # 解允许点和线各动一半，断言语义为残差满足（点在移动后的线上）。
    # 实际绘制路径中端点先吸附到线上（初值已满足），求解只微调。
    sketch = SketchData(Datum((0, 0, 0), (0, 0, 1)))
    sketch.add_line((0, 0), (4, 2))    # id 1：p2 偏离目标线
    sketch.add_line((0, 5), (8, 5))    # id 2：水平目标线 y=5
    sketch.add_constraint("point_on_line", [1, 2], endpoints=[1, None])
    ok, _msg = solver_apply.solve_and_apply(sketch)
    assert ok
    e1, e2 = sketch.entities[0], sketch.entities[1]
    p = e1["p2"]
    x1, y1 = e2["p1"]
    dx = e2["p2"][0] - x1
    dy = e2["p2"][1] - y1
    norm = (dx * dx + dy * dy) ** 0.5
    r = ((p[0] - x1) * dy - (p[1] - y1) * dx) / norm
    assert abs(r) < 1e-4


def test_dof_analysis_includes_point_on_line():
    ents = [line(1, (0, 0), (4, 5)), line(2, (0, 5), (8, 5))]
    rep = solver_dof.analyze(ents, [con()])
    assert rep.n_equations == 1
    assert rep.dof == 7  # 2 条线 8 参数 - 1 方程
    assert rep.status == "under"