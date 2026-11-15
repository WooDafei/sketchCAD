"""草图数据模型：Schema v1（SketchCAD.md v2 §3）。

几何真相存储为带 version 字段的 JSON，写入容器对象的自定义属性
SketchCAD_Data；宿主 mesh 中提交的顶点/边只是退出时的产物。
Phase 1 仅支持 line 实体，交互模型为多笔划折线：一次左键单击新增一个
确认点，双击/Enter 结束当前笔划后可另起新笔划；撤销按"被弹点是否为
最后一条段的终点"精确对应，跨笔划安全。

Phase 2 起数据模型承载 constraints 数组（§4 约束系统）。schema 细化：
- coincident 需要指定两端点（0=p1, 1=p2），存于可选 "endpoints" 字段，
  折线连接惯例默认 [1, 0]（前段终点接后段起点）；
- distance 携带 "value"（目标长度），残差采用 dx²+dy²-value² 光滑形式；
- 实体与约束共用同一 id 空间（_next_id 取两者最大值 +1）。
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
        self.constraints = []

    # ---- 实体操作 ----

    def _next_id(self):
        return max(
            [e["id"] for e in self.entities]
            + [c["id"] for c in self.constraints],
            default=0) + 1

    def add_line(self, p1, p2, construction=False):
        self.entities.append({
            "id": self._next_id(),
            "type": "line",
            "p1": [p1[0], p1[1]],
            "p2": [p2[0], p2[1]],
            "construction": bool(construction),
        })

    # ---- 约束操作 ----

    def add_constraint(self, ctype, targets, value=None,
                       endpoints=None, enabled=True):
        con = {
            "id": self._next_id(),
            "type": ctype,
            "targets": list(targets),
            "enabled": bool(enabled),
        }
        if value is not None:
            con["value"] = float(value)
        if endpoints is not None:
            con["endpoints"] = list(endpoints)
        self.constraints.append(con)
        return con

    def constraint_by_id(self, cid):
        for c in self.constraints:
            if c["id"] == cid:
                return c
        return None

    def remove_constraint(self, cid):
        """删除（彻底移除）；与禁用（enabled=False，可恢复）语义分离。"""
        for i, c in enumerate(self.constraints):
            if c["id"] == cid:
                self.constraints.pop(i)
                return True
        return False

    def set_constraint_enabled(self, cid, enabled):
        c = self.constraint_by_id(cid)
        if c is None:
            return False
        c["enabled"] = bool(enabled)
        return True

    def pop_line_ending_at(self, p):
        """仅当最后一条 line 的终点与 p 匹配（round6）时弹出该段。

        返回是否弹出。多笔划下新笔划起点不成段，撤销起点时
        不能误删上一笔划的段，故按终点坐标精确匹配。
        弹段时级联清理指向该实体的约束——Phase 2 起实体可能携带
        coincident / horizontal / vertical 等约束，悬空 target 会让
        求解器与 DOF 分析直接报错。
        """
        if self.entities and self.entities[-1]["type"] == "line":
            p2 = self.entities[-1]["p2"]
            if (round(p2[0], 6), round(p2[1], 6)) == (round(p[0], 6), round(p[1], 6)):
                eid = self.entities[-1]["id"]
                self.entities.pop()
                self.remove_constraints_for_entity(eid)
                return True
        return False

    def remove_constraints_for_entity(self, eid):
        """删除所有 target 引用 eid 的约束，返回删除数量。"""
        before = len(self.constraints)
        self.constraints = [
            c for c in self.constraints
            if eid not in (c.get("targets") or [])
        ]
        return before - len(self.constraints)

    def remove_entity_and_constraints(self, eid):
        """删除实体及其关联约束（编辑工具与撤销共用的级联语义）。"""
        self.remove_constraints_for_entity(eid)
        for i, e in enumerate(self.entities):
            if e["id"] == eid:
                self.entities.pop(i)
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
            "constraints": self.constraints,
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
        s.constraints = list(data.get("constraints") or [])
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