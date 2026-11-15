"""面驱动草图 Modal Operator（Phase 0 + Phase 1）。

修复要点：
- 视图对齐改用 normal.to_track_quat('Z', 'Y')（原实现旋转矩阵存在
  行/列转置方向与 Y 轴手性错误，导致每次进入视图姿态不一致）；
- 基准面坐标系持久化 + 宿主面快照匹配（重进续画，坐标系不再漂移）；
- 法线变换使用逆转置矩阵，兼容宿主对象非均匀缩放；
- 视图导航事件 PASS_THROUGH 交给 Blender 默认处理；
- 右键 / Ctrl+Z 撤销上一点，ESC 提交退出；
- 折线以双击左键或 Enter 结束（末点捕捉回起点时自然闭合轮廓），
  结束后可另起新折线，数据层按独立 line 段存储。
"""

import time

import bmesh

import bpy

from bpy_extras.view3d_utils import (
    location_3d_to_region_2d,
    region_2d_to_origin_3d,
    region_2d_to_vector_3d,
)
from mathutils.geometry import intersect_line_plane

from .. import manager
from ..core import sketch_data as skd
from ..core.datum import Datum
from ..core.state import state
from ..drawing import auto_constraints as ac
from ..drawing import picking
from ..gpu.draw_2d import draw_sketch
from ..solver import apply as solver_apply
from ..solver import dof as solver_dof
from ..snapping import snap

_NAV_EVENTS = {'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'MIDDLEMOUSE'}

# select 模式数值输入的事件键（主键盘数字 / 小键盘数字 / 小数点）
_DIGIT_KEYS = {str(i) for i in range(10)}
_NUMPAD_DIGIT = {f"NUMPAD_{i}": str(i) for i in range(10)}
_PERIOD_KEYS = ('PERIOD', 'NUMPAD_PERIOD')

# 草图会话中放行给 N-Panel（UI region）的鼠标事件（约束管理/阈值配置）
_UI_PASS_EVENTS = {'LEFTMOUSE', 'RIGHTMOUSE', 'MIDDLEMOUSE', 'MOUSEMOVE',
                   'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}

# 拖动实时求解的节流间隔（~60Hz；20 实体规模单次求解 ~15ms，见 bench_20x30）
_DRAG_SOLVE_INTERVAL = 1.0 / 60.0


class SKETCH_OT_face_driven_sketch(bpy.types.Operator):
    """基于选中面进入 SketchCAD 草图模式"""
    bl_idname = "sketch.face_driven_sketch"
    bl_label = "Start Face Sketch"

    # ---- 进入 ----

    def invoke(self, context, event):
        if context.mode != 'EDIT_MESH':
            self.report({'WARNING'}, "请进入编辑模式并选中一个面")
            return {'CANCELLED'}

        obj = context.active_object
        rv3d = context.region_data
        if obj is None or obj.type != 'MESH' or rv3d is None:
            self.report({'WARNING'}, "请在 3D 视口中对网格对象操作")
            return {'CANCELLED'}

        bm = bmesh.from_edit_mesh(obj.data)
        selected = [f for f in bm.faces if f.select]
        if len(selected) != 1:
            self.report({'WARNING'}, "请且仅选中一个面作为基准面")
            return {'CANCELLED'}
        face = selected[0]

        mw = obj.matrix_world
        normal_w = (mw.inverted().transposed().to_3x3() @ face.normal).normalized()
        center_w = mw @ face.calc_center_median()

        # Phase 0 重进对齐：按宿主对象名 + 面快照匹配已有草图
        matched_obj, matched_data = manager.find_matching_sketch(
            obj.name, center_w, normal_w)
        if matched_data is not None:
            sketch = matched_data
            container_name = matched_obj.name
            self.report({'INFO'},
                        f"继续草图 {container_name}"
                        f"（{len(sketch.entities)} 条线段）")
        else:
            sketch = skd.SketchData(
                Datum(center_w, normal_w),
                host={
                    "object": obj.name,
                    "face_snapshot": {
                        "center": list(center_w),
                        "normal": list(normal_w),
                    },
                },
            )
            container_name = None
            # S1 孤儿提示：宿主有历史草图但当前面不匹配（原基准面可能已被删改）
            existing = manager.count_sketches_for_host(obj.name)
            if existing:
                self.report({'WARNING'},
                            f"宿主 {obj.name} 已有 {existing} 条草图但当前面"
                            "不匹配任何快照（原基准面可能已被删除/改动）；"
                            "已新建草图，旧草图可 F3 搜索 "
                            "\"Delete SketchCAD Sketch\" 清理")
            else:
                self.report({'INFO'}, "新建草图")

        state.reset()
        state.active = True
        state.datum = sketch.datum
        state.sketch = sketch
        state.container_name = container_name
        state.points_2d = sketch.rebuild_points()
        self._refresh_dof()

        self._align_view(context, sketch.datum)

        state.draw_handler = bpy.types.SpaceView3D.draw_handler_add(
            draw_sketch, (), 'WINDOW', 'POST_VIEW')

        context.window_manager.modal_handler_add(self)
        self.report({'INFO'},
                    "SketchCAD 草图模式 | 画线: 左键加点, 双击/Enter 结束"
                    " | Tab 选择模式(点线+输数值+回车定尺寸)"
                    " | 右键/Ctrl+Z 撤销 | ESC 提交退出")
        return {'RUNNING_MODAL'}

    def _align_view(self, context, datum):
        rv3d = context.region_data
        space = context.space_data

        state.view_rot = rv3d.view_rotation.copy()
        state.view_loc = rv3d.view_location.copy()
        state.view_dist = rv3d.view_distance
        state.is_persp = rv3d.is_perspective
        state.shading_type = space.shading.type
        state.show_xray = space.shading.show_xray
        state.clip_end = space.clip_end

        # 视图 Z 轴对齐基准面法线（从法线正侧正视），视图 Y 尽量朝世界 Z
        rv3d.view_rotation = datum.normal.to_track_quat('Z', 'Y')
        rv3d.is_perspective = False
        space.shading.type = 'SOLID'
        space.shading.show_xray = True
        space.clip_end *= 10.0
        bpy.ops.view3d.view_selected()

    # ---- Modal ----


    def modal(self, context, event):
        if not state.active:
            return {'FINISHED'}
        context.area.tag_redraw()

        # N-Panel（UI region）事件放行：草图进行中可用侧边栏
        if (event.type in _UI_PASS_EVENTS
                and self._cursor_in_ui_region(context, event)):
            return {'PASS_THROUGH'}

        if event.type == 'MOUSEMOVE':
            self._update_mouse(context, event)
            if state.dragging is not None:
                self._drag_update()
            return {'RUNNING_MODAL'}

        if event.type == 'TAB' and event.value == 'PRESS':
            self._toggle_mode()
            return {'RUNNING_MODAL'}

        if (event.value == 'PRESS' and state.mode == "select"
                and state.selected_entity is not None):
            if self._on_input_key(event.type):
                return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            if state.mouse_2d is not None:
                if state.mode == "select":
                    self._press_select(context, state.mouse_2d)
                elif not state.stroke_open:
                    # 新折线起点：只记录点，不与历史末点拼段
                    state.points_2d.append(state.mouse_2d)
                    state.stroke_open = True
                else:
                    self._confirm_segment(context, state.mouse_2d)
            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            if state.dragging is not None:
                state.dragging = None
                self._refresh_dof()
                self.report({'INFO'}, "拖动求解结束")
            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE' and event.value == 'DOUBLE_CLICK':
            # 双击：结束当前折线；若末点捕捉回起点则已自然闭合轮廓
            state.stroke_open = False
            return {'RUNNING_MODAL'}

        if event.type == 'RET' and event.value == 'PRESS':
            if state.mode == "select":
                self._apply_dimension()
            else:
                state.stroke_open = False
            return {'RUNNING_MODAL'}

        if ((event.type == 'RIGHTMOUSE' or (event.type == 'Z' and event.ctrl))
                and event.value == 'PRESS'):
            self._undo_point()
            return {'RUNNING_MODAL'}

        if event.type == 'ESC' and event.value == 'PRESS':
            if state.mode == "select" and (
                    state.selected_entity is not None or state.input_buffer):
                # 先清选中/取消输入，再按一次 ESC 才退出草图
                state.selected_entity = None
                state.input_buffer = ""
                return {'RUNNING_MODAL'}
            return self._finish(context)

        if event.type in _NAV_EVENTS:
            return {'PASS_THROUGH'}

        return {'RUNNING_MODAL'}

    def _update_mouse(self, context, event):
        region = context.region
        rv3d = context.region_data
        if region is None or rv3d is None:
            return
        coord = (event.mouse_region_x, event.mouse_region_y)
        view_vector = region_2d_to_vector_3d(region, rv3d, coord)
        ray_origin = region_2d_to_origin_3d(region, rv3d, coord)
        hit = intersect_line_plane(
            ray_origin, ray_origin + view_vector,
            state.datum.origin, state.datum.normal)
        state.snap = None
        if hit is None:
            return
        snap_result = self._find_snap(context, event)
        if snap_result is not None:
            state.snap = snap_result
            state.mouse_2d = snap_result[1]
        else:
            state.mouse_2d = state.datum.to_2d(hit)

    def _find_snap(self, context, event):
        """像素级最近捕捉特征；端点优先于中点。返回 (kind, (u, v)) 或 None。"""
        region = context.region
        rv3d = context.region_data
        if region is None or rv3d is None:
            return None
        mx, my = event.mouse_region_x, event.mouse_region_y
        d = state.datum
        best = None  # (priority, dist, kind, (u, v))
        for u, v, kind in snap.collect_candidates(state.sketch.entities):
            px = location_3d_to_region_2d(region, rv3d, d.to_world(u, v))
            if px is None:
                continue
            dist = ((px[0] - mx) ** 2 + (px[1] - my) ** 2) ** 0.5
            if dist > snap.SNAP_PIXEL_TOLERANCE:
                continue
            priority = 0 if kind == snap.SNAP_ENDPOINT else 1
            cand = (priority, dist, kind, (u, v))
            if best is None or (cand[0], cand[1]) < (best[0], best[1]):
                best = cand
        if best is None:
            return None
        return best[2], best[3]

    def _undo_point(self):
        if not state.points_2d:
            return
        popped = state.points_2d.pop()
        # 仅当最后一条段的终点恰为被弹点时才撤销该段（跨笔划安全）；
        # 弹段在 sketch_data 内级联清理其自动约束
        state.sketch.pop_line_ending_at(popped)
        if not state.points_2d:
            state.stroke_open = False
        if (state.selected_entity is not None
                and all(e["id"] != state.selected_entity
                        for e in state.sketch.entities)):
            # 选中的段已被撤销，清空选择态防悬空
            state.selected_entity = None
            state.input_buffer = ""
        self._refresh_dof()

    def _confirm_segment(self, context, p2):
        """确认连续段：推断水平/垂直并吸附，写入实体与自动约束。

        端点与既有实体端点重合（捕捉对齐 / 折线连续 / 闭合轮廓）时
        写入 coincident 固接，端点定位沿用 round6 语义。
        """
        prev = state.points_2d[-1]
        same = (
            round(prev[0], 6), round(prev[1], 6)) == (
            round(p2[0], 6), round(p2[1], 6))
        if same:
            return
        tol = state.hv_pixel_tol * self._world_per_pixel(context)
        ctype = ac.classify_segment(prev, p2, tol)
        if ctype is not None:
            _, p2 = ac.snapped_segment(prev, p2, ctype)
        state.points_2d.append(p2)

        sketch = state.sketch
        sketch.add_line(prev, p2)
        new_id = sketch.entities[-1]["id"]
        for point, new_pos in ((prev, 0), (p2, 1)):
            hit = ac.find_endpoint_hit(point, sketch.entities,
                                       exclude_id=new_id)
            if hit is not None:
                sketch.add_constraint(
                    ac.COINCIDENT, [new_id, hit[0]],
                    endpoints=[new_pos, hit[1]])
        if ctype is not None:
            sketch.add_constraint(ctype, [new_id])
        self._refresh_dof()

    def _refresh_dof(self):
        """实体/约束变化后刷新 DOF 报告缓存（不进 MOUSEMOVE 每帧路径）。"""
        if state.sketch is not None:
            state.dof_report = solver_dof.analyze(
                state.sketch.entities, state.sketch.constraints)

    def _world_per_pixel(self, context):
        """正交正视近似换算：可视世界高度 / 视口像素高度。"""
        region = context.region
        rv3d = context.region_data
        if region is None or rv3d is None:
            return 0.0
        return rv3d.view_distance * 2.0 / max(region.height, 1)

    def _cursor_in_ui_region(self, context, event):
        """鼠标是否位于 N-Panel（UI region）矩形内（窗口坐标判定）。"""
        if context.area is None:
            return False
        for r in context.area.regions:
            if r.type == 'UI':
                return (r.x <= event.mouse_x < r.x + r.width
                        and r.y <= event.mouse_y < r.y + r.height)
        return False

    # ---- 选择模式与尺寸驱动（SketchCAD.md §4）----

    def _toggle_mode(self):
        # 切换前结束进行中的笔划，防跨模式状态错乱
        state.stroke_open = False
        state.mode = "select" if state.mode == "draw" else "draw"
        state.selected_entity = None
        state.input_buffer = ""
        state.solve_failed = False
        state.dragging = None
        if state.mode == "draw":
            self.report({'INFO'}, "SketchCAD：画线模式（左键加点）")
        else:
            self.report({'INFO'},
                        "SketchCAD：选择模式——点击线段选中，输入数值后"
                        "回车定尺寸，Tab 返回画线")

    def _select_at(self, context, p):
        tol = picking.SELECT_PIXEL_TOL * self._world_per_pixel(context)
        state.selected_entity = picking.pick_segment(
            p, state.sketch.entities, tol)
        state.input_buffer = ""
        state.solve_failed = False

    def _press_select(self, context, p):
        """select 模式按下：端点命中则进入拖动求解，否则选线段。"""
        tol = picking.SELECT_PIXEL_TOL * self._world_per_pixel(context)
        hit = picking.pick_endpoint(p, state.sketch.entities, tol)
        if hit is not None:
            state.dragging = hit
            state.selected_entity = hit[0]
            state.solve_failed = False
            state.drag_last_solve = 0.0
        else:
            self._select_at(context, p)

    def _drag_update(self):
        """拖动端点实时求解（节流 ~60Hz；失败时保持上次成功几何）。"""
        now = time.perf_counter()
        if now - state.drag_last_solve < _DRAG_SOLVE_INTERVAL:
            return
        state.drag_last_solve = now
        eid, pos = state.dragging
        if state.mouse_2d is None:
            return
        for e in state.sketch.entities:
            if e.get("id") == eid:
                x, y = state.mouse_2d
                if pos == 0:
                    e["p1"] = [x, y]
                else:
                    e["p2"] = [x, y]
                break
        else:
            state.dragging = None  # 实体已不在（被撤销等），结束拖动
            return
        self._solve_and_apply()
        self._refresh_dof()

    def _on_input_key(self, key_type):
        """收集数值输入；返回事件是否被消费（仅选中态有效）。"""
        if key_type in _NUMPAD_DIGIT or key_type in _DIGIT_KEYS:
            state.input_buffer += _NUMPAD_DIGIT.get(key_type, key_type)
            return True
        if key_type in _PERIOD_KEYS:
            if "." not in state.input_buffer:
                state.input_buffer = (state.input_buffer or "0") + "."
            return True
        if key_type == 'BACKSPACE':
            state.input_buffer = state.input_buffer[:-1]
            return True
        return False

    def _apply_dimension(self):
        """数值确认：创建/更新选中线段的 distance 约束并求解回写。"""
        if state.selected_entity is None or not state.input_buffer:
            return
        try:
            value = float(state.input_buffer)
        except ValueError:
            state.input_buffer = ""
            return
        if value <= 0:
            state.input_buffer = ""
            self.report({'WARNING'}, "尺寸必须为正数")
            return
        sketch = state.sketch
        eid = state.selected_entity
        existing = None
        for c in sketch.constraints:
            if (c.get("type") == "distance" and c.get("enabled", True)
                    and eid in (c.get("targets") or [])):
                existing = c
                break
        if existing is not None:
            existing["value"] = value
        else:
            sketch.add_constraint("distance", [eid], value=value)
        state.input_buffer = ""
        if self._solve_and_apply():
            self.report({'INFO'}, f"尺寸已应用：{value:g}")
        self._refresh_dof()

    def _solve_and_apply(self):
        """以约束系统为准从当前几何求解；成功回写坐标，失败几何不动。"""
        ok, message = solver_apply.solve_and_apply(state.sketch)
        state.solve_failed = not ok
        if not ok:
            self.report({'WARNING'}, message)
        return ok

    # ---- 提交与退出 ----

    def _finish(self, context):
        count = self._commit(context)
        segments = len(state.sketch.entities)
        self._cleanup(context)
        self.report({'INFO'}, f"草图完成：{count} 个点 / {segments} 条线段")
        return {'FINISHED'}

    def _commit(self, context):
        if not state.points_2d:
            return 0

        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            return 0

        # 1. 数据层：容器对象 JSON（新建或更新）
        if state.container_name:
            if not manager.update_container(state.container_name, state.sketch):
                cont = manager.create_container(context, state.sketch)
                state.container_name = cont.name
        else:
            cont = manager.create_container(context, state.sketch)
            state.container_name = cont.name

        # 2. mesh 提交：2D -> 世界 -> 宿主局部
        bm = bmesh.from_edit_mesh(obj.data)
        mwi = obj.matrix_world.inverted()
        vert_cache = {}

        def get_vert(p):
            key = (round(p[0], 6), round(p[1], 6))
            v = vert_cache.get(key)
            if v is None:
                v = bm.verts.new(mwi @ state.datum.to_world(p[0], p[1]))
                vert_cache[key] = v
            return v

        for p1, p2 in state.sketch.line_segments():
            try:
                bm.edges.new((get_vert(p1), get_vert(p2)))
            except ValueError:
                pass  # 重合边，跳过

        bm.verts.ensure_lookup_table()
        bmesh.update_edit_mesh(obj.data)

        try:
            bpy.ops.ed.undo_push()
        except Exception:
            pass
        return len(state.points_2d)

    def _cleanup(self, context):
        state.active = False
        if state.draw_handler is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(
                    state.draw_handler, 'WINDOW')
            except Exception:
                pass
            state.draw_handler = None

        rv3d = context.region_data
        if rv3d is not None and state.view_rot is not None:
            rv3d.view_rotation = state.view_rot
            rv3d.view_location = state.view_loc
            rv3d.view_distance = state.view_dist
            rv3d.is_perspective = state.is_persp

        space = context.space_data
        if space is not None and space.type == 'VIEW_3D':
            if state.shading_type is not None:
                space.shading.type = state.shading_type
            if state.show_xray is not None:
                space.shading.show_xray = state.show_xray
            if state.clip_end is not None:
                space.clip_end = state.clip_end

        if context.area is not None:
            context.area.tag_redraw()