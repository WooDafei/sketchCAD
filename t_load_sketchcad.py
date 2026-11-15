"""SketchCAD 开发加载器（dev loader）。

用法：Blender 文本编辑器 -> 打开本文件（从磁盘）-> 运行脚本。
每次修改 sketchcad/ 包代码后重新运行即可热重载。

打包发布：对 sketchcad/ 目录 zip 后经
Preferences -> Add-ons -> Install from Disk 安装
（Extension 格式，见 sketchcad/blender_manifest.toml）。

定位策略：Blender 文本编辑器在当前 .blend 未保存时，__file__ 可能
退化为相对形式（如 "\\t_load_sketchcad.py"，无目录），因此不直接
信任 __file__，而是解析后校验 sketchcad/blender_manifest.toml 是否
真实存在，校验失败回退到下方 PROJECT_DIR_FALLBACK。
"""

import importlib
import os
import sys

import bpy

# 自动定位失败时的兜底目录（sketchcad/ 包所在目录），换机器时按需修改
PROJECT_DIR_FALLBACK = r"E:\autoprogram\projects\sketchCAD"

_MANIFEST = ("sketchcad", "blender_manifest.toml")


def _valid_pkg_dir(path):
    return bool(path) and os.path.isfile(os.path.join(path, *_MANIFEST))


def _resolve_pkg_dir():
    """定位 sketchcad/ 包所在目录：__file__ 解析 -> 常量兜底。"""
    raw = globals().get("__file__")
    candidates = []
    if raw:
        try:
            # bpy.path.abspath 先解析 // 相对路径（相对 .blend），失败则按原值处理
            resolved = bpy.path.abspath(raw)
        except Exception:
            resolved = raw
        candidates.append(os.path.dirname(os.path.abspath(resolved)))
    candidates.append(PROJECT_DIR_FALLBACK)

    for d in candidates:
        if _valid_pkg_dir(d):
            return d

    raise RuntimeError(
        "无法定位 sketchcad 包目录：\n"
        f"  __file__ = {raw!r}\n"
        f"  尝试过的目录 = {candidates}\n"
        "  请检查 t_load_sketchcad.py 顶部的 PROJECT_DIR_FALLBACK "
        "是否指向 sketchcad/ 所在目录，以及该目录下存在 "
        "sketchcad/blender_manifest.toml")


_pkg_parent = _resolve_pkg_dir()
if _pkg_parent not in sys.path:
    sys.path.insert(0, _pkg_parent)

_MODULES = [
    "sketchcad.core.state",
    "sketchcad.core.datum",
    "sketchcad.core.sketch_data",
    "sketchcad.manager",
    "sketchcad.snapping.snap",
    "sketchcad.gpu.draw_2d",
    "sketchcad.operators.delete_sketch",
    "sketchcad.operators.face_sketch",
    "sketchcad",
]

# 1. 若已加载，先用旧模块状态注销（keymap/类）
_old = sys.modules.get("sketchcad")
if _old is not None:
    try:
        _old.unregister()
    except Exception:
        pass

# 2. 热重载（依赖顺序：core -> manager/gpu -> operators -> 包根）
for _name in _MODULES:
    if _name in sys.modules:
        importlib.reload(sys.modules[_name])
    else:
        importlib.import_module(_name)

# 3. 注册
sys.modules["sketchcad"].register()
print("SketchCAD dev loaded from:", _pkg_parent)
print("SketchCAD modules:", ", ".join(_MODULES))