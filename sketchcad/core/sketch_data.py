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

Phase 3 起顶层新增可选 "visible" 字段（管理器隐藏/显示，缺省 True
兼容既有数据）；约束级联提供按端点（coincident@endpoint）与按类型
（如 trim/extend 后移除失效的 distance）的删除入口。

Phase 3 圆弧实体（§4 混合草图）：circle 存 center/radius（3 参数），
arc 存 center/radius/a1/a2（5 参数），弧从 a1 逆时针扫到 a2（方向
约定见 core/geom.py）；新约束 radius / concentric / tangent。

Phase 4 椭圆实体：ellipse 存 center/a/b/rot（4 参数 + 旋转角），
长半轴 a >= 短半轴 b（数据层规范化），rot 为长轴方向角、不参与
求解（MVP 无椭圆专属约束）；多边形不引入新实体类型（生成 N 条
line + coincident，见 drawing/polygon.py）。
"""

import json

from .datum import Datum
from .geom import arc_point, ellipse_normalize

SCHEMA_VERSION = 1
PROP_KEY = "SketchCAD_Data"


class SketchData:
    def __init__(self, datum, host=None):
        self.datum = datum
        self.host = host if host is not None else {
            "object": None, "face_snapshot": None}
        self.entities = []
        self.constraints = []
        self.visible = True

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

    def add_circle(self, center, radius, construction=False):
        self.entities.append({
            "id": self._next_id(),
            "type": "circle",
            "center": [center[0], center[1]],
            "radius": float(radius),
            "construction": bool(construction),
        })

    def add_arc(self, center, radius, a1, a2, construction=False):
        self.entities.append({
            "id": self._next_id(),
            "type": "arc",
            "center": [center[0], center[1]],
            "radius": float(radius),
            "a1": float(a1),
            "a2": float(a2),
            "construction": bool(construction),
        })

    def add_ellipse(self, center, a, b, rot, construction=False):
        """椭圆实体（4 几何参数 + 旋转）：center/a/b/rot。

        数据层不变式：a >= b（长半轴在前）；b > a 时交换两轴并把
        rot 转 90°（geom.ellipse_normalize），交互层任意击点顺序
        都落到一致的存储形态。rot 为长轴方向角（基准面坐标弧度），
        不参与约束求解（Phase 4 MVP：椭圆自由参数，无专属约束）。
        """
        A, B, ROT = geom.ellipse_normalize(float(a), float(b), float(rot))
        self.entities.append({
            "id": self._next_id(),
            "type": "ellipse",
            "center": [center[0], center[1]],
            "a": A,
            "b": B,
            "rot": ROT,
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

    def remove_constraints_for_entity(self, eid, ctype=None):
        """删除所有 target 引用 eid 的约束，返回删除数量。

        ctype 指定时仅删除该类型（如 trim/extend 后仅移除失效的
        distance，保留 horizontal/vertical 等方向语义约束）。
        """
        before = len(self.constraints)

        def hit(c):
            if eid not in (c.get("targets") or []):
                return False
            return ctype is None or c.get("type") == ctype

        self.constraints = [
            c for c in self.constraints if not hit(c)
        ]
        return before - len(self.constraints)

    def remove_constraints_for_endpoint(self, eid, pos):
        """删除 coincident 约束中引用 eid 实体 endpoint pos 的约束。

        endpoints 与 targets 一一对应；trim/extend 改变某端位置后，
        固接在原位置会与用户意图冲突，由编辑工具级联清理。固接类 =
        coincident 与 point_on_line（线上点），endpoints 缺省时按各自
        惯例默认值判定（coincident=[1,0]，point_on_line=[0,None]）。
        """
        before = len(self.constraints)

        def hit(c):
            ctype = c.get("type")
            if (ctype not in ("coincident", "point_on_line")
                    or eid not in (c.get("targets") or [])):
                return False
            ep = c.get("endpoints")
            if ep is None:
                ep = (1, 0) if ctype == "coincident" else (0, None)
            for i, t in enumerate(c.get("targets") or []):
                if t == eid and i < len(ep) and ep[i] == pos:
                    return True
            return False

        self.constraints = [
            c for c in self.constraints if not hit(c)
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

    def remove_constraints_across_selection(self, selected_ids):
        """删除固接类约束中跨选集边界的引用（变换工具级联语义）。

        coincident / point_on_line 的 targets 同时含选集内与选集外
        实体时，选集整体移动/旋转/缩放会拉断原连接——固接约束与
        变换意图矛盾，确认时级联移除；选集内部互连约束与完全不涉
        及选集实体的约束保留（方向/尺寸语义约束一律保留）。返回
        删除数量。
        """
        sel = set(selected_ids)

        def across(c):
            if c.get("type") not in ("coincident", "point_on_line"):
                return False
            targets = c.get("targets") or []
            return (any(t in sel for t in targets)
                    and any(t not in sel for t in targets))

        before = len(self.constraints)
        self.constraints = [c for c in self.constraints if not across(c)]
        return before - len(self.constraints)

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
            "visible": self.visible,
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
        s.visible = bool(data.get("visible", True))
        return s

    def to_json(self):
        return json.dumps(self.to_dict())


def arc_endpoints(e):
    """arc 实体的两端点坐标 (pos0, pos1)（center/r/a1/a2 现算）。"""
    c, r = e["center"], e["radius"]
    return arc_point(c, r, e["a1"]), arc_point(c, r, e["a2"])


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