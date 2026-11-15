"""约束求解器：纯 Python + numpy，禁止 import bpy（可独立 pytest）。

模块分层：
- params      实体 dict <-> 参数向量布局（ParamLayout）；
- constraints 约束残差库（coincident / horizontal / vertical / distance）；
- lm          Levenberg-Marquardt 求解核心（Madsen-Nielsen 阻尼策略）；
- dof         DOF 分析与冗余约束检测（SVD 数值秩 + 增量定位）。
"""