"""镜像与阵列（SketchCAD.md v2 §6 Mirror/Pattern）。

纯几何函数，不依赖 bpy。交互遵循显式阶段化（与 Trim/Extend/Offset
同一交互原则：显式选择 + 实时预览 + 显式确认）：select 模式 M/P 键
激活 → 点击累加选择实体（再点移出）→ Enter 锁定 → 选择参照
（镜像轴 / 阵列方向线 / 阵列中心圆弧）→ 实时预览 → Enter 确认生成。

语义与 MVP 边界：
- 镜像（M）：选中实体关于轴线（line 实体）的对称副本，
  line/circle/arc 全支持。arc 端点身份对调（原 pos0 的镜像点成为
  新 pos1）——镜像翻转扫向（CW↔CCW），按"从 a1 逆时针扫到 a2"
  约定重标后端点互换，扫角（a2-a1）不变；
- 线性阵列（P + 线）：以线方向为阵列方向，步距带符号（鼠标在
  基准点正侧为正、负侧为负），副本 i 位于基准点 + i*step*û；
- 圆形阵列（P + 圆/弧）：以圆心为阵列中心，角增量带符号
  （正 = 逆时针）；arc 绕中心旋转时弧自身朝向同步旋转
  （a1/a2 加同一增量）；
- 份数 count 含原件，生成 count-1 个副本（主流 CAD 惯例）；
- 生成独立实体副本（MVP 不复制约束，与 offset 一致——参数化
  关联为远期）。
"""

from math import atan2, cos, pi, sin

from ..core.geom import TWO_PI

_GEOM_EPS = 1e-12


def mirror_point(p, a, b):
    """点 p 关于轴线 a->b 的镜像；零长轴返回 None。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length_sq = dx * dx + dy * dy
    if length_sq < _GEOM_EPS:
        return None
    px, py = p[0] - a[0], p[1] - a[1]
    # p' = a + 2*(d·(b-a))/|b-a|² * (b-a) - d，d = p - a
    dot = (px * dx + py * dy) / length_sq
    return (a[0] + 2.0 * dot * dx - px, a[1] + 2.0 * dot * dy - py)


def mirror_entity(e, axis_p1, axis_p2):
    """实体关于轴线的镜像副本 dict（不含 id，由 add_* 分配）。

    line/circle/arc 支持；轴退化（两端重合）返回 None。
    arc 端点身份对调（新 a1 = 原终点镜像、新 a2 = 原起点镜像），
    扫角不变。
    """
    construction = bool(e.get("construction", False))
    t = e.get("type")
    if t == "line":
        p1 = mirror_point(e["p1"], axis_p1, axis_p2)
        p2 = mirror_point(e["p2"], axis_p1, axis_p2)
        if p1 is None or p2 is None:
            return None
        return {"type": "line", "p1": [p1[0], p1[1]],
                "p2": [p2[0], p2[1]], "construction": construction}
    if t == "circle":
        c = mirror_point(e["center"], axis_p1, axis_p2)
        if c is None:
            return None
        return {"type": "circle", "center": [c[0], c[1]],
                "radius": float(e["radius"]), "construction": construction}
    if t == "arc":
        c = mirror_point(e["center"], axis_p1, axis_p2)
        if c is None:
            return None
        axis_ang = atan2(axis_p2[1] - axis_p1[1],
                         axis_p2[0] - axis_p1[0])
        # M(θ) = 2*axis_ang - θ 翻转扫向；CCW 重标后端点对调：
        # 原弧 [a1, a2] 镜像几何 = 从 M(a2) CCW 扫到 M(a1)
        delta = e["a2"] - e["a1"]
        na1 = (2.0 * axis_ang - e["a2"]) % TWO_PI
        return {"type": "arc", "center": [c[0], c[1]],
                "radius": float(e["radius"]),
                "a1": na1, "a2": na1 + delta,
                "construction": construction}
    return None


def mirror_entities(entities, axis_p1, axis_p2):
    """批量镜像；任一实体退化（零长轴）整体返回 None。"""
    out = []
    for e in entities:
        d = mirror_entity(e, axis_p1, axis_p2)
        if d is None:
            return None
        out.append(d)
    return out


def translate_entity(e, dx, dy):
    """实体平移副本 dict（不含 id）；未知类型返回 None。"""
    construction = bool(e.get("construction", False))
    t = e.get("type")
    if t == "line":
        return {"type": "line",
                "p1": [e["p1"][0] + dx, e["p1"][1] + dy],
                "p2": [e["p2"][0] + dx, e["p2"][1] + dy],
                "construction": construction}
    if t == "circle":
        return {"type": "circle",
                "center": [e["center"][0] + dx, e["center"][1] + dy],
                "radius": float(e["radius"]), "construction": construction}
    if t == "arc":
        return {"type": "arc",
                "center": [e["center"][0] + dx, e["center"][1] + dy],
                "radius": float(e["radius"]),
                "a1": float(e["a1"]), "a2": float(e["a2"]),
                "construction": construction}
    return None


def rotate_point(p, c, angle):
    """点 p 绕 c 逆时针旋转 angle（弧度）。"""
    cos_a, sin_a = cos(angle), sin(angle)
    px, py = p[0] - c[0], p[1] - c[1]
    return (c[0] + px * cos_a - py * sin_a,
            c[1] + px * sin_a + py * cos_a)


def rotate_entity(e, center, angle):
    """实体绕 center 旋转副本 dict（不含 id）；未知类型返回 None。

    arc 的圆心位置与弧自身朝向同时旋转（a1/a2 加同一角度）。
    """
    construction = bool(e.get("construction", False))
    t = e.get("type")
    if t == "line":
        p1 = rotate_point(e["p1"], center, angle)
        p2 = rotate_point(e["p2"], center, angle)
        return {"type": "line", "p1": [p1[0], p1[1]],
                "p2": [p2[0], p2[1]], "construction": construction}
    if t == "circle":
        c = rotate_point(e["center"], center, angle)
        return {"type": "circle", "center": [c[0], c[1]],
                "radius": float(e["radius"]), "construction": construction}
    if t == "arc":
        c = rotate_point(e["center"], center, angle)
        return {"type": "arc", "center": [c[0], c[1]],
                "radius": float(e["radius"]),
                "a1": e["a1"] + angle, "a2": e["a2"] + angle,
                "construction": construction}
    return None


def linear_pattern(entity, direction, step, count):
    """线性阵列副本列表（i = 1..count-1，平移 i*step*û）。

    direction 为方向向量（内部归一化）；step 带符号（负 = 反向
    阵列）；count 含原件。count < 2、|step| 过小或零方向返回 []。
    """
    if count is None or count < 2 or step is None or abs(step) < _GEOM_EPS:
        return []
    dx, dy = direction[0], direction[1]
    length = (dx * dx + dy * dy) ** 0.5
    if length < _GEOM_EPS:
        return []
    ux, uy = dx / length, dy / length
    out = []
    for i in range(1, count):
        d = i * step
        out.append(translate_entity(entity, d * ux, d * uy))
    return out


def circular_pattern(entity, center, angle_step, count):
    """圆形阵列副本列表（i = 1..count-1，绕 center 转 i*angle_step）。

    angle_step 带符号（正 = 逆时针）；count 含原件。
    count < 2 或角增量过小返回 []。
    """
    if (count is None or count < 2 or angle_step is None
            or abs(angle_step) < _GEOM_EPS):
        return []
    return [rotate_entity(entity, center, i * angle_step)
            for i in range(1, count)]


def add_entity_copy(sketch, d):
    """把镜像/阵列生成的实体 dict 写入 SketchData（自动分配 id）。

    返回新实体 id；未知类型返回 None（不写入）。
    """
    t = d.get("type")
    construction = bool(d.get("construction", False))
    if t == "line":
        sketch.add_line(d["p1"], d["p2"], construction=construction)
    elif t == "circle":
        sketch.add_circle(d["center"], d["radius"],
                          construction=construction)
    elif t == "arc":
        sketch.add_arc(d["center"], d["radius"], d["a1"], d["a2"],
                       construction=construction)
    else:
        return None
    return sketch.entities[-1]["id"]