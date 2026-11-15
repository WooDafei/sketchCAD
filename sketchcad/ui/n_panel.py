"""N-Panel：SketchCAD 侧边栏（Item 式尺寸信息与约束管理）。

替代视口 GPU 标注路线（用户实测不可见后弃用）：选中线段的当前长度
与目标尺寸以 Item 面板数值形式显示；约束列表支持启用/禁用与删除；
推断阈值可配置。草图 modal 对 UI 区域事件放行，面板可在草图进行中
直接操作。
"""

import bpy

from ..core.state import state
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
            layout.prop(settings, "hv_pixel_tol")
            return

        sketch = state.sketch
        layout.label(text=f"活动草图: {state.container_name}")

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
        else:
            layout.label(text="未选中线段（Tab 进入选择模式）")

        layout.separator()
        layout.label(text=f"约束 ({len(sketch.constraints)}):")
        box = layout.box()
        if not sketch.constraints:
            box.label(text="（无约束）")
        for c in sketch.constraints:
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

        layout.separator()
        layout.prop(settings, "hv_pixel_tol")


classes = (
    SketchCADSettings,
    SKETCHCAD_OT_toggle_constraint,
    SKETCHCAD_OT_remove_constraint,
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