"""实体参数化：草图实体 dict <-> numpy 参数向量。

求解器工作在参数向量 x 上，实体坐标的真相仍存于 schema dict；
ParamLayout 负责两者间的双向映射。布局在约束编辑时重建，
拖动/求解循环内不重建（引用同一 layout）。

参数布局：line -> [p1x, p1y, p2x, p2y]（4 参数/实体）。
"""

import numpy as np

PARAM_DIMS = {"line": 4}


class UnknownEntityTypeError(ValueError):
    pass


class ParamLayout:
    """实体 id -> 参数向量偏移布局（尺寸随实体集合确定）。"""

    __slots__ = ("offsets", "dims", "size")

    def __init__(self, entities):
        self.offsets = {}
        self.dims = {}
        n = 0
        for e in entities:
            t = e.get("type")
            if t not in PARAM_DIMS:
                raise UnknownEntityTypeError(f"未知实体类型: {t!r}")
            self.offsets[e["id"]] = n
            self.dims[e["id"]] = PARAM_DIMS[t]
            n += PARAM_DIMS[t]
        self.size = n

    def pack(self, entities):
        """实体 dict -> 参数向量 x。"""
        x = np.empty(self.size)
        for e in entities:
            off = self.offsets[e["id"]]
            p1, p2 = e["p1"], e["p2"]
            x[off] = p1[0]
            x[off + 1] = p1[1]
            x[off + 2] = p2[0]
            x[off + 3] = p2[1]
        return x

    def apply(self, x, entities):
        """解向量回写实体 dict（原位，保留 construction 等无关字段）。"""
        for e in entities:
            off = self.offsets[e["id"]]
            e["p1"] = [float(x[off]), float(x[off + 1])]
            e["p2"] = [float(x[off + 2]), float(x[off + 3])]