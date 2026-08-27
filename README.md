# MolManager · 分子管理器

基于 **Tkinter** 的桌面分子管理工具：统一管理计算产物文件、批量格式转换、调用
**PSI4** 做量化计算、**OpenBabel** 做结构渲染与描述符，并生成反应动画字幕。

> 技术栈：Python 3.12 · Tkinter · PSI4 · OpenBabel · NumPy/SciPy/Matplotlib
> 运行环境固定见 `environment.yml`（conda-forge，含 psi4 / openbabel C++ 扩展）。

---

## ✨ 功能概览

| 模块 | 能力 |
| --- | --- |
| 文件管理 | 扫描工作目录、按编号/中英文名映射归档、批量导入导出 |
| 格式转换 | SMILES↔mol/sdf/pdb、2D 结构 PNG、描述符（MW/LogP/TPSA…）、InChIKey |
| 量化计算 | 单点能 / 几何优化 / 频率 / 过渡态 / 激发态 / SAPT / 热化学（PSI4 驱动，可取消） |
| 反应动画 | 帧序列 + 字幕，导出为视频素材 |
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
docker build -t molmanager:0.1.0 .
docker run --rm -it molmanager:0.1.0          # GUI（需 X11 转发）
# 或仅跑 API：
docker run --rm -p 8000:8000 molmanager:0.1.0 \
  bash -lc "pip install -e '.[api]' && uvicorn api.server:app --host 0.0.0.0 --port 8000"
```

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
# 文档： http://127.0.0.1:8000/docs
# 端点： /health /descriptors /inchikey /substructure /similarity /query
```

---

## 🗂️ 代码结构

```
core/        领域模型与数据层（domain 数据类 / storage_sqlite 持久化）
ui/          界面构建（ui_builder 已按功能拆分为 _theme/_menu/_tabs/… 子模块）
chem/        化学能力（openbabel_utils 已拆分为 _cli/_io/_descriptors/_search/…）
utils/       纯逻辑工具（chem_query、mapping_utils 等，均带 pytest 单测）
api/         FastAPI 接口层（可选）
tests/       单元测试（pytest）
```

核心数据结构定义在 `core/domain.py`（dataclass），分子 / 映射 / 计算结果持久化见
`core/storage_sqlite.py`。

---

## 🔧 开发规范

- **格式化 / 导入排序**：`ruff check .` 与 `ruff format .`
- **类型检查**：`mypy core/domain.py core/storage_sqlite.py`
- **测试**：`pytest tests/ -q`
- **CI**：`.github/workflows/ci.yml` 自动跑 ruff 检查 + conda 环境下 pytest

详见 `CHANGELOG.md`。
