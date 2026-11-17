"""gpu.toolbar 布局 / 命中 / 提示查询测试（纯函数部分，无 bpy 依赖）。"""

from sketchcad.gpu import toolbar


def actions(rects):
    return [r[0] for r in rects]


def test_layout_draw_mode_button_list():
    rects = toolbar.layout("draw", 800)
    acts = actions(rects)
    # 顶部模式切换 + 5 绘图工具 + 底部撤销/退出
    assert acts[0] == "mode"
    assert acts[1:-2] == [a for a, _l, _t in toolbar._DRAW_BTNS]
    assert acts[-2:] == ["undo", "finish"]
    assert len(rects) == 8


def test_layout_select_mode_button_list():
    rects = toolbar.layout("select", 800)
    acts = actions(rects)
    assert acts[0] == "mode"
    assert acts[1:-2] == [a for a, _l, _t in toolbar._SELECT_BTNS]
    assert acts[-2:] == ["undo", "finish"]
    assert len(rects) == 11


def test_layout_rects_bounded_and_ordered():
    h = 600
    rects = toolbar.layout("draw", h)
    prev_top = None
    for _a, _l, _t, (x0, y0, x1, y1) in rects:
        # 矩形有效且在 region 内
        assert x1 > x0 and y1 > y0
        assert y0 >= 0 and y1 <= h
        assert x0 == toolbar._BAR_X
        # 自顶向下排列且相邻按钮不重叠（y 严格递减）
        if prev_top is not None:
            assert y1 < prev_top
        prev_top = y0


def test_hit_test_inside_and_outside():
    rects = toolbar.layout("draw", 600)
    first = rects[0]
    x0, y0, x1, y1 = first[3]
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    assert toolbar.hit_test(mx, my, rects) == first[0]
    # 远离工具栏（视口中央右侧）
    assert toolbar.hit_test(400, 300, rects) is None
    # 按钮间空隙（间距 _BTN_PAD 内）
    gap_y = first[3][1] - toolbar._BTN_PAD / 2
    assert toolbar.hit_test((x0 + x1) / 2, gap_y, rects) is None


def test_hit_test_returns_topmost_matching():
    # 命中判定按清单顺序先到先得；按钮不重叠时无歧义
    rects = toolbar.layout("select", 800)
    for action, _l, _t, (x0, y0, x1, y1) in rects:
        assert toolbar.hit_test(
            (x0 + x1) / 2, (y0 + y1) / 2, rects) == action


def test_tooltip_for_all_buttons():
    for mode, group in (("draw", toolbar._DRAW_BTNS),
                        ("select", toolbar._SELECT_BTNS)):
        for action, _l, tip in group:
            assert toolbar.tooltip_for(mode, action, 800) == tip
    assert toolbar.tooltip_for("draw", "undo", 800) == "撤销（右键 / Ctrl+Z）"
    assert toolbar.tooltip_for("draw", "nonexistent", 800) is None


def test_is_active_follows_session_state():
    # is_active 读取会话状态单例（reset 后 draw 模式 line 工具激活）
    from sketchcad.core.state import state
    state.reset()
    try:
        state.mode = "draw"
        state.draw_tool = "line"
        assert toolbar.is_active("draw:line") is True
        assert toolbar.is_active("draw:circle") is False
        assert toolbar.is_active("tool:trim") is False
        state.mode = "select"
        state.tool = "trim"
        assert toolbar.is_active("tool:trim") is True
        assert toolbar.is_active("draw:line") is False
        assert toolbar.is_active("mode") is False
        assert toolbar.is_active("undo") is False
    finally:
        state.reset()