"""最小草图管理器（SketchCAD.md v2 §2 / Phase 0）。

负责 SketchCAD_Collection 集合、Sketch_NNN 命名、容器 EMPTY 对象的
创建与"宿主对象 + 面快照"匹配（重进对齐）。face index 在网格编辑后
会重排失效，因此关联一律采用坐标快照匹配。
"""

import bpy

from mathutils import Matrix, Vector

from .core import sketch_data as skd

COLLECTION_NAME = "SketchCAD_Collection"
NAME_FMT = "Sketch_{:03d}"
NAME_PREFIX = "Sketch_"

_CENTER_TOL = 1e-4        # 面中心快照匹配距离容差（世界单位）
_NORMAL_DOT_MIN = 1.0 - 1e-5


def ensure_collection(context):
    col = bpy.data.collections.get(COLLECTION_NAME)
    if col is None:
        col = bpy.data.collections.new(COLLECTION_NAME)
        context.scene.collection.children.link(col)
    return col


def next_sketch_name(col):
    max_id = 0
    for obj in col.objects:
        if obj.name.startswith(NAME_PREFIX):
            suffix = obj.name[len(NAME_PREFIX):]
            if suffix.isdigit():
                max_id = max(max_id, int(suffix))
    return NAME_FMT.format(max_id + 1)


def find_matching_sketch(host_name, center_w, normal_w):
    """按宿主对象名 + 面中心/法线快照查找已有草图。

    返回 (container_object, SketchData) 或 (None, None)。
    """
    col = bpy.data.collections.get(COLLECTION_NAME)
    if col is None:
        return None, None
    for obj in col.objects:
        data = skd.load_from_object(obj)
        if data is None:
            continue
        host = data.host or {}
        if host.get("object") != host_name:
            continue
        snap = host.get("face_snapshot") or {}
        c, n = snap.get("center"), snap.get("normal")
        if not c or not n:
            continue
        if (Vector(c) - center_w).length > _CENTER_TOL:
            continue
        if Vector(n).normalized().dot(normal_w.normalized()) < _NORMAL_DOT_MIN:
            continue
        return obj, data
    return None, None


def count_sketches_for_host(host_name):
    """统计某宿主对象名下的草图数量（含快照已失效的孤儿草图）。"""
    col = bpy.data.collections.get(COLLECTION_NAME)
    if col is None:
        return 0
    n = 0
    for obj in col.objects:
        data = skd.load_from_object(obj)
        if data is not None and (data.host or {}).get("object") == host_name:
            n += 1
    return n


def create_container(context, sketch):
    """创建 Sketch_NNN 容器 EMPTY（本地轴对齐基准面坐标系）并写入 JSON。"""
    col = ensure_collection(context)
    obj = bpy.data.objects.new(next_sketch_name(col), None)
    obj.empty_display_type = 'PLAIN_AXES'
    obj.empty_display_size = 0.5

    d = sketch.datum
    mat = Matrix((d.x_axis, d.y_axis, d.normal)).transposed().to_4x4()
    mat.translation = d.origin
    obj.matrix_world = mat

    col.objects.link(obj)
    skd.save_to_object(obj, sketch)
    return obj


def update_container(container_name, sketch):
    obj = bpy.data.objects.get(container_name)
    if obj is not None:
        skd.save_to_object(obj, sketch)
    return obj is not None