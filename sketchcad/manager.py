"""草图管理器（SketchCAD.md v2 §2 / Phase 0+3）。

负责 SketchCAD_Collection 集合、Sketch_NNN 命名、容器 EMPTY 对象的
创建与"宿主对象 + 面快照"匹配（重进对齐）。face index 在网格编辑后
会重排失效，因此关联一律采用坐标快照匹配。

Phase 3 管理器完善：隐藏/显示（JSON visible 字段 + 容器 hide_viewport
同步）、断开关联（Break Link——清除宿主关联，实体与约束全部保留，
求解器继续生效，草图变为纯 2D 参数化对象）。
"""

import bpy

from mathutils import Matrix, Vector

from .core import sketch_data as skd

COLLECTION_NAME = "SketchCAD_Collection"
NAME_FMT = "Sketch_{:03d}"
NAME_PREFIX = "Sketch_"

# 容器对象上的"上一轮提交快照"属性（§9 提交物所有权标记）：
# 内容为退出提交时的段端点局部坐标序列，重进修改尺寸再退出时
# 按它定位并替换宿主 mesh 中的旧轮廓边（替换式提交）
COMMITTED_PROP = "SketchCAD_Committed"

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


# ---- Phase 3：隐藏/显示与断开关联（§2 状态控制）----

def load_sketch(obj_name):
    """按名取容器对象与其 SketchData；不存在返回 (None, None)。"""
    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        return None, None
    return obj, skd.load_from_object(obj)


def set_sketch_visible(obj_name, visible):
    """设置草图可见性：JSON visible 字段 + 容器 hide_viewport 同步。

    草图几何真相在 JSON、提交物在宿主 mesh 中（所有权边界 §9），
    本阶段的可见性作用于容器 EMPTY 的视口显示；几何级隐藏待
    GPU 常驻自绘后由绘制侧尊重该标志实现。
    """
    obj, data = load_sketch(obj_name)
    if data is None:
        return False
    data.visible = bool(visible)
    skd.save_to_object(obj, data)
    obj.hide_viewport = not data.visible
    return True


def break_link(obj_name):
    """断开宿主关联：host 清空，实体与约束全部保留（§2 语义）。

    返回 SketchData；容器不存在或数据缺失返回 None。
    断开后快照不再匹配（孤儿化），但求解器仍可作用于其约束。
    """
    obj, data = load_sketch(obj_name)
    if data is None:
        return None
    data.host = {"object": None, "face_snapshot": None}
    skd.save_to_object(obj, data)
    return data

def prune_collection():
    """集合为空时移除（删除最后一条草图后不留空集合）。"""
    col = bpy.data.collections.get(COLLECTION_NAME)
    if col is not None and not col.objects:
        bpy.data.collections.remove(col)

# ---- Phase 3：提交快照（替换式提交的旧轮廓定位依据）----

def load_committed_segments(obj):
    """读容器上的上一轮提交快照（局部坐标段序列），缺失/损坏返回 None。

    结构校验为 [[ [x,y,z], [x,y,z] ], ...]；形状不符按损坏处理。
    """
    import json

    raw = obj.get(COMMITTED_PROP)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        segments = data.get("segments")
        if not isinstance(segments, list):
            return None
        for seg in segments:
            if (not isinstance(seg, list) or len(seg) != 2
                    or any(not isinstance(pt, list) or len(pt) < 2
                           for pt in seg)):
                return None
        return segments
    except (ValueError, TypeError):
        return None


def save_committed_segments(obj, segments):
    """写上一轮提交快照（局部坐标段序列）到容器。"""
    import json

    obj[COMMITTED_PROP] = json.dumps({"segments": segments})