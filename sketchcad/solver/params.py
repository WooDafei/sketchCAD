"""实体参数化：草图实体 dict <-> numpy 参数向量。

求解器工作在参数向量 x 上，实体坐标的真相仍存于 schema dict；
ParamLayout 负责两者间的双向映射。布局在约束编辑时重建，
拖动/求解循环内不重建（引用同一 layout）。

参数布局：
- line   -> [p1x, p1y, p2x, p2y]（4 参数）；
- circle -> [cx, cy, r]（3 参数）；
- arc    -> [cx, cy, r, a1, a2]（5 参数；弧从 a1 逆时针扫到 a2，
  端点 = center + r*(cos a, sin a)，由残差层现算，见 constraints）。

types 记录实体类型映射，供残差层按类型取端点/判别校验。
"""

import numpy as np

PARAM_DIMS = {"line": 4, "circle": 3, "arc": 5}


class UnknownEntityTypeError(ValueError):
    pass


class ParamLayout:
    """实体 id -> 参数向量偏移布局（尺寸随实体集合确定）。"""

    __slots__ = ("offsets", "dims", "types", "size")

    def __init__(self, entities):
        self.offsets = {}
        self.dims = {}
        self.types = {}
        n = 0
        for e in entities:
            t = e.get("type")
            if t not in PARAM_DIMS:
                raise UnknownEntityTypeError(f"未知实体类型: {t!r}")
            self.offsets[e["id"]] = n
            self.dims[e["id"]] = PARAM_DIMS[t]
            self.types[e["id"]] = t
            n += PARAM_DIMS[t]
        self.size = n

    def pack(self, entities):
        """实体 dict -> 参数向量 x。"""
        x = np.empty(self.size)
        for e in entities:
            off = self.offsets[e["id"]]
            t = e.get("type")
            if t == "line":
                p1, p2 = e["p1"], e["p2"]
                x[off:off + 4] = (p1[0], p1[1], p2[0], p2[1])
            elif t == "circle":
                c, r = e["center"], e["radius"]
                x[off:off + 3] = (c[0], c[1], r)
            else:  # arc
                c, r = e["center"], e["radius"]
                x[off:off + 5] = (c[0], c[1], r, e["a1"], e["a2"])
        return x

    def apply(self, x, entities):
        """解向量回写实体 dict（原位，保留 construction 等无关字段）。"""
        for e in entities:
            off = self.offsets[e["id"]]
            t = e.get("type")
            if t == "line":
                e["p1"] = [float(x[off]), float(x[off + 1])]
                e["p2"] = [float(x[off + 2]), float(x[off + 3])]
            elif t == "circle":
                e["center"] = [float(x[off]), float(x[off + 1])]
                e["radius"] = float(x[off + 2])
            else:  # arc
                e["center"] = [float(x[off]), float(x[off + 1])]
                e["radius"] = float(x[off + 2])
                e["a1"] = float(x[off + 3])
                e["a2"] = float(x[off + 4])