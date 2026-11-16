"""GPU 绘制回调（POST_VIEW）。

修复要点：草图几何与宿主网格表面共面，若开启深度测试（LESS_EQUAL），
共面片段会因 Z-fighting 被剔除，导致"草图模式下看不见画线"。
因此草图叠加层一律 depth_test_set('NONE')，保证始终可见。

Phase 3 圆弧：已确认 circle/arc 以折线近似绘制（geom.arc_points，
约 2° 步长）；绘制预览为橡皮筋圆 / 三点弧实时预览；捕捉标记新增
圆心（CENTER）。
"""

import blf
import gpu

import math

import bpy

from gpu_extras.batch import batch_for_shader

from ..core import geom
from ..core.state import state
from ..solver.dof import STATUS_FULL, STATUS_OVER
from ..snapping import snap

_MODE_HINT = {
    "draw": ("LMB: add point / place circle & arc | 2xLMB / Enter: "
             "end polyline | L/C/A: line/circle/arc | RMB: undo | "
             "Tab / S: select | ESC: finish & commit"),
    "select": ("LMB: pick entity / drag endpoint or center to solve | "
               "type number + Enter: dimension | T: trim | E: extend | "
               "Tab / S: draw | ESC: deselect / finish"),
}

_TOOL_HINT = {
    "trim": ("TRIM: click the part of a segment to REMOVE "
             "(cut at nearest intersection, keep the other side; "
             "lines only)"),
    "extend": ("EXTEND: 1st click pick segment | move to preview | "
               "2nd click confirms end point | ESC cancels pick"),
    "offset": ("OFFSET: 1st click pick line (closed profile/chain) / "
               "circle / arc | move to preview, or type distance + "
               "Enter | 2nd click confirms | ESC cancels pick"),
}

_DRAW_TOOL_HINT = {
    "line": "LINE: click to add points, double-click / Enter ends stroke",
    "circle": "CIRCLE: 1st click center, 2nd click radius",
    "arc": "ARC (3-point): click start -> point on arc -> end",
}

_COLOR_CROSS = (0.2, 0.6, 1.0, 1.0)
_COLOR_LINES = (0.0, 0.85, 1.0, 1.0)
_COLOR_RUBBER = (1.0, 0.9, 0.0, 0.9)
_COLOR_POINTS = (1.0, 1.0, 0.3, 1.0)
_COLOR_MOUSE = (1.0, 1.0, 1.0, 0.9)
_COLOR_SNAP_ENDPOINT = (1.0, 0.45, 0.1, 1.0)
_COLOR_SNAP_MIDPOINT = (1.0, 0.3, 0.85, 1.0)
_COLOR_SNAP_CENTER = (0.3, 1.0, 0.5, 1.0)
_COLOR_SNAP_ON_LINE = (0.2, 1.0, 1.0, 1.0)
_COLOR_CENTER_MARK = (0.75, 0.75, 0.75, 0.85)
_COLOR_SELECTED = (1.0, 0.5, 0.1, 1.0)

_CROSS_SIZE = 0.2


def _strip_coords(d, center, radius, a1, a2):
    """弧 CCW 离散折线的世界坐标序列（全圆时首尾重合闭合）。"""
    return [d.to_world(p[0], p[1])
            for p in geom.arc_points(center, radius, a1, a2)]


def _draw_line_strip(shader, d, coords, color, width=2.5):
    gpu.state.line_width_set(width)
    batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": coords})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)
    gpu.state.line_width_set(2.5)


def _draw_hud_text():
    font_id = 0
    blf.size(font_id, 12)
    mode = "SELECT" if state.mode == "select" else "DRAW"
    blf.color(font_id, 1.0, 1.0, 1.0, 0.85)
    blf.position(font_id, 24, 36)
    blf.draw(font_id, f"SketchCAD [{mode}] | " + _MODE_HINT[state.mode])

    y = 56
    if state.mode == "draw":
        blf.color(font_id, 0.65, 0.85, 1.0, 1.0)
        blf.position(font_id, 24, y)
        blf.draw(font_id, _DRAW_TOOL_HINT.get(
            state.draw_tool, state.draw_tool))
        y += 20

    if state.tool is not None:
        blf.color(font_id, 1.0, 0.65, 0.15, 1.0)
        blf.position(font_id, 24, y)
        blf.draw(font_id, _TOOL_HINT.get(state.tool, state.tool))
        y += 20

    if state.snap is not None:
        blf.color(font_id, 1.0, 0.75, 0.2, 1.0)
        blf.position(font_id, 24, y)
        blf.draw(font_id, "SNAP: " + state.snap[0])
        y += 20

    if state.mode == "select":
        parts = []
        if state.selected_entity is not None:
            etype = None
            if state.sketch is not None:
                for e in state.sketch.entities:
                    if e.get("id") == state.selected_entity:
                        etype = e.get("type")
                        break
            parts.append(f"SEL: {etype or '?'} {state.selected_entity}")
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
    y += 20

    if state.loops:
        blf.color(font_id, 0.35, 1.0, 0.45, 1.0)
        blf.position(font_id, 24, y)
        blf.draw(font_id, f"LOOPS: {len(state.loops)} closed profile(s)")


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

    # 2. 已确认实体（青色）：line 直接两点；circle/arc 折线近似
    seg_coords = []
    for p1, p2 in state.sketch.line_segments():
        seg_coords.append(d.to_world(p1[0], p1[1]))
        seg_coords.append(d.to_world(p2[0], p2[1]))
    if seg_coords:
        batch = batch_for_shader(shader, 'LINES', {"pos": seg_coords})
        shader.bind()
        shader.uniform_float("color", _COLOR_LINES)
        batch.draw(shader)

    for e in state.sketch.entities:
        t = e.get("type")
        if t == "circle":
            _draw_line_strip(shader, d, _strip_coords(
                d, e["center"], e["radius"], 0.0, geom.TWO_PI),
                _COLOR_LINES)
        elif t == "arc":
            _draw_line_strip(shader, d, _strip_coords(
                d, e["center"], e["radius"], e["a1"], e["a2"]),
                _COLOR_LINES)

    # 2.2 圆心常驻标记（circle/arc：灰色小点，点击可选中所属实体；
    # 选中实体的圆心橙色放大）——点圆心与按住拖动圆心的区分由
    # operator 层完成（实际移动几何才进入拖动求解）
    center_plain = []
    center_picked = []
    for e in state.sketch.entities:
        if e.get("type") not in ("circle", "arc"):
            continue
        c = e["center"]
        (center_picked if state.selected_entity == e.get("id")
         else center_plain).append(d.to_world(c[0], c[1]))
    if center_plain:
        gpu.state.point_size_set(6.0)
        batch = batch_for_shader(shader, 'POINTS', {"pos": center_plain})
        shader.bind()
        shader.uniform_float("color", _COLOR_CENTER_MARK)
        batch.draw(shader)
        gpu.state.point_size_set(8.0)
    if center_picked:
        gpu.state.point_size_set(10.0)
        batch = batch_for_shader(shader, 'POINTS', {"pos": center_picked})
        shader.bind()
        shader.uniform_float("color", _COLOR_SELECTED)
        batch.draw(shader)
        gpu.state.point_size_set(8.0)

    # 2.5 选中实体高亮（select 模式，加粗橙色）
    if state.selected_entity is not None:
        for e in state.sketch.entities:
            if e.get("id") != state.selected_entity:
                continue
            t = e.get("type")
            if t == "line":
                gpu.state.line_width_set(4.5)
                batch = batch_for_shader(shader, 'LINES', {"pos": [
                    d.to_world(e["p1"][0], e["p1"][1]),
                    d.to_world(e["p2"][0], e["p2"][1])]})
                shader.bind()
                shader.uniform_float("color", _COLOR_SELECTED)
                batch.draw(shader)
                gpu.state.line_width_set(2.5)
            elif t == "circle":
                _draw_line_strip(shader, d, _strip_coords(
                    d, e["center"], e["radius"], 0.0, geom.TWO_PI),
                    _COLOR_SELECTED, width=4.5)
            elif t == "arc":
                _draw_line_strip(shader, d, _strip_coords(
                    d, e["center"], e["radius"], e["a1"], e["a2"]),
                    _COLOR_SELECTED, width=4.5)
            break

    # 3. 橡皮筋：进行中笔划的末点 -> 鼠标候选点（黄色）
    if (state.mode == "draw" and state.draw_tool == "line"
            and state.stroke_open and state.points_2d
            and state.mouse_2d is not None):
        last = state.points_2d[-1]
        rubber = [
            d.to_world(last[0], last[1]),
            d.to_world(state.mouse_2d[0], state.mouse_2d[1]),
        ]
        batch = batch_for_shader(shader, 'LINES', {"pos": rubber})
        shader.bind()
        shader.uniform_float("color", _COLOR_RUBBER)
        batch.draw(shader)

    # 3.1 圆预览：圆心已定 -> 鼠标为半径的橡皮筋圆 + 中心点
    if (state.mode == "draw" and state.draw_tool == "circle"
            and state.circle_center is not None
            and state.mouse_2d is not None):
        c = state.circle_center
        r = ((state.mouse_2d[0] - c[0]) ** 2
             + (state.mouse_2d[1] - c[1]) ** 2) ** 0.5
        if r > 1e-9:
            _draw_line_strip(shader, d, _strip_coords(
                d, c, r, 0.0, geom.TWO_PI), _COLOR_RUBBER)
        batch = batch_for_shader(shader, 'POINTS', {
            "pos": [d.to_world(c[0], c[1])]})
        shader.bind()
        shader.uniform_float("color", _COLOR_RUBBER)
        batch.draw(shader)

    # 3.2 弧预览（三点式）：起点 ->（弧上点）-> 鼠标
    if (state.mode == "draw" and state.draw_tool == "arc"
            and state.arc_p1 is not None
            and state.mouse_2d is not None):
        m = state.mouse_2d
        if state.arc_p2 is None:
            rubber = [
                d.to_world(state.arc_p1[0], state.arc_p1[1]),
                d.to_world(m[0], m[1]),
            ]
            batch = batch_for_shader(shader, 'LINES', {"pos": rubber})
            shader.bind()
            shader.uniform_float("color", _COLOR_RUBBER)
            batch.draw(shader)
        else:
            res = geom.arc_from_3_points(state.arc_p1, state.arc_p2, m)
            if res is not None:
                center, r, a1, a2 = res
                _draw_line_strip(shader, d, _strip_coords(
                    d, center, r, a1, a2), _COLOR_RUBBER)
                # 弧预览同步显示圆心（三点已齐时圆心即确定）
                batch = batch_for_shader(shader, 'POINTS', {
                    "pos": [d.to_world(center[0], center[1])]})
                shader.bind()
                shader.uniform_float("color", _COLOR_RUBBER)
                batch.draw(shader)

    # 3.5 延伸预览（extend 第二段：被选端 -> 鼠标在延长线上的投影点）
    if state.extend_preview is not None:
        _pos, from_pt, to_pt = state.extend_preview
        batch = batch_for_shader(shader, 'LINES', {"pos": [
            d.to_world(from_pt[0], from_pt[1]),
            d.to_world(to_pt[0], to_pt[1])]})
        shader.bind()
        shader.uniform_float("color", _COLOR_RUBBER)
        batch.draw(shader)

    # 3.6 偏移预览（offset 第二段：黄色预览几何，侧向/距离由鼠标驱动）
    if state.offset_preview is not None:
        pv = state.offset_preview
        if pv["kind"] == "profile":
            coords = []
            for a, b in pv["segments"]:
                coords.append(d.to_world(a[0], a[1]))
                coords.append(d.to_world(b[0], b[1]))
            if coords:
                batch = batch_for_shader(shader, 'LINES', {"pos": coords})
                shader.bind()
                shader.uniform_float("color", _COLOR_RUBBER)
                batch.draw(shader)
        elif pv["kind"] == "circle":
            _draw_line_strip(shader, d, _strip_coords(
                d, pv["center"], pv["radius"], 0.0, geom.TWO_PI),
                _COLOR_RUBBER)
        elif pv["kind"] == "arc":
            _draw_line_strip(shader, d, _strip_coords(
                d, pv["center"], pv["radius"], pv["a1"], pv["a2"]),
                _COLOR_RUBBER)

    # 4. 确认点（黄白，画线笔划）
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

    # 5.5 捕捉标记（2026-11-16 实测反馈：参照 CAD 捕捉光标——特征
    # 符号屏幕固定大小随缩放换算世界尺寸：端点□ / 中点△ / 圆心○ /
    # 线上点×，颜色随特征类型区分，绘制于最高层）
    if state.snap is not None:
        kind, p = state.snap
        rv3d = bpy.context.region_data
        region = bpy.context.region
        if rv3d is not None and region is not None:
            half = 7.0 * rv3d.view_distance * 2.0 / max(region.height, 1)
        else:
            half = 0.1
        wp = d.to_world(p[0], p[1])
        x, y = wp[0], wp[1]
        mark_lines = None
        strip_pts = None
        if kind == snap.SNAP_ENDPOINT:
            # 空心方块（CAD 端点标记惯例）
            corners = [(x - half, y - half), (x + half, y - half),
                       (x + half, y + half), (x - half, y + half)]
            mark_lines = []
            for i in range(4):
                a, b = corners[i], corners[(i + 1) % 4]
                mark_lines += [d.to_world(a[0], a[1]),
                               d.to_world(b[0], b[1])]
        elif kind == snap.SNAP_MIDPOINT:
            # 三角形（CAD 中点标记惯例）
            tri = [(x, y + half * 1.2), (x - half, y - half * 0.8),
                   (x + half, y - half * 0.8)]
            mark_lines = []
            for i in range(3):
                a, b = tri[i], tri[(i + 1) % 3]
                mark_lines += [d.to_world(a[0], a[1]),
                               d.to_world(b[0], b[1])]
        elif kind == snap.SNAP_CENTER:
            # 圆圈（CAD 圆心标记惯例）
            strip_pts = [d.to_world(
                x + half * math.cos(i / 16.0 * 2.0 * math.pi),
                y + half * math.sin(i / 16.0 * 2.0 * math.pi))
                for i in range(17)]
        else:  # SNAP_ON_LINE：叉号（CAD 线上点/交点标记惯例）
            mark_lines = [
                d.to_world(x - half, y - half),
                d.to_world(x + half, y + half),
                d.to_world(x - half, y + half),
                d.to_world(x + half, y - half),
            ]
        color = (_COLOR_SNAP_ENDPOINT if kind == snap.SNAP_ENDPOINT
                 else _COLOR_SNAP_MIDPOINT if kind == snap.SNAP_MIDPOINT
                 else _COLOR_SNAP_CENTER if kind == snap.SNAP_CENTER
                 else _COLOR_SNAP_ON_LINE)
        shader.bind()
        shader.uniform_float("color", color)
        if mark_lines:
            batch = batch_for_shader(shader, 'LINES', {"pos": mark_lines})
            batch.draw(shader)
        if strip_pts:
            batch = batch_for_shader(shader, 'LINE_STRIP',
                                     {"pos": strip_pts})
            batch.draw(shader)

    gpu.state.point_size_set(3.0)
    gpu.state.line_width_set(1.0)
    gpu.state.blend_set('NONE')

    _draw_hud_text()