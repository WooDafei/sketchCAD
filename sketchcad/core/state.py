"""草图会话状态（单例）。

遵循 SketchCAD.md v2 §8 引用即取即用：不长期持有 BMesh/RNA 引用，
容器对象仅记录名字，提交时按名重新获取。
"""


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