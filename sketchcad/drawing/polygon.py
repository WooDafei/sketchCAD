"""正多边形生成器（Phase 4 绘图增强）。

多边形不引入新实体类型：一次生成 N 条首尾相接的 line 实体并自动
写入相邻段 coincident 固接约束（含闭合段），与既有约束系统、闭合
轮廓检测（loops）、Trim/Extend/Offset/镜像阵列全部天然兼容——
正多边形即"一张普通闭合折线轮廓"，后续所有编辑能力零适配可用。

纯几何函数，不依赖 bpy（venv pytest 可直接导入）。交互（边数输入、
两击定外接圆）由 operator 层完成（见 operators/face_sketch.py）。
"""

import math

MIN_SIDES = 3
MAX_SIDES = 32
DEFAULT_SIDES = 6


def polygon_vertices(center, radius, start_angle, n):
    """正多边形顶点序列：外接圆半径 + 起始角，n 个点 CCW 排列。

    返回 n 个顶点（不重复首点，闭合由"段 i 连接 v[i] -> v[(i+1)%n]"
    隐含）。n < 3 或半径退化（< 1e-9）返回 None（无效输入由调用方
    提示，不在数据层静默钳制）。
    """
    if n < MIN_SIDES or radius < 1e-9:
        return None
    step = 2.0 * math.pi / n
    return [(center[0] + radius * math.cos(start_angle + step * i),
             center[1] + radius * math.sin(start_angle + step * i))
            for i in range(n)]