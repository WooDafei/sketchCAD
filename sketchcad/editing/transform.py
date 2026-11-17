"""实体变换：移动 / 旋转 / 缩放（Phase 4 编辑增强）。

纯几何函数，不依赖 bpy（venv pytest 可直接导入）。副本语义：
变换函数返回变换后的实体 dict 副本（不含 id，由调用方经
apply_geometry 写回原实体）——预览与确认共用同一计算（预览=
绘制副本，确认=几何字段写回），不产生中间态。

支持的实体类型：line / circle / arc / ellipse。
- 旋转：arc 圆心位置与弧自身朝向同步旋转（a1/a2 加同一角度）；
  ellipse 长轴方向 rot 同步旋转（center 位置旋转）；
- 缩放：等比缩放（radius / a / b 同步缩放保持形状，rot 不变）；
  factor <= 0 无意义返回 None。

约束级联语义（见 SketchData.remove_constraints_across_selection）：
变换确认时移除固接类约束（coincident / point_on_line）中跨选集
边界的引用对——选集整体变换后原连接点必然分离，固接约束与用户
意图矛盾；选集内部相对约束保留（整体平移/旋转/等比缩放不破坏
内部相对关系）。

交互遵循显式两段式（与 Extend/Offset 同一原则：显式选择 + 实时
预览 + 显式确认）：select 模式 G（移动）/ R（旋转）/ X（缩放）
激活（选集非空）→ 点击基准点 → 实时预览 → 点击确认或数值 +
回车确认（旋转=角度度数、缩放=比例因子），ESC 逐阶段回退。
"""

import math

_GEOM_EPS = 1e-12
_MIN_SCALE = 1e-9


def moved_entity(e, dx, dy):
    """实体平移副本 dict（不含 id）；未知类型返回 None。"""
    t = e.get("type")
    out = {"type": t,
           "construction": bool(e.get("construction", False))}
    if t == "line":
        out["p1"] = [e["p1"][0] + dx, e["p1"][1] + dy]
        out["p2"] = [e["p2"][0] + dx, e["p2"][1] + dy]
    elif t in ("circle", "arc", "ellipse"):
        out["center"] = [e["center"][0] + dx, e["center"][1] + dy]
        if t == "circle":
            out["radius"] = float(e["radius"])
        elif t == "arc":
            out["radius"] = float(e["radius"])
            out["a1"] = float(e["a1"])
            out["a2"] = float(e["a2"])
        else:  # ellipse
            out["a"] = float(e["a"])
            out["b"] = float(e["b"])
            out["rot"] = float(e["rot"])
    else:
        return None
    return out


def rotated_entity(e, c, angle):
    """实体绕 c 逆时针旋转 angle（弧度）的副本 dict；未知类型 None。

    line 端点旋转；circle/arc/ellipse 的 center 旋转；arc 的扫向
    （a1/a2）与 ellipse 的长轴方向（rot）同步加同一角度。
    """
    cos_a, sin_a = math.cos(angle), math.sin(angle)

    def rp(p):
        px, py = p[0] - c[0], p[1] - c[1]
        return (c[0] + px * cos_a - py * sin_a,
                c[1] + px * sin_a + py * cos_a)

    t = e.get("type")
    out = {"type": t,
           "construction": bool(e.get("construction", False))}
    if t == "line":
        p1 = rp(e["p1"])
        p2 = rp(e["p2"])
        out["p1"] = [p1[0], p1[1]]
        out["p2"] = [p2[0], p2[1]]
    elif t in ("circle", "arc", "ellipse"):
        nc = rp(e["center"])
        out["center"] = [nc[0], nc[1]]
        if t == "circle":
            out["radius"] = float(e["radius"])
        elif t == "arc":
            out["radius"] = float(e["radius"])
            out["a1"] = float(e["a1"]) + angle
            out["a2"] = float(e["a2"]) + angle
        else:  # ellipse
            out["a"] = float(e["a"])
            out["b"] = float(e["b"])
            out["rot"] = float(e["rot"]) + angle
    else:
        return None
    return out


def scaled_entity(e, c, factor):
    """实体以 c 为基准等比缩放 factor 的副本 dict；无效返回 None。

    factor None / <=0 / 过小返回 None；line 端点、center、radius
    与 ellipse 的 a/b 同步缩放（形状不变），arc 扫角与 ellipse
    rot 不变。
    """
    if factor is None or factor <= 0 or factor < _MIN_SCALE:
        return None
    t = e.get("type")
    out = {"type": t,
           "construction": bool(e.get("construction", False))}
    if t == "line":
        out["p1"] = [c[0] + (e["p1"][0] - c[0]) * factor,
                     c[1] + (e["p1"][1] - c[1]) * factor]
        out["p2"] = [c[0] + (e["p2"][0] - c[0]) * factor,
                     c[1] + (e["p2"][1] - c[1]) * factor]
    elif t in ("circle", "arc", "ellipse"):
        out["center"] = [c[0] + (e["center"][0] - c[0]) * factor,
                         c[1] + (e["center"][1] - c[1]) * factor]
        if t == "circle":
            out["radius"] = float(e["radius"]) * factor
        elif t == "arc":
            out["radius"] = float(e["radius"]) * factor
            out["a1"] = float(e["a1"])
            out["a2"] = float(e["a2"])
        else:  # ellipse
            out["a"] = float(e["a"]) * factor
            out["b"] = float(e["b"]) * factor
            out["rot"] = float(e["rot"])
    else:
        return None
    return out


def transform_batch(entities, fn):
    """批量变换：对每个实体应用 fn（实体 -> 副本 dict）。

    任一实体变换失败（None）时整体返回 None——调用方以当前选集
    的完整性为原子单位，不产生半成品几何。
    """
    out = []
    for e in entities:
        d = fn(e)
        if d is None:
            return None
        out.append(d)
    return out


def apply_geometry(dst, src):
    """把变换副本 src 的几何字段写回实体 dst（原位，保留身份字段）。

    id / type / construction 属于身份字段一律不覆盖（防御：调用方
    传入带 id 的 dict 也不会污染原实体身份）；几何字段（p1/p2、
    center、radius、a1/a2、a/b/rot）按副本写入。
    """
    for k, v in src.items():
        if k in ("id", "type", "construction"):
            continue
        dst[k] = v