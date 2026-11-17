"""面驱动草图 Modal Operator（Phase 0-3）。

修复要点：
- 视图对齐改用 normal.to_track_quat('Z', 'Y')（原实现旋转矩阵存在
  行/列转置方向与 Y 轴手性错误，导致每次进入视图姿态不一致）；
- 基准面坐标系持久化 + 宿主面快照匹配（重进续画，坐标系不再漂移）；
- 法线变换使用逆转置矩阵，兼容宿主对象非均匀缩放；
- 视图导航事件 PASS_THROUGH 交给 Blender 默认处理；
- 折线以双击左键或 Enter 结束（末点捕捉回起点时自然闭合轮廓），
  结束后可另起新折线，数据层按独立 line 段存储。

Phase 3：
- select 模式编辑工具：T 修剪（点击侧裁除）、E 延伸（两段式：选线→
  预览→第二击定终点），ESC/T/E 再按退出工具；
- §8 自管撤销升级为完整快照栈（几何修改前 push），右键/Ctrl+Z 撤销、
  Ctrl+Shift+Z 重做，覆盖画线/尺寸/修剪/延伸/拖动全部会话操作；
- N-Panel 约束列表固定高度窗口：鼠标滚轮在侧边栏上滚动浏览约束
  （modal 拦截消费，不透传视口缩放），默认跟随最新约束。

Phase 3 圆弧（混合草图）：
- draw 模式子工具 L/C/A 切换（line/circle/arc）；圆两击（圆心→
  半径），弧三点式（起点→弧上点→终点，geom.arc_from_3_points）；
- select 模式选择/拖动/尺寸输入分派实体类型：circle 拖圆心
  （pos=2）、arc 拖端点（反算角度保持半径）、数值输入对 circle/arc
  生成 radius 约束、对 line 生成 distance 约束；
- 提交 mesh 时 circle/arc 离散为折线段（与 GPU 预览同一密度）；
- 替换式提交：重进修改尺寸再退出时按上一轮提交快照（容器
  SketchCAD_Committed）替换旧轮廓边，避免双影。

Phase 3 光标与圆心选择：
- 光标跟随模式：draw 十字（作图）/ select 箭头（选择），S 键或
  Tab 切换（cursor_modal_set 随 operator 结束自动恢复）；S 推荐
  依据：会话 modal 拦截下编辑模式 S=缩放不透传，无实际冲突；
- 端点/圆心点击与拖动区分：按下实际移动才 push 快照并实时求解，
  纯点击即选中（圆心常驻标记见 gpu/draw_2d.py）。

Phase 3 偏移（Offset，§6）：
- select 模式 O 键激活：两段式（选实体 → 鼠标预览侧向/距离 →
  再击确认）；圆/弧同心偏移（鼠标驱动半径，或数值输入偏移距离 +
  回车确认，鼠标在圆内/外决定增减）；闭合轮廓 miter 偏移 +
  开放链偏移（侧向=鼠标相对选中段左右侧，两端平端帽，算法见
  editing/offset.py）；生成独立实体（不加偏移约束），混合直/弧
  链偏移待适配。
"""

import math
import time
from copy import deepcopy

import bmesh

import bpy

from bpy_extras.view3d_utils import (
    location_3d_to_region_2d,
    region_2d_to_origin_3d,
    region_2d_to_vector_3d,
)
from mathutils.geometry import intersect_line_plane

from .. import manager
from ..core import geom
from ..core import sketch_data as skd
from ..core.datum import Datum
from ..core.state import CONSTRAINT_VISIBLE, CONSTRAINT_WHEEL_STEP, state
from ..drawing import auto_constraints as ac
from ..drawing import picking
from ..editing import commit as cmt
from ..editing import mirror_pattern as mp
from ..editing import offset as off
from ..editing import trim_extend as te
from ..editing.loops import detect_closed_loops
from ..gpu.draw_2d import draw_sketch
from ..solver import apply as solver_apply
from ..solver import dof as solver_dof
from ..snapping import aid_points as ap
from ..snapping import snap

_NAV_EVENTS = {'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'MIDDLEMOUSE'}

# select 模式数值输入的事件键（主键盘数字 / 小键盘数字 / 小数点）
_DIGIT_KEYS = {str(i) for i in range(10)}
_NUMPAD_DIGIT = {f"NUMPAD_{i}": str(i) for i in range(10)}
_PERIOD_KEYS = ('PERIOD', 'NUMPAD_PERIOD')

# select 模式编辑工具快捷键（T 修剪 / E 延伸 / O 偏移 / M 镜像 / P 阵列）
_TOOL_KEYS = {'T': 'trim', 'E': 'extend', 'O': 'offset',
              'M': 'mirror', 'P': 'pattern'}

# draw 模式子工具快捷键（L 直线 / C 圆 / A 三点弧）
_DRAW_TOOL_KEYS = {'L': 'line', 'C': 'circle', 'A': 'arc'}

# 圆/弧绘制的最小尺寸（世界单位），低于视为误击
_MIN_RADIUS = 1e-4

# 草图会话中放行给 N-Panel（UI region）的鼠标事件（约束管理/阈值配置）
# ——滚轮不在放行之列：被单独拦截用于约束列表滚动（见 modal 分支）
_UI_PASS_EVENTS = {'LEFTMOUSE', 'RIGHTMOUSE', 'MIDDLEMOUSE', 'MOUSEMOVE'}

# 拖动实时求解的节流间隔（~60Hz；20 实体规模单次求解 ~15ms，见 bench_20x30）
_DRAG_SOLVE_INTERVAL = 1.0 / 60.0

# §8 快照栈深度上限（防长时间会话内存膨胀）
_UNDO_LIMIT = 100

# 框选矩形最小对角线（像素）：低于视为点击空白（不启动框选判定）
_BOX_MIN_PIXELS = 5.0


class SKETCH_OT_face_driven_sketch(bpy.types.Operator):
    """基于选中面进入 SketchCAD 草图模式（container_name 指定时为独立打开入口）"""
    bl_idname = "sketch.face_driven_sketch"
    bl_label = "Start Face Sketch"

    # 独立打开入口（N-Panel"打开"按钮）：按容器名进入会话，不依赖
    # 宿主面（断开关联的草图以存储的 datum 坐标系直接对齐）
    container_name: bpy.props.StringProperty(default="")

    # ---- 进入 ----

    def invoke(self, context, event):
        if self.container_name:
            return self._invoke_open(context, event)
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
        # 宿主顶点世界坐标采集（§5 辅助点投影源数据，即取即用不持有）
        host_verts = [tuple(mw @ v.co) for v in bm.verts]

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

        return self._start_session(context, sketch, container_name,
                                   host_verts=host_verts)

    def _invoke_open(self, context, event):
        """独立打开入口（§2 遗留收尾）：按容器名进入会话。

        断开关联的草图无宿主面可匹配，以存储的 datum 坐标系直接
        对齐进入；退出时仅保存容器 JSON、不提交 mesh（§9 所有权
        边界——无宿主即无提交目标）。N-Panel 按钮触发时 context
        位于 UI region，定位 3D 视口后以 override 执行启动。
        """
        if state.active:
            self.report({'WARNING'}, "草图会话进行中，请先 ESC 退出")
            return {'CANCELLED'}
        obj, sketch = manager.load_sketch(self.container_name)
        if sketch is None:
            self.report({'WARNING'},
                        f"草图 {self.container_name} 不存在或数据缺失")
            return {'CANCELLED'}
        area = next((a for a in context.window.screen.areas
                     if a.type == 'VIEW_3D'), None)
        if area is None:
            self.report({'WARNING'}, "未找到 3D 视口区域")
            return {'CANCELLED'}
        region = next(r for r in area.regions if r.type == 'WINDOW')
        rv3d = area.spaces.active.region_data
        self.report({'INFO'},
                    f"独立打开草图 {self.container_name}"
                    f"（{len(sketch.entities)} 个实体，无宿主不提交 mesh）")
        with context.temp_override(area=area, region=region,
                                   region_data=rv3d):
            return self._start_session(context, sketch,
                                       self.container_name)

    def _start_session(self, context, sketch, container_name,
                       host_verts=None):
        """会话启动公共入口（选面进入 / 独立打开共用）。

        host_verts：宿主顶点世界坐标序列（选面进入时采集）；独立
        打开无宿主传 None，辅助点为空。宿主 ghost：display_type
        临时置线框（退出恢复），突出草图平面。
        """
        state.reset()
        state.active = True
        state.datum = sketch.datum
        state.sketch = sketch
        state.container_name = container_name
        state.points_2d = sketch.rebuild_points()
        self._refresh_dof()

        self._align_view(context, sketch.datum)

        # §5 辅助点：宿主顶点投影基准面（进入时固化——会话内宿主
        # 几何不变）；深度阈值取对齐后视图半径（平面外过远的顶点
        # 投影会产生干扰描图的幻影点）
        state.aid_points = []
        if host_verts:
            rv3d = context.region_data
            depth_tol = rv3d.view_distance if rv3d is not None else None
            state.aid_points = ap.collect_aid_points(
                host_verts, sketch.datum, depth_tol)

        state.host_obj_name = None
        state.host_display_type = None
        host_name = (sketch.host or {}).get("object")
        if host_name:
            host_obj = bpy.data.objects.get(host_name)
            if host_obj is not None:
                state.host_obj_name = host_name
                state.host_display_type = host_obj.display_type
                host_obj.display_type = 'WIRE'

        state.draw_handler = bpy.types.SpaceView3D.draw_handler_add(
            draw_sketch, (), 'WINDOW', 'POST_VIEW')

        # 进入即作图模式：十字光标（cursor_modal_set 随 operator
        # 结束自动恢复进入前的光标状态）
        if context.window is not None:
            context.window.cursor_modal_set('CROSSHAIR')

        context.window_manager.modal_handler_add(self)
        self.report({'INFO'},
                    "SketchCAD 草图模式 | 画线: 左键加点, 双击/Enter 结束"
                    " | L/C/A 切直线/圆/弧 | Tab 选择模式(点击选中, "
                    "Shift+点击加/减选, 空白拖动框选, DEL 删除, 输数值"
                    "+回车定尺寸, T 修剪, E 延伸, O 偏移, M 镜像, "
                    "P 阵列) | 右键/Ctrl+Z 撤销, Ctrl+Shift+Z 重做 | "
                    "ESC 提交退出")
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

        # 滚轮在 N-Panel 上：滚动约束列表（步长 CONSTRAINT_WHEEL_STEP
        # 行），消费事件避免透传视口缩放
        if (event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}
                and self._cursor_in_ui_region(context, event)):
            self._scroll_constraints(event.type == 'WHEELUPMOUSE')
            return {'RUNNING_MODAL'}

        # N-Panel（UI region）其余事件放行：草图进行中可用侧边栏
        if (event.type in _UI_PASS_EVENTS
                and self._cursor_in_ui_region(context, event)):
            return {'PASS_THROUGH'}

        if event.type == 'MOUSEMOVE':
            self._update_mouse(context, event)
            if state.box_select_start is not None:
                state.box_select_cur = (
                    event.mouse_region_x, event.mouse_region_y)
            if state.dragging is not None:
                self._drag_update()
            if state.extend_pending is not None:
                self._update_extend_preview()
            if state.offset_pending is not None:
                self._update_offset_preview()
            if (state.tool == 'pattern'
                    and state.pattern_stage in ('step', 'count')):
                self._update_pattern_preview()
            return {'RUNNING_MODAL'}

        if event.type == 'TAB' and event.value == 'PRESS':
            self._toggle_mode(context)
            return {'RUNNING_MODAL'}

        if event.type == 'S' and event.value == 'PRESS':
            # S 与 Tab 等价：draw（十字光标）↔ select（箭头光标）。
            # 冲突排查：会话 modal 拦截一切非放行键（仅滚轮/中键透传），
            # Blender 编辑模式 S=缩放在会话内本就不生效，无实际冲突
            self._toggle_mode(context)
            return {'RUNNING_MODAL'}

        if (event.value == 'PRESS' and state.mode == "draw"
                and event.type in _DRAW_TOOL_KEYS):
            self._set_draw_tool(_DRAW_TOOL_KEYS[event.type])
            return {'RUNNING_MODAL'}

        if (event.value == 'PRESS' and state.mode == "select"
                and (state.offset_pending is not None
                     or (state.tool == 'pattern'
                         and state.pattern_stage in ('step', 'count'))
                     or (state.tool is None
                         and state.selected_entity is not None))):
            # 数值输入：选中实体定尺寸、偏移距离或阵列步距/份数
            if self._on_input_key(event.type):
                return {'RUNNING_MODAL'}

        if (event.value == 'PRESS' and state.mode == "select"
                and event.type in _TOOL_KEYS):
            self._toggle_tool(_TOOL_KEYS[event.type])
            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            if state.mouse_2d is not None:
                if state.mode == "select":
                    if state.tool == 'trim':
                        self._apply_trim(context)
                    elif state.tool == 'extend':
                        self._apply_extend(context)
                    elif state.tool == 'offset':
                        self._apply_offset(context)
                    elif state.tool == 'mirror':
                        self._apply_mirror(context)
                    elif state.tool == 'pattern':
                        self._apply_pattern(context)
                    else:
                        self._press_select(context, event, state.mouse_2d)
                else:
                    self._press_draw(context, state.mouse_2d)
            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            if state.box_select_start is not None:
                self._finish_box_select(context)
            if state.dragging is not None:
                dragged = state.drag_undo_pushed
                state.dragging = None
                self._refresh_dof()
                if dragged:
                    self.report({'INFO'}, "拖动求解结束")
                else:
                    # 纯点击命中（未拖动）：端点/圆心即选中
                    self.report({'INFO'},
                                f"已选中实体 #{state.selected_entity}"
                                "（按住拖动可实时求解）")
            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE' and event.value == 'DOUBLE_CLICK':
            # 双击：结束当前折线；若末点捕捉回起点则已自然闭合轮廓
            if state.mode == "draw" and state.draw_tool == "line":
                state.stroke_open = False
            return {'RUNNING_MODAL'}

        if event.type == 'RET' and event.value == 'PRESS':
            if state.mode == "select":
                if state.tool == 'mirror':
                    if state.mirror_stage == 'pick':
                        if state.mirror_pending:
                            state.mirror_stage = 'axis'
                            self.report({'INFO'},
                                        "镜像：点击一条线作为镜像轴")
                    elif state.mirror_stage == 'confirm':
                        self._confirm_mirror()
                elif state.tool == 'pattern':
                    if state.pattern_stage == 'pick':
                        if state.pattern_pending:
                            state.pattern_stage = 'axis'
                            self.report({'INFO'},
                                        "阵列：点击线 = 线性阵列方向；"
                                        "点击圆/弧 = 圆形阵列中心")
                    elif state.pattern_stage == 'step':
                        self._confirm_pattern_step()
                    elif state.pattern_stage == 'count':
                        self._confirm_pattern()
                elif (state.tool == 'offset'
                        and state.offset_pending is not None):
                    # 数值确认：按输入距离偏移（侧向用当前鼠标位置）
                    self._confirm_offset()
                    state.input_buffer = ""
                else:
                    self._apply_dimension()
            elif state.draw_tool == "line":
                state.stroke_open = False
            return {'RUNNING_MODAL'}

        if (event.type == 'Z' and event.ctrl and event.shift
                and event.value == 'PRESS'):
            self._redo()
            return {'RUNNING_MODAL'}

        if (event.type == 'DEL' and event.value == 'PRESS'
                and state.mode == "select" and state.tool is None
                and state.selected_entities):
            self._delete_selection()
            return {'RUNNING_MODAL'}

        if ((event.type == 'RIGHTMOUSE' or (event.type == 'Z' and event.ctrl))
                and event.value == 'PRESS'):
            self._undo()
            return {'RUNNING_MODAL'}

        if event.type == 'ESC' and event.value == 'PRESS':
            if state.tool is not None:
                # 阶段化回退：mirror/pattern 逐阶段退，未确认时先取消
                # 选择，再按才退出工具
                if state.tool == 'mirror':
                    self._esc_mirror()
                elif state.tool == 'pattern':
                    self._esc_pattern()
                elif state.extend_pending is not None:
                    state.extend_pending = None
                    state.extend_preview = None
                elif state.offset_pending is not None:
                    state.offset_pending = None
                    state.offset_preview = None
                else:
                    state.tool = None
                return {'RUNNING_MODAL'}
            if state.mode == "draw" and (
                    state.circle_center is not None
                    or state.arc_p1 is not None or state.arc_p2 is not None):
                # 圆/弧绘制进行中：先取消未完成的绘制，再按才退出草图
                self._cancel_curve_pending()
                return {'RUNNING_MODAL'}
            if state.mode == "select" and (
                    state.selected_entities or state.input_buffer
                    or state.box_select_start is not None):
                # 先清选集/输入/取消框选，再按一次 ESC 才退出草图
                self._clear_selection()
                state.input_buffer = ""
                state.box_select_start = None
                state.box_select_cur = None
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
        raw_2d = state.datum.to_2d(hit)
        snap_result = self._find_snap(context, event, raw_2d)
        if snap_result is not None:
            state.snap = snap_result
            state.mouse_2d = snap_result[1]
        else:
            state.mouse_2d = raw_2d

    def _find_snap(self, context, event, raw_2d):
        """像素级最近捕捉特征；端点 > 中点/线上点 > 辅助点。返回 (kind, (u, v)) 或 None。

        线上点候选（鼠标在既有线上的垂足）依赖鼠标位置，由 raw_2d
        （未吸附的基准面投影点）驱动生成；辅助点（宿主顶点投影）
        优先级最低，避免干扰草图自身特征捕捉。
        """
        region = context.region
        rv3d = context.region_data
        if region is None or rv3d is None:
            return None
        mx, my = event.mouse_region_x, event.mouse_region_y
        d = state.datum
        tol_world = (snap.SNAP_PIXEL_TOLERANCE
                     * self._world_per_pixel(context))
        best = None  # (priority, dist, kind, (u, v))
        for u, v, kind in snap.collect_candidates(
                state.sketch.entities, mouse_2d=raw_2d, tol=tol_world):
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
        for u, v, kind in ap.collect_aid_candidates(
                state.aid_points, raw_2d, tol_world):
            px = location_3d_to_region_2d(region, rv3d, d.to_world(u, v))
            if px is None:
                continue
            dist = ((px[0] - mx) ** 2 + (px[1] - my) ** 2) ** 0.5
            if dist > snap.SNAP_PIXEL_TOLERANCE:
                continue
            cand = (2, dist, kind, (u, v))
            if best is None or (cand[0], cand[1]) < (best[0], best[1]):
                best = cand
        if best is None:
            return None
        return best[2], best[3]

    # ---- §8 自管撤销：完整快照栈（几何修改前 push）----

    def _snapshot_now(self):
        return {
            "entities": deepcopy(state.sketch.entities),
            "constraints": deepcopy(state.sketch.constraints),
            "points_2d": list(state.points_2d),
            "stroke_open": state.stroke_open,
        }

    def _push_undo(self):
        state.undo_stack.append(self._snapshot_now())
        if len(state.undo_stack) > _UNDO_LIMIT:
            state.undo_stack.pop(0)
        state.redo_stack.clear()

    def _restore(self, snap):
        state.sketch.entities = deepcopy(snap["entities"])
        state.sketch.constraints = deepcopy(snap["constraints"])
        state.points_2d = list(snap["points_2d"])
        state.stroke_open = snap["stroke_open"]
        state.selected_entity = None
        state.selected_entities = []
        state.input_buffer = ""
        state.dragging = None
        # 撤销/重做后清除未完成的圆/弧绘制状态（快照不含进行中预览）
        self._cancel_curve_pending()
        self._refresh_dof()

    def _undo(self):
        if not state.undo_stack:
            return
        state.redo_stack.append(self._snapshot_now())
        self._restore(state.undo_stack.pop())

    def _redo(self):
        if not state.redo_stack:
            return
        state.undo_stack.append(self._snapshot_now())
        self._restore(state.redo_stack.pop())

    # ---- select 模式编辑工具（Trim/Extend，SketchCAD.md §6）----

    def _toggle_tool(self, tool):
        state.tool = None if state.tool == tool else tool
        state.selected_entity = None
        state.selected_entities = []
        state.input_buffer = ""
        state.extend_pending = None
        state.extend_preview = None
        state.offset_pending = None
        state.offset_preview = None
        state.mirror_pending = []
        state.mirror_stage = None
        state.mirror_axis = None
        state.mirror_preview = None
        state.pattern_pending = []
        state.pattern_stage = None
        state.pattern_axis = None
        state.pattern_kind = None
        state.pattern_step = None
        state.pattern_preview = None
        if state.tool is None:
            self.report({'INFO'}, "SketchCAD：编辑工具已退出")
        elif state.tool == 'trim':
            self.report({'INFO'},
                        "SketchCAD 修剪：点击线/弧上要移除的部分"
                        "（在最近内部交点处剪断，裁除点击侧）")
        elif state.tool == 'extend':
            self.report({'INFO'},
                        "SketchCAD 延伸：点击选中要延伸的线/弧，移动"
                        "鼠标预览，再次点击确认终点（ESC 取消）")
        elif state.tool == 'offset':
            self.report({'INFO'},
                        "SketchCAD 偏移：点击选中线段（须属闭合轮廓）"
                        "或圆/弧，移动鼠标预览，再次点击确认"
                        "（ESC 取消选择）")
        elif state.tool == 'mirror':
            state.mirror_stage = 'pick'
            self.report({'INFO'},
                        "SketchCAD 镜像：点击选择实体（可多选，再点"
                        "取消），Enter 确认后点击一条线作镜像轴，"
                        "Enter 生成")
        else:
            state.pattern_stage = 'pick'
            self.report({'INFO'},
                        "SketchCAD 阵列：点击选择实体（可多选），"
                        "Enter 确认后点击线（线性）或圆/弧（圆形）"
                        "定参照，按提示定步距与份数")

    def _pick_for_tool(self, context):
        """trim/extend 工具拾取：line/arc 实体（circle 整圆不参与）。"""
        tol = picking.SELECT_PIXEL_TOL * self._world_per_pixel(context)
        eid = picking.pick_entity(state.mouse_2d, state.sketch.entities, tol)
        if eid is None:
            return None
        ent = self._entity_by_id(eid)
        if ent is None or ent.get("type") not in ("line", "arc"):
            return None
        return eid

    def _entity_by_id(self, eid):
        for e in state.sketch.entities:
            if e.get("id") == eid:
                return e
        return None

    def _apply_trim(self, context):
        eid = self._pick_for_tool(context)
        if eid is None:
            return
        ent = self._entity_by_id(eid)
        if ent is not None and ent.get("type") == "arc":
            ok, *rest = te.trim_arc(state.sketch.entities, eid,
                                    state.mouse_2d)
        else:
            ok, *rest = te.trim_line(state.sketch.entities, eid,
                                     state.mouse_2d)
        if not ok:
            reason = rest[0]
            if reason == "no-intersection":
                self.report({'WARNING'}, "修剪：该段无内部交点可裁")
            elif reason == "removed":
                self._push_undo()
                state.sketch.remove_entity_and_constraints(eid)
                self._refresh_dof()
                self.report({'INFO'}, "修剪：保留侧退化，整段已移除")
            return
        changed_pos, new_a, new_b = rest
        self._push_undo()
        ent = self._entity_by_id(eid)
        if ent is not None:
            if ent.get("type") == "arc":
                # new_a/new_b 为新扫角 (a1, a2)；半径/圆心不变，
                # radius 约束继续生效
                ent["a1"], ent["a2"] = new_a, new_b
            else:
                ent["p1"], ent["p2"] = new_a, new_b
                # 被改变端的原位置固接失效，级联清理；长度语义约束同步移除
                state.sketch.remove_constraints_for_entity(eid, "distance")
        # 被改变端的原位置固接失效，级联清理（line/arc 同语义）
        state.sketch.remove_constraints_for_endpoint(eid, changed_pos)
        self._refresh_dof()
        self.report({'INFO'}, "修剪完成（点击侧已裁除）")

    def _apply_extend(self, context):
        """两段式延伸：第一击选中线/弧，移动鼠标预览，第二击确认终点。"""
        if state.extend_pending is None:
            eid = self._pick_for_tool(context)
            if eid is None:
                return
            state.extend_pending = eid
            self._update_extend_preview()
            self.report({'INFO'},
                        f"延伸：已选中实体 #{eid}，移动鼠标预览，"
                        "再次点击确认终点（ESC 取消选择）")
            return
        eid = state.extend_pending
        ent = self._entity_by_id(eid)
        if ent is None or ent.get("type") not in ("line", "arc"):
            state.extend_pending = None
            state.extend_preview = None
            return
        preview = state.extend_preview
        if preview is None:
            self.report({'WARNING'},
                        "延伸：请在延长方向一侧点击确认终点"
                        "（弧：扫角外一侧）")
            return
        self._push_undo()
        pos = preview["pos"]
        if preview["kind"] == "arc":
            # 弧沿圆周延长：a1 端顺时针扩展 / a2 端逆时针扩展
            if pos == 0:
                ent["a1"] = ent["a1"] - preview["delta"]
            else:
                ent["a2"] = ent["a2"] + preview["delta"]
            to_pt = geom.arc_point(ent["center"], ent["radius"],
                                   ent["a1"] if pos == 0 else ent["a2"])
        else:
            to_pt = preview["to_pt"]
            if pos == 0:
                ent["p1"] = [to_pt[0], to_pt[1]]
            else:
                ent["p2"] = [to_pt[0], to_pt[1]]
            # 长度语义约束同步移除（弧的 radius 约束随半径不变保留）
            state.sketch.remove_constraints_for_entity(eid, "distance")
        # 被改变端的原位置固接失效，级联清理
        state.sketch.remove_constraints_for_endpoint(eid, pos)
        # 终点命中其他实体端点时自动固接（与绘制 coincident 语义一致）
        hit = ac.find_endpoint_hit(to_pt, state.sketch.entities,
                                   exclude_id=eid)
        if hit is not None:
            state.sketch.add_constraint(
                ac.COINCIDENT, [eid, hit[0]], endpoints=[pos, hit[1]])
        state.extend_pending = None
        state.extend_preview = None
        self._refresh_dof()
        self.report({'INFO'}, "延伸完成")

    def _extend_preview_result(self, ent):
        """延伸预览计算：返回预览 dict 或 None。

        line：延伸端取距鼠标较近的端点，终点 = 鼠标在该端延长线上
        的投影（投影落在线段内部时延伸无意义）；
        arc：延伸端取距鼠标方位角较近的端（a1 端顺时针扩展 /
        a2 端逆时针扩展），鼠标方位角落在扫角内时延伸无意义。
        """
        m = state.mouse_2d
        if m is None:
            return None
        if ent.get("type") == "arc":
            res = te.extend_arc_result(ent, m)
            if res is None:
                return None
            pos, delta = res
            c, r = ent["center"], ent["radius"]
            from_ang = ent["a1"] if pos == 0 else ent["a2"]
            to_ang = (ent["a1"] - delta) if pos == 0 \
                else (ent["a2"] + delta)
            lo, hi = (to_ang, from_ang) if pos == 0 else (from_ang, to_ang)
            return {"kind": "arc", "pos": pos, "delta": delta,
                    "center": [c[0], c[1]], "radius": r,
                    "from_pt": list(geom.arc_point(c, r, from_ang)),
                    "to_pt": list(geom.arc_point(c, r, to_ang)),
                    "strip": (lo, hi)}
        p1, p2 = ent["p1"], ent["p2"]
        t_m = te.project_param(p1, p2, m)
        if t_m > 1.0 + te.PARAM_EPS:
            pos = 1
        elif t_m < -te.PARAM_EPS:
            pos = 0
        else:
            return None
        pt = te.point_at(p1, p2, t_m)
        from_pt = (p1[0], p1[1]) if pos == 0 else (p2[0], p2[1])
        return {"kind": "line", "pos": pos, "to_pt": (pt[0], pt[1]),
                "from_pt": [from_pt[0], from_pt[1]]}

    def _update_extend_preview(self):
        ent = self._entity_by_id(state.extend_pending)
        if ent is None or ent.get("type") not in ("line", "arc"):
            state.extend_pending = None
            state.extend_preview = None
            return
        state.extend_preview = self._extend_preview_result(ent)

    # ---- Offset 偏移（SketchCAD.md §6，editing/offset.py 算法）----

    def _find_loop_for(self, eid):
        """查找包含实体 eid 的闭合轮廓（state.loops 缓存）。"""
        for loop in state.loops:
            if eid in loop:
                return loop
        return None

    def _update_offset_preview(self):
        """偏移预览：侧向由鼠标位置决定；距离优先取数值输入缓冲
        （回车确认），否则鼠标驱动（圆/弧半径=鼠标到圆心距离）。
        """
        ent = self._entity_by_id(state.offset_pending)
        if ent is None:
            state.offset_pending = None
            state.offset_preview = None
            return
        m = state.mouse_2d
        if m is None:
            state.offset_preview = None
            return
        # 数值输入缓冲优先（offset pending 状态下输入数字 + 回车确认）
        buffered = None
        if state.input_buffer:
            try:
                v = float(state.input_buffer)
                if v > 0:
                    buffered = v
            except ValueError:
                pass
        t = ent.get("type")
        if t == "circle":
            c, r = ent["center"], ent["radius"]
            if buffered is not None:
                # 数值模式：输入为偏移距离，鼠标在圆外/内决定增减
                outward = math.hypot(m[0] - c[0], m[1] - c[1]) >= r
                new_r = off.offset_circle_by_distance(c, r, buffered,
                                                      outward)
            else:
                new_r = off.offset_circle(c, r, m)
            state.offset_preview = (
                {"kind": "circle", "center": list(c), "radius": new_r}
                if new_r is not None else None)
        elif t == "arc":
            c, r = ent["center"], ent["radius"]
            if buffered is not None:
                outward = math.hypot(m[0] - c[0], m[1] - c[1]) >= r
                res = off.offset_arc_by_distance(c, r, ent["a1"],
                                                 ent["a2"], buffered,
                                                 outward)
            else:
                res = off.offset_arc(c, r, ent["a1"], ent["a2"], m)
            state.offset_preview = (
                {"kind": "arc", "center": list(c), "radius": res[0],
                 "a1": res[1], "a2": res[2]}
                if res is not None else None)
        else:  # line：闭合轮廓 → profile；开放链 → open chain
            by_id = {e["id"]: e for e in state.sketch.entities}
            loop = self._find_loop_for(state.offset_pending)
            if loop is not None:
                dist = (buffered if buffered is not None
                        else off.distance_to_segment(m, ent["p1"],
                                                     ent["p2"]))
                chain = off.orient_closed_chain(by_id, loop)
                segs = off.offset_profile(chain, dist, m)
                state.offset_preview = (
                    {"kind": "profile", "segments": segs}
                    if segs else None)
                return
            chain = off.collect_open_chain(by_id, state.offset_pending)
            if chain is None:
                state.offset_preview = None
                return
            dist = (buffered if buffered is not None
                    else off.distance_to_segment(m, ent["p1"], ent["p2"]))
            side_left = off.chain_side_for_entity(chain, ent, m)
            segs = off.offset_open_chain(chain, dist, side_left)
            state.offset_preview = (
                {"kind": "profile", "segments": segs} if segs else None)

    def _apply_offset(self, context):
        """两段式偏移：第一击选实体，移动鼠标预览，第二击确认生成。

        线段优先按闭合轮廓偏移，不属于轮廓时按所属开放链偏移
        （2026-11-16 实测反馈：未闭合线段也要能偏移）；混合链
        不支持，圆/弧可单独偏移；生成独立实体（MVP 不加偏移约束）。
        """
        if state.offset_pending is None:
            eid = picking.pick_entity(
                state.mouse_2d, state.sketch.entities,
                picking.SELECT_PIXEL_TOL * self._world_per_pixel(context))
            if eid is None:
                return
            ent = self._entity_by_id(eid)
            if (ent.get("type") == "line"
                    and self._find_loop_for(eid) is None):
                by_id = {e["id"]: e for e in state.sketch.entities}
                if off.collect_open_chain(by_id, eid) is None:
                    self.report({'WARNING'},
                                "偏移：该线段所属链含圆弧或存在分叉"
                                "（暂不支持），圆/弧可单独偏移")
                    return
            state.offset_pending = eid
            self._update_offset_preview()
            self.report({'INFO'},
                        "偏移：移动鼠标预览，或输入数值后回车定距离，"
                        "再次点击确认（ESC 取消选择）")
            return
        self._confirm_offset()

    def _confirm_offset(self):
        """以当前预览生成偏移实体（第二击与数值回车共用）。"""
        preview = state.offset_preview
        if preview is None:
            self.report({'WARNING'},
                        "偏移：当前位置无法生成有效偏移"
                        "（半径过小或有效段不足），换个位置再确认")
            return
        self._push_undo()
        sketch = state.sketch
        if preview["kind"] == "circle":
            sketch.add_circle(preview["center"], preview["radius"])
        elif preview["kind"] == "arc":
            sketch.add_arc(preview["center"], preview["radius"],
                           preview["a1"], preview["a2"])
        else:
            for a, b in preview["segments"]:
                sketch.add_line(a, b)
        state.offset_pending = None
        state.offset_preview = None
        self._refresh_dof()
        self.report({'INFO'}, "偏移完成（独立实体，未加偏移约束）")

    # ---- Mirror 镜像（SketchCAD.md §6，editing/mirror_pattern.py）----

    def _pick_multi(self, context, pending):
        """点击累加/移出多选实体（mirror/pattern 选集共用）。"""
        eid = picking.pick_entity(
            state.mouse_2d, state.sketch.entities,
            picking.SELECT_PIXEL_TOL * self._world_per_pixel(context))
        if eid is None:
            return
        if eid in pending:
            pending.remove(eid)
        else:
            pending.append(eid)

    def _apply_mirror(self, context):
        """镜像三阶段：选实体（累加多选）→ 选轴（线）→ Enter 生成。"""
        if state.mirror_stage == 'pick':
            self._pick_multi(context, state.mirror_pending)
            return
        # axis / confirm 阶段：点击线设置或更换镜像轴
        eid = picking.pick_entity(
            state.mouse_2d, state.sketch.entities,
            picking.SELECT_PIXEL_TOL * self._world_per_pixel(context))
        if eid is None:
            return
        ent = self._entity_by_id(eid)
        if ent is None or ent.get("type") != "line":
            self.report({'WARNING'}, "镜像：轴必须是线段（点击一条线）")
            return
        state.mirror_axis = eid
        self._update_mirror_preview()
        if state.mirror_preview is None:
            state.mirror_axis = None
            self.report({'WARNING'},
                        "镜像：预览计算失败（选集含已失效实体？）")
            return
        state.mirror_stage = 'confirm'
        self.report({'INFO'},
                    "镜像：预览已显示，Enter 确认生成（ESC 换轴）")

    def _update_mirror_preview(self):
        axis = self._entity_by_id(state.mirror_axis)
        ents = [self._entity_by_id(eid) for eid in state.mirror_pending]
        if (axis is None or not ents
                or any(e is None for e in ents)):
            state.mirror_preview = None
            return
        state.mirror_preview = mp.mirror_entities(
            ents, tuple(axis["p1"]), tuple(axis["p2"]))

    def _confirm_mirror(self):
        if (state.mirror_stage != 'confirm'
                or state.mirror_preview is None):
            self.report({'WARNING'}, "镜像：请先选择实体并点击镜像轴")
            return
        self._push_undo()
        for d in state.mirror_preview:
            mp.add_entity_copy(state.sketch, d)
        state.mirror_pending = []
        state.mirror_axis = None
        state.mirror_preview = None
        state.mirror_stage = 'pick'
        self._refresh_dof()
        self.report({'INFO'}, "镜像完成（独立实体副本）")

    def _esc_mirror(self):
        if state.mirror_stage == 'confirm':
            state.mirror_axis = None
            state.mirror_preview = None
            state.mirror_stage = 'axis'
            self.report({'INFO'}, "镜像：已取消轴，重新点击一条线")
        elif state.mirror_stage == 'axis':
            state.mirror_stage = 'pick'
        else:  # pick：选集非空先清空，空则退出工具
            if state.mirror_pending:
                state.mirror_pending = []
            else:
                state.tool = None
                self.report({'INFO'}, "SketchCAD：编辑工具已退出")

    # ---- Pattern 阵列（SketchCAD.md §6，editing/mirror_pattern.py）----

    def _apply_pattern(self, context):
        """阵列四阶段：选实体 → 选参照 → 定步距 → 定份数。

        参照语义：line = 线性阵列方向（沿线方向平移）；
        circle/arc = 圆形阵列中心（绕其圆心旋转）。
        """
        if state.pattern_stage == 'pick':
            self._pick_multi(context, state.pattern_pending)
            return
        if state.pattern_stage == 'axis':
            eid = picking.pick_entity(
                state.mouse_2d, state.sketch.entities,
                picking.SELECT_PIXEL_TOL * self._world_per_pixel(context))
            if eid is None:
                return
            ent = self._entity_by_id(eid)
            t = ent.get("type") if ent is not None else None
            if t == "line":
                state.pattern_kind = 'linear'
            elif t in ("circle", "arc"):
                state.pattern_kind = 'circular'
            else:
                self.report({'WARNING'},
                            "阵列：点击线（线性阵列）或圆/弧"
                            "（圆形阵列）")
                return
            state.pattern_axis = eid
            state.pattern_stage = 'step'
            state.input_buffer = ""
            if state.pattern_kind == 'linear':
                self.report({'INFO'},
                            "阵列：移动鼠标预览或输入间距（负值反向），"
                            "回车确认步距")
            else:
                self.report({'INFO'},
                            "阵列：移动鼠标预览或输入角度（度，负值"
                            "顺时针），回车确认步距")
            return
        # step / count 阶段：左键无操作（Enter 推进）

    def _pattern_reference_point(self):
        """阵列基准点：第一个选中实体的几何中心。"""
        for eid in state.pattern_pending:
            e = self._entity_by_id(eid)
            if e is None:
                continue
            t = e.get("type")
            if t == "line":
                return ((e["p1"][0] + e["p2"][0]) / 2.0,
                        (e["p1"][1] + e["p2"][1]) / 2.0)
            if t in ("circle", "arc"):
                return (e["center"][0], e["center"][1])
        return None

    def _pattern_mouse_step(self):
        """step 阶段鼠标驱动步距现算（数值缓冲优先时不用）。

        线性 = (鼠标 - 基准点) 在参照线方向上的带符号投影；
        圆形 = 鼠标方位角相对基准点方位角的带符号差（归一化到
        (-π, π]，鼠标驱动按近邻意图）。
        """
        if state.mouse_2d is None:
            return None
        axis = self._entity_by_id(state.pattern_axis)
        ref = self._pattern_reference_point()
        if axis is None or ref is None:
            return None
        m = state.mouse_2d
        if state.pattern_kind == 'linear':
            dx = axis["p2"][0] - axis["p1"][0]
            dy = axis["p2"][1] - axis["p1"][1]
            length = math.hypot(dx, dy)
            if length < 1e-9:
                return None
            return ((m[0] - ref[0]) * dx + (m[1] - ref[1]) * dy) / length
        c = axis["center"]
        raw = (math.atan2(m[1] - c[1], m[0] - c[0])
               - math.atan2(ref[1] - c[1], ref[0] - c[0]))
        return (raw + math.pi) % (2.0 * math.pi) - math.pi

    def _pattern_buffered_step(self):
        if not state.input_buffer:
            return None
        try:
            v = float(state.input_buffer)
        except ValueError:
            return None
        return v if abs(v) > 1e-9 else None

    def _pattern_buffered_count(self):
        if not state.input_buffer:
            return None
        try:
            v = int(float(state.input_buffer))
        except ValueError:
            return None
        return v if v >= 2 else None

    def _pattern_copies(self, ents, axis, step, count):
        """按参照类型生成全部副本实体 dict（不含原件）。"""
        out = []
        if state.pattern_kind == 'linear':
            direction = (axis["p2"][0] - axis["p1"][0],
                         axis["p2"][1] - axis["p1"][1])
            for e in ents:
                out.extend(mp.linear_pattern(e, direction, step, count))
        else:
            c = axis["center"]
            for e in ents:
                out.extend(mp.circular_pattern(e, c, step, count))
        return out

    def _update_pattern_preview(self):
        """step 阶段单副本预览；count 阶段全量副本预览。"""
        ents = [self._entity_by_id(eid) for eid in state.pattern_pending]
        axis = self._entity_by_id(state.pattern_axis)
        if (not ents or any(e is None for e in ents) or axis is None
                or state.pattern_kind is None):
            state.pattern_preview = None
            return
        if state.pattern_stage == 'step':
            step = self._pattern_buffered_step()
            if step is None:
                step = self._pattern_mouse_step()
            if step is None:
                state.pattern_preview = None
                return
            state.pattern_preview = self._pattern_copies(
                ents, axis, step, 2)
            return
        # count 阶段：份数未输入时按最小 2 份实时预览
        count = self._pattern_buffered_count() or 2
        state.pattern_preview = self._pattern_copies(
            ents, axis, state.pattern_step, count)

    def _confirm_pattern_step(self):
        if state.pattern_stage != 'step':
            return
        step = self._pattern_buffered_step()
        if step is None:
            step = self._pattern_mouse_step()
        if step is None or abs(step) < 1e-9:
            self.report({'WARNING'},
                        "阵列：步距无效——移动鼠标预览或输入数值"
                        "后回车")
            return
        state.pattern_step = step
        state.input_buffer = ""
        state.pattern_stage = 'count'
        self._update_pattern_preview()
        self.report({'INFO'}, "阵列：输入份数（≥2，含原件）后回车生成")

    def _confirm_pattern(self):
        if state.pattern_stage != 'count' or state.pattern_step is None:
            return
        count = self._pattern_buffered_count()
        if count is None:
            self.report({'WARNING'}, "阵列：份数须为 ≥2 的整数（含原件）")
            return
        ents = [self._entity_by_id(eid) for eid in state.pattern_pending]
        axis = self._entity_by_id(state.pattern_axis)
        if (not ents or any(e is None for e in ents) or axis is None):
            return
        copies = self._pattern_copies(
            ents, axis, state.pattern_step, count)
        if not copies:
            self.report({'WARNING'},
                        "阵列：无法生成副本（步距或方向退化）")
            return
        self._push_undo()
        for d in copies:
            mp.add_entity_copy(state.sketch, d)
        state.pattern_pending = []
        state.pattern_axis = None
        state.pattern_kind = None
        state.pattern_stage = 'pick'
        state.pattern_step = None
        state.pattern_preview = None
        state.input_buffer = ""
        self._refresh_dof()
        self.report({'INFO'},
                    f"阵列完成：{count} 份 × {len(ents)} 实体"
                    "（独立副本）")

    def _esc_pattern(self):
        s = state
        if s.pattern_stage == 'count':
            s.pattern_stage = 'step'
            s.input_buffer = ""
            s.pattern_preview = None
        elif s.pattern_stage == 'step':
            s.pattern_stage = 'axis'
            s.pattern_axis = None
            s.pattern_kind = None
            s.pattern_step = None
            s.input_buffer = ""
            s.pattern_preview = None
        elif s.pattern_stage == 'axis':
            s.pattern_stage = 'pick'
        else:  # pick：选集非空先清空，空则退出工具
            if s.pattern_pending:
                s.pattern_pending = []
            else:
                s.tool = None
                self.report({'INFO'}, "SketchCAD：编辑工具已退出")

    def _confirm_segment(self, context, p2):
        """确认连续段：推断水平/垂直并吸附，写入实体与自动约束。

        端点与既有实体端点重合（捕捉对齐 / 折线连续 / 闭合轮廓）时
        写入 coincident 固接，端点定位沿用 round6 语义；端点落在既有
        线内部（未命中端点）时吸附到垂足并写入 point_on_line 关联
        ——新画线与旧草图线的交点关联由此建立，拖动/求解持续生效。
        """
        prev = state.points_2d[-1]
        same = (
            round(prev[0], 6), round(prev[1], 6)) == (
            round(p2[0], 6), round(p2[1], 6))
        if same:
            return
        self._push_undo()
        tol = state.hv_pixel_tol * self._world_per_pixel(context)
        ctype = ac.classify_segment(prev, p2, tol)
        if ctype is not None:
            _, p2 = ac.snapped_segment(prev, p2, ctype)
        # 端点落在既有线内部 → 吸附到垂足（关联约束在实体写入后登记）
        line_hit = ac.find_line_hit(
            p2, state.sketch.entities,
            snap.SNAP_PIXEL_TOLERANCE * self._world_per_pixel(context))
        if line_hit is not None:
            p2 = line_hit[1]
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
            elif new_pos == 1 and line_hit is not None:
                sketch.add_constraint(
                    ac.POINT_ON_LINE, [new_id, line_hit[0]],
                    endpoints=[1, None])
        if ctype is not None:
            sketch.add_constraint(ctype, [new_id])
        self._refresh_dof()

    def _refresh_dof(self):
        """实体/约束变化后刷新 DOF 报告与闭合轮廓缓存（不进拖动帧路径）。"""
        if state.sketch is not None:
            state.dof_report = solver_dof.analyze(
                state.sketch.entities, state.sketch.constraints)
            state.loops = detect_closed_loops(state.sketch.entities)
            n = len(state.sketch.constraints)
            if state.constraint_follow and n > CONSTRAINT_VISIBLE:
                # 跟随最新约束：滚动窗口锚定底部（画线新加的始终可见）
                state.constraint_scroll_start = n - CONSTRAINT_VISIBLE

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

    def _scroll_constraints(self, up):
        """N-Panel 滚轮滚动约束列表（步长 CONSTRAINT_WHEEL_STEP 行）。

        滚动起始索引存于会话状态（无滑块控件）；向上滚动浏览历史后
        停止跟随最新约束，滚回底部自动恢复跟随。
        """
        n = len(state.sketch.constraints) if state.sketch else 0
        if n <= CONSTRAINT_VISIBLE:
            return
        max_scroll = n - CONSTRAINT_VISIBLE
        step = -CONSTRAINT_WHEEL_STEP if up else CONSTRAINT_WHEEL_STEP
        start = max(0, min(state.constraint_scroll_start + step, max_scroll))
        state.constraint_scroll_start = start
        state.constraint_follow = start >= max_scroll

    # ---- 选择模式与尺寸驱动（SketchCAD.md §4）----

    def _toggle_mode(self, context):
        # 切换前结束进行中的笔划/绘制，防跨模式状态错乱
        state.stroke_open = False
        self._cancel_curve_pending()
        state.tool = None
        state.extend_pending = None
        state.extend_preview = None
        state.offset_pending = None
        state.offset_preview = None
        state.mirror_pending = []
        state.mirror_stage = None
        state.mirror_axis = None
        state.mirror_preview = None
        state.pattern_pending = []
        state.pattern_stage = None
        state.pattern_axis = None
        state.pattern_kind = None
        state.pattern_step = None
        state.pattern_preview = None
        state.mode = "select" if state.mode == "draw" else "draw"
        state.selected_entity = None
        state.selected_entities = []
        state.input_buffer = ""
        state.solve_failed = False
        state.dragging = None
        # 光标跟随模式：draw 十字（作图）/ select 箭头（选择）
        if context.window is not None:
            context.window.cursor_modal_set(
                'CROSSHAIR' if state.mode == "draw" else 'DEFAULT')
        if state.mode == "draw":
            self.report({'INFO'},
                        "SketchCAD：画线模式（L 直线 / C 圆 / A 三点弧）")
        else:
            self.report({'INFO'},
                        "SketchCAD：选择模式——点击实体选中，拖动端点/"
                        "圆心实时求解，输入数值后回车定尺寸，Tab 返回画线")

    def _set_draw_tool(self, tool):
        state.draw_tool = tool
        self._cancel_curve_pending()
        state.stroke_open = False
        names = {'line': "直线", 'circle': "圆（两击：圆心→半径）",
                 'arc': "三点弧（起点→弧上点→终点）"}
        self.report({'INFO'}, f"SketchCAD：{names.get(tool, tool)}工具")

    def _cancel_curve_pending(self):
        state.circle_center = None
        state.arc_p1 = None
        state.arc_p2 = None

    def _press_draw(self, context, p):
        """draw 模式左键按下：按子工具分派（折线/圆/三点弧）。"""
        if state.draw_tool == "line":
            if not state.stroke_open:
                # 新折线起点：只记录点，不与历史末点拼段
                state.points_2d.append(p)
                state.stroke_open = True
            else:
                self._confirm_segment(context, p)
        elif state.draw_tool == "circle":
            self._press_circle(p)
        elif state.draw_tool == "arc":
            self._press_arc(p)

    def _press_circle(self, p):
        """圆两击状态机：第一击定圆心，第二击定半径并写入实体。"""
        if state.circle_center is None:
            state.circle_center = (p[0], p[1])
            return
        c = state.circle_center
        r = ((p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2) ** 0.5
        self._cancel_curve_pending()
        if r < _MIN_RADIUS:
            self.report({'WARNING'}, "圆：半径过小，已取消（重新点击圆心）")
            return
        self._push_undo()
        state.sketch.add_circle(c, r)
        self._refresh_dof()

    def _press_arc(self, p):
        """弧三点式状态机：起点 → 弧上点 → 终点（三点定圆现算）。"""
        if state.arc_p1 is None:
            state.arc_p1 = (p[0], p[1])
            return
        if state.arc_p2 is None:
            if ((p[0] - state.arc_p1[0]) ** 2
                    + (p[1] - state.arc_p1[1]) ** 2) ** 0.5 < _MIN_RADIUS:
                self.report({'WARNING'}, "弧：弧上点与起点重合，重新点击")
                return
            state.arc_p2 = (p[0], p[1])
            return
        p1, p2, p3 = state.arc_p1, state.arc_p2, (p[0], p[1])
        self._cancel_curve_pending()
        res = geom.arc_from_3_points(p1, p2, p3)
        if res is None:
            self.report({'WARNING'},
                        "弧：三点共线或重合，已取消（重新点击起点）")
            return
        center, r, a1, a2 = res
        if r < _MIN_RADIUS:
            self.report({'WARNING'}, "弧：半径过小，已取消（重新点击起点）")
            return
        self._push_undo()
        state.sketch.add_arc(center, r, a1, a2)
        self._refresh_dof()

    def _clear_selection(self):
        """清空选集（主选中一并清除）。"""
        state.selected_entities = []
        state.selected_entity = None

    def _toggle_entity_selection(self, eid):
        """Shift+点击 toggle：已在选集则移出，否则加入并设为主选中。"""
        if eid in state.selected_entities:
            state.selected_entities.remove(eid)
            if state.selected_entity == eid:
                state.selected_entity = (
                    state.selected_entities[-1]
                    if state.selected_entities else None)
        else:
            state.selected_entities.append(eid)
            state.selected_entity = eid
        state.input_buffer = ""
        state.solve_failed = False

    def _press_select(self, context, event, p):
        """select 模式按下（Phase 4 选集重构）。

        端点/圆心命中：Shift 按下时 toggle 选中所属实体（不进入
        拖动），否则进入拖动求解并把所属实体纳入选集；空白处按下
        一律先记框选起点——RELEASE 时矩形过小视为点击空白（Shift
        保持选集、否则清空），拖动超阈值即为框选。框选与拖动求解
        天然互斥：命中特征按下永不启动框选。
        """
        shift = bool(event.shift)
        tol = picking.SELECT_PIXEL_TOL * self._world_per_pixel(context)
        hit = picking.pick_endpoint(p, state.sketch.entities, tol)
        if hit is not None:
            if shift:
                self._toggle_entity_selection(hit[0])
                return
            # 拖动快照延迟到实际移动时 push：纯点击（端点/圆心命中）
            # 仅选中，不污染 undo 栈；按住拖动才回退到拖动前
            state.dragging = hit
            state.selected_entity = hit[0]
            state.solve_failed = False
            state.drag_last_solve = 0.0
            state.drag_undo_pushed = False
            if hit[0] not in state.selected_entities:
                state.selected_entities.append(hit[0])
            return
        state.box_select_start = (
            event.mouse_region_x, event.mouse_region_y)
        state.box_select_cur = state.box_select_start
        state.box_select_shift = shift

    def _finish_box_select(self, context):
        """框选完成：屏幕矩形转基准面 2D 矩形按相交判定更新选集。

        矩形过小（<_BOX_MIN_PIXELS 对角线）视为点击空白：Shift
        保持选集不动，否则清空。Shift 框选为加选（并集），普通框
        选为替换；替换后主选中取选集最后一个（尺寸输入归属明确）。
        """
        sx, sy = state.box_select_start
        cx, cy = state.box_select_cur or (sx, sy)
        state.box_select_start = None
        state.box_select_cur = None
        shift = state.box_select_shift
        state.box_select_shift = False
        if ((cx - sx) ** 2 + (cy - sy) ** 2) ** 0.5 < _BOX_MIN_PIXELS:
            if not shift:
                self._clear_selection()
            return
        region = context.region
        rv3d = context.region_data
        if region is None or rv3d is None:
            return
        d = state.datum
        pts_2d = []
        for px, py in ((sx, sy), (cx, cy)):
            ro = region_2d_to_origin_3d(region, rv3d, (px, py))
            rv = region_2d_to_vector_3d(region, rv3d, (px, py))
            hit = intersect_line_plane(ro, ro + rv, d.origin, d.normal)
            if hit is None:
                return
            pts_2d.append(d.to_2d(hit))
        (x1, y1), (x2, y2) = pts_2d
        rmin = (min(x1, x2), min(y1, y2))
        rmax = (max(x1, x2), max(y1, y2))
        picked = picking.entities_in_box(rmin, rmax,
                                         state.sketch.entities)
        if shift:
            for eid in picked:
                if eid not in state.selected_entities:
                    state.selected_entities.append(eid)
        else:
            state.selected_entities = list(picked)
            state.selected_entity = picked[-1] if picked else None
        if picked:
            self.report({'INFO'},
                        f"框选{'加选' if shift else '选中'} "
                        f"{len(picked)} 个实体"
                        f"（选集 {len(state.selected_entities)}）")

    def _delete_selection(self):
        """删除选集全部实体（级联约束），一次 undo 快照覆盖整批。"""
        eids = [eid for eid in state.selected_entities
                if self._entity_by_id(eid) is not None]
        if not eids:
            return
        self._push_undo()
        for eid in eids:
            state.sketch.remove_entity_and_constraints(eid)
        self._clear_selection()
        self._refresh_dof()
        self.report({'INFO'},
                    f"已删除 {len(eids)} 个实体"
                    "（级联约束同步移除）")

    def _drag_update(self):
        """拖动实时求解（节流 ~60Hz；失败时保持上次成功几何）。

        dragging=(eid, pos)：line/arc 端点 pos 0/1（arc 端点由鼠标
        反算角度、保持半径），circle 圆心 pos=2。
        """
        now = time.perf_counter()
        if now - state.drag_last_solve < _DRAG_SOLVE_INTERVAL:
            return
        state.drag_last_solve = now
        eid, pos = state.dragging
        if state.mouse_2d is None:
            return
        x, y = state.mouse_2d
        for e in state.sketch.entities:
            if e.get("id") == eid:
                # 实际移动几何前 push 快照（纯点击不进 undo 栈）
                if not state.drag_undo_pushed:
                    self._push_undo()
                    state.drag_undo_pushed = True
                t = e.get("type")
                if t == "line":
                    if pos == 0:
                        e["p1"] = [x, y]
                    else:
                        e["p2"] = [x, y]
                elif t == "circle" and pos == picking.POS_CENTER:
                    e["center"] = [x, y]
                elif t == "arc" and pos in (0, 1):
                    # 拖弧端点：鼠标投影到圆周上反算角度（保持半径）；
                    # 圆心/半径在端点拖动中保持不变
                    c = e["center"]
                    r = e["radius"]
                    ang = math.atan2(y - c[1], x - c[0])
                    key = "a1" if pos == 0 else "a2"
                    e[key] = ang
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
        if key_type in ('MINUS', 'NUMPAD_MINUS'):
            # 负号仅用于带符号输入（阵列步距/角度）；尺寸输入的负值
            # 由 _apply_dimension 的 value<=0 校验拒绝
            if not state.input_buffer.startswith('-'):
                state.input_buffer = '-' + state.input_buffer
            return True
        if key_type == 'BACKSPACE':
            state.input_buffer = state.input_buffer[:-1]
            return True
        return False

    def _apply_dimension(self):
        """数值确认：按选中实体类型写入尺寸约束并求解回写。

        line -> distance（线长）；circle/arc -> radius（半径）。
        选中实体上已有同类型启用约束时更新其 value（避免重复约束）。
        """
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
        ent = self._entity_by_id(eid)
        etype = ent.get("type") if ent is not None else None
        if etype == "line":
            ctype = "distance"
        elif etype in ("circle", "arc"):
            ctype = "radius"
        else:
            state.input_buffer = ""
            return
        existing = None
        for c in sketch.constraints:
            if (c.get("type") == ctype and c.get("enabled", True)
                    and eid in (c.get("targets") or [])):
                existing = c
                break
        if existing is not None:
            self._push_undo()
            existing["value"] = value
        else:
            self._push_undo()
            sketch.add_constraint(ctype, [eid], value=value)
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
        sketch = state.sketch
        n_line = sum(1 for e in sketch.entities if e.get("type") == "line")
        n_curve = sum(1 for e in sketch.entities
                      if e.get("type") in ("circle", "arc"))
        self._cleanup(context)
        msg = (f"草图完成：{count} 个点 / {n_line} 条线段"
               + (f" / {n_curve} 个圆弧" if n_curve else ""))
        if not (sketch.host or {}).get("object"):
            msg += "（断开关联草图：仅保存数据，未提交 mesh）"
        self.report({'INFO'}, msg)
        return {'FINISHED'}

    def _commit(self, context):
        entities = state.sketch.entities
        if not state.points_2d and not entities:
            return 0

        # 1. 数据层：容器对象 JSON（新建或更新）——不依赖活动对象，
        #    独立打开（无宿主）时这是唯一持久化路径
        if state.container_name:
            if not manager.update_container(state.container_name, state.sketch):
                cont = manager.create_container(context, state.sketch)
                state.container_name = cont.name
            else:
                cont = bpy.data.objects.get(state.container_name)
        else:
            cont = manager.create_container(context, state.sketch)
            state.container_name = cont.name

        # 2. mesh 提交前置检查：断开关联的草图无宿主（§9 所有权边界
        #    ——无提交目标，仅保存数据）；活动对象须为宿主 mesh
        if not (state.sketch.host or {}).get("object"):
            return 0

        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            return 0

        # 3. mesh 提交（替换式）：重进续画后再退出时，先按上一轮提交
        #    快照删除宿主 mesh 中的旧轮廓边，再提交当前几何——否则
        #    尺寸修改后新边与旧边不重合，"原来的草图"会残留成双影。
        #    旧边被建模引用（关联面）或已被手动编辑时保留并提示，
        #    绝不触碰非本草图提交物的几何。
        bm = bmesh.from_edit_mesh(obj.data)
        mwi = obj.matrix_world.inverted()

        def to_local(p):
            return mwi @ state.datum.to_world(p[0], p[1])

        if cont is not None:
            old_segments = manager.load_committed_segments(cont)
            if old_segments:
                removed, blocked, missing = self._remove_committed_edges(
                    bm, old_segments)
                if removed:
                    self.report({'INFO'},
                                f"已替换上一轮提交的 {removed} 条轮廓边")
                if blocked:
                    self.report({'WARNING'},
                                f"{blocked} 条旧轮廓边已被建模引用"
                                "（存在关联面），保留未删；如需清理"
                                "请手动处理")
                if missing:
                    self.report({'WARNING'},
                                f"{missing} 条旧轮廓边未能定位"
                                "（可能已被手动编辑），保留未删")

        # 3. 提交当前几何：line 直接两点；circle/arc 离散为折线段
        #    （与 GPU 预览同密度），端点经 round6 缓存共享顶点
        #    （相邻实体端点自动焊接）；同步收集本次提交快照
        vert_cache = {}

        def get_vert(p):
            key = (round(p[0], 6), round(p[1], 6))
            v = vert_cache.get(key)
            if v is None:
                v = bm.verts.new(to_local(p))
                vert_cache[key] = v
            return v

        new_segments = []
        for e in entities:
            for a, b in cmt.entity_segments(e):
                try:
                    bm.edges.new((get_vert(a), get_vert(b)))
                except ValueError:
                    pass  # 重合边，跳过
                new_segments.append(
                    [list(to_local(a)), list(to_local(b))])

        bm.verts.ensure_lookup_table()
        bmesh.update_edit_mesh(obj.data)

        if cont is not None:
            manager.save_committed_segments(cont, new_segments)

        try:
            bpy.ops.ed.undo_push()
        except Exception:
            pass
        return len(state.points_2d)

    def _remove_committed_edges(self, bm, old_segments):
        """按上一轮提交快照（宿主局部坐标段）删除 mesh 中的旧轮廓边。

        边两端点 round6 匹配即视为本插件提交物；被 face 引用（用户
        已基于提交物建模）或坐标已被手动编辑（匹配不到）的边保留
        并计数——绝不触碰非本草图提交物的几何。删边后清理被删边
        留下的孤立顶点（不再被任何边/面引用时）。
        返回 (removed, blocked, missing)。
        """
        def vkey(co):
            return (round(co[0], 6), round(co[1], 6), round(co[2], 6))

        edge_map = {}
        for e in bm.edges:
            k = cmt.seg_key(e.verts[0].co, e.verts[1].co)
            edge_map.setdefault(k, e)

        touched = set()
        to_remove = []
        removed = blocked = missing = 0
        for seg in old_segments:
            try:
                k = cmt.seg_key(seg[0], seg[1])
            except (TypeError, IndexError):
                missing += 1
                continue
            edge = edge_map.pop(k, None)
            if edge is None:
                missing += 1
                continue
            if edge.link_faces:
                blocked += 1
                continue
            to_remove.append(edge)
            touched.update(cmt.seg_key(seg[0], seg[1]))
            removed += 1
        for e in to_remove:
            bm.edges.remove(e)
        for v in list(bm.verts):
            if v.link_edges or v.link_faces or vkey(v.co) not in touched:
                continue
            bm.verts.remove(v)
        return removed, blocked, missing

    def _cleanup(self, context):
        state.active = False
        if state.draw_handler is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(
                    state.draw_handler, 'WINDOW')
            except Exception:
                pass
            state.draw_handler = None

        # 宿主 ghost 恢复（引用即取即用：按名取回，不长期持有）
        if state.host_obj_name and state.host_display_type is not None:
            host_obj = bpy.data.objects.get(state.host_obj_name)
            if host_obj is not None:
                host_obj.display_type = state.host_display_type
        state.host_obj_name = None
        state.host_display_type = None
        state.aid_points = []

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