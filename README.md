# SketchCAD

面向 Blender 5.3+ 的**面驱动参数化草图插件**（对标专业 CAD 草图体验，保持 Blender 原生 3D 建模工作流），Extension 格式，单插件多模块包结构。

核心设计理念：**面驱动、独立管理、非破坏性**——几何真相存储在容器对象的自定义属性 JSON 中，mesh 提交物只是退出时的产物。

## 当前状态

Phase 0+1 已完成并在 Blender 5.3.0 正式版实测：

- 选面进入草图：基准面坐标系确定性规则 + 持久化，重进按"宿主对象 + 面快照"匹配续画，坐标系不漂移
- GPU 实时画线：多笔划折线（双击 / Enter 结束当前笔划），SOLID + X-ray 半透明宿主，面上实时可见
- 端点 / 中点捕捉：12px 像素容差（与缩放无关），端点优先于中点，HUD 显示捕捉类型
- 草图管理：`SketchCAD_Collection` 集合、`Sketch_NNN` 容器、孤儿草图提示与删除 operator（只删容器不碰 mesh 提交物）

下一阶段 Phase 2：numpy 自研 LM 约束求解器 + DOF 分析（见 `SketchCAD.md` §4/§11）。

## 分支结构

| 分支 | 内容 |
|---|---|
| `main` | 最新开发线（当前 = v2） |
| `v1-phase1` | v1 定版：Phase 0+1 首个可测版本（画线 / 笔划 / 捕捉 / 重进对齐） |
| `v2-s1s2` | v2 定版：孤儿草图提示 + 删除草图 operator（S1+S2） |

## 安装与测试（开发模式）

1. Blender 文本编辑器 → **Text → Open** 从磁盘打开 `t_load_sketchcad.py` → **Run Script**（控制台输出 `SketchCAD dev loaded from: ...`）
2. 编辑模式选一个面 → `Ctrl+Shift+S` 进入草图
3. 左键加点 · 双击 / Enter 结束折线 · 右键 / Ctrl+Z 撤销 · ESC 提交退出
4. 修改代码后重新运行 loader 即可热重载

打包发布：zip `sketchcad/` 目录 → Preferences → Add-ons → Install from Disk（Extension 格式，见 `sketchcad/blender_manifest.toml`）。

## 文档

- `SketchCAD.md`：核心功能架构设计（数据模型 / 约束求解器选型 / undo 策略 / 路线图与验收标准）

## 许可

GPL-3.0-or-later（与 Blender 插件生态一致，见 `sketchcad/blender_manifest.toml`）。