"""SketchData constraints 扩展测试（走 conftest 的 mathutils stub 链）。"""

from sketchcad.core.datum import Datum
from sketchcad.core.sketch_data import SketchData


def make_sketch():
    return SketchData(Datum((0, 0, 0), (0, 0, 1)))


def test_initial_constraints_empty():
    assert make_sketch().constraints == []


def test_add_constraint_and_roundtrip():
    s = make_sketch()
    s.add_line((0, 0), (3, 0))
    s.add_constraint("horizontal", [1])
    con = s.add_constraint("distance", [1], value=3.0)
    assert con["id"] == 3
    assert con["value"] == 3.0

    s2 = SketchData.from_dict(s.to_dict())
    assert s2 is not None
    assert [c["id"] for c in s2.constraints] == [2, 3]
    assert s2.constraints[1]["value"] == 3.0


def test_coincident_endpoints_roundtrip():
    s = make_sketch()
    s.add_line((0, 0), (1, 0))
    s.add_line((1, 0), (1, 1))
    s.add_constraint("coincident", [1, 2], endpoints=[1, 0])
    s2 = SketchData.from_dict(s.to_dict())
    assert s2.constraints[0]["endpoints"] == [1, 0]


def test_id_space_shared_with_entities():
    s = make_sketch()
    s.add_line((0, 0), (1, 0))
    s.add_line((1, 0), (2, 0))
    c = s.add_constraint("horizontal", [1])
    assert c["id"] == 3


def test_remove_and_enable():
    s = make_sketch()
    s.add_line((0, 0), (1, 1))
    c = s.add_constraint("horizontal", [1])
    assert s.set_constraint_enabled(c["id"], False) is True
    assert s.constraint_by_id(c["id"])["enabled"] is False
    assert s.remove_constraint(c["id"]) is True
    assert s.remove_constraint(c["id"]) is False
    assert s.constraint_by_id(c["id"]) is None


def test_add_line_construction_field():
    s = make_sketch()
    s.add_line((0, 0), (1, 0))
    s.add_line((5, 5), (6, 6), construction=True)
    assert s.entities[0]["construction"] is False
    assert s.entities[1]["construction"] is True

def test_pop_line_cascades_constraints():
    s = make_sketch()
    s.add_line((0, 0), (3, 0))          # id 1
    s.add_line((3, 0), (3, 2))          # id 2
    s.add_constraint("coincident", [1, 2], endpoints=[1, 0])  # id 3
    s.add_constraint("horizontal", [1])                       # id 4
    s.add_constraint("horizontal", [2])                       # id 5
    assert s.pop_line_ending_at((3, 2)) is True   # 弹段 2
    assert len(s.entities) == 1
    ids = {c["id"] for c in s.constraints}
    assert 3 not in ids and 5 not in ids          # 指向段 2 的被级联删除
    assert 4 in ids                               # 指向段 1 的保留


def test_remove_entity_and_constraints():
    s = make_sketch()
    s.add_line((0, 0), (1, 0))
    s.add_line((5, 5), (6, 6))
    s.add_constraint("horizontal", [1])
    assert s.remove_entity_and_constraints(1) is True
    assert s.entities[0]["id"] == 2
    assert s.constraints == []
    assert s.remove_entity_and_constraints(99) is False

# ---- Phase 3：visible 字段与按端点/类型级联删除 ----

def test_visible_roundtrip():
    s = make_sketch()
    assert s.visible is True
    s.add_line((0, 0), (1, 0))
    s2 = SketchData.from_dict(s.to_dict())
    assert s2.visible is True
    s.visible = False
    s3 = SketchData.from_dict(s.to_dict())
    assert s3.visible is False


def test_visible_backward_compat():
    # 旧数据无 visible 字段：缺省 True（schema v1 可选字段兼容）
    s = make_sketch()
    s.add_line((0, 0), (1, 0))
    raw = s.to_dict()
    del raw["visible"]
    s2 = SketchData.from_dict(raw)
    assert s2 is not None and s2.visible is True


def make_trim_fixture():
    """带端点固接与尺寸约束的两段折线（模拟 trim 前状态）。"""
    s = make_sketch()
    s.add_line((0, 0), (5, 0))           # id 1
    s.add_line((5, 0), (5, 3))           # id 2
    s.add_constraint("coincident", [1, 2], endpoints=[1, 0])   # id 3
    s.add_constraint("coincident", [2, 1], endpoints=[0, 1])   # id 4（同点反向引用）
    s.add_constraint("distance", [1], value=5.0)               # id 5
    s.add_constraint("horizontal", [1])                        # id 6
    return s


def test_remove_constraints_for_endpoint():
    s = make_trim_fixture()
    # trim 段 1 保留 p1 侧（p2 变为交点）：清理挂在其 endpoint 1 的固接
    n = s.remove_constraints_for_endpoint(1, 1)
    assert n == 2  # id 3（targets[0]=1@ep[0]=1）与 id 4（targets[1]=1@ep[1]=1）
    ids = {c["id"] for c in s.constraints}
    assert 3 not in ids and 4 not in ids
    assert 5 in ids and 6 in ids
    assert s.remove_constraints_for_endpoint(1, 1) == 0


def test_remove_constraints_for_entity_ctype():
    s = make_trim_fixture()
    # 仅删 distance（长度语义失效），horizontal（方向语义）保留
    n = s.remove_constraints_for_entity(1, "distance")
    assert n == 1
    ids = {c["id"] for c in s.constraints}
    assert 5 not in ids
    assert 3 in ids and 4 in ids and 6 in ids
    # 不带 ctype：全部级联（原有语义不变）
    n2 = s.remove_constraints_for_entity(1)
    assert n2 == 3

def test_remove_endpoint_includes_point_on_line():
    # 固接类含 point_on_line：trim/extend 后端点被旧线拽住的约束一并清理
    s = make_sketch()
    s.add_line((0, 0), (5, 0))           # id 1（被编辑）
    s.add_line((3, 2), (3, 8))           # id 2（目标线）
    s.add_constraint("coincident", [1, 2], endpoints=[1, 0])        # id 3
    s.add_constraint("point_on_line", [1, 2], endpoints=[1, None])  # id 4
    s.add_constraint("horizontal", [1])                             # id 5
    n = s.remove_constraints_for_endpoint(1, 1)
    assert n == 2
    ids = {c["id"] for c in s.constraints}
    assert 3 not in ids and 4 not in ids
    assert 5 in ids


def test_remove_endpoint_default_endpoints_semantics():
    # endpoints 缺省时按惯例默认值判定：coincident=[1,0]，point_on_line=[0,None]
    s = make_sketch()
    s.add_line((0, 0), (5, 0))   # id 1
    s.add_line((5, 0), (5, 3))   # id 2
    s.add_constraint("coincident", [1, 2])   # 缺省 [1,0]
    s.add_constraint("point_on_line", [2, 1], endpoints=[0, None])
    assert s.remove_constraints_for_endpoint(1, 1) == 1
    assert s.remove_constraints_for_endpoint(2, 0) == 1