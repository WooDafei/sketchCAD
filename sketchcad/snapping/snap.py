"""捕捉系统（Phase 1：端点 / 中点）。

collect_candidates 是不依赖 bpy 的纯几何函数（可独立 pytest）；
像素级的最近特征选择依赖视口投影（location_3d_to_region_2d），
由 operator 层完成（见 operators/face_sketch.py:_find_snap）。

性能：候选线性遍历 O(n)，草图规模（<100 元素）远低于文档 §10
"单次捕捉查询 <5ms" 的预算，无需空间索引。
"""

SNAP_ENDPOINT = "ENDPOINT"
SNAP_MIDPOINT = "MIDPOINT"

SNAP_PIXEL_TOLERANCE = 12.0  # 捕捉触发半径（屏幕像素，与缩放无关）


def collect_candidates(entities):
    """从草图实体收集捕捉候选 [(u, v, kind), ...]。

    - 端点：每条 line 实体的 p1 / p2（相邻段共享端点自动去重）；
    - 中点：每条 line 的 (p1 + p2) / 2；
    - 坐标键按 round(6) 去重，端点与重合中点取先出现的端点。
    """
    seen = set()
    out = []
    for e in entities:
        if e.get("type") != "line":
            continue
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
    return out