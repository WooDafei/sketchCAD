"""solver.params 测试：布局 / pack / apply 往返与错误处理。"""

import numpy as np
import pytest

from sketchcad.solver.params import ParamLayout, UnknownEntityTypeError


def line(eid, p1, p2):
    return {"id": eid, "type": "line", "p1": list(p1), "p2": list(p2)}


def test_layout_offsets_and_size():
    ents = [line(1, (0, 0), (1, 0)), line(2, (2, 2), (3, 3))]
    layout = ParamLayout(ents)
    assert layout.size == 8
    assert layout.offsets == {1: 0, 2: 4}


def test_pack_values():
    ents = [line(7, (1.5, -2.0), (3.25, 4.0))]
    x = ParamLayout(ents).pack(ents)
    assert np.allclose(x, [1.5, -2.0, 3.25, 4.0])


def test_apply_roundtrip_and_field_preservation():
    e = {"id": 1, "type": "line", "p1": [0, 0], "p2": [1, 1],
         "construction": True}
    layout = ParamLayout([e])
    layout.apply(np.array([5.0, 6.0, 7.0, 8.0]), [e])
    assert e["p1"] == [5.0, 6.0]
    assert e["p2"] == [7.0, 8.0]
    assert e["construction"] is True


def test_unknown_entity_type_raises():
    with pytest.raises(UnknownEntityTypeError):
        ParamLayout([{"id": 1, "type": "circle", "center": [0, 0],
                      "radius": 1.0}])