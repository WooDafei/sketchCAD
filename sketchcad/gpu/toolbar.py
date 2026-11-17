"""草图竖排工具栏（Phase 4：参照 Blender 3D 视口左侧工具栏样式）。

屏幕空间（region 坐标，Y 向上）POST_PIXEL 绘制 + 命中测试；按钮
按会话模式分组：顶部模式切换常驻，中间为当前模式工具组（draw
绘图 5 工具 / select 编辑 8 工具），底部撤销与退出常驻。点击按钮
由 operator 层分发到与快捷键相同的处理函数（等效快捷键语义，见
operators/face_sketch.py:_toolbar_action）。

图标 MVP：按钮中央 blf 绘制键位字母（无图标资源依赖，信息即键位），
active 工具橙色高亮、hover 淡色、HUD 左上角显示工具名提示。

布局 / 命中 / 提示查询为纯函数（不依赖 bpy，venv pytest 可直接
导入测试）；绘制依赖 bpy/gpu/blf，模块级 try-import 降级——
Blender 进程内正常加载，独立 pytest 环境 _BLENDER=False 且绘制
入口直接返回（纯函数测试不受影响）。
"""

try:
    import blf
    import bpy
    import gpu
    from gpu_extras.batch import batch_for_shader

    _BLENDER = True
except ImportError:  # 独立 pytest 环境（无 bpy）
    _BLENDER = False

from ..core.state import state

_BTN_SIZE = 30   # 按钮方形边长（像素）
_BTN_PAD = 4     # 按钮间距
_BAR_X = 8       # 工具栏左缘（region 坐标）
_BAR_TOP = 8     # 顶部留白

# 按钮定义：(action, 图标字母, 提示文字)
_DRAW_BTNS = [
    ("draw:line", "L", "直线（L）：点击加点，双击/Enter 结束笔划"),
    ("draw:circle", "C", "圆（C）：两击——圆心→半径"),
    ("draw:arc", "A", "三点弧（A）：起点→弧上点→终点"),
    ("draw:ellipse", "E", "椭圆（E）：三击——圆心→长轴端点→短轴"),
    ("draw:polygon", "N", "多边形（N）：数字定边数，两击——中心→顶点"),
]
_SELECT_BTNS = [
    ("tool:trim", "T", "修剪（T）：点击要移除的部分"),
    ("tool:extend", "E", "延伸（E）：选线→预览→点击定终点"),
    ("tool:offset", "O", "偏移（O）：选实体→预览→点击确认"),
    ("tool:mirror", "M", "镜像（M）：多选→Enter→选轴→Enter"),
    ("tool:pattern", "P", "阵列（P）：多选→Enter→选参照→步距/份数"),
    ("tool:move", "G", "移动（G）：点基准点→预览→点击确认"),
    ("tool:rotate", "R", "旋转（R）：点中心→预览/输角度→确认"),
    ("tool:scale", "X", "缩放（X）：点中心→预览/输比例→确认"),
]
_FIXED_BTNS = [
    ("undo", "U", "撤销（右键 / Ctrl+Z）"),
    ("finish", "Q", "退出草图并提交（ESC）"),
]
_MODE_BTN = ("mode", "S", "切换 画线/选择 模式（Tab / S）")


def layout(mode, region_height):
    """按钮矩形清单 [(action, letter, tip, (x0, y0, x1, y1)), ...]。

    顶部常驻模式切换，中间为当前模式工具组，底部常驻撤销/退出；
    y 自 region 顶部向下排列（region 坐标 Y 向上）。
    """
    btns = [_MODE_BTN]
    btns += _DRAW_BTNS if mode == "draw" else _SELECT_BTNS
    btns += _FIXED_BTNS
    rects = []
    y = region_height - _BAR_TOP - _BTN_SIZE
    for action, letter, tip in btns:
        rects.append((action, letter, tip,
                      (_BAR_X, y, _BAR_X + _BTN_SIZE, y + _BTN_SIZE)))
        y -= _BTN_SIZE + _BTN_PAD
    return rects


def hit_test(mx, my, rects):
    """命中测试：返回命中的按钮 action，未命中 None。"""
    for action, _letter, _tip, (x0, y0, x1, y1) in rects:
        if x0 <= mx <= x1 and y0 <= my <= y1:
            return action
    return None


def tooltip_for(mode, action, region_height):
    """返回 action 的提示文字（HUD hover 显示）；未知 None。"""
    for a, _letter, tip, _rect in layout(mode, region_height):
        if a == action:
            return tip
    return None


def is_active(action):
    """按钮是否为当前激活工具（橙色高亮判定）。"""
    if action.startswith("draw:"):
        return (state.mode == "draw"
                and state.draw_tool == action[len("draw:"):])
    if action.startswith("tool:"):
        return (state.mode == "select"
                and state.tool == action[len("tool:"):])
    return False


_COLOR_BG = (0.10, 0.10, 0.12, 0.55)
_COLOR_BG_HOVER = (1.0, 1.0, 1.0, 0.18)
_COLOR_BG_ACTIVE = (1.0, 0.5, 0.1, 0.30)
_COLOR_EDGE = (0.8, 0.8, 0.8, 0.45)
_COLOR_EDGE_ACTIVE = (1.0, 0.55, 0.15, 0.95)
_COLOR_LETTER = (1.0, 1.0, 1.0, 0.95)


def draw_toolbar():
    """POST_PIXEL 绘制回调（会话激活时由 operator 注册）。

    非 Blender 环境 / 会话未激活 / 无 region 时直接返回。
    """
    if not _BLENDER or not state.active:
        return
    region = bpy.context.region
    if region is None:
        return
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')

    hover = state.toolbar_hover
    for action, letter, _tip, (x0, y0, x1, y1) in layout(
            state.mode, region.height):
        active = is_active(action)
        if active:
            bg = _COLOR_BG_ACTIVE
            edge = _COLOR_EDGE_ACTIVE
        elif action == hover:
            bg = _COLOR_BG_HOVER
            edge = _COLOR_EDGE
        else:
            bg = _COLOR_BG
            edge = _COLOR_EDGE
        batch = batch_for_shader(shader, 'TRIS', {"pos": [
            (x0, y0), (x1, y0), (x1, y1),
            (x0, y0), (x1, y1), (x0, y1)]})
        shader.bind()
        shader.uniform_float("color", bg)
        batch.draw(shader)
        border = [(x0, y0), (x1, y0), (x1, y1), (x0, y1),
                  (x0, y0), (x1, y0), (x1, y0), (x1, y1),
                  (x1, y1), (x0, y1), (x0, y1), (x0, y0)]
        batch = batch_for_shader(shader, 'LINES', {"pos": border})
        shader.bind()
        shader.uniform_float("color", edge)
        batch.draw(shader)
        blf.size(0, 14)
        blf.color(0, *_COLOR_LETTER)
        w, h = blf.dimensions(0, letter)
        blf.position(0, (x0 + x1) * 0.5 - w * 0.5,
                     (y0 + y1) * 0.5 - h * 0.5)
        blf.draw(0, letter)
    gpu.state.blend_set('NONE')