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
from ..gpu.draw_2d import draw_sketch
from ..snapping import snap

_NAV_EVENTS = {'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'MIDDLEMOUSE'}


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

        self._align_view(context, sketch.datum)

        state.draw_handler = bpy.types.SpaceView3D.draw_handler_add(
            draw_sketch, (), 'WINDOW', 'POST_VIEW')

        context.window_manager.modal_handler_add(self)
        self.report({'INFO'},
                    "SketchCAD 草图模式 | 左键加点 | 双击/Enter 结束折线"
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

        if event.type == 'MOUSEMOVE':
            self._update_mouse(context, event)
            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            if state.mouse_2d is not None:
                if not state.stroke_open:
                    # 新折线起点：只记录点，不与历史末点拼段
                    state.points_2d.append(state.mouse_2d)
                    state.stroke_open = True
                else:
                    prev = state.points_2d[-1]
                    same = (
                        round(prev[0], 6), round(prev[1], 6)) == (
                        round(state.mouse_2d[0], 6), round(state.mouse_2d[1], 6))
                    if not same:
                        state.points_2d.append(state.mouse_2d)
                        state.sketch.add_line(prev, state.mouse_2d)
            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE' and event.value == 'DOUBLE_CLICK':
            # 双击：结束当前折线；若末点捕捉回起点则已自然闭合轮廓
            state.stroke_open = False
            return {'RUNNING_MODAL'}

        if event.type == 'RET' and event.value == 'PRESS':
            state.stroke_open = False
            return {'RUNNING_MODAL'}

        if ((event.type == 'RIGHTMOUSE' or (event.type == 'Z' and event.ctrl))
                and event.value == 'PRESS'):
            self._undo_point()
            return {'RUNNING_MODAL'}

        if event.type == 'ESC' and event.value == 'PRESS':
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
        # 仅当最后一条段的终点恰为被弹点时才撤销该段（跨笔划安全）
        state.sketch.pop_line_ending_at(popped)
        if not state.points_2d:
            state.stroke_open = False

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