"""chem 子系统 mixin（由原 core/model.py 拆分而来）。"""
from ._common import *  # noqa: F401,F403
from typing import Dict, Tuple


class ChemMixin:
    def run_linear_scan(self, reactant_files, product_files, steps=20, method='b3lyp', basis='6-31g*',
                        output_dir=None, preset_name=None, solvent=None, d3=False,
                        charge=0, multiplicity=1, progress_callback=None):
        return psi4_utils.run_linear_scan(
            reactant_files, product_files, steps, method, basis, output_dir,
            preset_name, solvent, d3, charge, multiplicity,
            _progress_callback=progress_callback
        )

    def run_rigid_scan(self, input_file, scan_atoms, distance_range, method='b3lyp', basis='6-31g*',
                       output_dir=None, preset_name=None, solvent=None, d3=False,
                       charge=0, multiplicity=1, progress_callback=None):
        return psi4_utils.run_rigid_scan(
            input_file, scan_atoms, distance_range, method, basis, output_dir,
            preset_name, solvent, d3, charge, multiplicity,
            _progress_callback=progress_callback
        )

    def run_psi4_task(self, input_file, task_type='energy', method='b3lyp', basis='6-31g*',
                      output_dir=None, preset_name=None, solvent=None, d3=False,
                      charge=0, multiplicity=1, progress_callback=None):
        return psi4_utils.run_psi4_task(
            input_file, task_type, method, basis, output_dir, preset_name,
            solvent, d3, charge, multiplicity, _progress_callback=progress_callback
        )

    def convert_file(self, input_path, output_path, output_format):
        return ob_utils.convert_file(input_path, output_path, output_format)

    def generate_from_smiles(self, smiles, output_prefix, generate_3d=True, optimize=True):
        return ob_utils.generate_from_smiles(smiles, output_prefix, str(self.work_dir), generate_3d, optimize)

    def optimize_geometry(self, input_path, output_path, forcefield='mmff94'):
        return ob_utils.optimize_geometry(input_path, output_path, forcefield)

    def calculate_descriptors(self, input_path):
        return ob_utils.calculate_descriptors(input_path)

    def align_molecules(self, ref_path, mobile_path, output_path):
        return ob_utils.align_molecules(ref_path, mobile_path, output_path)

    def render_png_2d(self, input_name, width=800, height=600):
        input_path = (self.work_dir / input_name).resolve()
        preview_dir = (self.work_dir / ".preview").resolve()
        preview_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(input_name).stem
        output_path = (preview_dir / f"{stem}.png").resolve()
        return ob_utils.render_png_2d(str(input_path), str(output_path), width, height)

    def cleanup_stale_previews(self) -> int:
        """删除 .preview 中已无对应源文件的孤立缩略图，避免无限堆积（审计 2.3）。

        返回删除的文件数。.preview 已加入 PROTECTED_DIR_NAMES，不会被扫描/重命名/
        删除逻辑误当作普通文件处理。
        """
        preview_dir = (self.work_dir / ".preview")
        if not preview_dir.is_dir():
            return 0
        removed = 0
        try:
            for png in preview_dir.glob("*.png"):
                stem = png.stem
                if not any((self.work_dir / f"{stem}{e}").exists() for e in SUPPORTED_EXTS):
                    try:
                        png.unlink()
                        removed += 1
                    except OSError:
                        continue
        except OSError:
            pass
        return removed

    def _read_summary_json(self, path: Path) -> dict:
        import json
        try:
            with open(win_longpath(path), encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def collect_results(self) -> list[dict]:
        rows = []
        if not self.work_dir.exists():
            return rows
        for summary_json in self.work_dir.rglob("*_summary.json"):
            # 🔴 T08：rglob 会递归进 .backup / .trash_backup，把快照副本当成
            #    真实计算结果列出来（同一算例出现多份）。这里显式过滤掉。
            try:
                if self._touches_protected(
                    summary_json.relative_to(self.work_dir)
                ):
                    continue
            except (ValueError, OSError):
                if self._touches_protected(summary_json):
                    continue
            try:
                summary_dir = summary_json.parent
                base_with_suffix = summary_json.stem
                base = base_with_suffix[:-len("_summary")] if base_with_suffix.endswith("_summary") else base_with_suffix

                log_path = summary_dir / f"{base}.log"
                fchk_path = summary_dir / f"{base}.fchk"
                optxyz_path = summary_dir / f"{base}_opt.xyz"

                data = self._read_summary_json(summary_json)
                task_type = data.get("task_type", "")
                method = data.get("method", "")
                basis = data.get("basis", "")
                energy = data.get("energy")
                success = data.get("success", False)

                extra = {}
                if log_path.exists():
                    try:
                        extra_data = psi4_utils.parse_psi4_output(str(log_path), task_type)
                        if extra_data:
                            for k, v in extra_data.items():
                                if k not in ("energy", "optimized_xyz") and v is not None:
                                    extra[k] = v
                    except Exception:
                        pass

                row = {
                    "base": base,
                    "task_type": task_type,
                    "method": method,
                    "basis": basis,
                    "energy_Ha": energy,
                    "success": bool(success),
                    "log": str(log_path) if log_path.exists() else "",
                    "fchk": str(fchk_path) if fchk_path.exists() else "",
                    "opt_xyz": str(optxyz_path) if optxyz_path.exists() else "",
                    "summary": str(summary_json),
                    **extra
                }
                try:
                    row["_mtime_ns"] = summary_json.stat().st_mtime_ns
                except OSError:
                    row["_mtime_ns"] = 0
                rows.append(row)
            except Exception:
                continue

        # 审计 UX5 修复：按结果文件（summary.json）修改时间倒序，确保最新计算结果排在顶部
        rows.sort(key=lambda r: r.get("_mtime_ns", 0), reverse=True)
        for r in rows:
            r.pop("_mtime_ns", None)
        return rows

    def compute_deltas(self, rows: list[dict], operation: str) -> list[dict]:
        HA_TO_KJ = 2625.4996
        HA_TO_KCAL = 627.5095
        results = []
        if len(rows) < 2:
            return results

        def get_e(r):
            v = r.get("energy_Ha")
            try:
                return float(v) if v is not None else 0.0
            except (TypeError, ValueError):
                return 0.0

        if operation == "A-B（单分子差）":
            C = rows[0]
            A = rows[1]
            c_base = C.get("base", "C")
            a_base = A.get("base", "A")
            delta_ha = get_e(C) - get_e(A)
            delta_kj = delta_ha * HA_TO_KJ
            delta_kcal = delta_ha * HA_TO_KCAL
            label = f"{c_base} - {a_base}"
            comment = "ΔE = E(C) - E(A)"
            results.append({
                "label": label,
                "delta_Ha": delta_ha,
                "delta_kJ": delta_kj,
                "delta_kcal": delta_kcal,
                "comment": comment
            })

        elif operation == "C - A - B（反应/结合能）":
            if len(rows) >= 3:
                C = rows[0]
                A = rows[1]
                B = rows[2]
                c_base = C.get("base", "C")
                a_base = A.get("base", "A")
                b_base = B.get("base", "B")
                delta_ha = get_e(C) - get_e(A) - get_e(B)
                delta_kj = delta_ha * HA_TO_KJ
                delta_kcal = delta_ha * HA_TO_KCAL
                label = f"{c_base} - {a_base} - {b_base}"
                comment = "ΔE = E(C) - E(A) - E(B)"
                results.append({
                    "label": label,
                    "delta_Ha": delta_ha,
                    "delta_kJ": delta_kj,
                    "delta_kcal": delta_kcal,
                    "comment": comment
                })
        return results

    def _file_signature(self, path: Path) -> Tuple[int, float]:
        try:
            st = path.stat()
            return (st.st_size, st.st_mtime_ns)
        except OSError:
            return (-1, 0.0)

    def compare_directories(self, left: str | Path, right: str | Path) -> dict:
        left_path = Path(left)
        right_path = Path(right)
        left_files: Dict[str, Tuple[int, float]] = {}
        right_files: Dict[str, Tuple[int, float]] = {}
        only_left: list[dict] = []
        only_right: list[dict] = []
        diff_content: list[dict] = []
        if left_path.is_dir():
            for entry in left_path.iterdir():
                if entry.is_file():
                    size, mtime = self._file_signature(entry)
                    left_files[entry.name] = (size, mtime)
        if right_path.is_dir():
            for entry in right_path.iterdir():
                if entry.is_file():
                    size, mtime = self._file_signature(entry)
                    right_files[entry.name] = (size, mtime)
        left_names = set(left_files.keys())
        right_names = set(right_files.keys())
        for name in left_names - right_names:
            size, mtime = left_files[name]
            only_left.append({"name": name, "size": size, "mtime": mtime})
        for name in right_names - left_names:
            size, mtime = right_files[name]
            only_right.append({"name": name, "size": size, "mtime": mtime})
        for name in left_names & right_names:
            ls, lm = left_files[name]
            rs, rm = right_files[name]
            if ls != rs or lm != rm:
                diff_content.append({
                    "name": name,
                    "left_size": ls,
                    "left_mtime": lm,
                    "right_size": rs,
                    "right_mtime": rm
                })
        only_left.sort(key=lambda x: x["name"])
        only_right.sort(key=lambda x: x["name"])
        diff_content.sort(key=lambda x: x["name"])
        return {
            "only_left": only_left,
            "only_right": only_right,
            "diff_content": diff_content
        }

    def copy_from_left_to_right(self, names: list[str], left, right):
        left_path = Path(left)
        right_path = Path(right)
        # 🔴 BUG-4 / BUG-6 守卫：源/目标两侧目录都不得命中受保护目录（.backup/.trash_backup）。
        # 使用 is_protected 双保险——先字符串级（_touches_protected）再 resolve 级
        # （_is_inside_protected，对 NTFS 大小写不敏感，可挡下 .BACKUP/.Backup/.TRASH_BACKUP
        # 等变体），同时 enforce_no_symlink_target 拦下 symlink/junction 穿透，防止覆盖/污染备份。
        if self.is_protected(left) or self.is_protected(right):
            raise ValueError(
                f"同步目录命中受保护目录（.backup/.trash_backup），拒绝操作: {left!r} / {right!r}"
            )
        enforce_no_symlink_target(left)
        enforce_no_symlink_target(right, allow_nonexistent=True)
        right_path.mkdir(parents=True, exist_ok=True)
        success = 0
        errors: list[str] = []
        left_resolved = left_path.resolve(strict=False)
        right_resolved = right_path.resolve(strict=False)
        for name in names:
            try:
                self._strict_basename(name, allow_subdir=False)
                src = left_path / name
                dst = right_path / name
                src_real = src.resolve(strict=True)
                dst_real = dst.parent.resolve(strict=False) / src.name
                src_real.relative_to(left_resolved)
                dst_real.relative_to(right_resolved)
                shutil.copy2(str(src_real), str(dst_real))
                self._log(f"✅ 复制: {name} (左→右)", "success")
                success += 1
            except Exception as e:
                self._log(f"❌ 复制失败 {name}: {e}", "error")
                errors.append(str(e))
        return success, errors

    def copy_from_right_to_left(self, names: list[str], left, right):
        left_path = Path(left)
        right_path = Path(right)
        # 🔴 BUG-4 / BUG-6 守卫：源/目标两侧目录都不得命中受保护目录（.backup/.trash_backup）。
        # 使用 is_protected 双保险——先字符串级（_touches_protected）再 resolve 级
        # （_is_inside_protected，对 NTFS 大小写不敏感，可挡下 .BACKUP/.Backup/.TRASH_BACKUP
        # 等变体），同时 enforce_no_symlink_target 拦下 symlink/junction 穿透，防止覆盖/污染备份。
        if self.is_protected(left) or self.is_protected(right):
            raise ValueError(
                f"同步目录命中受保护目录（.backup/.trash_backup），拒绝操作: {left!r} / {right!r}"
            )
        enforce_no_symlink_target(left)
        enforce_no_symlink_target(right, allow_nonexistent=True)
        left_path.mkdir(parents=True, exist_ok=True)
        success = 0
        errors: list[str] = []
        left_resolved = left_path.resolve(strict=False)
        right_resolved = right_path.resolve(strict=False)
        for name in names:
            try:
                self._strict_basename(name, allow_subdir=False)
                src = right_path / name
                dst = left_path / name
                src_real = src.resolve(strict=True)
                dst_real = dst.parent.resolve(strict=False) / src.name
                src_real.relative_to(right_resolved)
                dst_real.relative_to(left_resolved)
                shutil.copy2(str(src_real), str(dst_real))
                self._log(f"✅ 复制: {name} (右→左)", "success")
                success += 1
            except Exception as e:
                self._log(f"❌ 复制失败 {name}: {e}", "error")
                errors.append(str(e))
        return success, errors

    def sync_overwrite_left_to_right(self, names: list[str], left, right):
        left_path = Path(left)
        right_path = Path(right)
        # 🔴 BUG-4 / BUG-6 守卫：源/目标两侧目录都不得命中受保护目录（.backup/.trash_backup）。
        # 使用 is_protected 双保险——先字符串级（_touches_protected）再 resolve 级
        # （_is_inside_protected，对 NTFS 大小写不敏感，可挡下 .BACKUP/.Backup/.TRASH_BACKUP
        # 等变体），同时 enforce_no_symlink_target 拦下 symlink/junction 穿透，防止覆盖/污染备份。
        if self.is_protected(left) or self.is_protected(right):
            raise ValueError(
                f"同步目录命中受保护目录（.backup/.trash_backup），拒绝操作: {left!r} / {right!r}"
            )
        enforce_no_symlink_target(left)
        enforce_no_symlink_target(right, allow_nonexistent=True)
        right_path.mkdir(parents=True, exist_ok=True)
        success = 0
        errors: list[str] = []
        left_resolved = left_path.resolve(strict=False)
        right_resolved = right_path.resolve(strict=False)
        for name in names:
            try:
                self._strict_basename(name, allow_subdir=False)
                src = left_path / name
                dst = right_path / name
                src_real = src.resolve(strict=True)
                dst_real = dst.parent.resolve(strict=False) / src.name
                src_real.relative_to(left_resolved)
                dst_real.relative_to(right_resolved)
                shutil.copy2(str(src_real), str(dst_real))
                self._log(f"🔁 覆盖: {name} (左→右)", "success")
                success += 1
            except Exception as e:
                self._log(f"❌ 覆盖失败 {name}: {e}", "error")
                errors.append(str(e))
        return success, errors

    def sync_overwrite_right_to_left(self, names: list[str], left, right):
        left_path = Path(left)
        right_path = Path(right)
        # 🔴 BUG-4 / BUG-6 守卫：源/目标两侧目录都不得命中受保护目录（.backup/.trash_backup）。
        # 使用 is_protected 双保险——先字符串级（_touches_protected）再 resolve 级
        # （_is_inside_protected，对 NTFS 大小写不敏感，可挡下 .BACKUP/.Backup/.TRASH_BACKUP
        # 等变体），同时 enforce_no_symlink_target 拦下 symlink/junction 穿透，防止覆盖/污染备份。
        if self.is_protected(left) or self.is_protected(right):
            raise ValueError(
                f"同步目录命中受保护目录（.backup/.trash_backup），拒绝操作: {left!r} / {right!r}"
            )
        enforce_no_symlink_target(left)
        enforce_no_symlink_target(right, allow_nonexistent=True)
        left_path.mkdir(parents=True, exist_ok=True)
        success = 0
        errors: list[str] = []
        left_resolved = left_path.resolve(strict=False)
        right_resolved = right_path.resolve(strict=False)
        for name in names:
            try:
                self._strict_basename(name, allow_subdir=False)
                src = right_path / name
                dst = left_path / name
                src_real = src.resolve(strict=True)
                dst_real = dst.parent.resolve(strict=False) / src.name
                src_real.relative_to(right_resolved)
                dst_real.relative_to(left_resolved)
                shutil.copy2(str(src_real), str(dst_real))
                self._log(f"🔁 覆盖: {name} (右→左)", "success")
                success += 1
            except Exception as e:
                self._log(f"❌ 覆盖失败 {name}: {e}", "error")
                errors.append(str(e))
        return success, errors
