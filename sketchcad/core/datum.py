"""基准面坐标系（Datum Plane Coordinate System）。

规则与 SketchCAD.md v2 §1 一致：
- Z 轴 = 基准面法线；
- X 轴 = normalize(world_z × normal)，法线与世界 Z 近平行时回退为 world_x；
- Y 轴 = normal × x_axis（右手系）。

坐标系随草图 JSON 持久化（只存 origin / normal / x_axis），重进时直接
恢复、不再重算——这是修复"再次进入草图坐标系变化"的核心。
"""

from mathutils import Vector

WORLD_Z = Vector((0.0, 0.0, 1.0))
WORLD_X = Vector((1.0, 0.0, 0.0))

_PARALLEL_THRESHOLD = 0.9999
_DEGENERATE_EPS = 1e-6


def build_x_axis(normal):
    """由法线按确定性规则求平面内 X 轴（单位向量）。"""
    n = normal.normalized()
    if abs(n.z) > _PARALLEL_THRESHOLD:
        return WORLD_X.copy()
    x = WORLD_Z.cross(n)
    if x.length < _DEGENERATE_EPS:
        return WORLD_X.copy()
    return x.normalized()


class Datum:
    """持久化基准面：origin + normal + x_axis（世界坐标），y_axis 由右手系现算。"""

    __slots__ = ("origin", "normal", "x_axis", "y_axis")

    def __init__(self, origin, normal, x_axis=None):
        self.origin = Vector(origin).copy()
        self.normal = Vector(normal).normalized()
        if x_axis is not None:
            self.x_axis = Vector(x_axis).normalized()
        else:
            self.x_axis = build_x_axis(self.normal)
        self.y_axis = self.normal.cross(self.x_axis).normalized()

    def to_world(self, u, v):
        """2D 平面坐标 -> 世界 3D 坐标。"""
        return self.origin + self.x_axis * u + self.y_axis * v

    def to_2d(self, co_world):
        """世界 3D 坐标 -> 2D 平面坐标 (u, v)。"""
        d = Vector(co_world) - self.origin
        return (d.dot(self.x_axis), d.dot(self.y_axis))