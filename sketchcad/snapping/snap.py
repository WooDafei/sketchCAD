"""捕捉系统（Phase 1：端点 / 中点；Phase 3：线上点；Phase 3 圆弧：
圆心 / 弧端点 / 弧中点）。

collect_candidates 是不依赖 bpy 的纯几何函数（可独立 pytest）；
像素级的最近特征选择依赖视口投影（location_3d_to_region_2d），
由 operator 层完成（见 operators/face_sketch.py:_find_snap）。

线上点候选依赖鼠标位置（鼠标在既有线段上的垂足），故以可选
mouse_2d + tol（世界单位）参数传入生成；未提供时行为与 Phase 1
完全一致（线上点仅对 line 实体，弧的线上点为 MVP 边界外）。

性能：候选线性遍历 O(n)，草图规模（<100 元素）远低于文档 §10
"单次捕捉查询 <5ms" 的预算，无需空间索引。
"""

from ..core.geom import arc_mid_angle, arc_point

SNAP_ENDPOINT = "ENDPOINT"
SNAP_MIDPOINT = "MIDPOINT"
SNAP_ON_LINE = "ON_LINE"
SNAP_CENTER = "CENTER"

SNAP_PIXEL_TOLERANCE = 12.0  # 捕捉触发半径（屏幕像素，与缩放无关）

_ON_LINE_END_EPS = 1e-6  # 垂足参数贴近端点时视为端点（去重交给 seen）


def collect_candidates(entities, mouse_2d=None, tol=None):
    """从草图实体收集捕捉候选 [(u, v, kind), ...]。

    - 端点：line 实体 p1/p2 与 arc 实体两端点（相邻实体共享端点
      自动去重）；
    - 中点：line 的 (p1 + p2) / 2 与 arc 的弧中点；
    - 圆心：circle / arc 实体的 center；
    - 线上点：提供 mouse_2d + tol（世界单位容差）时，鼠标在每条
      line 上的垂足（截断到段内），垂足距鼠标超容差的跳过；
    - 坐标键按 round(6) 去重，端点与重合中点取先出现的端点。
    """
    seen = set()
    out = []
    for e in entities:
        t = e.get("type")
        if t == "line":
            p1, p2 = e["p1"], e["p2"]
            for p in (p1, p2):
                key = (round(p[0], 6), round(p[1], 6))
                if key not in seen:
                    seen.add(key)
                    out.append((p[0], p[1], SNAP_ENDPOINT))
            m = ((p1[0] + p2[0]) * 0.5, (p1[1] + p2[1]) * 0.5)
            mkey = (round(m[0], 6), round(m[1], 6))
            if mkey not in seen:
                seen.add(mkey)
                out.append((m[0], m[1], SNAP_MIDPOINT))
        elif t in ("circle", "arc"):
            c = e["center"]
            ckey = (round(c[0], 6), round(c[1], 6))
            if ckey not in seen:
                seen.add(ckey)
                out.append((c[0], c[1], SNAP_CENTER))
            if t == "arc":
                r = e["radius"]
                for a in (e["a1"], e["a2"]):
                    p = arc_point(c, r, a)
                    key = (round(p[0], 6), round(p[1], 6))
                    if key not in seen:
                        seen.add(key)
                        out.append((p[0], p[1], SNAP_ENDPOINT))
                m = arc_point(c, r, arc_mid_angle(e["a1"], e["a2"]))
                mkey = (round(m[0], 6), round(m[1], 6))
                if mkey not in seen:
                    seen.add(mkey)
                    out.append((m[0], m[1], SNAP_MIDPOINT))
    if mouse_2d is None or tol is None:
        return out
    for e in entities:
        if e.get("type") != "line":
            continue
        p1, p2 = e["p1"], e["p2"]
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        length_sq = dx * dx + dy * dy
        if length_sq < 1e-18:
            continue
        t = ((mouse_2d[0] - p1[0]) * dx + (mouse_2d[1] - p1[1]) * dy) \
            / length_sq
        if t <= _ON_LINE_END_EPS or t >= 1.0 - _ON_LINE_END_EPS:
            continue  # 贴近端点的垂足由端点候选覆盖
        foot = (p1[0] + t * dx, p1[1] + t * dy)
        dist_sq = ((mouse_2d[0] - foot[0]) ** 2
                   + (mouse_2d[1] - foot[1]) ** 2)
        if dist_sq > tol * tol:
            continue
        fkey = (round(foot[0], 6), round(foot[1], 6))
        if fkey not in seen:
            seen.add(fkey)
            out.append((foot[0], foot[1], SNAP_ON_LINE))
    return out