import os
import re
import shutil
import tempfile
from typing import Any

from utils.constants import (
    OB_CONVERT_TIMEOUT_SEC,
    OB_LARGE_TIMEOUT_SEC,
    OB_PNG_TIMEOUT_SEC,
)
from utils.logger import default_logger as logger

# ======================== 导入与版本兼容 ========================
from ._cli import _run_obabel, _secure_output_path
from ._common import *  # noqa: F403  # 取 ob / pybel / PYBEL_AVAILABLE
from ._io import _read_molecules


def _perceive_stereo(obmol) -> None:
    """跨版本的立体化学感知：swig 绑定差异下，``PerceiveStereo`` 既可能是
    模块级函数（OpenBabel 3.x 正确签名 ``ob.PerceiveStereo(mol)``）也可能是成员方法。"""
    try:
        ob.PerceiveStereo(obmol)
        return
    except Exception:
        pass
    try:
        obmol.PerceiveStereo()
    except Exception:
        pass


def _symbol_of(atom) -> str:
    """元素符号规范化：本机绑定无 ``OBAtom.GetSymbol``，``GetType()`` 返回的是
    元素类型字符串（如 ``C3``/``Cl``/``Fe``），取字母前缀并首字母大写。"""
    try:
        t = str(atom.GetType())
        m = re.match(r"[A-Za-z]+", t)
        return m.group(0).capitalize() if m else "?"
    except Exception:
        return "?"


def _tetrahedral_stereos(obmol) -> list[tuple[int, Any]]:
    """收集四面体手性中心 → [(idx_1based, OBTetrahedralStereo), ...]。

    优先 OBStereoFacade 逐原子查询（部分绑定 ``GetAllTetrahedralStereo`` 返回的
    裸指针不可迭代，逐原子接口稳定可用）。
    """
    out: list[tuple[int, Any]] = []
    try:
        fac = ob.OBStereoFacade(obmol)
        for atom in ob.OBMolAtomIter(obmol):
            aid = atom.GetId()
            if fac.HasTetrahedralStereo(aid):
                ts = fac.GetTetrahedralStereo(aid)
                if ts is not None:
                    out.append((int(atom.GetIdx()), ts))
    except Exception:
        out = []
    return out


def analyze_chirality(input_path: str) -> dict[str, Any]:
    """
    返回：
      n_centers: int (sp3 手性中心个数)
      centers: [{ idx_1based, symbol, label: R|S|? }]
      has_unknown: bool

    注：CIP R/S 标注依赖 OpenBabel 3.2+ 的 CIP 能力，3.1 下不谎报、恒为 ``?``；
    手性中心**个数与位置**检测是可靠的。
    """
    try:
        ext = os.path.splitext(input_path)[1][1:].lower()
        mols = _read_molecules(input_path, ext)
        if not mols:
            return {"success": False, "message": "OpenBabel 无法读取该文件为分子"}
        obmol = mols[0].OBMol
        _perceive_stereo(obmol)
        centers: list[dict[str, Any]] = []
        for idx, ts in _tetrahedral_stereos(obmol):
            label = "?"
            try:
                # OpenBabel 3.2+ 若提供 CIP 标签则采用，否则诚实保持 "?"
                cfg = ts.GetConfig()
                label = getattr(cfg, "label", None) or "?"
            except Exception:
                label = "?"
            atom = obmol.GetAtom(idx)
            centers.append(
                {
                    "idx_1based": int(idx),
                    "symbol": _symbol_of(atom),
                    "label": label,
                }
            )
        return {
            "success": True,
            "n_centers": len(centers),
            "centers": centers,
            "has_unknown": any(c["label"] == "?" for c in centers),
            "total_atoms": obmol.NumAtoms(),
        }
    except Exception as e:
        return {"success": False, "message": f"手性分析失败：{e}"}


def invert_enantiomer(input_path: str, output_path: str) -> dict[str, Any]:
    """翻转所有手性中心 → 生成对映体并写文件。

    双重翻转保证任意输出格式手性一致：
      1. **拓扑层**：每个四面体中心 winding 取反（写出 SMILES 时 @ ↔ @@ 互换）；
      2. **几何层**：x 坐标镜像（3D 坐标即精确镜像几何，与对映体能量等价）。
    """
    try:
        # 【审计 1.1】输出路径安全解析
        try:
            output_path = str(_secure_output_path(output_path, create_parent=True))
        except ValueError as e:
            return {"success": False, "message": f"输出路径非法: {e}"}
        ext = os.path.splitext(input_path)[1][1:].lower()
        out_ext = os.path.splitext(output_path)[1][1:].lower()
        if not PYBEL_AVAILABLE:
            return {"success": False, "message": "需要 pybel"}
        mols = _read_molecules(input_path, ext)
        if not mols:
            return {"success": False, "message": "OpenBabel 无法读取该文件为分子"}
        obmol = mols[0].OBMol
        _perceive_stereo(obmol)
        tets = _tetrahedral_stereos(obmol)
        if not tets:
            return {"success": False, "message": "未检测到手性中心，无需反转"}
        n_flipped = 0
        for _idx, ts in tets:
            try:
                cfg = ts.GetConfig()
                cfg.winding = (
                    ob.OBStereo.AntiClockwise
                    if cfg.winding == ob.OBStereo.Clockwise
                    else ob.OBStereo.Clockwise
                )
                ts.SetConfig(cfg)
                n_flipped += 1
            except Exception:
                continue
        # 几何镜像：x → -x（对映体 = 精确镜像，能量等价）
        for atom in ob.OBMolAtomIter(obmol):
            v = atom.GetVector()
            atom.SetVector(-v.GetX(), v.GetY(), v.GetZ())
        mol2 = pybel.Molecule(obmol)
        mol2.write(out_ext or "mol", output_path, overwrite=True)
        if not os.path.exists(output_path):
            return {"success": False, "message": "对映体写入失败"}
        return {
            "success": True,
            "output_path": output_path,
            "n_flipped": n_flipped,
            "message": f"已翻转 {n_flipped} 个手性中心（拓扑 @↔@@ + 3D 坐标镜像）",
        }
    except Exception as e:
        return {"success": False, "message": f"生成对映体失败：{e}"}


# ======================== O7：生理 pH=7.4 一键加氢 ========================


def protonate_ph(input_path: str, output_path: str, ph: float = 7.4) -> dict[str, Any]:
    """
    用 `obabel -p <ph>` 做 pH 下的质子化：
      - COOH → COO⁻
      - NH2 → NH3⁺
      - 吡啶 N → N⁺H
    """
    try:
        if not 0 <= ph <= 14:
            return {"success": False, "message": "pH 范围 0-14"}
        # 【审计 1.1】输出路径安全解析
        try:
            output_path = str(_secure_output_path(output_path, create_parent=True))
        except ValueError as e:
            return {"success": False, "message": f"输出路径非法: {e}"}
        with tempfile.NamedTemporaryFile(
            suffix="." + (os.path.splitext(input_path)[1][1:] or "xyz"), delete=False
        ) as _t1:
            pass
        with tempfile.NamedTemporaryFile(
            suffix="." + (os.path.splitext(output_path)[1][1:] or "xyz"), delete=False
        ) as _t2:
            pass
        try:
            shutil.copy2(input_path, _t1.name)
            cmd = ["obabel", _t1.name, "-O", _t2.name, "-p", f"{ph:g}"]
            # pH 加氢需要构建完整 3D + 电荷分配，使用 OB_LARGE_TIMEOUT_SEC
            r = _run_obabel(cmd, timeout=OB_LARGE_TIMEOUT_SEC)
            if r.returncode != 0 or not os.path.exists(_t2.name) or os.path.getsize(_t2.name) == 0:
                return {"success": False, "message": f"obabel -p 返回码 {r.returncode}: {r.stderr[:300]}"}
            shutil.copy2(_t2.name, output_path)
            return {
                "success": True,
                "output_path": output_path,
                "ph": ph,
                "message": f"已在 pH={ph:g} 下加氢：-COOH→-COO⁻、-NH2→-NH3⁺ 等",
            }
        finally:
            for t in (_t1.name, _t2.name):
                try:
                    os.unlink(t)
                except OSError as _oe:
                    logger.debug("清理 pH 加氢临时文件失败 %s: %s", t, _oe)
    except Exception as e:
        return {"success": False, "message": f"pH 加氢失败：{e}"}


# ======================== O8：SDF 拆分/合并 ========================


def split_multi_sdf(input_sdf: str, out_dir: str, prefix: str = "mol", format_ext: str = "xyz") -> dict[str, Any]:
    """把一个 SDF（或任何多分子文件，.sdf/.mol2/.xyz 都行）拆成多个单分子文件。"""
    try:
        # 【审计 1.1】输出目录安全解析 + prefix 不允许包含路径分隔符
        try:
            out_dir = str(_secure_output_path(out_dir, is_dir=True, create_parent=True))
        except ValueError as e:
            return {"success": False, "message": f"输出目录非法: {e}"}
        try:
            from core.model import enforce_no_path_separators
        except Exception:

            def enforce_no_path_separators(name: str) -> None:
                if any(ch in name for ch in ("/", "\\", "\x00", "\r", "\n")):
                    raise ValueError(f"文件名前缀包含非法字符: {name!r}")

        try:
            enforce_no_path_separators(prefix)
        except ValueError as e:
            return {"success": False, "message": f"文件前缀非法: {e}"}
        if not PYBEL_AVAILABLE:
            return {"success": False, "message": "需要 pybel"}
        ext_in = os.path.splitext(input_sdf)[1][1:].lower() or "sdf"
        mols = _read_molecules(input_sdf, ext_in)
        if not mols:
            return {"success": False, "message": "未读取到任何分子"}
        ok = 0
        names: list[str] = []
        pad = max(3, len(str(len(mols))))
        ext_use = format_ext.lower().lstrip(".")
        for i, mol in enumerate(mols, 1):
            try:
                title = ""
                try:
                    title = mol.title.strip().replace("/", "_").replace("\\", "_").replace(":", "_")
                except Exception:
                    title = ""
                if not title:
                    title = f"{prefix}_{str(i).zfill(pad)}"
                name = f"{title}.{ext_use}"
                fp = os.path.join(out_dir, name)
                uniq = 1
                while os.path.exists(fp):
                    fp = os.path.join(out_dir, f"{title}_{uniq}.{ext_use}")
                    uniq += 1
                mol.write(ext_use, fp, overwrite=True)
                if os.path.exists(fp):
                    ok += 1
                    names.append(fp)
            except Exception as _we:
                logger.debug("拆分分子写入 %s 失败: %s", name, _we)
                continue
        return {"success": ok > 0, "total": len(mols), "ok": ok, "output_dir": out_dir, "files": names}
    except Exception as e:
        return {"success": False, "message": f"拆分多分子文件失败：{e}"}


def merge_to_sdf(input_paths: list[str], output_sdf: str) -> dict[str, Any]:
    """把一堆分子文件（任意格式）合并成一个 SDF。"""
    try:
        if not PYBEL_AVAILABLE:
            return {"success": False, "message": "需要 pybel"}
        all_mols = []
        for fp in input_paths:
            try:
                ext = os.path.splitext(fp)[1][1:].lower()
                ms = _read_molecules(fp, ext) or []
                all_mols.extend(ms)
            except Exception as _re:
                logger.debug("SDF 合并跳过文件 %s: %s", fp, _re)
                continue
        if not all_mols:
            return {"success": False, "message": "未读取到任何分子"}
        # 【审计 1.1】输出 SDF 路径安全解析
        try:
            output_sdf = str(_secure_output_path(output_sdf, create_parent=True))
        except ValueError as e:
            return {"success": False, "message": f"输出 SDF 路径非法: {e}"}
        # 逐个 append 写 sdf（pybel write('sdf', multi=True)）
        with tempfile.NamedTemporaryFile(suffix=".sdf", delete=False, mode="wb") as _tmp:
            tmp_name = _tmp.name
        try:
            conv = ob.OBConversion() if PYBEL_AVAILABLE and "ob" in globals() else None
            if conv is not None:
                conv.SetOutFormat("sdf")
                with open(tmp_name, "wb") as f:
                    for m in all_mols:
                        try:
                            if hasattr(m, "OBMol"):
                                s = conv.WriteString(m.OBMol)
                                if s:
                                    f.write(s.encode("utf-8", errors="replace"))
                        except Exception as _we:
                            logger.debug("SDF 合并写入单分子失败: %s", _we)
                            continue
            else:
                with open(tmp_name, "w", encoding="utf-8") as f:
                    for i, m in enumerate(all_mols):
                        try:
                            f.write(m.write("sdf"))
                        except Exception as _we:
                            logger.debug("SDF 合并 pybel.write 失败 (%d): %s", i, _we)
                            continue
            shutil.copy2(tmp_name, output_sdf)
        finally:
            try:
                os.unlink(tmp_name)
            except OSError as _oe:
                logger.debug("清理 SDF 合并临时文件失败: %s, err=%s", tmp_name, _oe)
        size = os.path.getsize(output_sdf)
        return {"success": size > 0, "output_sdf": output_sdf, "molecules": len(all_mols), "bytes": size}
    except Exception as e:
        return {"success": False, "message": f"合并为 SDF 失败：{e}"}


# ======================== 分子叠加 ========================


def align_molecules(ref_path: str, mobile_path: str, output_path: str) -> dict[str, Any]:
    """
    将移动分子叠加到参考分子上。
    返回: {'success': bool, 'message': str, 'output_path': str}
    """
    # 【审计 1.1】输出路径安全解析
    try:
        output_path = str(_secure_output_path(output_path, create_parent=True))
    except ValueError as e:
        return {"success": False, "message": f"输出路径非法: {e}", "output_path": None}

    try:
        # 使用 obabel 的 --align 选项
        cmd = ["obabel", mobile_path, "-O", output_path, "--align", ref_path]
        result = _run_obabel(cmd, timeout=OB_CONVERT_TIMEOUT_SEC)
        if result.returncode == 0 and os.path.exists(output_path):
            return {"success": True, "message": "叠加成功", "output_path": output_path}
        else:
            return {"success": False, "message": f"叠加失败: {result.stderr.strip()}", "output_path": None}
    except Exception as e:
        return {"success": False, "message": str(e), "output_path": None}


def render_png_2d(input_path: str, output_path: str, width: int = 800, height: int = 600) -> dict[str, Any]:
    """渲染 2D PNG 图：优先 pybel → OBDepict，最后回退 obabel CLI。"""
    # 【审计 1.1】输出路径安全解析
    try:
        output_path = str(_secure_output_path(output_path, create_parent=True))
    except ValueError as e:
        return {"success": False, "message": f"输出路径非法: {e}", "output_path": None}

    try:
        if PYBEL_AVAILABLE:
            try:
                input_ext = os.path.splitext(input_path)[1][1:].lower()
                mols = _read_molecules(input_path, input_ext)
                if not mols:
                    return {"success": False, "message": "无法读取输入文件（没有可识别的分子）", "output_path": None}
                mol = mols[0]

                try:
                    depict = ob.OBDepict()
                    depict.SetWidth(width)
                    depict.SetHeight(height)
                    obmol = mol.OBMol
                    depict.DrawMolecule(obmol)
                    depict.WritePNG(output_path)
                    if os.path.exists(output_path):
                        return {"success": True, "message": "2D PNG 渲染成功（OBDepict）", "output_path": output_path}
                except Exception as _de:
                    logger.debug("OBDepict 渲染失败: %s", _de)

                try:
                    mol.draw(width=width, height=height, filename=output_path)
                    if os.path.exists(output_path):
                        return {"success": True, "message": "2D PNG 渲染成功（pybel.draw）", "output_path": output_path}
                except Exception as _de2:
                    logger.debug("pybel.draw 渲染失败: %s", _de2)
            except Exception as _re:
                logger.debug("读取分子失败（2D PNG 渲染阶段）: %s", _re)

        cmd = ["obabel", input_path, "-O", output_path, "-xS", "-xN", str(width), "-xW", str(height)]
        # 2D 渲染有时很慢，使用 OB_PNG_TIMEOUT_SEC
        result = _run_obabel(cmd, timeout=OB_PNG_TIMEOUT_SEC)
        if result.returncode == 0 and os.path.exists(output_path):
            return {"success": True, "message": "2D PNG 渲染成功（obabel CLI）", "output_path": output_path}
        else:
            return {"success": False, "message": f"渲染失败: {result.stderr.strip()}", "output_path": None}
    except Exception as e:
        return {"success": False, "message": str(e), "output_path": None}
