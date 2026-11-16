"""捕捉候选测试：线上点（ON_LINE）生成与过滤（Phase 3 实测反馈）。"""

from sketchcad.snapping import snap


def line(eid, p1, p2):
    return {"id": eid, "type": "line", "p1": list(p1), "p2": list(p2)}


def kinds(cands):
    return {k for _u, _v, k in cands}


def test_base_candidates_unchanged_without_mouse():
    ents = [line(1, (0, 0), (10, 0))]
    cands = snap.collect_candidates(ents)
    assert kinds(cands) == {"ENDPOINT", "MIDPOINT"}


def test_on_line_candidate_generated():
    ents = [line(1, (0, 0), (10, 0))]
    cands = snap.collect_candidates(ents, mouse_2d=(4, 0.5), tol=1.0)
    assert "ON_LINE" in kinds(cands)
    pts = [(u, v) for u, v, k in cands if k == "ON_LINE"]
    assert (4.0, 0.0) in pts


def test_on_line_far_mouse_filtered():
    ents = [line(1, (0, 0), (10, 0))]
    cands = snap.collect_candidates(ents, mouse_2d=(4, 5.0), tol=1.0)
    assert "ON_LINE" not in kinds(cands)


def test_on_line_endpoint_zone_skipped():
    # 垂足参数贴近端点（t <= 1e-6）时由端点候选覆盖，不生成线上点
    ents = [line(1, (0, 0), (10, 0))]
    cands = snap.collect_candidates(ents, mouse_2d=(2e-7, 0.2), tol=1.0)
    pts = [(u, v) for u, v, k in cands if k == "ON_LINE"]
    assert pts == []


def test_on_line_dedup_with_midpoint():
    # 垂足恰为中点 (5,0)：已被 MIDPOINT 候选占据，不重复生成
    ents = [line(1, (0, 0), (10, 0))]
    cands = snap.collect_candidates(ents, mouse_2d=(5, 0.5), tol=1.0)
    on_line = [(u, v) for u, v, k in cands if k == "ON_LINE"]
    assert on_line == []


def test_on_line_two_lines():
    # 线 2（x=2 竖线）中点恰为 (2,0)——mouse 选 (3, 0.4) 避开候选去重：
    # 线 1 垂足 (3,0)、线 2 垂足 (2,0.4)（距鼠标 1.0 恰在容差内）
    ents = [line(1, (0, 0), (10, 0)), line(2, (2, -5), (2, 5))]
    cands = snap.collect_candidates(ents, mouse_2d=(3, 0.4), tol=1.0)
    pts = sorted((round(u, 6), round(v, 6))
                 for u, v, k in cands if k == "ON_LINE")
    assert (3.0, 0.0) in pts
    assert (2.0, 0.4) in pts

def circle(eid, center, radius):
    return {"id": eid, "type": "circle",
            "center": list(center), "radius": radius}


def arc(eid, center, radius, a1, a2):
    return {"id": eid, "type": "arc", "center": list(center),
            "radius": radius, "a1": a1, "a2": a2}


def test_circle_center_candidate():
    ents = [circle(1, (3, 4), 2.0)]
    cands = snap.collect_candidates(ents)
    assert kinds(cands) == {"CENTER"}
    assert ((3.0, 4.0, "CENTER")) in cands


def test_arc_candidates_center_endpoints_midpoint():
    import math
    ents = [arc(1, (0, 0), 2.0, 0.0, math.pi)]
    cands = snap.collect_candidates(ents)
    kinds_found = kinds(cands)
    assert "CENTER" in kinds_found
    assert "ENDPOINT" in kinds_found
    assert "MIDPOINT" in kinds_found
    pts = {(round(u, 6), round(v, 6)): k for u, v, k in cands}
    assert pts[(0.0, 0.0)] == "CENTER"
    assert pts[(2.0, 0.0)] == "ENDPOINT"   # a1 端
    assert pts[(-2.0, 0.0)] == "ENDPOINT"  # a2 端
    assert pts[(0.0, 2.0)] == "MIDPOINT"   # 弧中点（π/2 处）