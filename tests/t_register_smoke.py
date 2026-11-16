"""注册级冒烟验证：验证 Phase 3 新增 operator 注册正常。

运行：blender --background --python t_register_smoke.py
（无头模式 print 可能被吞，结果同时落盘 tests/smoke_result.json）
"""

import json
import sys
from pathlib import Path

import bpy

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

RESULTS = {"register": False, "classes": {}, "errors": []}

EXPECTED = [

    "SKETCHCAD_OT_toggle_sketch_visible",
    "SKETCHCAD_OT_break_sketch_link",
    "SKETCH_OT_delete_sketch",
    "SKETCH_OT_face_driven_sketch",
    "SKETCHCAD_PT_main",
]

try:
    import sketchcad
    try:
        sketchcad.unregister()
    except Exception:
        pass
    sketchcad.register()
    RESULTS["register"] = True
    for cls_name in EXPECTED:
        RESULTS["classes"][cls_name] = cls_name in dir(bpy.types)
    # PropertyGroup 注册后不进 bpy.types.dir()（Blender 5.3 实测），
    # 改由 Scene 的 PointerProperty 属性间接验证——PointerProperty
    # 注册时已校验 Settings -> CollectionProperty(Item) 引用链
    RESULTS["prop_group"] = hasattr(bpy.types.Scene, "sketchcad_settings")
    # 独立打开入口（Phase 3 §2）：operator 的 container_name 属性注册
    op_rna = bpy.ops.sketch.face_driven_sketch.get_rna_type()
    RESULTS["open_container_prop"] = "container_name" in {
        p.identifier for p in op_rna.properties}
except Exception as e:
    RESULTS["errors"].append(f"{type(e).__name__}: {e}")

out = PROJECT_DIR / "tests" / "smoke_result.json"
out.write_text(json.dumps(RESULTS, indent=2), encoding="utf-8")
print("SMOKE_RESULT:", json.dumps(RESULTS))