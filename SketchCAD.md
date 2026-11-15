# 插件核心功能架构设计：SketchCAD（v2）

为了在 Blender 中提供专业 CAD 水准的参数化草图体验，同时保持 Blender 原生 3D 建模工作流的纯粹性，插件命名为 SketchCAD。核心设计理念：**"面驱动、独立管理、非破坏性"**。

> **版本基线（2026-11-15）**：Blender 5.3+（内置 Python 3.13）· Extension 格式 · 代码语法以 Python 3.13 为基线（不得使用 3.14-only 特性，求解器需在本地 3.14 venv 可测）。
>
> **v2 变更**：新增数据模型与存储方案对比、约束求解器选型对比、undo/redo 策略、范围边界界定；重排路线图（新增 Phase 0）并补充各阶段验收标准与性能预算。

---

## 0. 技术选型与包结构

### 0.1 插件格式：Extension（blender_manifest.toml）

| | Extension（`blender_manifest.toml`） | legacy addon（bl_info zip） |
|---|---|---|
| 定位 | Blender 4.2+ 官方推荐方向 | 过渡形态，未来弃用 |
| 多模块包 | 天然支持（符合本项目"单插件多模块"定位） | 支持但需自行组织 |
| Python 依赖 | 可声明 wheels；blender.org 平台**仅允许纯 Python wheel** | 需自行捆绑，无约束 |

**结论**：新项目直接采用 Extension 格式。该选择反向约束了依赖策略——**约束求解器必须零第三方依赖（纯 numpy 自研）**，因为 scipy 等 C 扩展依赖无法上架且 Blender 内置 Python 不含 scipy。

### 0.2 包结构（单插件多模块）

```
sketchcad/
  blender_manifest.toml
  __init__.py          # 注册入口
  core/                # sketch_data.py（数据模型+序列化）、datum.py（基准面坐标系）
  solver/              # 约束求解器：纯 Python + numpy，禁止 import bpy（可独立 pytest）
  drawing/             # 绘图工具（line / rect / circle / arc / polygon）
  snapping/            # 捕捉系统
  gpu/                 # draw_2d.py、hud.py（POST_VIEW draw handler）
  operators/           # enter_sketch.py、modal_draw.py 等
  ui/                  # n_panel.py 等
  manager.py           # Collection / 命名 / 草图生命周期
```

`solver/` 与 `core/` 的几何部分不依赖 bpy，可在 `envs/python3.14` 的 venv 中用 pytest 直接测试。

---

## 1. 面驱动的智能草图系统（Face-Driven Sketching）

插件的入口与核心交互逻辑。用户无需手动创建平面，而是直接在现有 3D 模型上进行操作。

- **一键进入草图模式**：编辑模式下面选择状态下，选中任意一个面，按快捷键（默认 `Ctrl+Shift+S`，注册为可配置 Keymap，需排查系统/其他插件冲突）进入 SketchCAD 草图编辑环境。
- **动态基准面计算**：获取选中面的中心点（Center）与法线（Normal）作为草图局部坐标系 Z 轴。**平面内 X 轴采用确定性规则**：`x_axis = normalize(world_z × normal)`；当法线与世界 Z 接近平行时（叉积长度低于阈值）回退为世界 X 轴。**整个坐标系（origin / normal / x_axis）必须持久化写入草图数据**，保证退出重进后坐标系不漂移、已有约束不错位。
- **仅支持平面 face**：N-gon 与弯曲面的 center + 平均法线不构成有效基准面，需检测并给出警告（专业 CAD 草图同样仅允许平面）。
- **相机自动对齐**：进入草图时视图旋转至基准面正视角（`view3d.view_rotation`），退出时恢复原视角，复刻专业 CAD 的沉浸式对齐体验。
- **宿主面关联**：不依赖 face index（网格编辑后索引重排失效）。关联信息存储为**宿主对象名 + 面中心 + 法线快照**，重进时按坐标匹配；匹配失败（面已被改动/删除）时提示用户确认或取消关联。
- **退出草图**：`ESC` 仅在无进行中的绘制操作时退出（避免与 Blender 全局"取消当前操作"语义冲突）；进行中操作由 Modal 内部先取消；同时提供 HUD 显式"退出草图"按钮。

## 2. 独立草图管理器（Independent Sketch Manager）

借鉴专业 CAD 软件的独立管理机制，解决 Blender 原生曲线/蜡笔工具难以管理的问题。

- **独立对象化**：每个草图对应一个容器对象，几何真相存储在该对象名为 `SketchCAD_Data` 的自定义属性中，内容为**带 `version` 字段的 JSON**（schema 见 §3）。
- **层级与命名**：所有草图自动归入 `SketchCAD_Collection` 集合；命名规则为 `Sketch_NNN`（自动递增序号，用户可改语义名）。不使用 face id 命名（索引不稳定），宿主面信息存于 JSON 的 `host` 字段。
- **状态控制**：管理器中一键隐藏/显示特定草图；支持"断开关联（Break Link）"——**语义定义**：清除宿主对象关联，但 JSON 中的约束与尺寸标注**全部保留、求解器继续生效**，草图变为纯 2D 参数化对象。
- **删除草图**：`sketch.delete_sketch` operator（F3 搜索 "Delete SketchCAD Sketch"，作用于选中的草图容器）删除容器与 JSON；**明确不碰 mesh 中已提交的几何**——提交物在退出时已归属建模资产（所有权边界见 §9）。宿主面被删除后旧草图成为孤儿（快照永不匹配），可通过该命令清理。
- **最小管理前移**：Collection 创建、命名、重进对齐等最小管理能力随 Phase 0 落地，而非等到后期。

## 3. 草图数据模型（Sketch Data Model）

约束求解器工作在参数化 2D 几何上，而 Blender Curve 对象只能存储样条——元素类型与约束关系的存储方案是本插件最关键的架构决策：

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| A. 单 Curve 多 spline | 对象少，Outliner 干净 | 元素↔spline 索引映射脆弱，增删元素后索引漂移；圆/弧表示受限；约束回写复杂 | 否决 |
| B. 每元素独立 Curve 对象 | 定位直观，可用原生编辑器 | 对象数量爆炸；跨对象约束管理困难 | 否决 |
| **C. 容器对象 + JSON 自定义属性存全部几何 + GPU 自绘** | schema 可版本化；求解器直接作用于 JSON 模型；不受 Curve 表达力限制；天然兼容 undo 重建问题 | 需自绘 GPU（HUD 与标注渲染本就需要）；Outliner 看不到内部元素 | **采用** |

方案 C 下，Curve 对象只是"退出时可选导出物"，几何真相永远在 JSON schema 中。

**Schema v1 骨架**（正式定义随 Phase 0 定稿）：

```json
{
  "version": 1,
  "datum": {
    "origin": [0.0, 0.0, 0.0],
    "normal": [0.0, 0.0, 1.0],
    "x_axis": [1.0, 0.0, 0.0]
  },
  "host": {
    "object": "Cube",
    "face_snapshot": {"center": [0.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0]}
  },
  "entities": [
    {"id": 1, "type": "line",   "p1": [0, 0], "p2": [10, 0], "construction": false},
    {"id": 2, "type": "circle", "center": [5, 5], "radius": 2.5, "construction": false}
  ],
  "constraints": [
    {"id": 10, "type": "coincident", "targets": [1, 2], "endpoints": [1, 0], "enabled": true},
    {"id": 11, "type": "horizontal", "targets": [1], "enabled": true},
    {"id": 12, "type": "distance",   "targets": [1], "value": 10.0, "enabled": true}
  ]
}
```

要点：`construction` 字段区分**构造线**（参与约束求解、不参与轮廓闭合，常用于对称/对齐参照）；约束 `enabled` 支持"禁用"（disable，可恢复）与"删除"（delete，彻底移除）两种语义。Phase 2 schema 细化：`coincident` 通过可选 `endpoints` 字段（0=p1, 1=p2）指定端点，默认 `[1, 0]`（前段终点接后段起点的折线连接惯例）；实体与约束**共用同一 id 空间**（递增时取两者最大值 +1）。

## 4. 专业级 2D 绘图与智能约束引擎（2D Drawing & Constraints）

在 3D 视图中提供纯粹的 2D 绘图体验，底层基于 Python 几何求解器。

- **基础图形工具**：直线、矩形、圆、圆弧、多边形。鼠标射线与基准面求交得到 2D 平面坐标，所有绘制自动投影到当前基准面。
- **约束求解器选型**：

| 路线 | 优点 | 风险/代价 | 结论 |
|---|---|---|---|
| **numpy 自研 LM（Levenberg-Marquardt）** | 零依赖、可脱离 Blender 跑 pytest、体积小 | 工作量最大；数值细节需踩坑 | **采用**（草图元素通常 <100，规模可控） |
| py_slvs（solvespace 绑定） | 算法成熟、DOF 分析现成 | C 扩展 wheel 需验证 cp313 版本；打包受 Extension 平台限制 | 备选（若自研受挫再评估） |
| scipy.optimize | 现成优化器 | Blender 内置 Python 不含 scipy；Extension 平台禁二进制 wheel | 排除 |

  不采用裸牛顿法（无阻尼极易发散）。求解器自研的 DOF/冗余检测参考 solvespace 源码思路。
- **自由度（DOF）分析**（一等需求）：实时显示草图"欠约束 / 满约束 / 过约束"状态与 DOF 计数，过约束冲突给出提示——这是专业 CAD 草图体验的关键组成。
- **冗余约束检测**：求解前检测并告警，避免数值求解直接崩溃。
- **自动识别约束**：绘制/拖动中根据轨迹阈值推断水平、垂直、平行、相切、重合等约束；**推断错误的约束可一键删除**，推断阈值在属性面板可配置。
- **手动约束与尺寸驱动**：选中元素后通过快捷键或侧边栏添加尺寸（Distance）、角度（Angle）、相等（Equal）约束；选中后直接输入数值驱动求解器重算，保持设计意图。
- **尺寸标注渲染**：标注线、数值文本基于 GPU draw handler 绘制（与几何绘制同一套 gpu 模块管线）。

## 5. 强大的捕捉与辅助系统（Snapping & Guides）

- **关键点捕捉**：端点、中点、圆心、交点。捕捉查询在当前草图几何上求最近特征（规模内 Python 实现 + 简单空间剪枝即可）。
- **网格与辅助线**：基准面上自适应 2D 网格，支持网格交点捕捉；支持无限长辅助线（Guide Lines，仅作对齐参考，不参与约束）。
- **3D 辅助点投影**：基于 `mathutils.bvhtree.BVHTree`（可从宿主 BMesh 构建）将基准面背后的 3D 顶点、边线投影到草图平面，显示为虚线/辅助点，用于描图与对齐。
- **宿主 ghost 显示**：进入草图时宿主模型半透明化，突出当前草图平面，退出恢复。

## 6. 辅助编辑工具集（Auxiliary Editing Tools）

- **修剪与延伸（Trim/Extend）**：裁剪多余线段、延伸至交点（纯几何运算，可独立单测）。
- **偏移边线（Offset Edge）**：封闭轮廓或开放线段向内/向外偏移指定距离。
- **镜像与阵列（Mirror/Pattern）**：基于草图内辅助线做线性/圆形阵列与对称镜像。

## 7. 专属 UI 与交互体验（UI & Interaction）

- **沉浸式 HUD**：进入草图模式时隐藏不必要的默认 UI（退出时恢复），右下角 HUD 提示当前工具、约束状态与 DOF 计数；含草图内 undo/redo 按钮与显式退出按钮。**最小 HUD 随 Phase 1 落地**（画线阶段即有价值），完整版随约束系统完善。
- **侧边栏面板（N-Panel）**：SketchCAD 标签页包含：
  - 工具栏：切换绘图与编辑工具；
  - 约束列表：显示全部约束，区分**禁用（勾选框）与删除（右键/按钮）**；
  - 属性面板：网格间距、捕捉容差、自动约束推断阈值。

## 8. undo/redo 与 Modal 交互策略

Blender undo 会重建对象，BMesh/RNA 引用跨阶段保存必然失效（本机实测坑），草图模式是常驻 Modal + 实时求解，该风险被放大。策略：

1. **草图模式内自管 undo 栈**：每步操作 push 一份几何 JSON 快照，undo/redo 在快照间切换（HUD 按钮与快捷键）；
2. **退出时合并**：整个草图会话合并为一次 Blender undo 操作；
3. **引用即取即用**：任何 BMesh/RNA/对象引用不做长期持有，按对象名 + 坐标快照即时匹配获取。

## 9. 范围边界（MVP 与远期）

- **"非破坏性"的界定**：指草图是独立持久的参数化对象，本身不被建模操作破坏；**v1 不做草图→实体自动联动**——退出后由用户使用原生挤出、倒角等工具继续建模，改动草图不会自动更新实体。
- **远期 Phase 4（预留，不进 v1）**：草图-实体联动，候选路线为修改器/几何节点驱动，届时再对比选型。
- 原则：先收敛 MVP 边界，避免"完美复刻主流 CAD 草图功能"导致的范围蔓延。

## 10. 测试与性能策略

- **单测**：`solver/` 与 `core/` 几何部分不依赖 bpy，在 `envs/python3.14` venv 用 pytest 覆盖（约束求解、trim/offset/交点等纯几何）；
- **冒烟**：Blender 端手工冒烟脚本（参考 wanderer-cad 的 `t_*.py` 模式）；
- **性能预算**：
  - 拖动尺寸实时求解：20 元素 / 30 约束时 **<50ms/帧**；
  - 单次捕捉查询 **<5ms**；
  - 规模目标：100 元素 / 80 约束的草图仍可交互（允许求解降频）。

## 11. 开发实施路线图

### Phase 0：数据模型与包骨架

- Extension 包骨架建立、Blender 5.3 安装加载成功；
- Schema v1 定稿（§3），基准面坐标系持久化与重进对齐；
- 最小管理器（Collection 创建、`Sketch_NNN` 命名）；
- **验收**：空插件加载无报错；JSON 读写往返一致；坐标系跨会话恢复。

### Phase 1：基准面与绘图引擎

- 选面进入 → 鼠标 raycast 基准面求交 → GPU 画直线、矩形；
- 端点、中点捕捉；相机自动对齐；最小 HUD；自管 undo 栈雏形；
- **验收**：编辑模式选面进入→画直线/矩形→退出为独立对象→**重进后坐标系不错位**；undo/redo 在草图内可用。

### Phase 2：约束系统与尺寸驱动

- numpy 自研 LM 求解器 + DOF 分析 + 冗余检测；
- 自动约束识别（含阈值配置、一键删除）；尺寸标注 UI 与数值驱动；标注 GPU 渲染；完整 HUD；
- **验收**：20 元素/30 约束草图拖动尺寸 <50ms/帧；过约束场景给出明确提示且求解器不崩溃。

### Phase 3：辅助工具与完善管理

- Trim/Extend、Offset、Mirror/Pattern；
- 3D 辅助点投影（BVHTree）、宿主 ghost 显示；
- 完整管理器（隐藏/显示、断开关联）、闭合轮廓检测；
- **验收**：全部编辑工具在混合直/圆/弧草图上正确工作；断开关联后约束仍生效。

### Phase 4（远期预留）：草图-实体联动

- 候选路线（修改器 / 几何节点）届时对比选型，不进 v1。

## 12. 技术风险与对策

| 风险 | 对策 |
|---|---|
| Python 求解性能不达标 | 纯 numpy 向量化；规模预算与降频求解兜底；必要时再评估 py_slvs（§4 备选路线） |
| Modal + Blender undo 交互不可靠 | §8 自管 undo 栈 + 引用即取即用 |
| gpu 模块 API 随版本变动 | 锁定 Blender 5.x 基线，manifest 声明最低版本 |
| 快捷键与系统/其他插件冲突 | 全部 Keymap 可配置；发布前提供冲突排查清单 |
| 依赖打包限制 | 求解器零第三方依赖（§0.1 结论） |
| undo 重建对象导致引用失效 | 一切引用按坐标/名称快照匹配，禁止长期持有 RNA 引用 |
