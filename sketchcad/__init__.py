"""SketchCAD：面驱动的参数化草图插件（Phase 0-2）。

注册入口与快捷键（编辑模式 Mesh keymap：Ctrl+Shift+S）。
"""

import bpy

from .operators.delete_sketch import SKETCH_OT_delete_sketch
from .operators.face_sketch import SKETCH_OT_face_driven_sketch
from .ui import n_panel

addon_keymaps = []


def register():
    n_panel.register()
    bpy.utils.register_class(SKETCH_OT_face_driven_sketch)
    bpy.utils.register_class(SKETCH_OT_delete_sketch)

    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name='Mesh', space_type='EMPTY')
        kmi = km.keymap_items.new(
            SKETCH_OT_face_driven_sketch.bl_idname,
            'S', 'PRESS', ctrl=True, shift=True)
        addon_keymaps.append((km, kmi))


def unregister():
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()
    bpy.utils.unregister_class(SKETCH_OT_delete_sketch)
    bpy.utils.unregister_class(SKETCH_OT_face_driven_sketch)
    n_panel.unregister()