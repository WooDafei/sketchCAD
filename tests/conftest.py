"""pytest 共享基建（沿用 2026-11-15 第四轮验证过的方案）。

- mathutils stub：core/datum.py 依赖 mathutils.Vector，venv 中无该
  模块，注入 FakeVector（copy/normalized/cross/dot/加减/标量乘）；
- 假包注册：sketchcad 顶层包以 types.ModuleType 注册且 __path__ 指向
  真实目录——真实的 sketchcad/__init__.py（含 import bpy）不会被执行，
  子模块按 __path__ 正常导入。

solver/ 子包本身不依赖 bpy/mathutils；仅 sketch_data 测试走 stub 链。
"""

import sys
import types
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]


class FakeVector:
    def __init__(self, seq):
        self.x, self.y, self.z = (float(v) for v in seq)

    def __iter__(self):
        return iter((self.x, self.y, self.z))

    def __len__(self):
        return 3

    def copy(self):
        return FakeVector((self.x, self.y, self.z))

    def normalized(self):
        length = (self.x ** 2 + self.y ** 2 + self.z ** 2) ** 0.5
        if length < 1e-12:
            return FakeVector((0.0, 0.0, 0.0))
        return FakeVector(
            (self.x / length, self.y / length, self.z / length))

    def cross(self, other):
        return FakeVector((
            self.y * other.z - self.z * other.y,
            self.z * other.x - self.x * other.z,
            self.x * other.y - self.y * other.x,
        ))

    def dot(self, other):
        return self.x * other.x + self.y * other.y + self.z * other.z

    def __add__(self, other):
        return FakeVector((
            self.x + other.x, self.y + other.y, self.z + other.z))

    def __sub__(self, other):
        return FakeVector((
            self.x - other.x, self.y - other.y, self.z - other.z))

    def __mul__(self, k):
        return FakeVector((self.x * k, self.y * k, self.z * k))


_mu = types.ModuleType("mathutils")
_mu.Vector = FakeVector
sys.modules.setdefault("mathutils", _mu)

_pkg = types.ModuleType("sketchcad")
_pkg.__path__ = [str(PROJECT_DIR / "sketchcad")]
sys.modules.setdefault("sketchcad", _pkg)