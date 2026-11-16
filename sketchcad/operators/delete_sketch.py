"""删除草图容器 operator（S2 最小版，SketchCAD.md v2 §2）。

所有权边界（§9）：本命令只删除插件自己的资产（容器 EMPTY + JSON
自定义属性），明确不碰宿主 mesh 中已提交的几何——退出草图后提交物
已成为建模资产（用户可能已在其上挤出/倒角），由用户自主管理。

用法：物体模式在 Outliner 选中 Sketch_NNN 容器，F3 搜索
"Delete SketchCAD Sketch" 执行。
"""

import bpy

from .. import manager
from ..core import sketch_data as skd
from ..core.state import state


def _is_sketch_container(obj):
    return obj is not None and obj.get("SketchCAD_Data") is not None


class SKETCH_OT_delete_sketch(bpy.types.Operator):
    """删除选中的 SketchCAD 草图容器（不影响 mesh 中已提交的几何）"""
    bl_idname = "sketch.delete_sketch"
    bl_label = "Delete SketchCAD Sketch"
    bl_options = {'REGISTER', 'UNDO'}

    # 按名删除（N-Panel 管理列表按钮）；留空时用当前活动对象（F3 搜索路径）
    name: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        # poll 为类方法拿不到实例属性 name：仅做静态放行，目标校验
        # 与错误提示在 execute 内处理（与 n_panel 约束 operator 同模式）
        obj = context.active_object
        if state.active and obj is not None and obj.name == state.container_name:
            return False
        if _is_sketch_container(obj):
            return True
        col = bpy.data.collections.get(manager.COLLECTION_NAME)
        return bool(col and col.objects)

    def execute(self, context):
        obj = (bpy.data.objects.get(self.name) if self.name
               else context.active_object)
        if not _is_sketch_container(obj):
            self.report({'WARNING'}, "当前活动对象不是 SketchCAD 草图容器")
            return {'CANCELLED'}
        if state.active and obj.name == state.container_name:
            self.report({'WARNING'}, "草图会话进行中，请先 ESC 退出再删除")
            return {'CANCELLED'}

        data = skd.load_from_object(obj)
        seg_count = len(data.entities) if data else 0
        name = obj.name

        bpy.data.objects.remove(obj, do_unlink=True)
        manager.prune_collection()  # 最后一条草图被删后不留空集合
        self.report({'INFO'},
                    f"已删除草图 {name}（{seg_count} 条线段的数据）；"
                    "mesh 中已提交的几何不受影响")
        return {'FINISHED'}