"""提交段序列与快照 key（Phase 3 替换式提交）。

提交物（宿主 mesh 中的轮廓边）与草图 JSON 的关系：JSON 是参数化
真相，提交物是退出时的导出快照。重进修改尺寸后再退出，若旧快照
几何与新几何并存，mesh 中会留下"原来的草图"（双影）。替换式提交：
按上一轮提交快照（局部坐标段序列，存容器 SketchCAD_Committed，
读写见 manager）定位并删除旧轮廓边，再提交当前几何；被建模引用
（关联面）或已被手动编辑的旧边保留并计数提示，不触碰非本草图
提交物的几何。

纯几何函数，不依赖 bpy：实体 -> 段序列离散、段端点 -> 无序 key
（round6，与端点匹配/顶点缓存语义一致）。bmesh 的删除执行由
operator 层完成（face_sketch._remove_committed_edges）。
"""

from ..core.geom import TWO_PI, arc_points, ellipse_points

_TOL_DECIMALS = 6


def _k(p):
    z = p[2] if len(p) > 2 else 0.0
    return (round(p[0], _TOL_DECIMALS), round(p[1], _TOL_DECIMALS),
            round(z, _TOL_DECIMALS))


def seg_key(a, b):
    """段端点的无序 key（边无方向，两端任意序等价）。"""
    ka, kb = _k(a), _k(b)
    return (min(ka, kb), max(ka, kb))


def entity_segments(e):
    """实体 -> 提交用折线段序列 [(p1, p2), ...]（2D 平面坐标）。

    line 一段；circle 离散为闭合折线（首尾点重合）；arc 离散为
    开放折线；ellipse 离散为闭合折线（与 GPU 预览同一密度）。
    GPU 预览与 mesh 提交共用同一密度（geom.ARC_MAX_STEP /
    ELLIPSE_MAX_STEP），保证提交快照与视口所见一致。
    """
    t = e.get("type")
    if t == "line":
        p1, p2 = e["p1"], e["p2"]
        return [((p1[0], p1[1]), (p2[0], p2[1]))]
    if t == "circle":
        pts = arc_points(e["center"], e["radius"], 0.0, TWO_PI)
    elif t == "arc":
        pts = arc_points(e["center"], e["radius"], e["a1"], e["a2"])
    elif t == "ellipse":
        pts = ellipse_points(e["center"], e["a"], e["b"], e["rot"])
    else:
        return []
    return list(zip(pts, pts[1:]))