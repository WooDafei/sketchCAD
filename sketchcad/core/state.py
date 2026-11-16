"""草图会话状态（单例）。

遵循 SketchCAD.md v2 §8 引用即取即用：不长期持有 BMesh/RNA 引用，
容器对象仅记录名字，提交时按名重新获取。
"""

# N-Panel 约束列表固定窗口高度与滚轮步长（face_sketch 与 n_panel 共用）
CONSTRAINT_VISIBLE = 8
CONSTRAINT_WHEEL_STEP = 2


class SketchSession:
    def __init__(self):
        self.reset()

    def reset(self):
        self.active = False
        self.datum = None           # core.datum.Datum
        self.sketch = None          # core.sketch_data.SketchData
        self.container_name = None  # 容器 EMPTY 对象名（仅记名字，不持引用）
        self.points_2d = []         # 已确认点 [(u, v), ...]（跨笔划累积）
        self.stroke_open = False    # 是否存在进行中的折线笔划
        self.mouse_2d = None        # 当前鼠标在基准面上的候选点 (u, v)
        self.snap = None            # 当前捕捉结果 (kind, (u, v)) 或 None
        self.dof_report = None      # solver.dof.DofReport 缓存（约束变化时刷新）
        self.hv_pixel_tol = 8.0     # 水平/垂直推断阈值（屏幕像素；N-Panel 可配置前先取默认值）
        self.mode = "draw"          # 工具模式：draw 画线 / select 选择与尺寸驱动
        self.draw_tool = "line"     # draw 模式子工具：line / circle / arc
        self.circle_center = None   # 圆绘制：已确认圆心 (u, v)（None=未开始）
        self.arc_p1 = None          # 弧绘制：起点（三点式第一点）
        self.arc_p2 = None          # 弧绘制：弧上中间点（三点式第二点）
        self.tool = None            # select 模式编辑工具：None / 'trim' / 'extend'
        self.selected_entity = None # select 模式选中的线段实体 id
        self.input_buffer = ""      # 尺寸数值输入缓冲（select 模式）
        self.solve_failed = False   # 上次求解失败标记（HUD 红色提示，成功/切换时清除）
        self.dragging = None        # 拖动求解中的端点 (entity_id, pos)；None = 未拖动
        self.drag_last_solve = 0.0  # 拖动求解节流时间戳（perf_counter 秒）
        self.drag_undo_pushed = False  # 拖动是否已 push 快照（实际移动才 push，纯点击仅选中）
        self.undo_stack = []        # §8 自管快照栈：几何修改前 push，undo/redo 切换
        self.redo_stack = []
        self.loops = []             # 闭合轮廓缓存（实体变化时随 DOF 一并刷新）
        self.extend_pending = None  # extend 两段式：已选待延伸的实体 id（None=未选）
        self.extend_preview = None  # 延伸预览 (pos, from_pt, to_pt) 或 None
        self.offset_pending = None  # offset 两段式：已选待偏移的实体 id（None=未选）
        self.offset_preview = None  # 偏移预览 dict：kind=profile/circle/arc 或 None
        self.constraint_scroll_start = 0  # N-Panel 约束列表滚动起始索引（滚轮驱动）
        self.constraint_follow = True     # 跟随最新约束（滚回底部自动恢复）
        self.draw_handler = None
        # 视图/视口状态快照（退出时恢复）
        self.view_rot = None
        self.view_loc = None
        self.view_dist = None
        self.is_persp = None
        self.shading_type = None
        self.show_xray = None
        self.clip_end = None

    @property
    def has_points(self):
        return bool(self.points_2d)


state = SketchSession()