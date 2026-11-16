"""闭合轮廓检测（SketchCAD.md v2 Phase 3；Phase 3 圆弧适配）。

纯几何函数，不依赖 bpy。以 line/arc 实体端点坐标（round6 归并，与
端点匹配/捕捉语义一致）为节点建图：剪去度 <= 1 的开放尾巴后，
仅由度 = 2 节点构成的连通分量即为简单闭合轮廓。circle 实体本身
是闭合轮廓，直接独立成环（不进端点图）。

MVP 边界：共享端点的分叉（度 > 2，如"8"字形、十字连接）分量
不识别为闭合轮廓；交点在段中部（非端点）的交叉不构成图节点，
不影响检测。coincident 约束求解后端点坐标本就重合，故无需读约束。
"""

from ..core.geom import arc_point

_TOL_DECIMALS = 6


def _key(p):
    return (round(p[0], _TOL_DECIMALS), round(p[1], _TOL_DECIMALS))


def detect_closed_loops(entities):
    """检测闭合轮廓。

    返回环列表 [[entity_id, ...], ...]，每个环的实体 id 按连接顺序
    排列（便于后续 offset/挤出按序遍历）；circle 实体单独成环
    （单元素环）。
    """
    loops = []
    adj = {}  # 端点 key -> [(entity_id, 对端 key), ...]
    for e in entities:
        t = e.get("type")
        if t == "circle":
            if e.get("radius", 0.0) > 1e-9:
                loops.append([e["id"]])
            continue
        if t == "line":
            k1, k2 = _key(e["p1"]), _key(e["p2"])
        elif t == "arc":
            c, r = e["center"], e["radius"]
            k1 = _key(arc_point(c, r, e["a1"]))
            k2 = _key(arc_point(c, r, e["a2"]))
        else:
            continue
        if k1 == k2:
            continue  # 零长段不成环
        adj.setdefault(k1, []).append((e["id"], k2))
        adj.setdefault(k2, []).append((e["id"], k1))

    # 反复剪去度 <= 1 的节点及其边（开放链尾巴）
    changed = True
    while changed:
        changed = False
        stubs = [k for k, lst in adj.items() if len(lst) <= 1]
        for k in stubs:
            for eid, ok in adj.pop(k, []):
                lst = adj.get(ok)
                if lst:
                    adj[ok] = [(i, o) for (i, o) in lst
                               if not (i == eid and o == k)]
            changed = True

    # 剩余图按连通分量分派：全度 = 2 的分量走链成环
    # （circle 独立环已在实体遍历时收集进 loops，此处不再重置）
    visited = set()
    for start in adj:
        if start in visited:
            continue
        comp = []
        stack = [start]
        visited.add(start)
        while stack:
            k = stack.pop()
            comp.append(k)
            for _eid, ok in adj[k]:
                if ok not in visited:
                    visited.add(ok)
                    stack.append(ok)
        if any(len(adj[k]) != 2 for k in comp):
            continue  # 存在分叉节点，非简单闭合轮廓
        eid0, k1 = adj[start][0]
        chain = [eid0]
        prev, cur = start, k1
        while cur != start:
            edges = adj[cur]
            nxt = edges[0] if edges[0][1] != prev else edges[1]
            chain.append(nxt[0])
            prev, cur = cur, nxt[1]
        loops.append(chain)
    return loops