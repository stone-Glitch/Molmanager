import os
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


def analyze_chirality(input_path: str) -> dict[str, Any]:
    """
    返回：
      n_centers: int (sp3 手性中心个数)
      centers: [{ idx_1based, symbol, label: R|S|? }]
      has_unknown: bool
    """
    try:
        ext = os.path.splitext(input_path)[1][1:].lower()
        mols = _read_molecules(input_path, ext)
        if not mols:
            return {"success": False, "message": "OpenBabel 无法读取该文件为分子"}
        mol = mols[0]
        obmol = mol.OBMol
        try:
            obmol.UnsetFlag(ob.OB_CHIRALITY_PERCEIVED)
            obmol.PerceiveStereo()
        except Exception:
            pass
        centers: list[dict[str, Any]] = []
        n_atoms = obmol.NumAtoms() if hasattr(obmol, "NumAtoms") else 0
        try:
            stereo_data = list(obmol.GetAllStereoData())
        except Exception:
            stereo_data = []
        chiral_idxs: set[int] = set()
        label_by_idx: dict[int, str] = {}
        try:
            for sd in stereo_data:
                try:
                    typ = sd.GetType()
                    # OBStereo::Tetrahedral = 1
                    if typ == 1 or getattr(sd, "IsTetrahedral", lambda: False)():
                        refs = list(sd.GetReferenceAtoms())
                        if refs:
                            c = refs[0]
                            chiral_idxs.add(int(c))
                            try:
                                cfg = sd.GetConfig()
                                label_by_idx[int(c)] = "R" if cfg > 0 else ("S" if cfg < 0 else "?")
                            except Exception:
                                pass
                except Exception:
                    continue
        except Exception:
            pass
        # 兜底：FindStereoCenters
        if not chiral_idxs:
            try:
                ch = list(obmol.FindStereoCenters())
                for c in ch:
                    chiral_idxs.add(int(c))
            except Exception:
                pass
        sym = {a.GetIdx(): a.GetSymbol() for a in obmol.GetAtoms()} if hasattr(obmol, "GetAtoms") else {}
        for idx in sorted(chiral_idxs):
            centers.append(
                {
                    "idx_1based": int(idx),
                    "symbol": sym.get(idx, "?"),
                    "label": label_by_idx.get(idx, "?"),
                }
            )
        return {
            "success": True,
            "n_centers": len(centers),
            "centers": centers,
            "has_unknown": any(c["label"] == "?" for c in centers),
            "total_atoms": n_atoms,
        }
    except Exception as e:
        return {"success": False, "message": f"手性分析失败：{e}"}


def invert_enantiomer(input_path: str, output_path: str) -> dict[str, Any]:
    """翻转所有手性中心 → 生成对映体并写文件。"""
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
        mol = mols[0]
        obmol = mol.OBMol
        try:
            obmol.UnsetFlag(ob.OB_CHIRALITY_PERCEIVED)
            obmol.PerceiveStereo()
        except Exception:
            pass
        try:
            obmol.InvertStereo()
        except Exception:
            # 回退：每个四面体 stereo data 取反配置
            try:
                for sd in list(obmol.GetAllStereoData()):
                    try:
                        typ = sd.GetType()
                        if typ == 1 or getattr(sd, "IsTetrahedral", lambda: False)():
                            cfg = sd.GetConfig()
                            sd.SetConfig(-cfg)
                    except Exception:
                        continue
            except Exception as e2:
                return {"success": False, "message": f"InvertStereo 不可用: {e2}"}
        mol2 = pybel.Molecule(obmol)
        mol2.write(out_ext or "xyz", output_path, overwrite=True)
        if not os.path.exists(output_path):
            return {"success": False, "message": "对映体写入失败"}
        return {"success": True, "output_path": output_path}
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
