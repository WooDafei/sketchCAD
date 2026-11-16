"""宿主顶点投影辅助点测试（SketchCAD.md §5 Phase 3）。

投影坐标计算、深度过滤、去重；捕捉候选容差命中与最近选择。
"""

from sketchcad.core.datum import Datum
from sketchcad.snapping import aid_points as ap

EPS = 1e-9


def _datum():
    # 原点 (0,0,0)、法线 +Z：u = x，v = y
    return Datum((0.0, 0.0, 0.0), (0.0, 0.0, 1.0))


def test_project_basic_xy_plane():
    pts = ap.collect_aid_points(
        [(1.0, 2.0, 0.0), (3.0, -1.0, 0.0)], _datum())
    assert ((1.0, 2.0) in pts) and ((3.0, -1.0) in pts)


def test_project_offset_origin():
    d = Datum((10.0, 5.0, 0.0), (0.0, 0.0, 1.0))
    pts = ap.collect_aid_points([(11.0, 6.0, 0.0)], d)
    assert len(pts) == 1 and abs(pts[0][0] - 1.0) < EPS \
        and abs(pts[0][1] - 1.0) < EPS


def test_depth_filter():
    d = _datum()
    near = (1.0, 2.0, 0.1)
    far = (3.0, -1.0, 50.0)
    pts = ap.collect_aid_points([near, far], d, depth_tol=1.0)
    assert len(pts) == 1 and abs(pts[0][0] - 1.0) < EPS
    # 无阈值时全部投影
    assert len(ap.collect_aid_points([near, far], d)) == 2


def test_dedup_round4():
    pts = ap.collect_aid_points(
        [(1.0, 1.0, 0.0), (1.00001, 1.0, 0.0), (1.5, 1.0, 0.0)], _datum())
    assert len(pts) == 2


def test_collect_aid_candidates_hit_and_nearest():
    aids = [(0.0, 0.0), (5.0, 5.0)]
    cands = ap.collect_aid_candidates(aids, (0.1, 0.05), 0.5)
    assert len(cands) == 1
    u, v, kind = cands[0]
    assert kind == ap.SNAP_AID_POINT
    assert (u, v) == (0.0, 0.0)


def test_collect_aid_candidates_out_of_tol():
    assert ap.collect_aid_candidates([(0.0, 0.0)], (3.0, 3.0), 0.5) == []


def test_collect_aid_candidates_empty():
    assert ap.collect_aid_candidates([], (0.0, 0.0), 1.0) == []