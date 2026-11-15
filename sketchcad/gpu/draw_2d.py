"""GPU 绘制回调（POST_VIEW）。

修复要点：草图几何与宿主网格表面共面，若开启深度测试（LESS_EQUAL），
共面片段会因 Z-fighting 被剔除，导致"草图模式下看不见画线"。
因此草图叠加层一律 depth_test_set('NONE')，保证始终可见。
"""

import blf
import gpu

from gpu_extras.batch import batch_for_shader

from ..core.state import state
from ..solver.dof import STATUS_FULL, STATUS_OVER
from ..snapping import snap

_MODE_HINT = {
    "draw": ("LMB: add point | 2xLMB / Enter: end polyline | "
             "RMB / Ctrl+Z: undo | Tab: select | ESC: finish & commit"),
    "select": ("LMB: pick line / drag endpoint to solve | "
               "type number + Enter: dimension | RMB / Ctrl+Z: undo | "
               "Tab: draw | ESC: deselect / finish"),
}

_COLOR_CROSS = (0.2, 0.6, 1.0, 1.0)
_COLOR_LINES = (0.0, 0.85, 1.0, 1.0)
_COLOR_RUBBER = (1.0, 0.9, 0.0, 0.9)
_COLOR_POINTS = (1.0, 1.0, 0.3, 1.0)
_COLOR_MOUSE = (1.0, 1.0, 1.0, 0.9)
_COLOR_SNAP_ENDPOINT = (1.0, 0.45, 0.1, 1.0)
_COLOR_SNAP_MIDPOINT = (1.0, 0.3, 0.85, 1.0)
_COLOR_SELECTED = (1.0, 0.5, 0.1, 1.0)


_CROSS_SIZE = 0.2


def _draw_hud_text():
    font_id = 0
    blf.size(font_id, 12)
    mode = "SELECT" if state.mode == "select" else "DRAW"
    blf.color(font_id, 1.0, 1.0, 1.0, 0.85)
    blf.position(font_id, 24, 36)
    blf.draw(font_id, f"SketchCAD [{mode}] | " + _MODE_HINT[state.mode])

    y = 56
    if state.snap is not None:
        blf.color(font_id, 1.0, 0.75, 0.2, 1.0)
        blf.position(font_id, 24, y)
        blf.draw(font_id, "SNAP: " + state.snap[0])
        y += 20

    if state.mode == "select":
        parts = []
        if state.selected_entity is not None:
            parts.append(f"SEL: line {state.selected_entity}")
        if state.input_buffer:
            parts.append("value: " + state.input_buffer + "_")
        if parts:
            blf.color(font_id, 1.0, 0.8, 0.3, 1.0)
            blf.position(font_id, 24, y)
            blf.draw(font_id, " | ".join(parts))
            y += 20
        if state.solve_failed:
            blf.color(font_id, 1.0, 0.35, 0.35, 1.0)
            blf.position(font_id, 24, y)
            blf.draw(font_id, "SOLVE FAILED - conflicting constraints?")
            y += 20

    rep = state.dof_report
    if rep is None or state.sketch is None or rep.n_params == 0:
        return
    n_cons = len(state.sketch.constraints)
    if rep.status == STATUS_OVER:
        text = (f"DOF: {rep.dof} | CONS: {n_cons} | "
                f"OVER-CONSTRAINED (redundant: {rep.n_redundant})")
        blf.color(font_id, 1.0, 0.35, 0.35, 1.0)
    elif rep.status == STATUS_FULL:
        text = f"DOF: 0 | CONS: {n_cons} | fully constrained"
        blf.color(font_id, 0.35, 1.0, 0.45, 1.0)
    else:
        text = f"DOF: {rep.dof} | CONS: {n_cons} | under-constrained"
        blf.color(font_id, 0.65, 0.85, 1.0, 1.0)
    blf.position(font_id, 24, y)
    blf.draw(font_id, text)



def draw_sketch():
    if not state.active or state.datum is None or state.sketch is None:
        return

    d = state.datum
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')

    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')
    gpu.state.point_size_set(8.0)
    gpu.state.line_width_set(2.5)

    # 1. 基准面中心十字准星
    o = d.origin
    cross = [
        o - d.x_axis * _CROSS_SIZE, o + d.x_axis * _CROSS_SIZE,
        o - d.y_axis * _CROSS_SIZE, o + d.y_axis * _CROSS_SIZE,
    ]
    batch = batch_for_shader(shader, 'LINES', {"pos": cross})
    shader.bind()
    shader.uniform_float("color", _COLOR_CROSS)
    batch.draw(shader)

    # 2. 已确认线段（青色）
    seg_coords = []
    for p1, p2 in state.sketch.line_segments():
        seg_coords.append(d.to_world(p1[0], p1[1]))
        seg_coords.append(d.to_world(p2[0], p2[1]))
    if seg_coords:
        batch = batch_for_shader(shader, 'LINES', {"pos": seg_coords})
        shader.bind()
        shader.uniform_float("color", _COLOR_LINES)
        batch.draw(shader)

    # 2.5 选中线段高亮（select 模式，加粗橙色）
    if state.selected_entity is not None:
        for e in state.sketch.entities:
            if (e.get("id") == state.selected_entity
                    and e.get("type") == "line"):
                gpu.state.line_width_set(4.5)
                batch = batch_for_shader(shader, 'LINES', {"pos": [
                    d.to_world(e["p1"][0], e["p1"][1]),
                    d.to_world(e["p2"][0], e["p2"][1])]})
                shader.bind()
                shader.uniform_float("color", _COLOR_SELECTED)
                batch.draw(shader)
                gpu.state.line_width_set(2.5)
                break

    # 3. 橡皮筋：进行中笔划的末点 -> 鼠标候选点（黄色）
    if state.stroke_open and state.points_2d and state.mouse_2d is not None:
        last = state.points_2d[-1]
        rubber = [
            d.to_world(last[0], last[1]),
            d.to_world(state.mouse_2d[0], state.mouse_2d[1]),
        ]
        batch = batch_for_shader(shader, 'LINES', {"pos": rubber})
        shader.bind()
        shader.uniform_float("color", _COLOR_RUBBER)
        batch.draw(shader)

    # 4. 确认点（黄白）
    if state.points_2d:
        pts = [d.to_world(u, v) for u, v in state.points_2d]
        batch = batch_for_shader(shader, 'POINTS', {"pos": pts})
        shader.bind()
        shader.uniform_float("color", _COLOR_POINTS)
        batch.draw(shader)

    # 5. 鼠标候选点（白色）
    if state.mouse_2d is not None:
        batch = batch_for_shader(
            shader, 'POINTS',
            {"pos": [d.to_world(state.mouse_2d[0], state.mouse_2d[1])]})
        shader.bind()
        shader.uniform_float("color", _COLOR_MOUSE)
        batch.draw(shader)

    # 5.5 捕捉标记（端点橙 / 中点洋红，绘制于最高层）
    # （视口 GPU 标注已弃用：尺寸信息改由 N-Panel Item 式显示）
    if state.snap is not None:
        kind, p = state.snap
        gpu.state.point_size_set(14.0)
        batch = batch_for_shader(
            shader, 'POINTS', {"pos": [d.to_world(p[0], p[1])]})
        shader.bind()
        shader.uniform_float(
            "color",
            _COLOR_SNAP_ENDPOINT if kind == snap.SNAP_ENDPOINT
            else _COLOR_SNAP_MIDPOINT)
        batch.draw(shader)
        gpu.state.point_size_set(8.0)

    gpu.state.point_size_set(3.0)
    gpu.state.line_width_set(1.0)
    gpu.state.blend_set('NONE')

    _draw_hud_text()