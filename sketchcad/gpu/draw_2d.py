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

from bpy_extras.view3d_utils import (
    location_3d_to_region_2d,
    region_2d_to_origin_3d,
    region_2d_to_vector_3d,
)
from gpu_extras.batch import batch_for_shader
from mathutils.geometry import intersect_line_plane

from ..core import geom
from ..core.state import state
from ..drawing import polygon as poly
from ..solver.dof import STATUS_FULL, STATUS_OVER
from ..snapping import aid_points as aid_pts
from ..snapping import snap

_MODE_HINT = {
    "draw": ("LMB: add point / place circle & arc | 2xLMB / Enter: "
             "end polyline | L/C/A: line/circle/arc | RMB: undo | "
             "Tab / S: select | ESC: finish & commit"),
    "select": ("LMB: pick | Shift+click: toggle select | drag on "
               "empty: box select | drag endpoint or center to solve | "
               "number + Enter: dimension | DEL: delete | T: trim | "
               "E: extend | Tab / S: draw | ESC: deselect / finish"),
}

_TOOL_HINT = {
    "trim": ("TRIM: click the part of a line/arc to REMOVE "
             "(cut at nearest inner intersection, keep the other "
             "side; circles excluded)"),
    "extend": ("EXTEND: 1st click pick line/arc | move to preview | "
               "2nd click confirms end point | ESC cancels pick"),
    "offset": ("OFFSET: 1st click pick line (closed profile/chain) / "
               "circle / arc | move to preview, or type distance + "
               "Enter | 2nd click confirms | ESC cancels pick"),
    "mirror": ("MIRROR: click entities (click again to deselect) | "
               "Enter to lock | click a LINE as mirror axis | "
               "Enter generates | ESC steps back"),
    "pattern": ("PATTERN: click entities | Enter | click a line "
                "(linear) or circle/arc (circular) | move/type step + "
                "Enter | type count + Enter | ESC steps back"),
}

_DRAW_TOOL_HINT = {
    "line": "LINE: click to add points, double-click / Enter ends stroke",
    "circle": "CIRCLE: 1st click center, 2nd click radius",
    "arc": "ARC (3-point): click start -> point on arc -> end",
    "ellipse": ("ELLIPSE: 1st click center, 2nd click major axis end "
                "(a + rotation), 3rd click minor axis"),
    "polygon": ("POLYGON: type number + Enter to set sides | 1st click "
                "center, 2nd click vertex direction"),
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
_COLOR_SNAP_AID = (0.7, 0.4, 1.0, 1.0)
_COLOR_AID = (0.55, 0.55, 0.62, 0.65)
_COLOR_CENTER_MARK = (0.75, 0.75, 0.75, 0.85)
_COLOR_SELECTED = (1.0, 0.5, 0.1, 1.0)
_COLOR_SELECTED_SUB = (1.0, 0.7, 0.35, 0.9)
_COLOR_BOX_FILL = (0.2, 0.55, 1.0, 0.12)
_COLOR_BOX_EDGE = (0.35, 0.65, 1.0, 0.9)

_CROSS_SIZE = 0.2


def _strip_coords(d, center, radius, a1, a2):
    """弧 CCW 离散折线的世界坐标序列（全圆时首尾重合闭合）。"""
    return [d.to_world(p[0], p[1])
            for p in geom.arc_points(center, radius, a1, a2)]


def _strip_coords_ellipse(d, e):
    """ellipse 实体离散折线的世界坐标序列（闭合，首尾重合）。"""
    return [d.to_world(p[0], p[1])
            for p in geom.ellipse_points(e["center"], e["a"], e["b"],
                                         e["rot"])]


def _draw_line_strip(shader, d, coords, color, width=2.5):
    gpu.state.line_width_set(width)
    batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": coords})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)
    gpu.state.line_width_set(2.5)


def _entity_by_id(entities, eid):
    for e in entities:
        if e.get("id") == eid:
            return e
    return None


def _draw_entity_preview(shader, d, e, color, width=None):
    """预览实体绘制（mirror/pattern 生成结果：line/circle/arc/ellipse）。"""
    t = e.get("type")
    if t == "line":
        if width is not None:
            gpu.state.line_width_set(width)
        batch = batch_for_shader(shader, 'LINES', {"pos": [
            d.to_world(e["p1"][0], e["p1"][1]),
            d.to_world(e["p2"][0], e["p2"][1])]})
        shader.bind()
        shader.uniform_float("color", color)
        batch.draw(shader)
        if width is not None:
            gpu.state.line_width_set(2.5)
    elif t == "circle":
        _draw_line_strip(shader, d, _strip_coords(
            d, e["center"], e["radius"], 0.0, geom.TWO_PI), color,
            width=width or 2.5)
    elif t == "arc":
        _draw_line_strip(shader, d, _strip_coords(
            d, e["center"], e["radius"], e["a1"], e["a2"]), color,
            width=width or 2.5)
    elif t == "ellipse":
        _draw_line_strip(shader, d, _strip_coords_ellipse(d, e), color,
                         width=width or 2.5)


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
        if state.draw_tool == "polygon" and state.input_buffer:
            blf.color(font_id, 1.0, 0.8, 0.3, 1.0)
            blf.position(font_id, 24, y)
            blf.draw(font_id,
                     f"sides: {state.input_buffer}_ (Enter to confirm)")
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
        elif t == "ellipse":
            _draw_line_strip(shader, d, _strip_coords_ellipse(d, e),
                             _COLOR_LINES)

    # 2.2 圆心/椭圆中心常驻标记（circle/arc/ellipse：灰色小点，
    # 点击可选中所属实体；选中实体的中心橙色放大）——点中心与按住
    # 拖动中心的区分由 operator 层完成（实际移动几何才进入拖动求解）
    center_plain = []
    center_picked = []
    for e in state.sketch.entities:
        if e.get("type") not in ("circle", "arc", "ellipse"):
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

    # 2.55 宿主顶点投影辅助点（§5 3D 投影：暗灰小点，描图对齐参照，
    # 捕捉候选 AID_POINT 优先级低于草图自身特征）
    if state.aid_points:
        pts = [d.to_world(u, v) for u, v in state.aid_points]
        gpu.state.point_size_set(4.0)
        batch = batch_for_shader(shader, 'POINTS', {"pos": pts})
        shader.bind()
        shader.uniform_float("color", _COLOR_AID)
        batch.draw(shader)
        gpu.state.point_size_set(8.0)

    # 2.5 选集高亮（select 模式）：主选中亮橙加粗，其余选中次橙；
    # 单选场景只有一个（原行为），多选/框选后逐实体渲染
    if state.selected_entities:
        for eid in state.selected_entities:
            e = _entity_by_id(state.sketch.entities, eid)
            if e is None:
                continue
            main = eid == state.selected_entity
            _draw_entity_preview(
                shader, d, e,
                _COLOR_SELECTED if main else _COLOR_SELECTED_SUB,
                width=4.5 if main else 3.5)

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

    # 3.15 椭圆预览：圆心已定 → 长轴橡皮筋；长轴已定 → 按 b 规范化
    # 后的最终形态实时预览（与 add_ellipse 数据层同一 normalize）
    if (state.mode == "draw" and state.draw_tool == "ellipse"
            and state.ellipse_center is not None
            and state.mouse_2d is not None):
        c = state.ellipse_center
        m = state.mouse_2d
        if state.ellipse_major is None:
            rubber = [d.to_world(c[0], c[1]), d.to_world(m[0], m[1])]
            batch = batch_for_shader(shader, 'LINES', {"pos": rubber})
            shader.bind()
            shader.uniform_float("color", _COLOR_RUBBER)
            batch.draw(shader)
        else:
            a, rot = state.ellipse_major
            b = ((m[0] - c[0]) ** 2 + (m[1] - c[1]) ** 2) ** 0.5
            A, B, R = geom.ellipse_normalize(a, b, rot)
            if A > 1e-9:
                pts_w = [d.to_world(p[0], p[1]) for p in
                         geom.ellipse_points(c, A, B, R)]
                _draw_line_strip(shader, d, pts_w, _COLOR_RUBBER)

    # 3.16 多边形预览：中心已定 → 外接圆橡皮筋 + 顶点折线实时预览
    if (state.mode == "draw" and state.draw_tool == "polygon"
            and state.polygon_center is not None
            and state.mouse_2d is not None):
        c = state.polygon_center
        m = state.mouse_2d
        r = ((m[0] - c[0]) ** 2 + (m[1] - c[1]) ** 2) ** 0.5
        if r > 1e-9:
            start = math.atan2(m[1] - c[1], m[0] - c[0])
            verts = poly.polygon_vertices(c, r, start,
                                          state.polygon_sides)
            if verts is not None:
                coords = []
                for i in range(len(verts)):
                    nxt = verts[(i + 1) % len(verts)]
                    coords += [d.to_world(verts[i][0], verts[i][1]),
                               d.to_world(nxt[0], nxt[1])]
                batch = batch_for_shader(shader, 'LINES',
                                         {"pos": coords})
                shader.bind()
                shader.uniform_float("color", _COLOR_RUBBER)
                batch.draw(shader)
            _draw_line_strip(shader, d, _strip_coords(
                d, c, r, 0.0, geom.TWO_PI), _COLOR_RUBBER)

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

    # 3.5 延伸预览（extend 第二段：line 弦线 / arc 圆周延长段）
    if state.extend_preview is not None:
        pv = state.extend_preview
        if pv["kind"] == "arc":
            lo, hi = pv["strip"]
            _draw_line_strip(shader, d, _strip_coords(
                d, pv["center"], pv["radius"], lo, hi), _COLOR_RUBBER)
        else:
            from_pt, to_pt = pv["from_pt"], pv["to_pt"]
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

    # 3.7 Mirror：选集高亮 + 镜像轴橙色加粗 + 镜像预览（黄色）
    if state.tool == 'mirror':
        for eid in state.mirror_pending:
            e = _entity_by_id(state.sketch.entities, eid)
            if e is not None:
                _draw_entity_preview(shader, d, e, _COLOR_SELECTED,
                                     width=4.5)
        if state.mirror_axis is not None:
            ax = _entity_by_id(state.sketch.entities, state.mirror_axis)
            if ax is not None and ax.get("type") == "line":
                gpu.state.line_width_set(4.5)
                batch = batch_for_shader(shader, 'LINES', {"pos": [
                    d.to_world(ax["p1"][0], ax["p1"][1]),
                    d.to_world(ax["p2"][0], ax["p2"][1])]})
                shader.bind()
                shader.uniform_float("color", _COLOR_SELECTED)
                batch.draw(shader)
                gpu.state.line_width_set(2.5)
        if state.mirror_preview:
            for e in state.mirror_preview:
                _draw_entity_preview(shader, d, e, _COLOR_RUBBER)

    # 3.8 Pattern：选集高亮 + 参照高亮（线加粗 / 中心橙点）+
    # 阵列预览（黄色，step=单副本 / count=全量）
    if state.tool == 'pattern':
        for eid in state.pattern_pending:
            e = _entity_by_id(state.sketch.entities, eid)
            if e is not None:
                _draw_entity_preview(shader, d, e, _COLOR_SELECTED,
                                     width=4.5)
        if state.pattern_axis is not None:
            ax = _entity_by_id(state.sketch.entities, state.pattern_axis)
            if ax is not None:
                t = ax.get("type")
                if t == "line":
                    gpu.state.line_width_set(4.5)
                    batch = batch_for_shader(shader, 'LINES', {"pos": [
                        d.to_world(ax["p1"][0], ax["p1"][1]),
                        d.to_world(ax["p2"][0], ax["p2"][1])]})
                    shader.bind()
                    shader.uniform_float("color", _COLOR_SELECTED)
                    batch.draw(shader)
                    gpu.state.line_width_set(2.5)
                elif t in ("circle", "arc"):
                    gpu.state.point_size_set(10.0)
                    batch = batch_for_shader(shader, 'POINTS', {
                        "pos": [d.to_world(ax["center"][0],
                                           ax["center"][1])]})
                    shader.bind()
                    shader.uniform_float("color", _COLOR_SELECTED)
                    batch.draw(shader)
                    gpu.state.point_size_set(8.0)
        if state.pattern_preview:
            for e in state.pattern_preview:
                _draw_entity_preview(shader, d, e, _COLOR_RUBBER)

    # 3.9 框选矩形（select 模式空白处拖动）：按下/当前屏幕坐标经
    # 射线与基准面求交转 2D，四角 to_world 以基准面轴对齐矩形绘制
    # （与 entities_in_box 的 2D 判定同一语义）；半透明填充 + 边框
    if (state.box_select_start is not None
            and state.box_select_cur is not None):
        rv3d = bpy.context.region_data
        region = bpy.context.region
        if rv3d is not None and region is not None:
            corners_2d = []
            for sx, sy in (state.box_select_start, state.box_select_cur):
                ro = region_2d_to_origin_3d(region, rv3d, (sx, sy))
                rv = region_2d_to_vector_3d(region, rv3d, (sx, sy))
                hit = intersect_line_plane(
                    ro, ro + rv, d.origin, d.normal)
                if hit is None:
                    corners_2d = None
                    break
                corners_2d.append(d.to_2d(hit))
            if corners_2d is not None:
                (x1, y1), (x2, y2) = corners_2d
                quad_2d = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
                quad_w = [d.to_world(u, v) for u, v in quad_2d]
                tris = [quad_w[0], quad_w[1], quad_w[2],
                        quad_w[0], quad_w[2], quad_w[3]]
                batch = batch_for_shader(shader, 'TRIS', {"pos": tris})
                shader.bind()
                shader.uniform_float("color", _COLOR_BOX_FILL)
                batch.draw(shader)
                edges = []
                for i in range(4):
                    edges += [quad_w[i], quad_w[(i + 1) % 4]]
                batch = batch_for_shader(shader, 'LINES', {"pos": edges})
                shader.bind()
                shader.uniform_float("color", _COLOR_BOX_EDGE)
                batch.draw(shader)

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
        elif kind == aid_pts.SNAP_AID_POINT:
            # 菱形（宿主顶点投影辅助点标记惯例）
            dia = [(x, y + half), (x + half, y),
                   (x, y - half), (x - half, y)]
            mark_lines = []
            for i in range(4):
                a, b = dia[i], dia[(i + 1) % 4]
                mark_lines += [d.to_world(a[0], a[1]),
                               d.to_world(b[0], b[1])]
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
                 else _COLOR_SNAP_AID if kind == aid_pts.SNAP_AID_POINT
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