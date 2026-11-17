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
        ParamLayout([{"id": 1, "type": "spline"}])

def ellipse(eid, center, a, b, rot):
    return {"id": eid, "type": "ellipse", "center": list(center),
            "a": a, "b": b, "rot": rot}


def test_ellipse_layout_and_pack():
    ents = [line(1, (0, 0), (1, 0)), ellipse(2, (5, 5), 3.0, 2.0, 0.7)]
    layout = ParamLayout(ents)
    # line 4 参数 + ellipse 4 参数
    assert layout.size == 8
    assert layout.offsets[2] == 4
    x = layout.pack(ents)
    assert np.allclose(x, [0, 0, 1, 0, 5, 5, 3.0, 2.0])


def test_ellipse_apply_preserves_rot():
    # apply 回写 center/a/b，rot 为静态属性原样保留
    e = ellipse(1, (5, 5), 3.0, 2.0, 0.7)
    layout = ParamLayout([e])
    layout.apply(np.array([1.0, 2.0, 4.0, 1.5]), [e])
    assert e["center"] == [1.0, 2.0]
    assert e["a"] == 4.0 and e["b"] == 1.5
    assert e["rot"] == 0.7