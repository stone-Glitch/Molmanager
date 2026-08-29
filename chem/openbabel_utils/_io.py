import os
from typing import Any

from utils.constants import (
    DEFAULT_FORCEFIELD,
    OB_CONVERT_TIMEOUT_SEC,
    OB_LARGE_TIMEOUT_SEC,
)

from ._cache import _cache_key
from ._cli import _run_obabel, _secure_output_path
from ._common import *  # noqa: F403  # 取 ob / pybel / PYBEL_AVAILABLE / mol_read_cache

# ======================== 导入与版本兼容 ========================
from ._common import _COMMON_IN_FORMATS, _MOL_READ_CACHE_MAX_BYTES, _MOL_READ_CACHE_MAX_MOLECULES


def _read_molecules(input_path: str, input_ext: str) -> list:
    """从 pybel 读入，空扩展名时先尝试常见扩展名，失败后再穷举。带 (path,mtime,size,ext) LRU 缓存；读写均加锁。"""
    ck = _cache_key(input_path)
    cache_full_key: tuple | None = (ck[0], ck[1], ck[2], ck[3], input_ext) if ck is not None else None
    # 审计建议：超大文件不进缓存（仅跳过缓存，正常返回解析结果），
    # 防止单个巨量 SDF 把 _MOL_READ_CACHE 撑爆。
    if cache_full_key is not None and ck[2] > _MOL_READ_CACHE_MAX_BYTES:
        cache_full_key = None
    if cache_full_key is not None:
        cached = mol_read_cache.get(cache_full_key)
        if cached is not None:
            return list(cached)
    if input_ext:
        result = list(pybel.readfile(input_ext, input_path))
    else:
        result = []
        tried_paths: list[tuple[str, str]] = []
        for fmt in _COMMON_IN_FORMATS:
            try:
                mols = list(pybel.readfile(fmt, input_path))
                if mols:
                    result = mols
                    break
            except Exception as e:
                tried_paths.append((fmt, str(e)))
        if not result:
            for fmt in pybel.informats:
                if fmt in _COMMON_IN_FORMATS:
                    continue
                try:
                    mols = list(pybel.readfile(fmt, input_path))
                    if mols:
                        result = mols
                        break
                except Exception:
                    continue
    # 审计 P-3：含分子数过多的文件（典型：上千分子的巨量 SDF）不进缓存，
    # 仅跳过缓存、正常返回解析结果，避免整表 pybel 分子对象撑爆内存。
    if cache_full_key is not None and len(result) <= _MOL_READ_CACHE_MAX_MOLECULES:
        mol_read_cache.put(cache_full_key, list(result))
    return result


def convert_file(input_path: str, output_path: str, output_format: str, base_dir=None) -> dict[str, Any]:
    """
    转换分子文件格式。
    返回: {'success': bool, 'message': str, 'output_path': str}
    """
    # 处理输出路径扩展名
    base, ext = os.path.splitext(output_path)
    if not ext or ext[1:].lower() != output_format.lower():
        output_path = f"{base}.{output_format}" if base else f"output.{output_format}"

    # 【审计 1.1 路径遍历加固】：输出路径走安全解析，创建父目录
    try:
        output_path = str(_secure_output_path(output_path, create_parent=True))
    except ValueError as e:
        return {"success": False, "message": f"输出路径非法: {e}", "output_path": None}

    try:
        if PYBEL_AVAILABLE:
            input_ext = os.path.splitext(input_path)[1][1:].lower()
            mols = _read_molecules(input_path, input_ext)
            if not mols:
                return {"success": False, "message": "无法读取输入文件（没有可识别的分子）"}

            # 写入输出
            with pybel.Outputfile(output_format, output_path, overwrite=True) as out:
                for mol in mols:
                    out.write(mol)
            return {"success": True, "message": f"成功转换为 {output_format}", "output_path": output_path}
        else:
            # 使用命令行
            cmd = ["obabel", input_path, "-O", output_path]
            result = _run_obabel(cmd, timeout=OB_CONVERT_TIMEOUT_SEC)
            if result.returncode == 0 and os.path.exists(output_path):
                return {"success": True, "message": f"成功转换为 {output_format}", "output_path": output_path}
            else:
                return {"success": False, "message": f"转换失败: {result.stderr.strip()}", "output_path": None}
    except Exception as e:
        return {"success": False, "message": str(e), "output_path": None}


# ======================== SMILES → 分子 ========================

def generate_from_smiles(
    smiles: str,
    output_prefix: str,
    output_dir: str = ".",
    generate_3d: bool = True,
    optimize: bool = True,
    forcefield: str = DEFAULT_FORCEFIELD,
) -> dict[str, Any]:
    """
    从 SMILES 生成 3D 分子文件（.mol 和 .xyz）。
    返回: {'success': bool, 'message': str, 'mol': str, 'xyz': str}
    """
    # 【审计 1.1】输出目录安全解析
    try:
        output_dir = str(_secure_output_path(output_dir, is_dir=True, create_parent=True))
    except ValueError as e:
        return {"success": False, "message": f"输出目录非法: {e}", "mol": None, "xyz": None}
    # 同样校验 prefix：避免包含路径分隔符 / ..，保证只会在 output_dir 下生成文件
    try:
        from core.model import enforce_no_path_separators
    except Exception:
        def enforce_no_path_separators(name: str) -> None:
            if any(ch in name for ch in ("/", "\\", "\x00", "\r", "\n")):
                raise ValueError(f"文件名前缀包含非法字符: {name!r}")
    try:
        enforce_no_path_separators(output_prefix)
    except ValueError as e:
        return {"success": False, "message": f"文件前缀非法: {e}", "mol": None, "xyz": None}

    mol_path = os.path.join(output_dir, f"{output_prefix}.mol")
    xyz_path = os.path.join(output_dir, f"{output_prefix}.xyz")

    try:
        if PYBEL_AVAILABLE:
            mol = pybel.readstring("smi", smiles)
            if mol is None:
                return {"success": False, "message": "无效的 SMILES", "mol": None, "xyz": None}

            if generate_3d:
                mol.make3D()
                if optimize:
                    # 根据 forcefield 选择优化
                    mol.localopt(forcefield=forcefield, steps=500)

            # 写入 .mol 和 .xyz（基于同一个分子对象）
            mol.write("mol", mol_path, overwrite=True)
            mol.write("xyz", xyz_path, overwrite=True)
            return {"success": True, "message": "生成成功", "mol": mol_path, "xyz": xyz_path}
        else:
            # 命令行模式：先生成 .mol，再转换为 .xyz（避免重复 gen3d）
            # 生成 .mol（含 3D 和优化）
            cmd_mol = ["obabel", f"-:{smiles}", "-O", mol_path]
            if generate_3d:
                cmd_mol.append("--gen3d")
                if optimize:
                    cmd_mol.extend(["--minimize", "--ff", forcefield])
            # gen3d + minimize 对大分子可能较慢，使用较大超时
            result_mol = _run_obabel(cmd_mol, timeout=OB_LARGE_TIMEOUT_SEC)
            if result_mol.returncode != 0 or not os.path.exists(mol_path):
                return {
                    "success": False,
                    "message": f"生成 .mol 失败: {result_mol.stderr.strip()}",
                    "mol": None,
                    "xyz": None
                }

            # 从 .mol 转换为 .xyz（无需重新优化）
            cmd_xyz = ["obabel", mol_path, "-O", xyz_path]
            result_xyz = _run_obabel(cmd_xyz, timeout=30)
            if result_xyz.returncode == 0 and os.path.exists(xyz_path):
                return {"success": True, "message": "生成成功", "mol": mol_path, "xyz": xyz_path}
            else:
                # 即使 xyz 失败，mol 已生成，可返回部分成功
                return {
                    "success": True,
                    "message": f".mol 成功，但 .xyz 转换失败: {result_xyz.stderr.strip()}",
                    "mol": mol_path,
                    "xyz": None
                }
    except Exception as e:
        return {"success": False, "message": str(e), "mol": None, "xyz": None}


# ======================== 力场优化 ========================

def optimize_geometry(input_path: str, output_path: str,
                      forcefield: str = DEFAULT_FORCEFIELD) -> dict[str, Any]:
    """
    使用 Open Babel 力场优化分子结构。
    返回: {'success': bool, 'message': str, 'output_path': str}
    """
    # 【审计 1.1】输出路径安全解析
    try:
        output_path = str(_secure_output_path(output_path, create_parent=True))
    except ValueError as e:
        return {"success": False, "message": f"输出路径非法: {e}", "output_path": None}

    try:
        if PYBEL_AVAILABLE:
            # 自动检测输入格式
            ext = os.path.splitext(input_path)[1][1:].lower()
            if not ext:
                # 尝试 pybel 自动识别
                mols = None
                for fmt in pybel.informats:
                    try:
                        mols = list(pybel.readfile(fmt, input_path))
                        if mols:
                            break
                    except Exception:
                        continue
                if not mols:
                    return {"success": False, "message": "无法识别输入文件格式", "output_path": None}
            else:
                mols = list(pybel.readfile(ext, input_path))

            if not mols:
                return {"success": False, "message": "无法读取分子", "output_path": None}

            mol = mols[0]
            # 确保有 3D 结构（如果没有则生成）
            if not mol.OBMol.Has3D():
                mol.make3D()

            # 优化
            try:
                mol.localopt(forcefield=forcefield, steps=500)
            except TypeError:
                # 旧版参数可能不同
                mol.localopt(ff=forcefield, steps=500)

            # 写入输出（保持原格式或用户指定格式）
            out_ext = os.path.splitext(output_path)[1][1:] or ext
            mol.write(out_ext, output_path, overwrite=True)
            return {"success": True, "message": "优化完成", "output_path": output_path}
        else:
            # 命令行优化：obabel input -O output --minimize --ff MMFF94
            cmd = ["obabel", input_path, "-O", output_path, "--minimize", "--ff", forcefield]
            # 力场优化对大分子较慢，使用 OB_LARGE_TIMEOUT_SEC
            result = _run_obabel(cmd, timeout=OB_LARGE_TIMEOUT_SEC)
            if result.returncode == 0 and os.path.exists(output_path):
                return {"success": True, "message": "优化完成", "output_path": output_path}
            else:
                return {"success": False, "message": f"优化失败: {result.stderr.strip()}", "output_path": None}
    except Exception as e:
        return {"success": False, "message": str(e), "output_path": None}


# ======================== 计算描述符 ========================
