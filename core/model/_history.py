"""history 子系统 mixin（由原 core/model.py 拆分而来）。"""
from ._common import *  # noqa: F401,F403


class HistoryMixin:
    def _add_history(self, op_type, file_pairs, description=''):
        if not file_pairs:
            return
        # 汇聚模式：交给发起方合并提交，而不是丢弃（丢弃会导致操作无法撤销）
        sink = getattr(self, '_history_sink', None)
        if sink is not None:
            sink.extend(file_pairs)
            return
        if getattr(self, '_suppress_history', False):
            return
        self.history.append({
            'type': op_type,
            'files': file_pairs,
            'description': description or f"{op_type} ({len(file_pairs)} 个文件)",
            'ts': datetime.now().isoformat(),
        })
        self.redo_stack.clear()
        self._log(f"📝 已记录历史: {self.history[-1]['description']}", 'info')
        self.invalidate_scan_cache()
        # D-06：每次记录后立即持久化到 .history/，重启后可恢复（撤销链不丢）
        self._save_history()

    def _history_file_path(self) -> Path:
        return self.work_dir / ".history" / "history.json"

    def _save_history(self) -> None:
        """把当前 history 序列化到 work_dir/.history/history.json（仅内存历史，不含 redo）。"""
        try:
            path = self._history_file_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            snap = self.history[-self.HISTORY_MAX_ENTRIES:]
            payload = []
            for e in snap:
                files = e.get("files") or []
                # 归档对可能是 (Path/str, Path/str) 元组，统一落为 str 列表以便 JSON 序列化
                ser_files = []
                for pair in files:
                    if isinstance(pair, (list, tuple)) and len(pair) == 2:
                        ser_files.append([str(pair[0]), str(pair[1])])
                    else:
                        ser_files.append([str(pair), ""])
                payload.append({
                    "type": e.get("type", "unknown"),
                    "files": ser_files,
                    "description": e.get("description", ""),
                    "ts": e.get("ts", ""),
                })
            tmp = path.with_suffix(".json.tmp")
            with open(win_longpath(tmp), "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            if hasattr(os, "replace"):
                os.replace(tmp, path)
            else:  # pragma: no cover
                tmp.rename(path)
        except Exception as _e:
            logger.debug("历史持久化失败（不影响本次操作）: %s", _e)

    def _load_history(self) -> None:
        """启动时从 .history/history.json 恢复历史（redo 栈不恢复，避免跨会话撤销歧义）。"""
        self.history = []
        self.redo_stack = []
        try:
            path = self._history_file_path()
            if not path.exists():
                return
            with open(win_longpath(path), encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                return
            for e in data[-self.HISTORY_MAX_ENTRIES:]:
                if not isinstance(e, dict) or "files" not in e or "type" not in e:
                    continue
                self.history.append({
                    "type": e.get("type", "unknown"),
                    "files": [tuple(p) if isinstance(p, list) and len(p) == 2 else (str(p), "")
                              for p in e.get("files", [])],
                    "description": e.get("description", ""),
                    "ts": e.get("ts", ""),
                })
            if self.history:
                self._log(f"📂 已从 .history 恢复 {len(self.history)} 条操作历史", 'info')
        except Exception as _e:
            logger.debug("历史恢复失败（已忽略，重新开始）: %s", _e)

    def _file_md5(self, p: Path) -> str:
        h = hashlib.md5()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def _copy_is_pristine(self, src: Path, dst: Path) -> bool:
        """判断工作目录里的导入副本 dst 是否仍是源文件 src 的「未改动」副本。

        先比文件大小（瞬时、零拷贝），大小相同再比 MD5（编辑后恰好同大小属极罕见情形，
        此时仍需兜底校验内容）。用于撤销导入时的数据安全红线：
        只有确认副本未被用户编辑过，才允许删掉它。
        """
        try:
            if src.stat().st_size != dst.stat().st_size:
                return False
            return self._file_md5(src) == self._file_md5(dst)
        except OSError:
            return False

    def undo_last(self):
        if not self.history:
            self._log("⚠️ 没有可撤销的操作", 'warning')
            return False
        entry = self.history[-1]
        self.redo_stack.append(entry)
        self.history.pop()
        op_type = entry['type']
        file_pairs = entry['files']
        success_count = error_count = 0
        if op_type in ('rename', 'move', 'fix'):
            for src, dst in file_pairs:
                try:
                    if Path(dst).exists():
                        if Path(src).exists():
                            self._log(f"⚠️ 撤销跳过 {Path(dst).name}: 原位置已存在文件", 'warning')
                            error_count += 1
                            continue
                        Path(dst).rename(src)
                        self._log(f"↩️ 撤销: {Path(dst).name} -> {Path(src).name}", 'info')
                        success_count += 1
                    else:
                        self._log(f"⚠️ 撤销失败: 目标文件不存在 {dst}", 'warning')
                        error_count += 1
                except Exception as e:
                    self._log(f"❌ 撤销失败 {Path(dst).name}: {e}", 'error')
                    error_count += 1
        elif op_type == 'delete':
            for src, dst in file_pairs:
                try:
                    if Path(dst).exists():
                        if Path(src).exists():
                            self._log(f"⚠️ 恢复跳过 {Path(src).name}: 原位置已存在文件", 'warning')
                            error_count += 1
                            continue
                        Path(dst).rename(src)
                        self._log(f"↩️ 恢复文件: {Path(src).name}", 'info')
                        success_count += 1
                    else:
                        self._log(f"⚠️ 恢复失败: 备份不存在 {dst}", 'warning')
                        error_count += 1
                except Exception as e:
                    self._log(f"❌ 恢复失败 {Path(src).name}: {e}", 'error')
                    error_count += 1
        elif op_type == 'import':
            # F06 导入（复制模式）的撤销：删掉工作目录里的**副本**，外部原件一动不动。
            # 🔴 数据安全兜底（两层）：
            #   1) 外部原件已不在（用户导入后删了/移走源文件）：此时删副本=唯一数据消失，
            #      绝不允许 → 把副本搬回原位置。
            #   2) 外部原件仍在：先比 MD5 判断副本是否被用户编辑过。
            #      - 副本==原件（未改动）：它是冗余副本，安全 unlink。
            #      - 副本被编辑过：绝不直接 unlink 造成数据永久丢失，而是隔离到
            #        .trash_backup（受保护目录，不出现在文件列表），让用户能找回。
            for src, dst in file_pairs:
                try:
                    src_p, dst_p = Path(src), Path(dst)
                    if not dst_p.exists():
                        self._log(f"⚠️ 撤销导入失败: 副本不存在 {dst}", 'warning')
                        error_count += 1
                        continue
                    if src_p.exists() and self._copy_is_pristine(src_p, dst_p):
                        dst_p.unlink()
                        self._log(f"↩️ 撤销导入: 已移除未改动的重复副本 {dst_p.name}", 'info')
                    elif src_p.exists():
                        trash_dir = self.work_dir / ".trash_backup"
                        trash_dir.mkdir(parents=True, exist_ok=True)
                        quar = trash_dir / f"{dst_p.name}.undo_import_{int(datetime.now().timestamp())}.bak"
                        shutil.move(str(dst_p), str(quar))
                        self._log(
                            f"⚠️ 撤销导入：副本 {dst_p.name} 已被修改，已隔离到 .trash_backup"
                            f"（未直接删除，可找回）", 'warning'
                        )
                    else:
                        src_p.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(dst_p), str(src_p))
                        self._log(
                            f"↩️ 撤销导入: 源文件已不存在，副本已移回 {src_p}", 'warning'
                        )
                    success_count += 1
                except Exception as e:
                    self._log(f"❌ 撤销导入失败 {Path(dst).name}: {e}", 'error')
                    error_count += 1
        else:
            self._log(f"❌ 不支持撤销的操作类型: {op_type}", 'error')
        self._log(f"🔁 撤销完成: 成功 {success_count}, 失败 {error_count}", 'info' if error_count==0 else 'warning')
        return success_count > 0

    def redo_last(self):
        if not self.redo_stack:
            self._log("⚠️ 没有可重做的操作", 'warning')
            return {'success_count': 0, 'error_count': 0}
        entry = self.redo_stack.pop()
        op_type = entry['type']
        file_pairs = entry['files']
        success_count = error_count = 0
        if op_type in ('rename', 'move', 'fix'):
            for src, dst in file_pairs:
                try:
                    if Path(src).exists():
                        if Path(dst).exists():
                            self._log(f"⚠️ 重做跳过 {Path(src).name}: 目标位置已存在文件", 'warning')
                            error_count += 1
                            continue
                        Path(src).rename(dst)
                        self._log(f"↪️ 重做: {Path(src).name} -> {Path(dst).name}", 'info')
                        success_count += 1
                    else:
                        self._log(f"⚠️ 重做失败: 源文件不存在 {src}", 'warning')
                        error_count += 1
                except Exception as e:
                    self._log(f"❌ 重做失败 {Path(src).name}: {e}", 'error')
                    error_count += 1
        elif op_type == 'delete':
            for src, dst in file_pairs:
                try:
                    if Path(src).exists():
                        if Path(dst).exists():
                            self._log(f"⚠️ 重做跳过 {Path(src).name}: 备份位置已存在文件", 'warning')
                            error_count += 1
                            continue
                        Path(src).rename(dst)
                        self._log(f"↪️ 重做删除: {Path(src).name}", 'info')
                        success_count += 1
                    else:
                        self._log(f"⚠️ 重做失败: 源文件不存在 {src}", 'warning')
                        error_count += 1
                except Exception as e:
                    self._log(f"❌ 重做失败 {Path(src).name}: {e}", 'error')
                    error_count += 1
        elif op_type == 'import':
            # 重做导入 = 再复制一次。源文件不在了就只能放弃（不能凭空造数据）。
            for src, dst in file_pairs:
                try:
                    src_p, dst_p = Path(src), Path(dst)
                    if dst_p.exists():
                        self._log(f"⚠️ 重做导入跳过 {dst_p.name}: 目标已存在", 'warning')
                        error_count += 1
                        continue
                    if not src_p.exists():
                        self._log(f"⚠️ 重做导入失败: 源文件不存在 {src}", 'warning')
                        error_count += 1
                        continue
                    dst_p.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_p, dst_p)
                    self._log(f"↪️ 重做导入: {dst_p.name}", 'info')
                    success_count += 1
                except Exception as e:
                    self._log(f"❌ 重做导入失败 {Path(src).name}: {e}", 'error')
                    error_count += 1
        else:
            self._log(f"❌ 不支持重做的操作类型: {op_type}", 'error')
        self.history.append(entry)
        self._log(f"🔜 重做完成: 成功 {success_count}, 失败 {error_count}", 'info' if error_count==0 else 'warning')
        return {'success_count': success_count, 'error_count': error_count}

    def can_undo(self) -> bool:
        return len(self.history) > 0

    def can_redo(self) -> bool:
        return len(self.redo_stack) > 0

    def undo_until(self, target_index: int) -> dict:
        total_success = 0
        total_error = 0
        steps = 0
        if target_index < 0:
            target_index = 0
        while len(self.history) > target_index and self.history:
            entry = self.history[-1]
            self.redo_stack.append(entry)
            self.history.pop()
            op_type = entry['type']
            file_pairs = entry['files']
            step_success = 0
            step_error = 0
            if op_type in ('rename', 'move', 'fix') or op_type == 'delete':
                for src, dst in file_pairs:
                    try:
                        if Path(dst).exists():
                            if Path(src).exists():
                                step_error += 1
                                continue
                            Path(dst).rename(src)
                            step_success += 1
                        else:
                            step_error += 1
                    except Exception:
                        step_error += 1
            total_success += step_success
            total_error += step_error
            steps += 1
        self.invalidate_scan_cache()
        return {"total_success": total_success, "total_error": total_error, "steps": steps}

    def redo_until(self, target_index: int) -> dict:
        total_success = 0
        total_error = 0
        steps = 0
        if target_index > len(self.history) + len(self.redo_stack):
            target_index = len(self.history) + len(self.redo_stack)
        while len(self.history) < target_index and self.redo_stack:
            entry = self.redo_stack.pop()
            op_type = entry['type']
            file_pairs = entry['files']
            step_success = 0
            step_error = 0
            if op_type in ('rename', 'move', 'fix') or op_type == 'delete':
                for src, dst in file_pairs:
                    try:
                        if Path(src).exists():
                            if Path(dst).exists():
                                step_error += 1
                                continue
                            Path(src).rename(dst)
                            step_success += 1
                        else:
                            step_error += 1
                    except Exception:
                        step_error += 1
            self.history.append(entry)
            total_success += step_success
            total_error += step_error
            steps += 1
        self.invalidate_scan_cache()
        return {"total_success": total_success, "total_error": total_error, "steps": steps}

    def get_history_snapshot(self) -> list[dict]:
        result = []
        for idx, entry in enumerate(self.history):
            result.append({
                "idx": idx,
                "type": entry.get("type", ""),
                "description": entry.get("description", ""),
                "file_count": len(entry.get("files", []))
            })
        return result

    def get_redo_snapshot(self) -> list[dict]:
        result = []
        start_idx = len(self.history)
        for i, entry in enumerate(self.redo_stack):
            result.append({
                "idx": start_idx + i,
                "type": entry.get("type", ""),
                "description": entry.get("description", ""),
                "file_count": len(entry.get("files", []))
            })
        return result
