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
  段与闭合轮廓的端点重合由此自然约束化。
"""

HORIZONTAL = "horizontal"
VERTICAL = "vertical"
COINCIDENT = "coincident"

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

    返回 (entity_id, pos) 或 None；pos 0=p1 / 1=p2。exclude_id 用于
    排除刚创建的段自身（其两端点必然与自己重合）。
    """
    key = (round(point[0], 6), round(point[1], 6))
    for e in entities:
        if e.get("type") != "line" or e["id"] == exclude_id:
            continue
        if (round(e["p1"][0], 6), round(e["p1"][1], 6)) == key:
            return e["id"], 0
        if (round(e["p2"][0], 6), round(e["p2"][1], 6)) == key:
            return e["id"], 1
    return None