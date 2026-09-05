# MolManager 深度技术审计

> 审计日期：2026-09-05 · 仓库 `stone-Glitch/-` @ `a5f29d4`
> 方法：AST 静态分析 + SQLite `EXPLAIN QUERY PLAN` 实测 + 性能基准
> 配套文档：`UPGRADE_ASSESSMENT.md`（功能现状对照）

---

## 一、核心结论

| 维度 | 评级 | 说明 |
|------|------|------|
| 代码规范 | 🟢 优 | `ruff check` 全清、`ruff format` 121 文件一致、mypy 通过 |
| 查询写法 | 🟢 优 | 循环内 DB 调用 **0 处**（无 N+1 问题），全部参数化 |
| **测试覆盖** | 🔴 **差** | 核心业务模块大面积零覆盖 |
| **函数复杂度** | 🟠 中 | 5 个 400+ 行巨型函数 |
| **索引策略** | 🟠 中 | 有盲区，且流行建议中有 2 条经实测不成立 |

---

## 二、🔴 最大风险：测试覆盖缺口

**已有测试覆盖**：`utils/*`、`core/domain`、`core/storage_sqlite`、`api/`、`utils/cache`

**零覆盖模块（约 25 个）**——且全是核心业务逻辑：

| 模块 | 风险等级 | 说明 |
|------|---------|------|
| `core/controller.py` | 极高 | 主控制器，所有 UI 动作的入口 |
| `core/model/*`（6 个）| 极高 | 数据模型层，含 `_chem`/`_fileops`/`_backup` |
| `core/view.py` | 高 | 主视图（1326 行）|
| `chem/psi4/*`（8 个）| 高 | 量化计算全部模块（core/conformer/irc/nmr/pka/scans/thermo）|
| `chem/reaction_animation.py` | 高 | 反应动画（1271 行）|
| `core/drop_handler.py` | 中 | 拖拽导入 |

**为什么这是最大风险**：目前 125 个测试保护的是"工具函数"，而真正承载业务逻辑的 `controller`/`model`/`psi4` 完全没有回归保护——任何改动都可能静默破坏功能。

> 好消息：`chem/psi4/*` 需要 PSI4 环境，可通过 mock 测纯逻辑部分；`core/model/*` 多为纯函数，可测性良好。

---

## 三、🟠 巨型函数（维护性地雷）

| 行数 | 位置 | 建议 |
|------|------|------|
| **764** | `ui/dialogs/reaction_dialog.py::show_reaction_animation_dialog` | 拆为「参数面板构建」「动画控制」「导出逻辑」三部分 |
| **693** | `chem/psi4/core.py::run_psi4_task` | 拆为「参数校验」「进程启动」「结果解析」 |
| **605** | `ui/dialogs/advanced_tools_dialog.py::show_advanced_tools_dialog` | 按工具类型拆子函数 |
| 506 | `ui/ui_builder/_tabs.py::_build_paned_file_and_log` | 拆为文件面板 + 日志面板 |
| 440 | `chem/psi4/conformer.py::conformer_search_ensemble` | 拆构象生成与能量评估 |

**超长文件**：`_tabs.py`(1810) · `psi4/core.py`(1670) · `view.py`(1326) · `reaction_animation.py`(1271)

---

## 四、⚠️ 索引策略：实测推翻了两条流行建议

用 `EXPLAIN QUERY PLAN` 在 5000 行样本上实测（`molecule` 表结构见 `core/storage_sqlite.py:29`）：

### ❌ 建议「给 name 建索引」——**多余**

```
SEARCH molecule USING INDEX sqlite_autoindex_molecule_1 (name=?)
```

`name` 是 `TEXT PRIMARY KEY`，SQLite **已自动创建隐式索引**。再加一个纯属浪费写入性能。

### ✅ 建议「给 smiles/inchi 建索引」——**成立**

| 字段 | 现状 | 加索引后 |
|------|------|---------|
| `smiles` | `SCAN molecule`（全表扫描）| `SEARCH ... USING INDEX ix_smiles` |
| `inchi` | `SCAN molecule` | 同理 |

10000 行实测：`0.264ms → 0.003ms`（**约 88 倍**）。

### ❌ 建议「给 tags 建索引」——**无效，需改数据模型**

```
无索引: 3.627 ms
加索引: 3.683 ms   ← 完全没有改善
```

原因：`tags` 存的是 **JSON 数组文本**，查询用 `WHERE tags LIKE '%cat%'`，前置通配符使 B-tree 索引失效。

**真正的解法（三选一）**：

| 方案 | 改动 | 适用 |
|------|------|------|
| **关联表** `molecule_tags(name, tag)` | 中 | 最规范，支持多标签组合查询 |
| **JSON1 扩展** `json_each(tags)` | 小 | SQLite 3.38+ 内置，无需改存储 |
| **FTS5 虚拟表** | 大 | 需要全文检索时 |

---

## 五、🟢 表现良好的部分

- **无 N+1 查询**：AST 扫描全部循环体，数据库调用 0 处
- **无 SQL 注入**：全部 `?` 参数化
- **大数据集列表**：`ui/app_helpers.py` 已实现分批插入（每批 200 行 + `after_idle` 让出事件循环）
- **已有索引**：`idx_calc_molecule`、`idx_calc_type`（`calc_result` 表）

---

## 六、升级清单（按 ROI 排序）

### 🥇 第一梯队：立即做

| # | 项目 | 成本 | 收益 |
|---|------|------|------|
| 1 | **加 `smiles` / `inchi` 索引** | 2 行 SQL | 结构查询 **约 88 倍**加速，零风险 |
| 2 | **补 `core/model/*` 测试** | 中 | 数据层是纯函数，可测性最好，性价比最高 |
| 3 | **补 `core/controller` 关键路径测试** | 中 | 用 mock 隔离 tkinter，覆盖增删改查主流程 |

### 🥈 第二梯队：规划做

| # | 项目 | 说明 |
|---|------|------|
| 4 | 标签查询改造（JSON1 或关联表）| 配合「标签系统」功能一起做，避免二次返工 |
| 5 | 拆分 `run_psi4_task`(693行) | 计算核心，拆完可测性大幅提升 |
| 6 | 拆分 `show_reaction_animation_dialog`(764行) | UI 层最大痛点 |
| 7 | 3D 结构预览（matplotlib 方案）| 零新增依赖 |

### 🥉 第三梯队：视需求

- `chem/psi4/*` 测试（需 PSI4 或深度 mock）
- 剩余 614 个 ruff 建议（其中 235 个为 unsafe 改写）
- 多语言 i18n（全量 UI 文案改造）

---

## 七、一句话建议

**先花半天加 2 个索引（88 倍收益），再用一周补 `core/model` + `controller` 的测试**——前者立刻见效，后者是把这个项目从"能跑"变成"敢改"的关键。巨型函数重构可以等测试网铺好再动，否则没有安全网。
