# MolManager · 分子管理器

基于 **Tkinter** 的桌面分子管理工具：统一管理计算产物文件、批量格式转换、调用
**PSI4** 做量化计算、**OpenBabel** 做结构渲染与描述符，并生成反应动画字幕。

> 当前版本：**1.2.0**
> 技术栈：Python 3.12 · Tkinter · PSI4 · OpenBabel · NumPy/SciPy/Matplotlib · FastAPI（可选）
> 运行环境固定见 `environment.yml`（conda-forge，含 psi4 / openbabel C++ 扩展）。

---

## ✨ 功能概览

| 模块 | 能力 |
| --- | --- |
| 文件管理 | 扫描工作目录、按编号/中英文名映射归档、批量导入导出 |
| 格式转换 | SMILES↔mol/sdf/pdb、2D 结构 PNG、描述符（MW/LogP/TPSA…）、InChIKey |
| 量化计算 | 单点能 / 几何优化 / 频率 / 过渡态 / 激发态 / SAPT / 热化学（PSI4 驱动，可取消） |
| 反应动画 | 帧序列 + 字幕，导出为视频素材 |
| 量子反应能 | 预设/自定义反应 **ΔE · ΔE₀ · ΔH° · ΔG°**（PSI4 热化学，含零点能），附能量曲线与 MP4 过渡动画 |
| 分子搜索 | **SMARTS 子结构** 与 **指纹相似性** 检索（OpenBabel FP2，零额外依赖） |
| 高级工具 | 手性分析、对映体反转、质子化、多 SDF 拆分/合并、构象对齐 |

---

## 🚀 安装与运行

### 方式一：conda 环境（推荐，含 PSI4/OpenBabel）

```bash
conda env create -f environment.yml
conda activate mol_manager_312
python main.py
```

Windows 双击启动器：`run_main.bat` / `rebuild_env_windows.bat`。

### 方式二：Docker

```bash
docker build -t molmanager:1.0.1 .

# GUI（需 X11 转发）
xhost +local:docker
docker run --rm -it \
  -e DISPLAY=$DISPLAY -v /tmp/.X11-unix:/tmp/.X11-unix \
  molmanager:1.0.1

# 或仅跑 API
docker run --rm -p 8000:8000 molmanager:1.0.1 api
```

镜像内已装好接口层依赖，无需再 `pip install`；无 `DISPLAY` 时会自动退到 Xvfb 虚拟显示。

---

## 🧩 可选功能（额外依赖）

```bash
pip install -e ".[api]"     # FastAPI 接口层
pip install -e ".[rdkit]"   # 若改用 RDKit 指纹（默认已用 OpenBabel 指纹，无需安装）
pip install -e ".[dev]"     # 开发：pytest / ruff / mypy
```

### FastAPI 接口层

```bash
uvicorn api.server:app --reload --port 8000
# 交互式文档： http://127.0.0.1:8000/docs
```

| 端点 | 说明 | 需要 OpenBabel |
| --- | --- | --- |
| `GET /health` | 健康检查 + 后端能力探测（`?refresh=true` 强制重探） | 否 |
| `POST /inchikey` | SMILES → InChIKey，支持批量，单条失败不影响整体 | 是（pybel） |
| `POST /descriptors` | 分子描述符：MW / logP / TPSA / HBD / HBA / 环数 …，入参可为 `smiles` 或 `path` | 是（pybel） |
| `POST /substructure` | SMARTS 子结构检索 | 是 |
| `POST /similarity` | 指纹相似性检索（默认 FP2，可选 threshold / top_n） | 是 |
| `POST /query` | 化学条件过滤（`MW>200 logP<3` 这类串），纯 Python | 否 |

后端缺 OpenBabel 时，需要它的端点返回 **503 + 安装指引**，`/health` 与 `/query` 照常可用。

---

## ⚗️ 量子反应能计算（ΔE/ΔH°/ΔG°）

入口：菜单 **工具 → 量子反应能计算**，或 <kbd>Ctrl+K</kbd> 命令面板搜索「量子」。
（由独立的 Quantum Reaction Visualizer 项目融合而来，计算内核原样保留。）

工作流程：SMILES → RDKit 生成 3D 构型 → PSI4 几何优化 + 频率分析 → 汇总两侧热化学量：

| 输出 | 含义 |
| --- | --- |
| **ΔE** | 电子能差（优化后单点） |
| **ΔE₀** | 含零点能校正的能差 |
| **ΔH° / ΔG°** | 298.15 K、1 bar 标准态焓变 / 吉布斯能变（含自发性判定） |

- **反应来源**：8 个预设（水生成、氨合成、甲烷燃烧、氯化氢分解、乙烯加氢、水煤气变换、
  甲醇脱水、臭氧分解），或自定义输入 SMILES 列表；`O=O:3` 这种 `:N` 后缀指定自旋多重度
  （O₂ 默认已标三线态，避免激发态导致 ΔE 系统性偏差）。
- **方法**：HF/STO-3G（最快）· HF/6-31G* · B3LYP/6-31G* · MP2/6-31G*；开壳层自动切 UHF/UKS，
  SCF 不收敛自动分级重试；单原子体系走解析热化学（Sackur–Tetrode，避开 PSI4 单原子频率缺陷）。
- **产物落盘**：每次运行写入 `quantum_runs/<时间戳>/`（结果 JSON、优化后 XYZ、能量曲线 PNG、
  IQmol 兼容多帧 `trajectory.xyz`、可选 MP4 过渡动画，需要 ffmpeg）；对话框内一键打开目录
  或用 IQmol 校验工具核对轨迹。
- **依赖**：PSI4 必需；RDKit 仅自定义 SMILES 需要（预设内置 3D 构型可缺）；ffmpeg 仅 MP4 需要。
  缺依赖时对话框顶部给出安装指引，不会崩溃。
- 优化/频率结果带磁盘缓存，重复计算同分子同级别直接读缓存。

---

## 🗂️ 代码结构

```
core/        领域模型与数据层（domain 数据类 / storage_sqlite 持久化）
ui/          界面构建（ui_builder 已按功能拆分为 _theme/_menu/_tabs/… 子模块）
chem/        化学能力（openbabel_utils 已拆分为 _cli/_io/_descriptors/_search/…；
             quantum_reaction/ 为量子反应能计算子包：runner 编排 / quantum PSI4 包装 /
             molbuild 构型 / reactions 预设库 / animate 动画 / iqmol_check 轨迹校验）
utils/       纯逻辑工具（chem_query、mapping_utils 等，均带 pytest 单测）
api/         FastAPI 接口层（可选）
tests/       单元测试（pytest）
```

核心数据结构定义在 `core/domain.py`（dataclass），分子 / 映射 / 计算结果持久化见
`core/storage_sqlite.py`。

---

## 🔧 开发规范

```bash
ruff check .                                   # 静态检查（含 F821 未定义名称、I001 导入顺序）
ruff format .                                  # 格式化
mypy core/domain.py core/storage_sqlite.py     # 类型检查
pytest tests/ -q                               # 单元测试
```

- **测试策略**：需要 OpenBabel / PSI4 / FastAPI 的用例通过 `tests/conftest.py`
  里的 fixture 自动跳过，因此**纯 pip 环境下也能跑通大部分测试**。
  其中 `tests/test_openbabel_namespace.py` 是拆包事故的回归防线，
  不装 OpenBabel 同样会执行。
- **CI**：`.github/workflows/ci.yml` 跑 ruff（+ mypy）+ 两轮 pytest
  （conda 完整环境 / 纯 pip 环境）。
- **已知欠账**：历史代码尚未统一跑过 `ruff format`（全量格式化约 1.9 万行 diff），
  CI 中该步骤目前只提示不阻塞，待专项整改。

详见 `CHANGELOG.md`（1.2.0 记录了测试加固、UI 结构重构与 Aurora Frost 视觉精修）。
