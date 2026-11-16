"""宿主顶点投影辅助点（SketchCAD.md v2 §5 3D 辅助点投影）。

纯几何函数，不依赖 bpy / mathutils（datum 以 duck typing 访问
origin/normal/x_axis/y_axis 的 .x/.y/.z 属性，Blender 内为真
Vector、venv pytest 中为 conftest FakeVector）。

进入草图时把宿主 mesh 顶点（世界坐标）投影到基准面，深度超阈值
的过滤（平面外过远的顶点投影会产生干扰描图的幻影点），坐标按
round(4) 去重；结果存会话状态用于显示与捕捉（AID_POINT，优先级
低于草图自身特征）。顶点集合进入时固化（会话内 modal 拦截编辑
操作，宿主几何不变），重进时刷新。MVP 边界：边线投影虚线不做，
仅顶点辅助点。
"""

SNAP_AID_POINT = "AID_POINT"

_DEDUP_DIGITS = 4


def collect_aid_points(points_world, datum, depth_tol=None):
    """世界顶点序列 → 基准面 2D 投影辅助点 [(u, v), ...]。

    depth_tol 给定时过滤到基准面有向距离绝对值超阈值的顶点；
    投影坐标按 round(4) 去重（同位置顶点只留一个）。
    """
    o, n = datum.origin, datum.normal
    x, y = datum.x_axis, datum.y_axis
    seen = set()
    out = []
    for co in points_world:
        dx, dy, dz = co[0] - o.x, co[1] - o.y, co[2] - o.z
        if depth_tol is not None:
            depth = dx * n.x + dy * n.y + dz * n.z
            if abs(depth) > depth_tol:
                continue
        u = dx * x.x + dy * x.y + dz * x.z
        v = dx * y.x + dy * y.y + dz * y.z
        key = (round(u, _DEDUP_DIGITS), round(v, _DEDUP_DIGITS))
        if key not in seen:
            seen.add(key)
            out.append((u, v))
    return out


def collect_aid_candidates(aid_points, mouse_2d, tol):
    """辅助点捕捉候选 [(u, v, SNAP_AID_POINT)]：容差内最近一点。

    与 snap.collect_candidates 的返回形式一致（元素列表）；无命中
    返回空列表。辅助点不参与 round 去重（collect_aid_points 已去重）。
    """
    if not aid_points or mouse_2d is None:
        return []
    best = None
    best_d = tol
    for u, v in aid_points:
        d = ((mouse_2d[0] - u) ** 2 + (mouse_2d[1] - v) ** 2) ** 0.5
        if d <= best_d:
            best_d = d
            best = (u, v, SNAP_AID_POINT)
    return [best] if best is not None else []