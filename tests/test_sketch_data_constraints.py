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