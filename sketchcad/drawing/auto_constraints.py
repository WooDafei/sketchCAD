"""自动约束识别（SketchCAD.md §4）：绘制中按轨迹阈值推断约束。

纯几何函数，不依赖 bpy。Phase 1 进入草图时强制正交正视，屏幕像素与
基准面 u/v 轴对齐；但导航事件 PASS_THROUGH 放行后用户可转视角，故
推断以基准面平面坐标差为准，像素阈值由 operator 层按当前视口缩放
换算为世界阈值传入——保持"与缩放无关"的直觉语义（同捕捉的
SNAP_PIXEL_TOLERANCE）。

MVP 推断两类：
- horizontal / vertical：新段两端点在基准面内的 u/v 差小于阈值 →
  约束 + 吸附坐标（使残差精确为 0，避免累积残差导致后续求解漂移）；
- coincident：新段端点与既有实体端点 round6 匹配 → 固接；折线连续
  段与闭合轮廓的端点重合由此自然约束化（Phase 3 起含 arc 端点）。
"""

from ..core.geom import arc_point

HORIZONTAL = "horizontal"
VERTICAL = "vertical"
COINCIDENT = "coincident"
POINT_ON_LINE = "point_on_line"

DEFAULT_HV_PIXEL_TOL = 8.0


def classify_segment(p1, p2, tol):
    """按基准面内坐标差推断水平/垂直，方向不明返回 None。

    |du| 与 |dv| 都超阈（斜线）或都低于阈（零长段，无方向语义且
    绘制层已防重）时返回 None。
    """
    du = p2[0] - p1[0]
    dv = p2[1] - p1[1]
    if abs(du) <= tol and abs(dv) <= tol:
        return None
    if abs(dv) <= tol:
        return HORIZONTAL
    if abs(du) <= tol:
        return VERTICAL
    return None


def snapped_segment(p1, p2, ctype):
    """按推断结果吸附段端点（以先确认的 p1 为基准），使约束残差为 0。

    p1 原样返回，p2 被吸附：horizontal 吸附 p2.y 到 p1.y（x 方向自由
    延伸），vertical 吸附 p2.x 到 p1.x（y 方向自由延伸）。
    """
    if ctype == HORIZONTAL:
        return p1, (p2[0], p1[1])
    if ctype == VERTICAL:
        return p1, (p1[0], p2[1])
    return p1, p2


def find_endpoint_hit(point, entities, exclude_id=None):
    """查找与 point 坐标重合（round6）的既有实体端点。

    返回 (entity_id, pos) 或 None；pos 0/1 = line 或 arc 的端点
    （arc 端点由 center/r/angle 现算）。exclude_id 用于排除刚创建
    的实体自身（其端点必然与自己重合）。
    """

    key = (round(point[0], 6), round(point[1], 6))
    for e in entities:
        if e["id"] == exclude_id:
            continue
        t = e.get("type")
        if t == "line":
            slots = ((0, e["p1"]), (1, e["p2"]))
        elif t == "arc":
            c, r = e["center"], e["radius"]
            slots = ((0, arc_point(c, r, e["a1"])),
                     (1, arc_point(c, r, e["a2"])))
        else:
            continue
        for pos, p in slots:
            if (round(p[0], 6), round(p[1], 6)) == key:
                return e["id"], pos
    return None

def find_line_hit(point, entities, tol, exclude_id=None):
    """查找 point 落在其内部附近的既有线段（垂足距 point <= tol）。

    返回 (entity_id, foot)——foot 为垂足坐标（垂足截断到段内），
    距离最近者；无命中返回 None。用于绘制端点落在既有线内部时的
    point_on_line 关联（端点重合由 find_endpoint_hit 优先处理）。
    tol 由 operator 层按视口缩放从像素换算为世界单位传入。
    """
    best = None
    best_d = tol
    for e in entities:
        if e.get("type") != "line" or e["id"] == exclude_id:
            continue
        ax, ay = e["p1"][0], e["p1"][1]
        dx, dy = e["p2"][0] - ax, e["p2"][1] - ay
        length_sq = dx * dx + dy * dy
        if length_sq < 1e-18:
            continue
        t = ((point[0] - ax) * dx + (point[1] - ay) * dy) / length_sq
        t = max(0.0, min(1.0, t))
        foot = (ax + t * dx, ay + t * dy)
        d = ((point[0] - foot[0]) ** 2 + (point[1] - foot[1]) ** 2) ** 0.5
        if d <= best_d:
            best_d = d
            best = (e["id"], foot)
    return best