"""N-Panel：SketchCAD 侧边栏（Item 式尺寸信息与约束管理 + 草图管理器）。

替代视口 GPU 标注路线（用户实测不可见后弃用）：选中线段的当前长度
与目标尺寸以 Item 面板数值形式显示；约束列表支持启用/禁用与删除；
推断阈值可配置。草图 modal 对 UI 区域事件放行，面板可在草图进行中
直接操作。

Phase 3 草图管理器（§2）：列出全部草图（会话内外均可操作），支持
隐藏/显示（visible + 容器 hide_viewport）、断开关联（实体与约束
保留、求解器继续生效）与删除（只删容器不碰 mesh 提交物）。

2026-11-16 实测反馈（终版）：约束列表固定高度窗口（CONSTRAINT_VISIBLE
行），面板不随约束增加而延长；**鼠标滚轮在 N-Panel 上滚动浏览约束**
（modal 拦截滚轮，见 operators/face_sketch.py），无滑块控件；默认
跟随最新约束（画线新加的始终可见），向上滚动浏览历史后停止跟随，
滚回底部自动恢复跟随。
"""

import bpy

from .. import manager
from ..core import sketch_data as skd
from ..core.state import CONSTRAINT_VISIBLE, state
from ..solver import apply as solver_apply
from ..solver import dof as solver_dof


def _on_tol_update(self, context):
    state.hv_pixel_tol = self.hv_pixel_tol


class SketchCADSettings(bpy.types.PropertyGroup):
    hv_pixel_tol: bpy.props.FloatProperty(
        name="推断阈值(px)",
        description="水平/垂直自动约束的屏幕像素容差",
        default=8.0, min=1.0, max=50.0, update=_on_tol_update)


class SKETCHCAD_OT_toggle_constraint(bpy.types.Operator):
    """启用/禁用一条约束（启用时重算几何）"""
    bl_idname = "sketchcad.toggle_constraint"
    bl_label = "Toggle Constraint"
    bl_options = {'REGISTER', 'UNDO'}

    constraint_id: bpy.props.IntProperty()

    @classmethod
    def poll(cls, context):
        return state.active and state.sketch is not None

    def execute(self, context):
        con = state.sketch.constraint_by_id(self.constraint_id)
        if con is None:
            return {'CANCELLED'}
        enabling = not con.get("enabled", True)
        con["enabled"] = enabling
        if enabling:
            ok, message = solver_apply.solve_and_apply(state.sketch)
            state.solve_failed = not ok
            if not ok:
                self.report({'WARNING'}, message)
        state.dof_report = solver_dof.analyze(
            state.sketch.entities, state.sketch.constraints)
        self.report({'INFO'}, f"约束 {self.constraint_id} "
                      f"已{'启用' if enabling else '禁用'}")
        return {'FINISHED'}


class SKETCHCAD_OT_remove_constraint(bpy.types.Operator):
    """删除一条约束（几何保持现状）"""
    bl_idname = "sketchcad.remove_constraint"
    bl_label = "Remove Constraint"
    bl_options = {'REGISTER', 'UNDO'}

    constraint_id: bpy.props.IntProperty()

    @classmethod
    def poll(cls, context):
        return state.active and state.sketch is not None

    def execute(self, context):
        if not state.sketch.remove_constraint(self.constraint_id):
            return {'CANCELLED'}
        state.dof_report = solver_dof.analyze(
            state.sketch.entities, state.sketch.constraints)
        self.report({'INFO'}, f"约束 {self.constraint_id} 已删除")
        return {'FINISHED'}


class SKETCHCAD_OT_toggle_sketch_visible(bpy.types.Operator):
    """显示/隐藏草图（JSON visible 字段 + 容器 EMPTY 视口显示同步）"""
    bl_idname = "sketchcad.toggle_sketch_visible"
    bl_label = "Toggle Sketch Visibility"
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        # 按名调用，poll 拿不到实例属性；目标校验在 execute（同 delete 模式）
        return True

    def execute(self, context):
        if state.active and self.name == state.container_name:
            self.report({'WARNING'}, "草图会话进行中，请先 ESC 退出再操作")
            return {'CANCELLED'}
        _obj, data = manager.load_sketch(self.name)
        if data is None:
            self.report({'WARNING'}, f"草图 {self.name} 不存在或数据缺失")
            return {'CANCELLED'}
        manager.set_sketch_visible(self.name, not data.visible)
        self.report({'INFO'},
                    f"草图 {self.name} 已{'显示' if not data.visible else '隐藏'}")
        return {'FINISHED'}


class SKETCHCAD_OT_break_sketch_link(bpy.types.Operator):
    """断开与宿主面的关联（实体与约束全部保留，求解器继续生效）"""
    bl_idname = "sketchcad.break_sketch_link"
    bl_label = "Break Sketch Link"
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        if state.active and self.name == state.container_name:
            self.report({'WARNING'}, "草图会话进行中，请先 ESC 退出再操作")
            return {'CANCELLED'}
        _obj, data = manager.load_sketch(self.name)
        if data is None:
            self.report({'WARNING'}, f"草图 {self.name} 不存在或数据缺失")
            return {'CANCELLED'}
        if not (data.host or {}).get("object"):
            self.report({'INFO'}, f"草图 {self.name} 已无宿主关联")
            return {'CANCELLED'}
        manager.break_link(self.name)
        self.report({'INFO'},
                    f"已断开 {self.name} 与宿主的关联；约束与尺寸标注"
                    "全部保留，草图成为独立 2D 参数化对象")
        return {'FINISHED'}


class SKETCHCAD_PT_main(bpy.types.Panel):
    bl_idname = "SKETCHCAD_PT_main"
    bl_label = "SketchCAD"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "SketchCAD"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.sketchcad_settings

        if not state.active or state.sketch is None:
            layout.label(text="无活动草图会话", icon='INFO')
            layout.separator()
            self._draw_manager(layout)
            layout.prop(settings, "hv_pixel_tol")
            return

        sketch = state.sketch
        layout.label(text=f"活动草图: {state.container_name}")

        # 多选选集计数（单选详情各行不变，选集 >1 时追加提示行）
        if len(state.selected_entities) > 1:
            layout.label(
                text=f"选集: {len(state.selected_entities)} 个实体"
                "（DEL 批量删除）", icon='RESTRICT_SELECT_OFF')

        ent = None
        if state.selected_entity is not None:
            ent = next((x for x in sketch.entities
                        if x.get("id") == state.selected_entity), None)
        if ent is not None and ent.get("type") == "line":
            p1, p2 = ent["p1"], ent["p2"]
            length = ((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2) ** 0.5
            col = layout.column(align=True)
            col.label(text=f"选中线段 #{ent['id']}", icon='MESH_DATA')
            col.label(text=f"当前长度: {length:.4f}")
            target = None
            for c in sketch.constraints:
                if (c.get("type") == "distance"
                        and c.get("enabled", True)
                        and state.selected_entity in (c.get("targets") or [])):
                    target = c
                    break
            if target is not None:
                col.label(text=f"目标尺寸: {target.get('value', 0.0):g}")
            else:
                col.label(text="目标尺寸: 未设置")
            col.label(text="（select 模式选中后输数字 + 回车设置）")
        elif ent is not None and ent.get("type") in ("circle", "arc"):
            col = layout.column(align=True)
            kind = "圆" if ent["type"] == "circle" else "弧"
            col.label(text=f"选中{kind} #{ent['id']}", icon='MESH_CIRCLE')
            col.label(text=f"当前半径: {ent['radius']:.4f}")
            target = None
            for c in sketch.constraints:
                if (c.get("type") == "radius"
                        and c.get("enabled", True)
                        and state.selected_entity in (c.get("targets") or [])):
                    target = c
                    break
            if target is not None:
                col.label(text=f"目标半径: {target.get('value', 0.0):g}")
            else:
                col.label(text="目标半径: 未设置")
            col.label(text="（select 模式选中后输数字 + 回车设置）")
        else:
            layout.label(text="未选中实体（Tab 进入选择模式）")

        layout.separator()
        layout.label(text=f"约束 ({len(sketch.constraints)}):")
        box = layout.box()
        self._draw_constraint_list(box, sketch)

        layout.separator()
        layout.prop(settings, "hv_pixel_tol")

        layout.separator()
        self._draw_manager(layout)

    def _draw_constraint_list(self, box, sketch):
        """约束列表固定高度窗口（CONSTRAINT_VISIBLE 行），无滑块。

        滚动位置由鼠标滚轮驱动（modal 拦截，存于会话状态）；默认
        跟随最新约束，向上滚动浏览历史后停止跟随，滚回底部自动恢复。
        """
        cons = sketch.constraints
        n = len(cons)
        if n == 0:
            box.label(text="（无约束）")
            return
        if n <= CONSTRAINT_VISIBLE:
            for c in cons:
                self._draw_constraint_row(box, c)
            return
        max_scroll = n - CONSTRAINT_VISIBLE
        start = min(state.constraint_scroll_start, max_scroll)
        for c in cons[start:start + CONSTRAINT_VISIBLE]:
            self._draw_constraint_row(box, c)

    def _draw_constraint_row(self, box, c):
        row = box.row(align=True)
        enabled = c.get("enabled", True)
        targets = ",".join(str(t) for t in (c.get("targets") or []))
        extra = f" = {c['value']:g}" if "value" in c else ""
        ep = c.get("endpoints")
        if ep is not None:
            extra += f" @{ep[0]}->{ep[1]}"
        row.label(text=f"{c['type']}({targets}){extra}",
                  icon='CHECKMARK' if enabled else 'X')
        op = row.operator("sketchcad.toggle_constraint", text="",
                          icon='HIDE_ON' if enabled else 'HIDE_OFF')
        op.constraint_id = c["id"]
        op = row.operator("sketchcad.remove_constraint", text="",
                          icon='TRASH')
        op.constraint_id = c["id"]

    def _draw_manager(self, layout):
        """草图管理器（§2）：会话内外均可用。"""
        col = bpy.data.collections.get(manager.COLLECTION_NAME)
        layout.label(text="草图管理", icon='OUTLINER_COLLECTION')
        if col is None or not col.objects:
            layout.label(text="（无草图）", icon='INFO')
            return
        box = layout.box()
        for obj in col.objects:
            data = skd.load_from_object(obj)
            if data is None:
                continue
            host = (data.host or {}).get("object")
            is_session = state.active and obj.name == state.container_name
            label = obj.name
            if is_session:
                label += "（会话中）"
            elif host:
                label += f" · {host}"
            else:
                label += " · unlinked"
            row = box.row(align=True)
            row.label(text=label, icon='EMPTY_DATA')
            if not is_session:
                # 独立打开入口（§2 遗留收尾）：断开关联的草图也按
                # 存储的 datum 坐标系直接进入（不依赖宿主面）
                op = row.operator("sketch.face_driven_sketch", text="",
                                  icon='PLAY')
                op.container_name = obj.name
            op = row.operator("sketchcad.toggle_sketch_visible", text="",
                              icon='HIDE_ON' if data.visible else 'HIDE_OFF',
                              depress=not data.visible)
            op.name = obj.name
            if host:
                op = row.operator("sketchcad.break_sketch_link", text="",
                                  icon='UNLINKED')
                op.name = obj.name
            else:
                # 已断开：固定状态标识（按钮位置不变，避免"点击后消失"困惑）
                row.label(text="", icon='UNLINKED')
            op = row.operator("sketch.delete_sketch", text="", icon='TRASH')
            op.name = obj.name


classes = (
    SketchCADSettings,
    SKETCHCAD_OT_toggle_constraint,
    SKETCHCAD_OT_remove_constraint,
    SKETCHCAD_OT_toggle_sketch_visible,
    SKETCHCAD_OT_break_sketch_link,
    SKETCHCAD_PT_main,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.sketchcad_settings = bpy.props.PointerProperty(
        type=SketchCADSettings)


def unregister():
    del bpy.types.Scene.sketchcad_settings
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)