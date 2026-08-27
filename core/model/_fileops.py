"""fileops 子系统 mixin（由原 core/model.py 拆分而来）。"""
from ._common import *  # noqa: F401,F403
from typing import List, Tuple


class FileOpsMixin:
    def _plan_rename(self, file_entry, new_base: str | None, skip_reason: str | None = None):
        if skip_reason is not None:
            return ('skip', skip_reason)
        if new_base is None:
            return ('skip', '未提供新名称')
        try:
            self._strict_basename(f"{new_base}{file_entry.get('ext', '')}")
        except ValueError as exc:
            return ('skip', f"非法的文件名 {new_base!r}: {exc}")
        new_name = f"{new_base}{file_entry['ext']}"
        old_path = self.work_dir / file_entry['name']
        parent = old_path.parent
        new_path = parent / new_name
        try:
            enforce_no_symlink_target(old_path, allow_nonexistent=True, _level="src")
            enforce_no_symlink_target(parent, allow_nonexistent=False, _level="parent")
            # 科学红线 D-05：目标叶子同样要查（若 new_path 已是 symlink/Junction，必须拦下，
            # 否则 rename 会悄悄覆盖/穿透到链接指向的真实文件）。
            enforce_no_symlink_target(new_path, allow_nonexistent=True, _level="dst")
        except ValueError as exc:
            return ('skip', f"检测到符号链接/Junction: {exc}")
        if old_path == new_path:
            return ('skip', None)
        if new_path.exists():
            return ('skip', f"目标文件已存在，跳过: {new_path.name}")
        return ('rename', (file_entry['name'], new_name, str(old_path), str(new_path)))

    def _execute_rename_plan(self, plans, action_label: str, history_type: str,
                             history_desc: str, dry_run: bool,
                             _filtered_changes: list[dict] | None = None):
        if _filtered_changes is not None and len(_filtered_changes) == 0:
            return 0, 0, 0
        _ok_set: set[tuple[str, str]] | None = None
        if _filtered_changes is not None:
            _ok_set = set()
            for c in _filtered_changes:
                _ok_set.add((str(c.get("from", "")), str(c.get("to", ""))))
        success = failed = skipped = 0
        file_pairs = []
        # 科学红线 D-01：原子事务。先逐条执行改名，任一步失败则「整体回滚」（按逆序还原已完成步骤），
        # 绝不允许出现「部分文件已改名、部分未改」的中间态——那会让用户数据处于不可恢复的不一致状态。
        _done_undo = []  # [(new_str, old_str)]，用于失败时反向回滚
        for plan in plans:
            kind, payload = plan
            if kind == 'skip':
                if payload:
                    self._log(f"⚠️ {payload}", 'warning')
                skipped += 1
                continue
            old_display, new_display, old_str, new_str = payload
            if _ok_set is not None and (str(old_display), str(new_display)) not in _ok_set:
                skipped += 1
                continue
            if dry_run:
                self._log(f"[预览] {action_label}: {old_display} -> {new_display}", 'info')
                success += 1
            else:
                try:
                    Path(old_str).rename(new_str)
                    self._log(f"✅ {action_label}: {old_display} -> {new_display}", 'success')
                    _done_undo.append((new_str, old_str))
                    file_pairs.append((old_str, new_str))
                    success += 1
                except Exception as e:
                    # 任意一步失败 → 回滚本轮已完成的全部改名（逆序），保证「要么全改、要么全不改」
                    self._log(f"❌ {action_label}失败 {old_display}: {e}", 'error')
                    for _n, _o in reversed(_done_undo):
                        try:
                            Path(_n).rename(_o)
                            self._log(f"↩️ 已回滚: {_n} -> {_o}", 'warning')
                        except Exception as _re:
                            self._log(f"⚠️ 回滚失败（需手动恢复）: {_n} -> {_o}: {_re}", 'error')
                    failed += 1
                    # 磁盘状态已还原为改名前，因此成功数归零、仅上报一次批次失败
                    return 0, failed, skipped
        if file_pairs:
            # 缓存失效必须与「文件系统已发生改变」这一事实绑定，不能挂在 _add_history 上：
            # 历史被汇聚/抑制时 _add_history 会提前 return，若失效逻辑写在里面，
            # 后续步骤会读到脏的 scan_files 缓存（子目录内改名不会改变根目录 mtime）。
            self.invalidate_scan_cache()
            self._add_history(history_type, file_pairs, history_desc)
        return success, failed, skipped

    def rename_by_mapping(self, dry_run=False, *, _filtered_changes: list[dict] | None = None):
        plans = []
        for f in self.scan_files(ext_filter=list(STRUCTURE_EXTS)):
            if f['status'] != "⏳ 待重命名":
                continue
            eng = f['eng']
            with self._lock:
                chn = self.mapping.get(eng)
            if not chn:
                plans.append(('skip', f"跳过 {f['name']}: 映射中无此英文名 {eng}"))
                continue
            plans.append(self._plan_rename(f, f"{eng}（{chn}）"))
        return self._execute_rename_plan(plans, "重命名", "rename", "映射重命名", dry_run, _filtered_changes)

    def fix_chinese_names(self, dry_run=False, *, _filtered_changes: list[dict] | None = None):
        plans = []
        with self._lock:
            rev = dict(self._reverse_mapping)
        for f in self.scan_files(ext_filter=list(STRUCTURE_EXTS)):
            if f['status'] != "⏳ 纯中文，待修复":
                continue
            base = f['base']
            eng = rev.get(base)
            if not eng:
                plans.append(('skip', f"无法找到对应的英文名: {f['name']}"))
                continue
            plans.append(self._plan_rename(f, f"{eng}（{base}）"))
        return self._execute_rename_plan(plans, "修复", "fix", "修复中文名", dry_run, _filtered_changes)

    def fix_all_names(self, dry_run=False, *, _filtered_changes: list[dict] | None = None):
        plans = []
        with self._lock:
            mapping_snap = dict(self.mapping)
        for f in self.scan_files(ext_filter=list(STRUCTURE_EXTS)):
            correct_name = None
            if f['has_chinese'] or f['status'] == "⏳ 待重命名":
                eng = f['eng']
                if eng in mapping_snap:
                    correct_name = f"{eng}（{mapping_snap[eng]}）"
            if correct_name is None:
                plans.append(('skip', None))
                continue
            plans.append(self._plan_rename(f, correct_name))
        return self._execute_rename_plan(plans, "修正", "rename", "修复命名错误", dry_run, _filtered_changes)

    def fix_incorrect_chinese(self, dry_run=False, *, _filtered_changes: list[dict] | None = None):
        plans = []
        with self._lock:
            mapping_snap = dict(self.mapping)
            rev_snap = dict(self._reverse_mapping)
        for f in self.scan_files(ext_filter=list(STRUCTURE_EXTS)):
            if not f['has_chinese']:
                continue
            eng = f['eng']
            chn_in_file = f['chn']
            correct_base = None
            skip_reason = None
            if eng in mapping_snap:
                correct_chn = mapping_snap[eng]
                if chn_in_file == correct_chn:
                    plans.append(('skip', None))
                    continue
                correct_base = f"{eng}（{correct_chn}）"
            elif chn_in_file in rev_snap:
                correct_base = f"{rev_snap[chn_in_file]}（{chn_in_file}）"
            else:
                skip_reason = (f"无法处理: {f['name']} (英文名 '{eng}' 和中文名 "
                               f"'{chn_in_file}' 均不在映射中)")
            plans.append(self._plan_rename(f, correct_base, skip_reason))
        return self._execute_rename_plan(plans, "修正中文", "rename", "修正中文内容", dry_run, _filtered_changes)

    def fix_all(self, dry_run=False, *, _filtered_changes: list[dict] | None = None):
        results = {}
        # 用「历史汇聚」而非「先抑后取」：
        # 旧实现把 _suppress_history 置 True，导致 4 个子步骤的历史根本没入栈，
        # 随后 while self.history[-1]['description'] in (...) 弹栈合并时捞不到任何东西
        # ——一键修复完全无法撤销；更糟的是它可能误弹出用户之前遗留的同名旧历史。
        outer_sink = getattr(self, '_history_sink', None)
        is_outermost = outer_sink is None
        sink: list = [] if is_outermost else outer_sink
        self._history_sink = sink
        # 预置默认值：任一步骤抛异常时，后续 total 统计不会 UnboundLocalError
        r1 = r2 = r3 = r4 = (0, 0, 0)
        try:
            self._log("🔧 步骤1: 修复纯中文文件名...", 'info')
            r1 = self.fix_chinese_names(dry_run, _filtered_changes=_filtered_changes)
            results['fix_chinese'] = r1
            self._log("🔧 步骤2: 修复命名错误...", 'info')
            r2 = self.fix_all_names(dry_run, _filtered_changes=_filtered_changes)
            results['fix_all'] = r2
            self._log("🔧 步骤3: 修正中文内容...", 'info')
            r3 = self.fix_incorrect_chinese(dry_run, _filtered_changes=_filtered_changes)
            results['fix_content'] = r3
            self._log("🔧 步骤4: 映射重命名...", 'info')
            r4 = self.rename_by_mapping(dry_run, _filtered_changes=_filtered_changes)
            results['rename'] = r4
        finally:
            self._history_sink = outer_sink
            total = sum(r[0] for r in (r1, r2, r3, r4))
            # 放在 finally 中提交：即便某一步骤中途抛异常，
            # 已经真实改名的文件也必须留下可撤销的历史记录。
            if not dry_run and is_outermost and sink:
                self._add_history('fix', list(sink), f"一键修复（{total} 个文件）")

        self._log(f"🎉 一键修复完成！共修复 {total} 个文件", 'success')
        return results

    def supplement_mol(self, progress_callback=None):
        # 🔴 T08：受保护目录不参与补全（iterdir 不递归，此处仅作显式防御）
        files = [
            f for f in self.work_dir.iterdir()
            if f.name not in PROTECTED_DIR_NAMES
            and f.is_file()
            and f.suffix.lower() == '.xyz'
        ]
        total = len(files)
        supplemented = 0
        for idx, xyz in enumerate(files):
            base = xyz.stem
            mol_path = self.work_dir / f"{base}.mol"
            if mol_path.exists():
                continue
            if progress_callback and total > 0:
                progress_callback((idx / total) * 80, f"处理: {xyz.name}")
            try:
                success, _ = ob_utils.convert_file(str(xyz), str(mol_path), 'mol')
                if success:
                    self._log(f"✅ 补全: {mol_path.name} (从 xyz 转换)", 'success')
                    supplemented += 1
                else:
                    self._log(f"❌ 转换失败 {mol_path.name}", 'error')
            except Exception as e:
                self._log(f"❌ 转换异常 {mol_path.name}: {e}", 'error')
        if progress_callback:
            progress_callback(100, "补全完成")
        self._log(f"🎉 补全完成，共 {supplemented} 个 .mol 文件", 'success')
        return supplemented

    def _move_files_with_progress(self, moves, total: int, progress_label: str,
                                  history_desc: str, progress_callback=None,
                                  *,
                                  _filtered_changes: list[dict] | None = None):
        if _filtered_changes is not None and len(_filtered_changes) == 0:
            return 0
        _ok_set: set[tuple[str, str]] | None = None
        if _filtered_changes is not None:
            _ok_set = set()
            for c in _filtered_changes:
                _ok_set.add((str(c.get("from", "")), str(c.get("to", ""))))
        moved = 0
        file_pairs = []
        processed = 0
        wd_resolved = self._work_dir_resolved
        # 🔴 T08：受保护目录（.trash_backup / .backup）的真实路径集合，
        #    源文件在其中、或目标要写进其中，一律拒绝移动。
        protected_roots = {
            (self.work_dir / n).resolve(strict=False) for n in PROTECTED_DIR_NAMES
        }
        trash = (self.work_dir / ".trash_backup").resolve(strict=False)
        for src, dst, display_rel in moves:
            if progress_callback and total > 0:
                progress_callback((processed / total) * 100, f"{progress_label}: {Path(src).name}")
            processed += 1
            src_name = Path(src).name
            if _ok_set is not None and (str(src_name), str(display_rel)) not in _ok_set:
                continue
            # 🔴 T08：先按「名字」快速拒绝——src / dst 路径里只要出现受保护目录段就跳过。
            #    这一层不依赖文件系统状态，即使 resolve 失败也能兜住。
            if self._touches_protected(src) or self._touches_protected(dst):
                self._log(f"⚠️ 跳过受保护的备份目录内容: {src_name}", 'warning')
                continue
            # 审计 1.2 修复：统一通过安全输出路径解析校验落点，
            # 复用 commonpath + 符号链接链检查（不再是手写 relative_to 的差池版本）。
            try:
                dst_path = self.resolve_secure_output_path(
                    dst, allow_outside_work_dir=False, create_parent=True
                )
            except ValueError as _ve:
                self._log(f"⚠️ 拒绝移动 {src_name}: 目标路径非法/越界（{_ve}）", 'warning')
                continue
            try:
                src_real = Path(src).resolve(strict=True)
                # 🔴 T08：resolve 后再查一次——防止 src 通过相对路径/大小写差异绕过名字检查
                if src_real in protected_roots or self._is_inside_protected(src_real):
                    self._log(f"⚠️ 跳过保护目录 {src_name}", 'warning')
                    continue
                if src_real == trash:
                    self._log(f"⚠️ 跳过保护目录 {src_name}", 'warning')
                    continue
            except OSError:
                pass
            try:
                enforce_no_symlink_target(src, allow_nonexistent=False, _level="src")
                dst_parent = Path(dst).parent
                if dst_parent.exists():
                    enforce_no_symlink_target(dst_parent, allow_nonexistent=False, _level="dst-parent")
            except ValueError as _se:
                self._log(f"⚠️ 拒绝移动（存在符号链接/Junction）{src_name}: {_se}", 'warning')
                continue
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            if dst_path.exists():
                self._log(f"⚠️ 跳过 {Path(src).name}: 目标已存在", 'warning')
                continue
            try:
                # 审计 #2 修复：实际移动必须使用已通过 resolve_secure_output_path 校验、
                # 且 mkdir/exists 检查一致的绝对路径 dst_path，而不是原始 str(dst)
                # （str(dst) 在 cwd != work_dir 时会解析到错误位置，造成 cwd 错配 TOCTOU）。
                shutil.move(str(src), str(dst_path))
                self._log(f"📁 移动: {Path(src).name} -> {display_rel}", 'info')
                file_pairs.append((str(src), str(dst_path)))
                moved += 1
            except Exception as e:
                self._log(f"❌ 移动失败 {Path(src).name}: {e}", 'error')
        if file_pairs:
            self._add_history('move', file_pairs, history_desc)
        return moved

    def organize_by_type(self, progress_callback=None, *, _filtered_changes: list[dict] | None = None):
        ext_map = {
            '.mol': 'mol_files',
            '.xyz': 'xyz_files',
            '.sdf': 'sdf_files',
            '.pdb': 'pdb_files',
            '.mol2': 'mol2_files',
            '.cif': 'cif_files',
            '.pdbqt': 'pdbqt_files',
            '.cml': 'cml_files',
            '.fchk': 'fchk_files',
            '.out': 'out_files',
            '.inp': 'inp_files',
        }
        moves = []
        for entry in self.work_dir.iterdir():
            # 🔴 T08：受保护目录（含其内容）不参与整理
            if entry.name in PROTECTED_DIR_NAMES:
                continue
            if not entry.is_file():
                continue
            ext = entry.suffix.lower()
            if ext not in ext_map:
                continue
            try:
                self._strict_basename(ext_map[ext], allow_subdir=False)
            except ValueError as exc:
                self._log(f"⚠️  跳过按类型整理 {entry.name}: 目录名非法（{exc}）", 'warning')
                continue
            dest_dir = self.work_dir / ext_map[ext]
            dst = dest_dir / entry.name
            moves.append((str(entry), str(dst), f"{ext_map[ext]}/{entry.name}"))
        total = len(moves)
        moved = self._move_files_with_progress(
            moves, total, "移动", "按类型整理", progress_callback,
            _filtered_changes=_filtered_changes
        )
        if progress_callback:
            progress_callback(100, "整理完成")
        return moved

    def organize_by_basename(self, progress_callback=None, *, _filtered_changes: list[dict] | None = None):
        groups = {}
        for entry in self.work_dir.iterdir():
            # 🔴 T08：受保护目录（含其内容）不参与分组
            if entry.name in PROTECTED_DIR_NAMES:
                continue
            if not entry.is_file():
                continue
            groups.setdefault(entry.stem, []).append(entry)
        moves = []
        for base, entries in groups.items():
            try:
                self._strict_basename(base, allow_subdir=False)
            except ValueError as exc:
                for entry in entries:
                    self._log(f"⚠️  跳过按 stem 整理 {entry.name}: 目录名非法（{exc}）", 'warning')
                continue
            dest_dir = self.work_dir / base
            for entry in entries:
                dst = dest_dir / entry.name
                moves.append((str(entry), str(dst), f"{base}/{entry.name}"))
        total = len(moves)
        moved = self._move_files_with_progress(
            moves, total, "分组", "按文件名分组", progress_callback,
            _filtered_changes=_filtered_changes
        )
        if progress_callback:
            progress_callback(100, "分组完成")
        return moved

    def prefix_rename(self, prefix, file_list, dry_run=False):
        if not prefix:
            raise ValueError("前缀不能为空")
        if not file_list:
            raise ValueError("文件列表为空")
        has_placeholder = bool(re.search(r'\{[a-zA-Z_]+\}', prefix))
        desc_cache = {}
        date_str = datetime.now().strftime("%Y%m%d")
        renamed = 0
        file_pairs = []

        def _get_desc(path_str):
            if path_str in desc_cache:
                return desc_cache[path_str]
            result = self.calculate_descriptors(path_str)
            desc = {}
            if result and result.get('success') and result.get('descriptors'):
                desc = result['descriptors']
            desc_cache[path_str] = desc
            return desc

        def _fmt_num(val, digits):
            try:
                if val is None or val == '':
                    return 'N/A'
                return f"{round(float(val), digits):.{digits}f}"
            except Exception:
                return 'N/A'

        def _fmt_int(val):
            try:
                if val is None or val == '':
                    return 'N/A'
                return str(int(val))
            except Exception:
                return 'N/A'

        def _render_prefix(f, full_path):
            result = prefix
            if not has_placeholder:
                return result
            desc = _get_desc(str(full_path))
            replacements = {
                'stem': f['base'],
                'ext': f['ext'].lstrip('.'),
                'date': date_str,
                'mw': _fmt_num(desc.get('molecular_weight'), 1),
                'logP': _fmt_num(desc.get('logP'), 2),
                'tpsa': _fmt_num(desc.get('tpsa'), 1),
                'hbd': _fmt_int(desc.get('hbd')),
                'hba': _fmt_int(desc.get('hba')),
                'rotors': _fmt_int(desc.get('rotors')),
                'rings': _fmt_int(desc.get('rings')),
                'atoms': _fmt_int(desc.get('heavy_atoms')),
            }
            for key, val in replacements.items():
                result = result.replace('{' + key + '}', str(val))
            return result

        # 审计 2.4：若含描述符占位符（{mw}/{logP}/...），先在重命名前统一预读所有文件的
        # 描述符并缓存到 desc_cache（ob_utils 层也有 LRU 缓存兜底）。这样：
        # ① 不受支持的格式 / 解析失败会在重命名前暴露，避免「重了一半才报错」；
        # ② 给用户的耗时预期（逐文件串行调用 OpenBabel，与文件数线性相关）。
        # 注意：每个不同文件仍需一次 OpenBabel 调用（无批量描述符 API），此处仅将成本前置并复用缓存。
        if has_placeholder:
            self._log(
                f"正在为 {len(file_list)} 个文件预计算描述符（首次访问需逐文件调用 OpenBabel，"
                "与文件数线性相关，请稍候）…", 'info')
            for f in file_list:
                try:
                    _get_desc(str(self.work_dir / f['name']))
                except Exception as _de:
                    self._log(f"⚠️  预计算描述符失败 {f['name']}: {_de}", 'warning')

        for idx, f in enumerate(sorted(file_list, key=lambda x: x['name']), 1):
            try:
                self._strict_basename(f['name'])
            except ValueError as exc:
                self._log(f"⚠️  跳过 {f['name']}: 原始名称非法（{exc}）", 'warning')
                continue
            old_path = self.work_dir / f['name']
            rendered = _render_prefix(f, old_path)
            if rendered and rendered[-1] not in ('_', '-'):
                rendered += '_'
            base_stem = f"{rendered}{idx:03d}"
            new_name = f"{base_stem}{f['ext']}"
            try:
                self._strict_basename(new_name)
            except ValueError as exc:
                self._log(f"⚠️  跳过 {f['name']}: 生成的新文件名非法（{exc}）", 'warning')
                continue
            new_path = self.work_dir / new_name
            final_new_path = new_path
            if not dry_run:
                counter = 1
                while final_new_path.exists():
                    new_name = f"{base_stem}_{counter}{f['ext']}"
                    try:
                        self._strict_basename(new_name)
                    except ValueError:
                        counter += 1
                        continue
                    final_new_path = self.work_dir / new_name
                    counter += 1
                    if counter > 10000:
                        break
            if final_new_path.exists() and not dry_run:
                self._log(f"⚠️ 跳过 {f['name']}: {new_name} 已存在", 'warning')
                continue
            if dry_run:
                self._log(f"[预览] 重命名 {f['name']} -> {new_name}", 'info')
                renamed += 1
            else:
                try:
                    old_path.rename(final_new_path)
                    self._log(f"✅ 重命名: {f['name']} -> {new_name}", 'success')
                    file_pairs.append((str(old_path), str(final_new_path)))
                    renamed += 1
                except Exception as e:
                    self._log(f"❌ 重命名失败 {f['name']}: {e}", 'error')
        if file_pairs:
            self._add_history('rename', file_pairs, f"前缀重命名 '{prefix}'")
        return renamed

    def _trash_dir(self) -> Path:
        d = self.work_dir / ".trash_backup"
        d.mkdir(exist_ok=True)
        return d

    def delete_files(self, filenames: List[str], *, _filtered_names: List[str] | None = None):
        if not filenames:
            return 0, []
        filenames = list(filenames)
        if _filtered_names is not None:
            allowed = set(_filtered_names)
            filenames = [x for x in filenames if x in allowed]
            if not filenames:
                return 0, []
        trash = self._trash_dir()
        deleted = 0
        errors = []
        file_pairs = []
        wd_resolved = self._work_dir_resolved
        trash_resolved = trash.resolve(strict=False)
        for name in filenames:
            try:
                self._strict_basename(name, allow_subdir=True)
            except ValueError as exc:
                errors.append(f"非法文件名 {name!r}: {exc}")
                continue
            # 🔴 T08：受保护目录（.trash_backup / .backup）内容一律拒绝删除。
            #    先做字符串级判断，再做 resolve 级判断，双保险。
            if self._touches_protected(name):
                errors.append(f"拒绝删除受保护的备份目录内容: {name}")
                continue
            src = self.work_dir / name
            if not src.exists():
                errors.append(f"文件不存在: {name}")
                continue
            try:
                src_real = src.resolve(strict=True)
                src_real.relative_to(wd_resolved)
            except (OSError, ValueError):
                errors.append(f"文件解析后不在工作目录中，拒绝删除: {name}")
                continue
            if self._is_inside_protected(src_real):
                errors.append(f"拒绝删除受保护的备份目录内容: {name}")
                continue
            try:
                if src_real == trash_resolved:
                    errors.append(f"拒绝删除保护目录: {name}")
                    continue
                trash_resolved.relative_to(src_real)
                errors.append(f"拒绝删除回收站保护路径: {name}")
                continue
            except ValueError:
                pass
            try:
                src_rel_tp = src_real.relative_to(trash_resolved)
                errors.append(f"跳过回收站内部文件: {name}")
                continue
            except ValueError:
                pass
            if src.is_symlink() or not src_real.is_file():
                errors.append(f"仅删除工作目录中的真实文件，跳过: {name}")
                continue
            try:
                enforce_no_symlink_target(src, allow_nonexistent=False, _level="src")
                dst_parent = trash / Path(name).parent
                dst = trash / name
                dst_parent.mkdir(parents=True, exist_ok=True)
                if dst_parent.exists():
                    enforce_no_symlink_target(dst_parent, allow_nonexistent=False, _level="trash-parent")
            except ValueError as _se:
                errors.append(f"检测到符号链接/Junction，拒绝删除: {name} ({_se})")
                continue
            dst = trash / name
            counter = 1
            # 上限保护：回收站同名冲突极多、或文件系统异常导致 exists() 恒真时，
            # 无上限自增会让 UI 线程死循环卡死。超过上限直接跳过该文件并报错。
            _MAX_TRASH_SUFFIX = 10000
            while dst.exists():
                if counter > _MAX_TRASH_SUFFIX:
                    dst = None
                    break
                stem, ext = src.stem, src.suffix
                name_as_path = Path(name)
                new_name = name_as_path.parent / f"{stem}_{counter}{ext}"
                dst = trash / new_name
                counter += 1
            if dst is None:
                errors.append(
                    f"删除失败 {name}: 回收站中同名文件超过 {_MAX_TRASH_SUFFIX} 个，"
                    f"请先清理 .trash_backup 目录"
                )
                continue
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
            except OSError as _e_mk:
                errors.append(f"无法在回收站建目录 {os.fspath(dst.parent)!r}: {_e_mk}")
                continue
            try:
                shutil.move(str(src), str(dst))
                self._log(f"🗑️ 删除（已备份）: {name}", 'info')
                file_pairs.append((str(src), str(dst)))
                deleted += 1
            except Exception as e:
                errors.append(f"删除失败 {name}: {e}")
        if file_pairs:
            self._add_history('delete', file_pairs, f"删除文件 ({deleted} 个)")
        return deleted, errors

    def remove_duplicate_files(self, ext_list=None, progress_callback=None):
        if ext_list is None:
            ext_list = list(SUPPORTED_EXTS)
        # 🔴 T08：受保护目录（.trash_backup / .backup）不参与去重删除。
        #    iterdir 本身不递归，但显式挡一道，避免将来改成 rglob 时留坑。
        files_to_check = [
            p for p in self.work_dir.iterdir()
            if p.name not in PROTECTED_DIR_NAMES
            and p.is_file()
            and p.suffix.lower() in ext_list
        ]
        if not files_to_check:
            self._log("📂 没有找到需要检查的文件", 'info')
            return 0, []
        hash_map = {}
        errors = []
        total = len(files_to_check)
        for idx, path in enumerate(files_to_check):
            if progress_callback and total > 0:
                progress_callback((idx / total) * 80, f"扫描: {path.name}")
            try:
                with open(win_longpath(path), 'rb') as f:
                    file_hash = hashlib.md5(f.read()).hexdigest()
                hash_map.setdefault(file_hash, []).append(str(path))
            except Exception as e:
                errors.append(f"无法读取 {path.name}: {e}")
        duplicates_found = 0
        deleted = 0
        for hash_val, file_list in hash_map.items():
            if len(file_list) <= 1:
                continue
            duplicates_found += len(file_list) - 1
            file_list.sort()
            for path in file_list[1:]:
                try:
                    Path(path).unlink()
                    self._log(f"🗑️ 删除重复文件: {Path(path).name}", 'info')
                    deleted += 1
                except Exception as e:
                    errors.append(f"删除失败 {Path(path).name}: {e}")
        if progress_callback:
            progress_callback(100, "清理完成")
        self._log(f"✅ 重复文件清理完成：发现 {duplicates_found} 个重复副本，已删除 {deleted} 个", 'success')
        self.invalidate_scan_cache()
        return deleted, errors

    def _resolve_import_target_dir(self, target_dir=None) -> Path:
        """
        解析并校验导入落地目录。**必须**满足三条硬约束（T18 验收项）：

          1. 落在工作目录内（含工作目录本身），禁止把外部文件导到工作目录之外；
          2. 相对工作目录的任一层都不得命中 ``PROTECTED_DIR_NAMES``
             —— 导入的文件永远不能落进 ``.backup`` / ``.trash_backup``，
             否则备份区会被用户数据污染，回滚时反而覆盖真数据；
          3. 目录不存在则自动创建。

        抛出 ValueError 表示目标非法（调用方应把错误如实告诉用户）。
        """
        wd = Path(self.work_dir)
        raw = Path(target_dir) if target_dir else wd
        if not raw.is_absolute():
            raw = wd / raw
        try:
            dest = raw.resolve()
        except OSError:
            dest = raw
        # --- 约束 1：必须在工作目录内 ---
        if dest != wd and wd not in dest.parents:
            raise ValueError(f"导入目标目录必须位于工作目录内: {dest}")
        # --- 约束 2：不得落进受保护目录 ---
        if dest != wd:
            try:
                rel = dest.relative_to(wd)
            except ValueError:
                raise ValueError(f"无法解析导入目标的相对路径: {dest}")
            if is_protected_relpath(str(rel)):
                raise ValueError(f"禁止把文件导入受保护目录: {dest}")
        # --- 约束 3：不存在则创建 ---
        dest.mkdir(parents=True, exist_ok=True)
        return dest

    def _unique_target_path(self, target_dir: Path, name: str) -> Path:
        """同名冲突时生成 ``xxx (1).ext`` / ``xxx (2).ext``，绝不静默覆盖用户已有文件。"""
        stem = Path(name).stem
        suffix = Path(name).suffix
        candidate = target_dir / name
        idx = 1
        while candidate.exists():
            candidate = target_dir / f"{stem} ({idx}){suffix}"
            idx += 1
            if idx > 9999:
                raise OSError(f"无法为 {name} 生成不冲突的文件名（已尝试 9999 次）")
        return candidate

    def import_external_files(self, paths, *, target_dir=None, mode: str = "copy",
                              overwrite: bool = False, progress_callback=None) -> dict:
        """
        把外部文件导入工作目录（F06 拖放导入 / 菜单兜底导入的**唯一**落地实现）。

        约定与安全边界：
          - 只处理**文件**；目录的递归展开由 `core.drop_handler` 负责（本方法不做遍历）；
          - 拒绝 symlink / Windows junction 源（避免跟随到外部目录）；
          - 落地目录经 `_resolve_import_target_dir` 校验，永不落进 ``.backup`` / ``.trash_backup``；
          - 同名默认改名（``xxx (1).ext``），`overwrite=True` 时才覆盖，且覆盖前打 ``export`` 快照；
          - 单个文件失败不中断整批，最后统一汇总返回。

        撤销语义（与 `undo_last` 严格对齐）：
          - ``mode="copy"`` → 历史类型记为 ``'import'``，撤销 = 删除工作目录里的副本
            （外部原件仍在，删副本不会丢数据）；
          - ``mode="move"`` → 历史类型复用既有的 ``'move'``，撤销 = 搬回原位置。

        参数:
            paths:             文件路径可迭代对象（str / Path 混合均可）。
            target_dir:        落地目录，默认工作目录根。
            mode:              ``"copy"``（默认，安全）或 ``"move"``。
            overwrite:         同名是否覆盖，默认 False（改名）。
            progress_callback: ``(percent: float, message: str)``，可为 None。

        返回:
            ``{'imported': [(src, dst)…], 'skipped': [str…], 'errors': [str…],
               'count': int, 'target_dir': str, 'mode': str}``
        """
        mode = str(mode or "copy").lower()
        if mode not in ("copy", "move"):
            raise ValueError(f"不支持的导入模式: {mode!r}（仅支持 'copy' / 'move'）")

        dest_root = self._resolve_import_target_dir(target_dir)
        try:
            items = [Path(p) for p in (paths or [])]
        except TypeError:
            raise ValueError("paths 必须是可迭代的路径集合")

        imported: List[Tuple[str, str]] = []
        skipped: List[str] = []
        errors: List[str] = []
        total = len(items)

        for idx, src in enumerate(items):
            if progress_callback and total > 0:
                try:
                    progress_callback((idx / total) * 100.0, f"导入: {src.name}")
                except InterruptedError:
                    raise
                except Exception:
                    pass
            try:
                if not src.exists():
                    skipped.append(f"{src.name}：文件不存在")
                    continue
                if not src.is_file():
                    skipped.append(f"{src.name}：不是文件")
                    continue
                # 🔴 BUG-3：原仅查叶子（is_symlink/is_windows_junction），漏掉祖先链 junction，
                # 也漏掉「源文件位于受保护目录（.backup/.trash_backup）」的情况。
                # 改为：① 字符串级命中受保护目录即拒；② enforce_no_symlink_target 逐级查祖先链。
                try:
                    src_resolved = src.resolve()
                except OSError:
                    src_resolved = src
                if self._touches_protected(src) or self._is_inside_protected(src_resolved):
                    skipped.append(f"{src.name}：位于受保护目录（.backup/.trash_backup），已拒绝")
                    continue
                try:
                    enforce_no_symlink_target(src)
                except ValueError:
                    skipped.append(f"{src.name}：符号链接 / junction，已拒绝")
                    continue
                # 已经在落地目录里的文件不用导（自己拷自己会清空文件）
                if src_resolved.parent == dest_root:
                    skipped.append(f"{src.name}：已在目标目录中")
                    continue

                if overwrite:
                    dst = dest_root / src.name
                    if dst.exists():
                        # F17：覆盖用户已有文件前先快照（失败只警告，不阻断）
                        self.create_backup_snapshot(
                            "export", [dst], f"导入覆盖 {dst.name} 前的自动快照"
                        )
                else:
                    dst = self._unique_target_path(dest_root, src.name)

                if mode == "copy":
                    shutil.copy2(src_resolved, dst)
                else:
                    shutil.move(str(src_resolved), str(dst))
                imported.append((str(src_resolved), str(dst)))
                self._log(
                    f"📥 已导入: {src.name}"
                    + (f" → {dst.name}" if dst.name != src.name else ""),
                    'info',
                )
            except InterruptedError:
                raise
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{src.name}: {exc}")
                self._log(f"❌ 导入失败 {src.name}: {exc}", 'error')

        if progress_callback:
            try:
                progress_callback(100.0, "导入完成")
            except InterruptedError:
                raise
            except Exception:
                pass

        # 写历史：copy → 'import'（撤销=删副本）；move → 'move'（复用既有撤销逻辑）
        if imported:
            op_type = 'import' if mode == 'copy' else 'move'
            self._add_history(
                op_type, imported,
                f"{'导入' if mode == 'copy' else '移入'} {len(imported)} 个外部文件",
            )
        self.invalidate_scan_cache()
        self._log(
            f"✅ 导入完成：成功 {len(imported)} 个，跳过 {len(skipped)} 个，失败 {len(errors)} 个",
            'success' if not errors else 'warning',
        )
        return {
            'imported': imported,
            'skipped': skipped,
            'errors': errors,
            'count': len(imported),
            'target_dir': str(dest_root),
            'mode': mode,
        }
