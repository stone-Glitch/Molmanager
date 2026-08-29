"""mapping 子系统 mixin（由原 core/model.py 拆分而来）。"""

from ._common import *  # noqa: F401,F403


class MappingMixin:
    def save_mapping(self, mapping_dict: dict[str, str], *, path=None, backup: bool = True) -> Path:
        """
        保存映射表到磁盘（T10：从 ui/dialogs/mapping_dialog.py 下沉而来）。

        相比原 UI 层直写，这里额外提供三件事：
          1. **快照钩子** —— 覆盖前先备份旧的 JSON + TSV 产物（可回滚）；
          2. **原子写** —— tmp → chmod → os.replace，写一半崩溃不会损坏映射表；
          3. **状态同步** —— 自动 set_mapping + invalidate_scan_cache。

        参数:
            mapping_dict: 英文名 → 中文名
            path:         落盘路径，默认 ``<work_dir>/分子命名映射.json``
            backup:       是否在覆盖前创建快照（默认 True）

        返回:
            实际写入的 Path。

        抛出:
            OSError / ValueError —— 写盘失败时抛给调用方（保存失败必须让用户知道）。
            ⚠️ 注意：**备份失败不抛**，只记 WARNING 后继续保存（架构 §6.4）。
        """
        if not isinstance(mapping_dict, dict):
            raise ValueError("mapping_dict 必须是 dict")
        out_path = Path(path) if path else self.default_mapping_path()

        # ---- 1) 快照（失败只警告，绝不阻断保存）----
        if backup:
            try:
                artifacts = self.get_mapping_artifacts()
                if str(out_path) not in {str(a) for a in artifacts}:
                    artifacts.append(out_path)
                self.create_backup_snapshot(
                    "mapping",
                    artifacts,
                    f"保存映射表前的自动快照（{len(mapping_dict)} 条）",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("⚠️ 映射表快照失败（保存继续）: %s", exc)

        # ---- 2) 原子写（复用 config.save_config 范式，C19）----
        tmp_path: Path | None = None
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
            with open(win_longpath(tmp_path), "w", encoding="utf-8") as f:
                json.dump(mapping_dict, f, ensure_ascii=False, indent=2)
            if hasattr(os, "replace"):
                os.replace(tmp_path, out_path)
            else:  # pragma: no cover
                tmp_path.rename(out_path)
            tmp_path = None
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass

        # ---- 3) 同步内存状态 ----
        self.set_mapping(dict(mapping_dict))
        self.invalidate_scan_cache()
        self._log(f"💾 映射表已保存：{len(mapping_dict)} 条 → {out_path.name}", "success")
        return out_path

    def parse_mapping_file(self, path: Path) -> tuple[dict[str, str], dict[str, "object"]]:
        """
        M-05：只读解析映射文件，**不**写入内存。返回 (new_dict, info_dict)。
        供 controller 在真正 apply 之前做 Diff 预览；与 load_mapping_file 共用同一解析逻辑，
        避免重复实现导致格式漂移。
        """
        # 兼容调用方传入 str（如 backup_dialog 回滚后重载），统一转为 Path
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {path}")
        mapping: dict[str, str] = {}
        duplicate_count = 0
        eng_conflicts: list[str] = []  # 重复的英文名（后者被静默丢弃）
        chn_conflicts: list[tuple[str, str, str]] = []  # 科学红线 S-06
        seen_chn: dict[str, str] = {}
        with open(win_longpath(path), encoding="utf-8-sig") as f:
            lines = f.readlines()
        if len(lines) < 2:
            raise ValueError("映射文件为空或格式错误")
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 2:
                eng = parts[0].strip()
                chn = parts[1].strip()
                if not eng or not chn:
                    continue
                if eng in mapping:
                    duplicate_count += 1
                    eng_conflicts.append(eng)
                    continue
                # 科学红线 S-06：中文名冲突检测（反向映射冲突）。
                # 同一中文名被多个英文名共用 → 最终反向映射只会保留最后一个，其余悄悄丢失。
                if chn in seen_chn:
                    chn_conflicts.append((chn, seen_chn[chn], eng))
                else:
                    seen_chn[chn] = eng
                mapping[eng] = chn
        info = {
            "count": len(mapping),
            "dup_eng": duplicate_count,
            "dup_chn": len(chn_conflicts),
            "eng_conflicts": eng_conflicts,
            "chn_conflicts": chn_conflicts,
        }
        return mapping, info

    def load_mapping_file(self, path: Path):
        # 兼容调用方传入 str（如 backup_dialog 回滚后重载），统一转为 Path
        path = Path(path)
        mapping, info = self.parse_mapping_file(path)
        self.set_mapping(mapping)
        # 记录来源文件，供 save_mapping 的快照覆盖双格式产物（C9 / T10）
        try:
            self.mapping_source_path = Path(path)
        except (TypeError, ValueError):
            self.mapping_source_path = None
        return info
