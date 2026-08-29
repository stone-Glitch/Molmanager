"""scan 子系统 mixin（由原 core/model.py 拆分而来）。"""
from typing import List, Tuple

from ._common import *  # noqa: F401,F403


class ScanMixin:
    def filter_files(self, entries: list[dict], keyword: str="", status: str="全部", ext: str="全部") -> list[dict]:
        # E-04：化学感知搜索。含已知 key: 前缀（mw:/formula:/logP:…）时走化学查询分支，
        # 惰性按文件算描述符并富集 entry 后再过滤；无前缀时行为与历史完全一致（向后兼容）。
        if keyword and looks_like_chem_query(keyword):
            try:
                return self._filter_files_chem(entries, keyword, status, ext)
            except Exception as _ce:
                # 化学分支异常（如描述符计算全失败）一律回退到原逻辑，保证搜索框永不死（零回归）。
                logger.debug("化学查询分支异常，回退原逻辑: %s", _ce)
        result = entries
        if keyword:
            kw = keyword.lower()
            result = [
                e for e in result
                if kw in str(e.get('name', '')).lower()
                or kw in str(e.get('base', '')).lower()
                or kw in str(e.get('eng', '')).lower()
                or kw in str(e.get('chn', '')).lower()
            ]
        if status != "全部":
            result = [e for e in result if e.get('status') == status]
        if ext != "全部":
            target = "." + ext.lower()
            result = [e for e in result if e.get('ext', '').lower() == target]
        return result

    def _filter_files_chem(self, entries: list[dict], keyword: str, status: str, ext: str) -> list[dict]:
        """化学查询分支：惰性算描述符 → 富集 entry → 应用化学条件 + 自由文本 + status/ext。

        红线：描述符缺失/解析失败的条目，其 mw/formula 等字段不会被伪造，
        针对这些字段的条件会安全判定为 False（被排除），绝不产生假阳性命中。
        """
        from utils.chem_query import match_entry, matches_free_text, parse_chem_query

        conditions, free_terms = parse_chem_query(keyword)
        desc_cache: dict = {}

        def _enrich(e: dict) -> dict:
            path = self.work_dir / e.get('name', '')
            key = str(path)
            d = desc_cache.get(key)
            if d is None:
                d = {}
                try:
                    res = self.calculate_descriptors(key)
                    if res and res.get('success') and res.get('descriptors'):
                        d = res['descriptors']
                except Exception as _de:
                    logger.debug("富集描述符失败 %s: %s", key, _de)
                desc_cache[key] = d
            if not d:
                return e
            # 浅拷贝，避免污染缓存的扫描 entry（scan_files 结果可能被别处复用）
            e = dict(e)
            for src, dst in (
                ("molecular_weight", "mw"), ("mw", "mw"),
                ("formula", "formula"), ("molecular_formula", "formula"),
                ("logP", "logP"), ("logp", "logP"), ("xlogp", "logP"),
                ("heavy_atoms", "heavy"), ("heavy", "heavy"),
                ("atoms", "atoms"), ("natoms", "atoms"), ("num_atoms", "atoms"),
                ("rotors", "rotors"), ("rotatable_bonds", "rotors"),
            ):
                v = d.get(src)
                if v not in (None, "", "N/A"):
                    e[dst] = v
            return e

        enriched = [_enrich(e) for e in entries]
        result = [e for e in enriched if match_entry(e, conditions) and matches_free_text(e, free_terms)]
        if status != "全部":
            result = [e for e in result if e.get('status') == status]
        if ext != "全部":
            target = "." + ext.lower()
            result = [e for e in result if e.get('ext', '').lower() == target]
        return result

    def _compute_tree_signature(self, wd: Path) -> bytes:
        """递归目录签名：收集每个目录的相对路径与其 mtime_ns 后做内容哈希。

        原实现只用工作目录根自身的 mtime_ns 作缓存键；但「在深层子目录内增删文件」
        只更新对应子目录的 mtime、根目录 mtime 不变 → 缓存返回陈旧文件列表（审计 3.1）。
        这里把整棵目录树的目录 mtime 纳入键：任意子目录内的增删都会改变签名，
        从而正确失效缓存。仅遍历目录（不检查文件内容），比完整文件扫描便宜，
        且足以覆盖变更检测。
        """
        h = hashlib.md5()
        wd_s = os.fspath(wd)
        # 🔴 修复缓存正确性 bug：原实现只哈希「子目录」mtime，漏掉工作根目录自身的
        # mtime。后果：直接在根目录增删文件不会改变签名 → 缓存返回陈旧文件列表
        # （验证脚本 4b 即在根目录新增强发现象）。这里把根目录自身 mtime 纳入键。
        try:
            root_mtime = os.stat(wd_s, follow_symlinks=False).st_mtime_ns
        except OSError:
            root_mtime = 0
        h.update(b"ROOT")
        h.update(str(root_mtime).encode("ascii"))
        h.update(b"\x00")
        stack = [wd_s]
        while stack:
            d = stack.pop()
            try:
                with os.scandir(d) as it:
                    for entry in it:
                        if entry.is_dir(follow_symlinks=False):
                            try:
                                mtime_ns = entry.stat(follow_symlinks=False).st_mtime_ns
                            except OSError:
                                mtime_ns = 0
                            rel = os.path.relpath(entry.path, os.fspath(wd)).replace(os.sep, "/")
                            h.update(rel.encode("utf-8"))
                            h.update(str(mtime_ns).encode("ascii"))
                            h.update(b"\x00")
                            stack.append(entry.path)
            except (PermissionError, OSError):
                continue
        return h.digest()

    def scan_files(self, ext_filter=None):
        wd = self.work_dir
        if not wd.exists():
            raise FileNotFoundError(f"工作目录不存在: {wd}")
        if ext_filter is None:
            ext_filter = list(SUPPORTED_EXTS)
        ext_filter = tuple(e.lower() if e.startswith('.') else '.' + e.lower() for e in ext_filter)

        # 审计 3.1：缓存键从「仅根目录 mtime」改为「递归目录树签名」。
        # 否则在深层子目录内增删文件不会改变根目录 mtime，缓存会返回陈旧列表。
        sig = self._compute_tree_signature(wd)

        with self._lock:
            cached = self._scan_cache
            rev = self._scan_cache_revision
            if cached and len(cached) >= 4 and cached[0] == sig and cached[1] == ext_filter and cached[2] == rev:
                return cached[3]

        # 复制映射快照，避免长时间持锁
        with self._lock:
            mapping_snapshot = dict(self.mapping)
            reverse_snapshot = dict(self._reverse_mapping)
        # 🔴 修复映射匹配大小写敏感：文件名 `benzene（苯）` vs 映射键 `Benzene`
        # 原 `eng in mapping_snapshot` 区分大小写 → 误判「❌ 无映射」。
        # 建立一份小写键索引，仅用于查找，不影响内存中的原始映射（反向映射同理）。
        mapping_lower = {str(k).lower(): v for k, v in mapping_snapshot.items()}

        result: list[dict] = []
        # 🔴 T08：排除名单从单一 ".trash_backup" 扩为 PROTECTED_DIR_NAMES，
        #    新增 ".backup"（F17 快照根目录）。少了这一行，备份副本会出现在
        #    文件列表里，被「整理 / 重命名 / 删除」当成普通文件处理。
        protected_dir_names = PROTECTED_DIR_NAMES
        ext_set = frozenset(ext_filter)
        root_str = os.fspath(wd)

        # 性能优化：os.scandir 的 DirEntry 自带缓存的 d_type，
        # is_dir/is_file(follow_symlinks=False) 在常见情况下无需额外 stat 系统调用；
        # 同时先用后缀字符串过滤、再判断文件类型，避免对无关文件做无谓检查。
        # 相比 wd.rglob('*') + entry.is_file()（每个条目都 stat 一次），
        # 遍历大目录树时 syscalls 数量从 O(总条目数) 降为 O(目录数 + 命中文件数)。
        # 注：follow_symlinks=False 不进入符号链接目录，避免递归死循环/越界，
        # 与本项目「拒绝 symlink」的安全策略一致。
        stack = [root_str]
        while stack:
            dir_path = stack.pop()
            try:
                with os.scandir(dir_path) as it:
                    for entry in it:
                        name = entry.name
                        if name in protected_dir_names:
                            # 受保护目录整棵子树都不入栈，连带其中的文件一并隐身
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                            continue
                        ext = os.path.splitext(name)[1].lower()
                        if ext not in ext_set:
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        rel = os.path.relpath(entry.path, root_str).replace(os.sep, '/')
                        base = os.path.splitext(name)[0]
                        has_chinese = '（' in base and '）' in base
                        if has_chinese:
                            eng, chn = base.split('（', 1)
                            chn = chn.rstrip('）')
                        else:
                            eng, chn = base, ''

                        if ext in STRUCTURE_EXTS:
                            eng_key = str(eng).lower()
                            if eng_key in mapping_lower:
                                mapped_chn = mapping_lower[eng_key]
                                status = "✅ 已正确命名" if (has_chinese and chn == mapped_chn) else "⏳ 待重命名"
                            elif base in reverse_snapshot:
                                status = "⏳ 纯中文，待修复"
                            else:
                                status = "❌ 无映射"
                            mapped_chn_out = mapping_lower.get(eng_key, '')
                        else:
                            status = "📄 计算文件"
                            mapped_chn_out = ''

                        result.append({
                            'name': rel,
                            'base': base,
                            'ext': ext,
                            'eng': eng,
                            'chn': chn,
                            'has_chinese': has_chinese,
                            'status': status,
                            'mapped_chn': mapped_chn_out,
                        })
            except (PermissionError, OSError):
                # 跳过无权限访问的子目录，避免单次扫描整体失败
                continue
        result.sort(key=lambda x: x['name'])

        if sig:
            with self._lock:
                self._scan_cache = (sig, ext_filter, rev, result)
        self.cleanup_stale_previews()
        return result

    def generate_missing_list(self):
        files = self.scan_files(ext_filter=list(STRUCTURE_EXTS))
        missing = set()
        for f in files:
            if f['status'] == "❌ 无映射":
                if f['eng']:
                    missing.add(f['eng'])
        missing = sorted(missing)
        if missing:
            out_file = self.work_dir / "missing_eng_names.txt"
            with open(win_longpath(out_file), 'w', encoding='utf-8') as f:
                f.write("英文名\n")
                for name in missing:
                    f.write(f"{name}\n")
            self._log(f"📋 缺失列表已保存: {out_file} (共 {len(missing)} 个)", 'info')
        else:
            self._log("🎉 所有 .mol/.xyz 文件均有映射", 'success')
        return missing

    def export_missing_csv(self, csv_path: str) -> int:
        # 安全路径校验
        safe_path = self.resolve_secure_output_path(csv_path, create_parent=True)
        # F17：覆盖既有导出产物前先快照（文件不存在时 create_snapshot 自动跳过）
        self.create_backup_snapshot("export", [safe_path], "导出缺失映射表前的自动快照")
        missing_eng = self.generate_missing_list()
        if isinstance(missing_eng, dict):
            missing_list = list(missing_eng.keys())
        elif isinstance(missing_eng, (list, tuple, set)):
            missing_list = list(missing_eng)
        else:
            missing_list = []
        with open(win_longpath(safe_path), 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=['english', 'chinese'])
            writer.writeheader()
            for eng in missing_list:
                writer.writerow({'english': eng, 'chinese': ''})
        return len(missing_list)

    def export_mapping_csv(self, csv_path: str) -> int:
        """
        导出**当前完整映射表**为 CSV（列：english, chinese）。

        🔴 热修复：`ui/dialogs/mapping_dialog.py` 的「📤 导出当前映射表」按钮
        一直在调这个方法，但 model 侧从未实现 —— 用户一点就 AttributeError。
        这里补齐，并对齐 `export_missing_csv` 的三条既有约定：

          1. 路径过 `resolve_secure_output_path`（禁 ``..``、禁越出工作目录、
             禁 symlink/junction 目标）；
          2. 覆盖既有导出产物前先打 ``export`` 快照（F17，文件不存在时自动跳过）；
          3. 用 ``utf-8-sig`` 写，保证 Excel 直接双击打开中文不乱码。

        参数:
            csv_path: 目标 CSV 路径（相对路径以工作目录为根）。

        返回:
            实际写出的映射条数。

        抛出:
            ValueError / OSError —— 路径非法或写盘失败时抛给调用方（导出失败必须让用户知道）。
        """
        safe_path = self.resolve_secure_output_path(csv_path, create_parent=True)
        # F17：覆盖既有导出产物前先快照（失败只警告，不阻断导出）
        self.create_backup_snapshot("export", [safe_path], "导出完整映射表前的自动快照")
        with self._lock:
            rows: List[Tuple[str, str]] = sorted(
                self.mapping.items(), key=lambda kv: str(kv[0]).lower()
            )
        with open(win_longpath(safe_path), 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=['english', 'chinese'])
            writer.writeheader()
            for eng, chn in rows:
                writer.writerow({'english': eng, 'chinese': chn})
        self._log(f"📤 已导出完整映射表：{len(rows)} 条 → {Path(safe_path).name}", 'success')
        return len(rows)

    def import_mapping_csv(self, csv_path: str, overwrite: bool=False) -> dict:
        # 导入是「读取」操作：允许用户从任意位置选取映射 CSV（与 load_mapping_file 行为一致）。
        # 仅放宽工作目录限制；resolve_secure_output_path 内部的 symlink/junction 检查仍生效。
        safe_path = self.resolve_secure_output_path(csv_path, create_parent=False, allow_outside_work_dir=True)
        added = 0
        skipped = 0
        errors = 0
        total_rows = 0
        chn_conflicts = []   # 科学红线 S-06：中文名冲突（同一中文名被多个英文名共用 → 反向映射塌缩）
        # 本次导入批次内已见过的中文名 → 英文名，用于检测「文件内部」的中文名冲突
        batch_chn_seen = {}
        with open(win_longpath(safe_path), encoding='utf-8-sig', newline='') as f:
            # 🔴 D-03 修复：自动检测分隔符（TSV 制表符 / CSV 逗号）。
            # 旧实现硬编码 csv.DictReader 默认逗号，用户用「导入」选了 TSV 时，
            # 整行被当成单列 → english/chinese 取不到 → 全部 skipped → 静默导入 0 条（丢数据）。
            _head = f.readline()
            f.seek(0)
            if '\t' in _head:
                _delim = '\t'
            else:
                # 退化为 Sniffer（兼容分号等罕见分隔符），失败则回退逗号
                try:
                    _delim = csv.Sniffer().sniff(_head, delimiters=",;\t").delimiter
                except Exception:
                    _delim = ','
            reader = csv.DictReader(f, delimiter=_delim)
            for row in reader:
                total_rows += 1
                try:
                    eng = row.get('english', '').strip()
                    chn = row.get('chinese', '').strip()
                    if not eng or not chn:
                        continue
                    if not overwrite and eng in self.mapping:
                        skipped += 1
                    else:
                        with self._lock:
                            # 在写入前检查：该中文名是否已被「另一个」英文名占用（反向映射冲突）。
                            # 既要看已有的反向映射，也要看本次批次内刚写入的中文名。
                            _existing_eng = self._reverse_mapping.get(chn)
                            if _existing_eng is not None and _existing_eng != eng:
                                chn_conflicts.append((chn, _existing_eng, eng))
                            elif chn in batch_chn_seen and batch_chn_seen[chn] != eng:
                                chn_conflicts.append((chn, batch_chn_seen[chn], eng))
                            else:
                                batch_chn_seen[chn] = eng
                            self.mapping[eng] = chn
                            added += 1
                except Exception:
                    errors += 1
        with self._lock:
            self._reverse_mapping = {v: k for k, v in self.mapping.items()}
            self.invalidate_scan_cache()
        return {
            "added": added,
            "skipped": skipped,
            "errors": errors,
            "total_rows": total_rows,
            "dup_chn": len(chn_conflicts),
            "chn_conflicts": chn_conflicts,
        }

    def _strict_basename(self, name: str, allow_subdir: bool = False) -> str:
        """严格校验文件名，防止路径穿越。"""
        if not isinstance(name, str) or not name:
            raise ValueError("文件名不能为空")
        if any(ch in name for ch in ("\x00", "\r", "\n")):
            raise ValueError(f"文件名包含非法控制字符: {name!r}")
        _DANGEROUS_CHARS: tuple[str, ...] = ("<", ">", ":", '"', "|", "?", "*")
        for ch in _DANGEROUS_CHARS:
            if ch in name:
                raise ValueError(f"文件名包含非法字符 {ch!r}: {name!r}")
        if Path(name).is_absolute():
            raise ValueError(f"仅接受文件名或相对子目录，禁止绝对路径: {name!r}")
        raw_segs = []
        for ch in ("/", "\\"):
            if ch in name:
                raw_segs = name.replace("\\", "/").split("/")
                break
        else:
            raw_segs = [name]
        if any(seg == ".." for seg in raw_segs):
            raise ValueError(f"文件名不能包含 '..' 段（禁止向上穿越）: {name!r}")
        norm = os.path.normpath(name)
        if norm in ("", "."):
            raise ValueError(f"无效的文件名: {name!r}")
        parts = Path(norm).parts
        if not parts:
            raise ValueError(f"无效的文件名: {name!r}")
        if any(p == ".." for p in parts):
            raise ValueError(f"文件名不能包含 '..' 段（禁止向上穿越）: {name!r}")
        if any(p == "." for p in parts):
            raise ValueError(f"文件名段不能为 '.': {name!r}")
        if not allow_subdir and len(parts) != 1:
            raise ValueError(f"仅接受单级文件名，禁止子目录: {name!r}")
        _WIN_RESERVED: frozenset[str] = frozenset({
            "CON", "PRN", "AUX", "NUL",
            "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
            "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
        })
        for seg in parts:
            seg_stripped = seg.split(".", 1)[0].strip().rstrip(".").strip()
            if seg_stripped.upper() in _WIN_RESERVED:
                raise ValueError(f"文件名包含 Windows 保留名，禁止使用: {seg!r}")
            if seg.endswith((" ", ".")) and seg not in (".", ".."):
                raise ValueError(f"文件/目录段禁止以空格或点结尾: {seg!r}")
        if allow_subdir:
            wd_resolved = self._work_dir_resolved
            wd_norm = os.path.normpath(os.fspath(wd_resolved))
            candidate_norm = os.path.normpath(os.fspath(self.work_dir / norm))
            ok_by_norm = False
            try:
                common = os.path.commonpath([wd_norm, candidate_norm])
                ok_by_norm = os.path.normcase(common) == os.path.normcase(wd_norm)
            except (ValueError, OSError):
                ok_by_norm = False
            try:
                raw_cand = self.work_dir / norm
                if raw_cand.exists() or raw_cand.parent.exists():
                    candidate = raw_cand.resolve(strict=False)
                else:
                    candidate = Path(candidate_norm)
                candidate.relative_to(wd_resolved)
            except (OSError, ValueError) as exc:
                if not ok_by_norm:
                    raise ValueError(f"解析后位置超出工作目录范围: {name!r}") from exc
                raise ValueError(f"解析后（含软连接）位置超出工作目录范围: {name!r}") from exc
        return name

    def resolve_secure_output_path(
        self,
        requested_path: str | bytes | os.PathLike | None,
        *,
        is_dir: bool = False,
        default_name: str | None = None,
        base_dir: str | bytes | os.PathLike | None = None,
        allow_outside_work_dir: bool = False,
        create_parent: bool = False,
    ) -> Path:
        if base_dir is None:
            base_dir_resolved = self._work_dir_resolved
        else:
            try:
                base_dir_resolved = Path(base_dir).resolve(strict=True)
            except (OSError, ValueError) as _exc:
                raise ValueError(f"base_dir 无法解析（必须是已存在的目录）: {base_dir!r}") from _exc

        raw: str
        if requested_path is None:
            raw = ""
        elif isinstance(requested_path, bytes):
            raw = requested_path.decode("utf-8", "replace")
        else:
            raw = os.fspath(requested_path)
        raw = raw.strip() if isinstance(raw, str) else ""
        if not raw and default_name:
            raw = str(default_name)
        if not raw:
            raise ValueError("输出路径为空且未提供 default_name")

        raw_slashed = raw.replace("\\", "/")
        raw_segs = [s for s in raw_slashed.split("/") if s != ""]
        if any(seg == ".." for seg in raw_segs):
            raise ValueError(f"输出路径禁止包含 '..' 段: {raw!r}")

        p = Path(raw)
        if not p.is_absolute():
            p = base_dir_resolved / p

        try:
            norm_abs = os.path.normpath(os.fspath(p))
            base_norm = os.path.normpath(os.fspath(base_dir_resolved))
            if not allow_outside_work_dir:
                # 规范化到真实路径：展开 Windows 8.3 短名（如 LVDOUZ~1 → lvdouzhijia82）、
                # 统一大小写与分隔符，避免同一目录因短名/长名不一致被 commonpath 误判为「越界」。
                # 例：本机 TEMP 为 C:\Users\LVDOUZ~1，而 base_dir 经 resolve() 展开为长名，
                # 若不统一规范化会错误拒绝工作目录内合法路径。
                norm_abs_real = os.path.realpath(norm_abs)
                base_norm_real = os.path.realpath(base_norm)
                common = os.path.commonpath([base_norm_real, norm_abs_real])
                if os.path.normcase(common) != os.path.normcase(base_norm_real):
                    raise ValueError(
                        f"输出路径越出允许范围（commonpath 判定）：请求 {norm_abs!r}，允许根 {base_norm!r}"
                    )
        except (OSError, ValueError) as exc:
            if isinstance(exc, ValueError):
                raise
            raise ValueError(f"输出路径规范化失败: {raw!r}") from exc

        candidate_norm = Path(norm_abs)

        def _check_chain_up_to(target: Path, base: Path) -> None:
            # 🔴 BUG-5：绝不能用 resolve()（会把 junction 中间层折叠掉，使其从 parts 消失），
            # 必须用 absolute()（不跟随 symlink/junction），与 enforce_no_symlink_target 一致。
            try:
                rel = target.absolute().relative_to(base.absolute())
                parts_a = list(rel.parts)
            except (OSError, ValueError):
                parts_a = list(target.parts)
            cur = base
            for part in parts_a:
                cur = cur / part
                if not cur.exists():
                    continue
                enforce_no_symlink_target(cur, allow_nonexistent=True, _level="chain")
            # 🔴 BUG-5：叶子（输出文件本身）即使尚不存在也要检查其祖先链，否则 allow_nonexistent
            # 形同虚设、junction 仍能穿透。
            enforce_no_symlink_target(target, allow_nonexistent=True, _level="leaf")

        try:
            _check_chain_up_to(candidate_norm, base_dir_resolved)
        except ValueError as exc:
            raise ValueError(f"输出路径链中存在符号链接 / Junction，拒绝写入: {raw!r} ({exc})") from exc

        try:
            if candidate_norm.exists() or candidate_norm.parent.exists():
                resolved = candidate_norm.resolve(strict=False)
            else:
                resolved = candidate_norm
            if not allow_outside_work_dir:
                resolved.relative_to(base_dir_resolved)
        except (OSError, ValueError) as exc:
            raise ValueError(f"解析后真实路径超出允许范围（含 symlink 穿透）: {raw!r}") from exc

        final_path = resolved
        try:
            if create_parent:
                parent = final_path if is_dir else final_path.parent
                if not allow_outside_work_dir:
                    _ = Path(os.path.normpath(os.fspath(parent))).relative_to(
                        Path(os.path.normpath(os.fspath(base_dir_resolved)))
                    )
                parent.mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError) as exc:
            raise ValueError(f"无法为输出路径创建父目录: {final_path!r} ({exc})") from exc

        return final_path
