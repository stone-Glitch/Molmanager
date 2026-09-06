# 更新日志

本文件记录 MolManager 每个版本值得注意的变更。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.2.0] - 2026-09-06

一轮「先实测加固、再重构、后视觉精修」的工程质量版本：功能行为零变更，测试函数 157 → 279，
UI 代码结构重排 + Aurora Frost 视觉令牌化。

### 修复（Fixed）

- **手性分析跨版本兼容（OpenBabel 3.1 绑定缺失 API）**：手性链路重写为 `OBStereoFacade`
  逐原子查询（`GetAllStereoData` / `InvertStereo` 在 3.1 绑定中不存在）；对映体反转采用
  winding 翻转 + x 坐标镜像双保险；新增 `_symbol_of()` 规范化元素符号（`OBAtom` 无
  `GetSymbol`，`GetType()` 会返回 `C3`/`Cl` 等类型串）；CIP 标签在 3.1 下诚实返回 `?`，
  不再谎报 R/S。
- **`obabel` CLI 可执行缓存初始化缺失**：`_OBABEL_CLI_EXE` 模块级初始化缺失导致 NameError。
- **化学查询裸比较语法**：新增无冒号裸比较式查询（`MW>100` / `logP<3` / `formula=C6H6`），
  修复此类条件被误当自由文本、一条都查不到的问题。

### 新增（Added）

- **GUI 冒烟脚本 `scripts/smoke_gui.py`**：Xvfb 无头验证主窗口装配、6 页切换、7 个对话框
  调起，支持 `--screenshot` / `--theme` 参数，为 GUI 回归提供可自动化的冒烟入口。
- **约 120 项补充单测（11 个全新测试文件）**：P0 纯逻辑（规则引擎 / 元数据索引 / MO 能级图 /
  结构评分）、P1 领域模块（备份快照 / 项目打包 / 预设管理 / 示例分子库）、P2 基础设施
  （统一网络层 / 后台任务管理器 / 模型 Mixin）、手性分析与对映体反转（含输出护栏回归）。
  参数化展开后全量回归 **302 例（301 通过 / 1 环境跳过）**。

### 改进（Improved）——重构与视觉精修（行为不变）

- **设计令牌层 `utils/theme_tokens.py`**：几何/间距/控件尺寸/描边常量先行抽离（颜色无关），
  为后续视觉统一打底。
- **`ui/ui_builder/_tabs.py`（1810 行单文件）拆分为 `ui/pages/` 八页面模块**
  （仪表盘 / 文件管理 / 映射 / 计算动画 / 高级工具 / 面板-日志 / 计算队列 / 操作提示），
  原位置保留纯转发 shim；`ui.ui_builder.__init__` 改 PEP 562 懒加载导出，根治
  `ui.pages ↔ ui.ui_builder` 双向包初始化循环导入。
- **`ui/dialogs/reaction_dialog.py` 770 行主函数重构**：拆为 7 个 section 构建函数 +
  `SimpleNamespace` State 容器，30 参数的动画启动函数收敛为 `(app, dialog, st, controller)`；
  预设收集/回填改声明式字段清单 `_PRESET_FIELDS`。
- **`ui/dialogs/base.py` 新增 `ThemedDialog` 基类**：统一标题/初始尺寸/模态/ESC 关闭约定，
  历史记录、公式结果、目录同步三个对话框迁移为子类（`show_history_dialog` 等路由层零改动）。
- **主题归一**：`AuroraTheme` 28 个颜色常量全部改为从 `ui/ui_theme.py` 调色板派生，
  消除「同一颜色两处定义」的双源漂移隐患；`_theme.py` / `_menu.py` / `_statusbar.py`
  裸 hex 与 `COLORS.get(key, "#hex")` 兜底清零。
- **Aurora Frost 视觉令牌化**：侧边栏导航（行高/指示条/选中描边）、工具栏（控件高度自适应
  内边距）、状态栏、文件树、批量条、日志台、页面标题全部接入设计令牌；调色板新增
  `purple`；新增 `shade()` / `glow()` 明暗对偶；日志控制台改为恒定深色（`LOG_CONSOLE`，
  不随主题切换），tag 颜色经 `LOG_TAG_KEYS` 实时取调色板语义键。

### 已知问题（Known issues）

- `reaction_dialog` 的「运行扫描」按钮（`run_scan_btn`）为遗留死按钮（未绑定 command），
  本次重构原样保留，待确认产品意图后处置。

## [1.1.0] - 2026-09-06

两个主题：融合 **Quantum Reaction Visualizer** 为新的量子反应能计算功能；一轮终端用户友好性修复。

### 新增（Added）

- **⚗️ 量子反应能计算（ΔE/ΔE₀/ΔH°/ΔG°）**：融合独立的 Quantum Reaction Visualizer 项目。
  - 入口：工具菜单「量子反应能计算」或 <kbd>Ctrl+K</kbd> 命令面板；缺少 PSI4 时对话框
    顶部显示安装指引而非报错。
  - 8 个预设反应（水生成、氨合成、甲烷燃烧、氯化氢分解、乙烯加氢、水煤气变换、甲醇脱水、
    臭氧分解）+ 自定义 SMILES；`O=O:3` 语法指定自旋多重度，O₂ 预设默认标注三线态。
  - 计算内核（PSI4 包装）原样保留：c1 对称、开壳层自动 UHF/UKS、SCF 分级重试、
    优化/频率磁盘缓存、单原子解析热化学（Sackur–Tetrode）。
  - 新增框架无关编排层 `chem/quantum_reaction/runner.py`，支持任务内协作式取消，
    对接既有 TaskManager 线程模型。
  - 结果卡展示 ΔE 大字（放热绿/吸热红）+ ΔE₀/ΔH°/ΔG° + 自发性判定；内嵌能量曲线 PNG；
    落盘 `quantum_runs/<时间戳>/`（结果 JSON、优化 XYZ、IQmol 兼容多帧轨迹、可选 MP4）。
  - 新增 23 项单测 `tests/test_quantum_reaction.py`（stub PSI4 端到端覆盖编排/取消/配平预检，
    纯 pip 环境可跑）。
  - 并入时修正原版 Kabsch 对齐的旋转矩阵公式错误（列向量公式误用于行向量约定，
    导致产物对齐失败 RMSD≈2.8；修正后 <1e-14，反射修正保持有效）。

### 改进（Improved）

- **错误提示友好化（P0）**：15 处把原始异常文本直接弹窗的路径改为 `show_friendly_error`，
  按错误类型给出中文原因与可操作建议（含 reactions / openbabel / mapping / results 等对话框）。
- **命令面板可发现性（P1）**：帮助菜单新增「命令面板 (Ctrl+K)」入口；命令面板补齐
  「打开文件 (Ctrl+O)」等高频动作。
- **帮助菜单快速上手与反馈（P2）**：新增「🚀 快速上手（3 分钟）」新手指引对话框；
  新增「💬 反馈问题 / 提建议」对话框，打开即自动把版本/系统环境信息复制到剪贴板。
- **数据库 schema 迁移机制**：`Storage` 基于 `PRAGMA user_version` 的增量迁移框架——
  全新库自动标版本；1.0 时代无版本标记的旧库打开时补标且数据保留；未来结构变更只需在
  `_MIGRATIONS` 注册迁移步骤（每步独立事务，失败回滚保旧版本、下次启动重试，
  杜绝「结构未升级却标新版本」的假升级）。已配 6 项迁移单测。

### 修复（Fixed）

- **Ctrl+O 打开文件失效**：标注文件提示绑定到了错误的处理方法，已修正并纳入快捷键自检。

## [1.0.2] - 2026-08-29

一轮「代码质量与性能」优化（无功能变更，向后兼容）。

### 代码质量（Quality）

- **lint 全清 + 格式标准化**：`ruff check` 全部规则通过；`ruff format` 统一为项目风格
  （line-length=120，114 个源文件），CI 的 lint/format 门禁不再报红。
- **移除 87 处冗余的 `# -*- coding: utf-8 -*-` 声明**（Python 3 源文件默认即 UTF-8）。
- **日志化**：33 处调试/兜底 `print()` 改为 `logging`，仅在 logger 不可用的最终 stderr 兜底处保留
  `print(..., file=sys.stderr)` 并加 `# noqa: T201`。
- **现代写法**：20 处 `zip()` 补 `strict=False`；`raise ... from err` 链；
  `pytest.raises(ValueError, match=...)` 收窄；`dict()`/`list()` 字面量、未用循环变量改名 `_x`。

### 死代码清理（Dead code）

- 删除 14 处「赋值后从未读取」的局部变量（`F841`）。
- `ui/app_helpers.py`：移除未使用的 `tkinter.messagebox` 导入（同时消解一处 `F811` 重定义）。

### 性能（Performance）

- 2 处「先建空列表再循环 `append`」改写为 `dict.values()` / `list.copy()`，减少不必要的中间对象。

### 修复（Fixes）

- **【潜在 bug】`chem/psi4/conformer.py` / `nmr.py`**：重构遗留的 `prefix` 变量本应作为输出文件
  前缀传给 `run_psi4_task(base_name=)`，却被丢弃，导致构象 / NMR 输出文件未按 `conf_XX_psi4`、
  `nmr_confXX` 前缀命名。现已接回 `base_name=prefix`。
- **【导入健壮性】`chem/openbabel_utils/_search.py`**：`import openbabel as ob` 改为
  `from ._common import ob`，与 `_io` / `_descriptors` / `_advanced` 等子模块一致；未安装
  OpenBabel 时 `ob` 由 `_common` 安全置 `None`，不再在导入阶段抛 `ModuleNotFoundError`。

## [1.0.1] - 2026-08-29

一轮「把 README 承诺补齐 + 把拆分事故收拾干净」的维护版本。

### 修复（Fixes）

- **【严重】`chem/openbabel_utils` 拆包后所有化学功能静默失效。**
  从单文件拆成 `_common / _io / _descriptors / _advanced / _cli / _check / _cache` 时，
  各子模块漏了 `from ._common import *`，拿不到 `ob` / `pybel` / `PYBEL_AVAILABLE` /
  `desc_cache` / `mol_read_cache` / `OB_INSTALL_GUIDE`。
  表现为：语法检查通过、导入也不报错，但一点功能就抛 `NameError`，又被各自的
  `except Exception` 吞成 `success=False` —— 界面上就是「点了没反应」。
  实测修复前 `smiles_to_inchikey("CC(=O)Oc1ccccc1C(=O)O")` 返回
  `{'success': False, 'message': "name 'PYBEL_AVAILABLE' is not defined"}`，
  修复后返回 `BSYNRYMUTXBXSQ-UHFFFAOYSA-N`。

- **【严重】描述符里 LogP / TPSA / HBD / HBA / 环数恒为 0。**
  旧实现用 `getattr(pybel.Molecule, "logP")` 取值，但 OpenBabel ≥ 3.1 的
  `pybel.Molecule` 压根没有 `.logP` / `.tpsa` 属性；`OBMol.NumHBD()` /
  `NumHBA()` / `NumSSSR()` 在新版 SWIG 绑定里同样不存在。
  异常被 `except` 吞掉后，这五个指标永远是 0 —— 界面上有数，其实是假的。
  现改为优先走 OpenBabel 官方入口 `OBDescriptor.FindType(...).Predict()`，
  并依次回退 `Num*()` 方法与 pybel 属性；环数改用 `OBMol.GetSSSR()`。
  阿司匹林实测：logP 1.31、TPSA 63.6、HBD 1、HBA 4、rotors 3、rings 1（此前全为 0）。

- **`ui/dialogs/common.py` 的 PSI4 快速测试在失败时抛 `NameError`。**
  `except Exception as e:` 绑定的名字会在 except 块结束时被 Python 自动删除，
  而它被放进了延迟执行的 `lambda` 里 —— 报错信息还没显示就先炸了。
  现改为把消息固化成局部变量再传给回调。

- **状态栏 OB 指示灯与「环境诊断」全线报错。**
  `_MANUAL_OBABEL_PATH` 以下划线开头，不会被 `from ._common import *` 带进来，
  `_cli` / `_check` 又没显式导入 —— 启动时日志直接刷
  `OpenBabel 不可用：check_openbabel 抛错: name '_MANUAL_OBABEL_PATH' is not defined`。
  现把该状态的真相源收敛到 `_cli`（`set/get_manual_obabel_path`），
  `_check` 改为通过 getter 读取，不再各存一份副本。

- **`chem/psi4/core.py` 的频率任务缺 `_plot_ir` 导入**（IR 光谱图绘制会抛 NameError）。
- **`chem/reaction_animation.py` 的 `_kabsch_rotation()` 缺 `import numpy as np`**（构象对齐直接崩）。
- **`ui/dialogs/advanced_tools_dialog.py` 被拼进了另一个模块的副本**，
  文件中间多了一整套 shebang / docstring / import，且尾部 140 行是
  `analytics_dialog.py` 里已有函数的重复实现（实际未被引用）。已删除重复副本，
  并补回该文件真正需要的 `fit_dialog_geometry` 导入。

- **化学查询对字段名大小写敏感**，导致照着表头输入 `MW>200` 一条都查不到。
  `utils/chem_query._entry_field()` 现在会做大小写不敏感兜底匹配。
  注意：放宽的只是「字段名写法」，字段缺失依旧判定为不匹配（不造假阳性）。

### 新增（Added）

- `environment.yml`：conda 环境（python 3.12 + psi4 + openbabel + tk …）。
- `pyproject.toml`：打包配置 + `api` / `rdkit` / `dev` 可选依赖 + ruff / pytest / mypy 配置。
- `Dockerfile` + `docker-entrypoint.sh` + `.dockerignore`：容器化（`gui` / `api` 两种模式，无 DISPLAY 时自动用 Xvfb 兜底）。
- `rebuild_env_windows.bat`：Windows 一键重建 conda 环境（含依赖冲突排查提示）。
- `.github/workflows/ci.yml`：ruff 检查 + mypy + pytest（conda 完整环境 / 纯 pip 环境两条流水线）。
- `api/`：FastAPI 接口层，实现 README 承诺的 `/health` `/descriptors` `/inchikey`
  `/substructure` `/similarity` `/query` 六个端点。OpenBabel 缺失时相关端点返回
  503 + 安装指引，而不是 500 或崩溃。
- `tests/`：单元测试（144 例），覆盖纯逻辑工具、领域模型、SQLite 持久化、
  化学查询、LRU 缓存、映射工具、OpenBabel 功能与接口层；
  其中 `test_openbabel_namespace.py` 是专门防止上述拆包事故复发的回归测试，
  且**不需要安装 OpenBabel** 即可运行。
- `.gitignore`。

### 清理（Removed）

- 删除拆分后遗留的三个旧单文件死代码：`core/model.py`（2342 行）、
  `chem/openbabel_utils.py`（1622 行）、`ui/ui_builder.py`（2930 行）。
  它们与同名子包并存，而 Python 中包优先，这些文件**永远不会被执行**。
- 删除三份 `.bak` 备份（`core/model.py.bak`、`chem/openbabel_utils.py.bak`、
  `ui/ui_builder.py.bak`）。
  合计减少约 1.4 万行死代码（代码库从 4.09 万行降到 3.40 万行）。

### 变更（Changed）

- 导入排序按 ruff isort 规则统一（72 处自动修复 + 6 处手动拆分
  `import x; x.y()` 的单行写法）。
- README 中的 Docker 镜像标签与实际版本对齐（`0.1.0` → `1.0.0`）。

### 已知问题（Known issues）

- 代码库尚未统一跑过 `ruff format`（全量格式化会产生约 1.9 万行 diff），
  CI 中该步骤设为「只提示不阻塞」，待格式整改单独立项。
- `ruff` 的 `E402` / `E701` / `E702` / `E741` / `F841` 在配置中被显式忽略：
  前四项是纯风格，最后一项（未使用的局部变量）为历史遗留的预留变量，
  逐个清理收益低、风险高，留给后续专项整改。

## [1.0.0] - 2026-08-27

首个正式版本：文件管理、格式转换、PSI4 量化计算、反应动画、分子检索与高级工具箱。
