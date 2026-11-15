"""草图数据模型：Schema v1（SketchCAD.md v2 §3）。

几何真相存储为带 version 字段的 JSON，写入容器对象的自定义属性
SketchCAD_Data；宿主 mesh 中提交的顶点/边只是退出时的产物。
Phase 1 仅支持 line 实体，交互模型为多笔划折线：一次左键单击新增一个
确认点，双击/Enter 结束当前笔划后可另起新笔划；撤销按"被弹点是否为
最后一条段的终点"精确对应，跨笔划安全。
"""

import json

from .datum import Datum

SCHEMA_VERSION = 1
PROP_KEY = "SketchCAD_Data"


class SketchData:
    def __init__(self, datum, host=None):
        self.datum = datum
        self.host = host if host is not None else {
            "object": None, "face_snapshot": None}
        self.entities = []

    # ---- 实体操作 ----

    def _next_id(self):
        return max((e["id"] for e in self.entities), default=0) + 1

    def add_line(self, p1, p2):
        self.entities.append({
            "id": self._next_id(),
            "type": "line",
            "p1": [p1[0], p1[1]],
            "p2": [p2[0], p2[1]],
        })

    def pop_line_ending_at(self, p):
        """仅当最后一条 line 的终点与 p 匹配（round6）时弹出该段。

        返回是否弹出。多笔划下新笔划起点不成段，撤销起点时
        不能误删上一笔划的段，故按终点坐标精确匹配。
        """
        if self.entities and self.entities[-1]["type"] == "line":
            p2 = self.entities[-1]["p2"]
            if (round(p2[0], 6), round(p2[1], 6)) == (round(p[0], 6), round(p[1], 6)):
                self.entities.pop()
                return True
        return False

    def line_segments(self):
        out = []
        for e in self.entities:
            if e["type"] == "line":
                out.append((tuple(e["p1"]), tuple(e["p2"])))
        return out

    def rebuild_points(self):
        """由 line 实体恢复确认点序列（跨笔划）。

        段首尾连续（同一笔划）时只追加终点；与上一段终点不连续的
        段起点是新笔划起点，需一并收集。
        """
        pts = []
        for e in self.entities:
            if e["type"] != "line":
                continue
            p1 = tuple(e["p1"])
            if not pts:
                pts.append(p1)
            elif (round(pts[-1][0], 6), round(pts[-1][1], 6)) != (
                    round(p1[0], 6), round(p1[1], 6)):
                pts.append(p1)
            pts.append(tuple(e["p2"]))
        return pts

    # ---- 序列化（往返一致：to_dict -> from_dict）----

    def to_dict(self):
        d = self.datum
        return {
            "version": SCHEMA_VERSION,
            "datum": {
                "origin": list(d.origin),
                "normal": list(d.normal),
                "x_axis": list(d.x_axis),
            },
            "host": self.host,
            "entities": self.entities,
        }

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
            return None
        dm = data.get("datum") or {}
        try:
            datum = Datum(dm["origin"], dm["normal"], dm.get("x_axis"))
        except (KeyError, ValueError, TypeError):
            return None
        s = cls(datum, host=data.get("host"))
        s.entities = list(data.get("entities") or [])
        return s

    def to_json(self):
        return json.dumps(self.to_dict())


def load_from_object(obj):
    """从容器对象自定义属性读取 SketchData；缺失/损坏/版本不符返回 None。"""
    raw = obj.get(PROP_KEY)
    if not raw:
        return None
    try:
        return SketchData.from_dict(json.loads(raw))
    except (ValueError, TypeError):
        return None


def save_to_object(obj, data):
    obj[PROP_KEY] = data.to_json()