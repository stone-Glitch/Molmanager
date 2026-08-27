#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Controller - 协调 Model 和 View
修复：所有任务函数正确传递进度回调
"""
import logging
from pathlib import Path
from tkinter import (
    LEFT,
    Button,
    Entry,
    Frame,
    Label,
    StringVar,
    Toplevel,
    X,
    filedialog,
    messagebox,
)

from core.model import MolManagerModel
import utils.config as config
from utils.config import save_config
from utils.constants import SUPPORTED_EXTS
from utils.logger import default_logger as logger
from utils.logger import performance_timer


class Controller:
    def __init__(self, app, helpers):
        self.app = app
        self.helpers = helpers
        self.model = MolManagerModel(work_dir=self.app.config_data.get("work_dir", "output"))
        self.model.set_log_callback(self.helpers.on_log)
        # F17：把 config["backup"] 灌给 model（开关 / 每类保留份数 / 单文件上限）。
        # 配置读不出来时 model 内部会自动回落默认值，这里不需要额外兜底。
        try:
            self.model.configure_backup(self.app.config_data.get("backup", {}))
        except Exception as _e_backup_cfg:  # noqa: BLE001
            logger.debug("初始化备份配置失败（使用默认值）: %s", _e_backup_cfg)
        if not isinstance(self.app.config_data.get("recent_work_dirs"), list):
            self.app.config_data["recent_work_dirs"] = []
        self.push_recent_work_dir(str(self.model.work_dir))

    # ----- 配置 -----
    def push_recent_work_dir(self, path: str):
        path = str(path)
        if not path:
            return
        recent = self.app.config_data["recent_work_dirs"]
        if path in recent:
            recent.remove(path)
        recent.insert(0, path)
        self.app.config_data["recent_work_dirs"] = recent[:config.MAX_RECENT_DIRS]
        save_config(self.app.config_data)

    def get_recent_work_dirs(self) -> list[str]:
        return list(self.app.config_data.get("recent_work_dirs", []))

    def switch_recent_work_dir(self, index: int):
        recent = self.app.config_data.get("recent_work_dirs", [])
        if index < 0 or index >= len(recent):
            return
        path = recent[index]
        if not Path(path).is_dir():
            return
        self.model.work_dir = Path(path)
        self.app.work_dir_var.set(path)
        self.push_recent_work_dir(path)
        self.scan_files()

    def show_recent_dirs_dialog(self):
        self.app.dialogs.show_recent_dirs_dialog()

    def browse_work_dir(self):
        d = filedialog.askdirectory(title="选择工作目录")
        if d:
            self.app.work_dir_var.set(d)
            self.model.work_dir = Path(d)
            self.push_recent_work_dir(d)
            self.scan_files()

    def browse_mapping(self):
        f = filedialog.askopenfilename(title="选择映射文件", filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if f:
            self.app.mapping_file_var.set(f)

    def load_mapping_file(self):
        raw = self.app.mapping_file_var.get().strip()
        if not raw:
            self.helpers.on_log("❌ 请先选择映射文件", 'error')
            return
        path = Path(raw)
        # 🔴 校验必须是真实存在的「文件」：空串 / '.' / 目录都会让 Path(...).exists()
        # 为 True（当前目录必然存在），从而绕过上面的守卫去 open() 一个目录 →
        # Windows 上 PermissionError: [Errno 13] Permission denied: '.'。
        if not path.is_file():
            self.helpers.on_log(f"❌ 映射文件无效或不存在: {path}", 'error')
            return
        # ---- M-05：加载前先算 Diff 预览，让用户确认变更后再 apply ----
        try:
            from utils.mapping_utils import diff_mappings
            new_dict, _parse_info = self.model.parse_mapping_file(path)
            diff = diff_mappings(self.model.mapping, new_dict)
            c = diff["counts"]
            if c["added"] or c["changed"] or c["removed"]:
                try:
                    from ui.dialogs.mapping_dialog import show_mapping_diff_preview
                    ok = show_mapping_diff_preview(
                        self.app, self.model.mapping, new_dict, diff
                    )
                except Exception as _pe:  # 弹窗异常 → 回退为直接加载，按钮绝不死
                    logger.warning("Diff 预览弹窗异常，回退直接加载: %s", _pe)
                    ok = True
                if not ok:
                    self.helpers.on_log("ℹ️ 已取消加载（用户在 Diff 预览中拒绝）", 'info')
                    return
            else:
                self.helpers.on_log("ℹ️ 新映射表与当前内容一致，无需变更", 'info')
        except Exception as _de:  # Diff 计算异常 → 回退为直接加载
            logger.warning("Diff 预览计算异常，回退直接加载: %s", _de)
        # ---- 原有加载逻辑（保持不变）----
        try:
            info = self.model.load_mapping_file(path)
            count = info["count"]
            self.app.mapping_count.set(str(count))
            if info["dup_eng"] > 0:
                self.helpers.on_log(
                    f"✅ 映射加载成功：{count} 个有效条目，自动跳过 {info['dup_eng']} 个重复英文名", 'success'
                )
            else:
                self.helpers.on_log(f"✅ 映射加载成功，共 {count} 个条目", 'success')
            # 科学红线 S-06：中文名冲突必须显式告知，绝不能静默塌缩导致丢数据
            if info["dup_chn"] > 0:
                _examples = "\n".join(
                    f"  · 「{c[0]}」← 已属 {c[1]}，又被 {c[2]} 占用"
                    for c in info["chn_conflicts"][:10]
                )
                _more = "\n  …（更多冲突见日志）" if info["dup_chn"] > 10 else ""
                self.helpers.on_log(
                    f"⚠️ 发现 {info['dup_chn']} 处中文名冲突（多英文名映射到同一中文名，"
                    f"反向映射将只保留其一，其余悄悄丢失）："
                    + "；".join(f"「{c[0]}」←{c[1]}/{c[2]}" for c in info["chn_conflicts"][:10])
                    + (_more if info["dup_chn"] > 10 else ""),
                    'warning'
                )
                messagebox.showwarning(
                    "中文名冲突（映射将丢数据）",
                    f"检测到 {info['dup_chn']} 处中文名冲突：\n多个不同的英文名映射到了同一个中文名，"
                    "反向映射（中文→英文）只会保留最后载入的一条，其余会悄悄丢失。\n\n"
                    "冲突示例：\n" + _examples + _more + "\n\n"
                    "建议：把冲突的中文名改为互不相同，或用「映射表编辑器」手动拆分后再加载。",
                    parent=self.app
                )
            self.scan_files()
        except Exception as e:
            self.helpers.on_log(f"❌ 加载映射失败: {e}", 'error')

    # ----- 扫描 -----
    @performance_timer(name="Controller.scan_files", level=logging.DEBUG, min_ms=5.0)
    def scan_files(self):
        def _scan(**kwargs):
            try:
                ext_str = self.app.ext_filter_var.get().strip()
                if ext_str:
                    ext_list = [e.strip().lower() for e in ext_str.split(',') if e.strip()]
                    ext_list = [e if e.startswith('.') else '.' + e for e in ext_list]
                else:
                    ext_list = list(SUPPORTED_EXTS)
                files = self.model.scan_files(ext_filter=ext_list)
                def _after():
                    self.app.last_scan_result = list(files)
                    self.helpers.apply_filter()
                    self.helpers.on_log(f"📁 扫描完成，发现 {len(files)} 个文件", 'info')
                    # 工作台统计卡刷新（若工作台页已构建）
                    try:
                        if hasattr(self.app, "refresh_dashboard"):
                            self.app.refresh_dashboard()
                    except Exception:
                        pass
                self.app.after(0, _after)
            except Exception as e:
                import traceback
                # 提前获取异常对象和堆栈字符串，避免 lambda 延迟绑定取到已被清理的 e
                err_obj = e
                err_tb = traceback.format_exc()
                # 【加固】窗口正在关闭 / 已 destroy 时，app.after 本身会抛
                # TclError("application has been destroyed") 或
                # RuntimeError("main thread is not in main loop")，
                # 会把"扫描失败"这种普通错误升级成 worker 线程未捕获异常并刷屏。
                # 这里兜底：回调派发不了就直接写日志文件，不再向上抛。
                try:
                    self.app.after(0, lambda _e=err_obj, _tb=err_tb:
                                   self.helpers.on_log(f"❌ 扫描失败: {_e}\n{_tb}", 'error'))
                except Exception:
                    logger.error("扫描失败（且无法回调到 UI）: %s\n%s", err_obj, err_tb)
        self.helpers.run_task(_scan)

    # ----- 修复 -----
    def _collect_rename_preview_changes(self, dry_run_callable) -> list[dict]:
        changes = []
        orig_cb = getattr(self.model, 'log_callback', None)
        def _cap(msg, level='info'):
            if isinstance(msg, str) and ("->" in msg) and ("预览" in msg or "[预览]" in msg):
                try:
                    right = msg.split("]: ", 1)[-1] if "]:" in msg else msg
                    label_part, arrow_part = right.split("->", 1)
                    action_tok = label_part.strip().split(":", 1)[0].strip()
                    frm = label_part.split(":", 1)[1].strip() if ":" in label_part else label_part.strip()
                    to = arrow_part.strip()
                    changes.append({"action": action_tok or "rename", "from": frm, "to": to})
                except Exception:
                    pass
            if orig_cb:
                orig_cb(msg, level)
        self.model.set_log_callback(_cap)
        try:
            dry_run_callable()
        finally:
            self.model.set_log_callback(orig_cb)
        return changes

    def fix_all(self):
        def _dryrun():
            return self._collect_rename_preview_changes(lambda: self.model.fix_all(dry_run=True))
        def _run(_filtered_changes=None):
            def _task(**kwargs):
                results = self.model.fix_all(_filtered_changes=_filtered_changes)
                total = sum(r[0] for r in results.values())
                self.helpers.on_log(f"🎉 一键修复完成！共修复 {total} 个文件", 'success')
                self.scan_files()
            self.helpers.run_task(_task)
        self.helpers.preview_or_run("一键修复", _dryrun, _run)

    def rename_by_mapping(self):
        def _dryrun():
            return self._collect_rename_preview_changes(lambda: self.model.rename_by_mapping(dry_run=True))
        def _run(_filtered_changes=None):
            def _task(**kwargs):
                s, f, sk = self.model.rename_by_mapping(_filtered_changes=_filtered_changes)
                self.helpers.on_log(f"🎉 映射重命名完成: 成功 {s}, 失败 {f}, 跳过 {sk}", 'success')
                self.scan_files()
            self.helpers.run_task(_task)
        self.helpers.preview_or_run("映射重命名", _dryrun, _run)

    def fix_chinese(self):
        def _dryrun():
            return self._collect_rename_preview_changes(lambda: self.model.fix_chinese_names(dry_run=True))
        def _run(_filtered_changes=None):
            def _task(**kwargs):
                s, f, sk = self.model.fix_chinese_names(_filtered_changes=_filtered_changes)
                self.helpers.on_log(f"🎉 修复中文名完成: 成功 {s}, 失败 {f}, 跳过 {sk}", 'success')
                self.scan_files()
            self.helpers.run_task(_task)
        self.helpers.preview_or_run("修复中文名", _dryrun, _run)

    def fix_all_names(self):
        def _dryrun():
            return self._collect_rename_preview_changes(lambda: self.model.fix_all_names(dry_run=True))
        def _run(_filtered_changes=None):
            def _task(**kwargs):
                s, f, sk = self.model.fix_all_names(_filtered_changes=_filtered_changes)
                self.helpers.on_log(f"🎉 修复命名错误完成: 成功 {s}, 失败 {f}, 跳过 {sk}", 'success')
                self.scan_files()
            self.helpers.run_task(_task)
        self.helpers.preview_or_run("修复命名错误", _dryrun, _run)

    def fix_incorrect_chinese(self):
        def _dryrun():
            return self._collect_rename_preview_changes(lambda: self.model.fix_incorrect_chinese(dry_run=True))
        def _run(_filtered_changes=None):
            def _task(**kwargs):
                s, f, sk = self.model.fix_incorrect_chinese(_filtered_changes=_filtered_changes)
                self.helpers.on_log(f"🎉 修正中文内容完成: 成功 {s}, 失败 {f}, 跳过 {sk}", 'success')
                self.scan_files()
            self.helpers.run_task(_task)
        self.helpers.preview_or_run("修正中文内容", _dryrun, _run)

    # ----- 其他文件操作 -----
    def generate_missing(self):
        def _task(**kwargs):
            missing = self.model.generate_missing_list()
            if missing:
                self.helpers.on_log(f"📋 缺失列表已生成，共 {len(missing)} 个", 'info')
            else:
                self.helpers.on_log("🎉 所有 .mol/.xyz 文件均有映射", 'success')
        self.helpers.run_task(_task)

    def supplement_mol(self):
        def _dryrun() -> list[dict]:
            changes = []
            for entry in self.model.work_dir.iterdir():
                if entry.is_file() and entry.suffix.lower() == '.xyz':
                    dst = self.model.work_dir / f"{entry.stem}.mol"
                    if not dst.exists():
                        changes.append({"action": "convert", "from": entry.name, "to": dst.name})
            return changes
        def _run(_filtered_changes=None):
            def _task(**kwargs):
                progress_cb = kwargs.get('_progress_callback')
                count = self.model.supplement_mol(progress_callback=progress_cb)
                self.helpers.on_log(f"🎉 补全 .mol 完成，共 {count} 个", 'success')
                self.scan_files()
            self.helpers.run_task(_task)
        self.helpers.preview_or_run("补全 mol 文件", _dryrun, _run)

    def organize_by_type(self):
        def _dryrun() -> list[dict]:
            ext_map = {'.mol': 'mol_files', '.xyz': 'xyz_files', '.sdf': 'sdf_files',
                       '.pdb': 'pdb_files', '.mol2': 'mol2_files', '.cif': 'cif_files',
                       '.pdbqt': 'pdbqt_files', '.cml': 'cml_files',
                       '.fchk': 'fchk_files', '.out': 'out_files', '.inp': 'inp_files'}
            changes = []
            for entry in self.model.work_dir.iterdir():
                if not entry.is_file():
                    continue
                ext = entry.suffix.lower()
                if ext not in ext_map:
                    continue
                changes.append({"action": "move", "from": entry.name, "to": f"{ext_map[ext]}/{entry.name}"})
            return changes
        def _run(_filtered_changes=None):
            def _task(**kwargs):
                progress_cb = kwargs.get('_progress_callback')
                count = self.model.organize_by_type(
                    progress_callback=progress_cb, _filtered_changes=_filtered_changes
                )
                self.helpers.on_log(f"🎉 按类型整理完成，移动 {count} 个文件", 'success')
                self.scan_files()
            self.helpers.run_task(_task)
        self.helpers.preview_or_run("按类型整理", _dryrun, _run)

    def organize_by_basename(self):
        def _dryrun() -> list[dict]:
            changes = []
            for entry in self.model.work_dir.iterdir():
                if not entry.is_file():
                    continue
                changes.append({"action": "move", "from": entry.name, "to": f"{entry.stem}/{entry.name}"})
            return changes
        def _run(_filtered_changes=None):
            def _task(**kwargs):
                progress_cb = kwargs.get('_progress_callback')
                count = self.model.organize_by_basename(
                    progress_callback=progress_cb, _filtered_changes=_filtered_changes
                )
                self.helpers.on_log(f"🎉 按文件名分组完成，移动 {count} 个文件", 'success')
                self.scan_files()
            self.helpers.run_task(_task)
        self.helpers.preview_or_run("按文件名分组", _dryrun, _run)


    def prefix_rename_dialog(self):
        file_info = self.helpers.get_selected_file_info()
        if not file_info:
            self.helpers.on_log("⚠️ 没有选中任何文件，请先在列表中勾选", 'warning')
            return

        dialog = Toplevel(self.app)
        dialog.title("前缀重命名")
        dialog.transient(self.app)
        dialog.grab_set()
        dialog.resizable(False, False)

        placeholder_text = "可用占位符：{stem} {ext} {mw} {logP} {tpsa} {hbd} {hba} {rotors} {rings} {atoms} {date}"
        Label(dialog, text=placeholder_text, fg="blue", wraplength=520, justify="left").pack(padx=12, pady=(12, 4), anchor="w")

        prefix_var = StringVar()
        entry_frame = Frame(dialog)
        entry_frame.pack(padx=12, pady=8, fill=X)
        Label(entry_frame, text="前缀模板：").pack(side=LEFT)
        entry = Entry(entry_frame, textvariable=prefix_var, width=50)
        entry.pack(side=LEFT, fill=X, expand=True, padx=(6, 0))
        entry.focus_set()

        btn_frame = Frame(dialog)
        btn_frame.pack(padx=12, pady=(4, 12))

        result = {"prefix": None}
        preview_captured = []

        def on_preview():
            prefix = prefix_var.get().strip()
            if not prefix:
                messagebox.showwarning("提示", "请先输入前缀模板", parent=dialog)
                return
            first = sorted(file_info, key=lambda x: x['name'])[0]
            try:
                preview_captured.clear()
                orig_cb = self.model.log_callback

                def capture_log(msg, level='info'):
                    preview_captured.append(msg)
                    if orig_cb:
                        orig_cb(msg, level)

                self.model.set_log_callback(capture_log)
                try:
                    self.model.prefix_rename(prefix, [first], dry_run=True)
                finally:
                    self.model.set_log_callback(orig_cb)

                if preview_captured:
                    details = "\n".join(preview_captured)
                    messagebox.showinfo("预览", details, parent=dialog)
                else:
                    messagebox.showwarning("预览", "未能生成预览结果", parent=dialog)
            except Exception as e:
                messagebox.showerror("预览失败", str(e), parent=dialog)

        def on_ok():
            prefix = prefix_var.get().strip()
            if not prefix:
                messagebox.showwarning("提示", "前缀不能为空", parent=dialog)
                return
            result["prefix"] = prefix
            dialog.destroy()

        def on_cancel():
            dialog.destroy()

        Button(btn_frame, text="预览", width=8, command=on_preview).pack(side=LEFT, padx=4)
        Button(btn_frame, text="OK", width=8, command=on_ok).pack(side=LEFT, padx=4)
        Button(btn_frame, text="取消", width=8, command=on_cancel).pack(side=LEFT, padx=4)

        dialog.bind('<Return>', lambda e: on_ok())
        dialog.bind('<Escape>', lambda e: on_cancel())
        self.app.wait_window(dialog)

        prefix = result["prefix"]
        if prefix is None:
            return

        def _task(**kwargs):
            count = self.model.prefix_rename(prefix, file_info)
            self.helpers.on_log(f"🎉 前缀重命名完成，共 {count} 个", 'success')
            self.scan_files()
        self.helpers.run_task(_task)

    def remove_duplicate_files(self):
        if not messagebox.askyesno("确认删除", "将扫描工作目录中的所有文件，删除内容完全相同的重复副本。\n\n是否继续？"):
            return

        def _task(**kwargs):
            progress_cb = kwargs.get('_progress_callback')
            deleted, errors = self.model.remove_duplicate_files(progress_callback=progress_cb)
            self.helpers.on_log(f"🗑️ 删除重复文件完成：共删除 {deleted} 个文件", 'success')
            if errors:
                self.helpers.on_log(f"⚠️ 出现 {len(errors)} 个错误: " + "; ".join(errors[:3]), 'warning')
            self.scan_files()
        self.helpers.run_task(_task)

    def undo_last(self):
        def _task(**kwargs):
            success = self.model.undo_last()
            self.scan_files()
            if not success:
                self.helpers.on_log("⚠️ 没有可撤销的操作或撤销失败", 'warning')
        self.helpers.run_task(_task)

    def redo_last(self):
        def _task(**kwargs):
            result = self.model.redo_last()
            self.helpers.on_log(f"重做完成: 成功 {result['success_count']}, 失败 {result['error_count']}",
                                'info' if result['error_count'] == 0 else 'warning')
            self.scan_files()
        self.helpers.run_task(_task)

    def get_undo_redo_state(self) -> dict:
        return {'undo_count': len(self.model.history), 'redo_count': len(self.model.redo_stack)}

    # ================ F06：拖放导入 / 菜单兜底导入（T18） ================
    def handle_dropped_paths(self, paths, *, source: str = "drop") -> None:
        """
        处理「一批外部路径」→ 分类 → 确认 → 后台导入 → 刷新列表。

        这是 F06 的**唯一**业务入口，拖放（``<<Drop>>`` 事件）和菜单兜底
        （文件选择框）都汇流到这里，保证两条路径的规则完全一致：
        白名单、目录递归展开、去重、受保护目录拒绝，全部由 `core.drop_handler`
        统一裁决 —— controller 只负责「问用户 + 调 model + 刷 UI」。

        参数:
            paths:  tkdnd 的原始 data 字符串，或 str/Path 的列表。
            source: ``"drop"`` / ``"menu"``，只用于日志区分来源。

        契约：**永不抛异常**。任何失败都转成日志 / 提示框，绝不能让一次误拖
        把主窗口打崩（架构 §3.2）。
        """
        try:
            from core.drop_handler import DropHandler
        except Exception as exc:  # noqa: BLE001
            self.helpers.on_log(f"⚠️ 拖放模块不可用（已忽略本次导入）: {exc}", 'warning')
            return

        cfg = getattr(self.app, "config_data", {}) or {}
        if not DropHandler.is_enabled(cfg):
            self.helpers.on_log("⚠️ 拖放导入已在配置中关闭（dnd.enabled = false）", 'warning')
            return

        # ---- 1) 分类：白名单 / 目录展开 / 去重 / 受保护目录拒绝 ----
        try:
            handler = DropHandler.from_config(cfg, work_dir=self.model.work_dir)
            if isinstance(paths, (str, bytes)):
                result = handler.process_drop_data(paths)
            else:
                result = handler.process(paths)
        except Exception as exc:  # noqa: BLE001
            logger.warning("解析拖入路径失败: %s", exc)
            self.helpers.on_log(f"❌ 解析拖入内容失败: {exc}", 'error')
            return

        tag = "拖入" if source == "drop" else "选择"
        self.helpers.on_log(f"📥 {tag}内容分析：{result.summary()}", 'info')
        for line in result.rejection_lines():
            self.helpers.on_log(f"　└ 已忽略 · {line}", 'warning')

        # ---- 2) 一个都不能导 → 说清楚为什么，然后收工 ----
        if not result.has_accepted():
            detail = "\n".join(f"· {line}" for line in result.rejection_lines()) or \
                     "· 没有识别到任何文件"
            try:
                messagebox.showinfo(
                    "没有可导入的文件",
                    f"本次{tag}的内容都不符合导入条件：\n\n{detail}\n\n"
                    f"提示：可导入的扩展名由配置项 dnd.extensions 控制。",
                    parent=self.app,
                )
            except Exception:
                pass
            return

        # ---- 3) 确认（导入会真的写盘，必须让用户点头）----
        preview = "、".join(result.accepted_names()[:5])
        if result.accepted_count > 5:
            preview += " …"
        lines = [
            f"即将把 {result.accepted_count} 个文件**复制**到工作目录：",
            f"　{self.model.work_dir}",
            "",
            f"文件示例：{preview}",
        ]
        if result.scanned_dirs:
            lines.append(f"（已递归展开 {result.scanned_dirs} 个文件夹）")
        if result.rejected_count:
            lines.append(f"（另有 {result.rejected_count} 个不符合条件的项目已忽略）")
        if result.truncated:
            lines.append("⚠️ 数量已达单次上限，列表被截断，请分批导入。")
        lines += ["", "原文件保留在原位置不动，导入后可用 Ctrl+Z 撤销。", "", "是否继续？"]
        try:
            if not messagebox.askyesno("确认导入", "\n".join(lines), parent=self.app):
                self.helpers.on_log("已取消导入", 'info')
                return
        except Exception:
            pass  # 无法弹确认框（极端环境）时按「继续」处理，避免功能完全不可用

        # ---- 4) 后台导入（复制模式最安全：外部原件不动）----
        accepted = list(result.accepted)

        def _task(**kwargs):
            progress_cb = kwargs.get('_progress_callback')
            info = self.model.import_external_files(
                accepted, mode="copy", progress_callback=progress_cb
            )
            for msg in info['skipped'][:5]:
                self.helpers.on_log(f"⏭️ 跳过 {msg}", 'warning')
            for msg in info['errors'][:5]:
                self.helpers.on_log(f"❌ {msg}", 'error')
            self.helpers.on_log(
                f"🎉 导入完成：{info['count']} 个文件已进入工作目录（Ctrl+Z 可撤销）",
                'success' if not info['errors'] else 'warning',
            )
            # F06 即时反馈：导入结果不只写日志，还要给一个汇总弹窗。
            # _task 在后台线程运行，弹窗必须经 after(0) 调回主线程（Tk 非线程安全）。
            count = info.get('count', 0)
            skipped = len(info.get('skipped', []))
            errors = len(info.get('errors', []))
            _summary = [f"✅ 成功导入 {count} 个文件"]
            if skipped:
                _summary.append(f"⏭️ 跳过 {skipped} 个（重名/已存在等）")
            if errors:
                _summary.append(f"❌ 失败 {errors} 个")
            _summary.append("")
            _summary.append("原文件保留在原位置，可用 Ctrl+Z 撤销本次导入。")
            try:
                self.app.after(
                    0,
                    lambda _t="\n".join(_summary): messagebox.showinfo(
                        "导入完成", _t, parent=self.app
                    ),
                )
            except Exception:
                pass
            self.scan_files()

        self.helpers.run_task(_task)

    def import_files_from_dialog(self) -> None:
        """
        菜单兜底入口：没装 tkinterdnd2（或用户不习惯拖拽）时，用文件选择框导入。

        走的是和拖放**完全相同**的 `handle_dropped_paths`，因此规则不会漂移。
        """
        try:
            from core.drop_handler import normalize_extensions
            exts = normalize_extensions(
                (getattr(self.app, "config_data", {}) or {}).get("dnd", {}).get("extensions")
            )
        except Exception:
            exts = ()
        if exts:
            pattern = " ".join(f"*{e}" for e in exts)
            filetypes = [("可导入的分子/计算文件", pattern), ("所有文件", "*.*")]
        else:
            filetypes = [("所有文件", "*.*")]
        try:
            files = filedialog.askopenfilenames(
                title="选择要导入到工作目录的文件（也可直接拖入窗口）",
                filetypes=filetypes,
            )
        except Exception as exc:  # noqa: BLE001
            self.helpers.on_log(f"❌ 打开文件选择框失败: {exc}", 'error')
            return
        if not files:
            return
        self.handle_dropped_paths(list(files), source="menu")

    def delete_selected(self):
        selected = self.helpers.get_selected_filenames()
        if not selected:
            self.helpers.on_log("⚠️ 没有选中文件", 'warning')
            return
        if not messagebox.askyesno("确认删除", f"确定要删除选中的 {len(selected)} 个文件吗？\n注意：文件会被移到工作目录的 .trash_backup 文件夹，可通过「撤销」恢复。"):
            return

        def _task(**kwargs):
            deleted, errors = self.model.delete_files(selected)
            for err in errors:
                self.helpers.on_log(f"❌ {err}", 'error')
            self.helpers.on_log(f"删除完成，共删除 {deleted} 个（可撤销）", 'success')
            self.scan_files()
        self.helpers.run_task(_task)

    @performance_timer(name="Controller.run_fix_by_mode", level=logging.DEBUG, min_ms=5.0)
    def run_fix_by_mode(self):
        mode = self.app.fix_mode_var.get()
        if mode == "一键修复（推荐）":
            self.fix_all()
        elif mode == "映射重命名":
            self.rename_by_mapping()
        elif mode == "修复中文名":
            self.fix_chinese()
        elif mode == "修复命名错误":
            self.fix_all_names()
        elif mode == "修正中文内容":
            self.fix_incorrect_chinese()

    def show_context_menu(self, event):
        item = self.app.tree.identify_row(event.y)
        if item:
            self.app.tree.selection_set(item)
        self.app.context_menu.post(event.x_root, event.y_root)

    def preview_2d_structure(self):
        self.app.dialogs.preview_2d_structure()

    # ================ O1：批量计算 MW/LogP/TPSA 填入新 3 列 ================
    @performance_timer(name="Controller.batch_fill_descriptors", level=logging.DEBUG, min_ms=10.0)
    def batch_fill_descriptors(self, only_selected: bool = False):
        paths = self._get_paths_for_descriptor(only_selected=only_selected)
        if not paths:
            return
        from core.task_manager import TaskManager
        tm = TaskManager(self.app, self)

        # 并发度：默认 1（顺序，零回归）；config.descriptor_workers 或
        # 环境变量 MM_DESCRIPTOR_WORKERS 可覆盖。>1 才真正分片并行。
        import os as _os
        _cfg_workers = int((self.app.config_data or {}).get("descriptor_workers", 1) or 1)
        try:
            _env_workers = int(_os.environ.get("MM_DESCRIPTOR_WORKERS", "0") or "0")
        except ValueError:
            _env_workers = 0
        _workers = _env_workers or _cfg_workers or 1

        def _compute_one(item):
            from pathlib import Path

            import chem.openbabel_utils as obu
            iid, fpath = item
            name = Path(fpath).name
            res = obu.calculate_descriptors(fpath)
            return iid, fpath, name, res

        def _task(_progress=None, _log=None, **_kw):
            from utils.concurrency import run_sharded
            total = len(paths)
            ok, fail = 0, 0
            # 取消信号：task_manager 的取消会令 _progress 停止，但为稳妥这里用计数器感知
            def _is_cancelled():
                return False  # 批量描述符目前整体可取消由 task_manager 层处理

            results = run_sharded(
                paths,
                _compute_one,
                max_workers=_workers,
                on_progress=(lambda d, t: _progress(d, t, f"描述符计算中 {d}/{t}") if _progress else None),
                is_cancelled=_is_cancelled,
            )
            for (iid, fpath, name, res) in results:
                if isinstance(res, dict) and "_exc" in res:
                    fail += 1
                    msg = f"描述符异常 {name}: {res['_exc']}"
                    if _log:
                        _log(msg, level="warning")
                    logger.warning(msg, exc_info=res["_exc"])
                    continue
                try:
                    if res.get("success"):
                        d = res.get("descriptors") or {}
                        mw = d.get("molecular_weight")
                        lp = d.get("logP")
                        tp = d.get("tpsa")
                        vals = {}
                        if isinstance(mw, (int, float)) and mw:
                            vals["MW"] = f"{mw:.2f}"
                        if isinstance(lp, (int, float)):
                            vals["LogP"] = f"{lp:.2f}"
                        if isinstance(tp, (int, float)) and tp:
                            vals["TPSA"] = f"{tp:.2f}"
                        if vals:
                            self.app.after(0, lambda _iid=iid, _v=vals: _write_cols(_iid, _v))
                            ok += 1
                        else:
                            fail += 1
                            msg = f"描述符失败（无有效字段）{name}: {res.get('message') or 'descriptors 全部为空'}"
                            if _log:
                                _log(msg, level="warning")
                            logger.warning(msg)
                    else:
                        fail += 1
                        msg = f"描述符失败 {name}: {res.get('message') or '未知原因'}"
                        if _log:
                            _log(msg, level="warning")
                        logger.warning(msg)
                except Exception as e:
                    fail += 1
                    msg = f"描述符结果处理异常 {name}: {e}"
                    if _log:
                        _log(msg, level="warning")
                    logger.warning(msg, exc_info=True)
            return {"count": total, "ok": ok, "fail": fail}

        def _write_cols(iid, v):
            try:
                for c, vv in v.items():
                    self.app.tree.set(iid, c, vv)
            except Exception:
                pass

        def _on_done(r):
            def _do():
                total = r.get('count', 0)
                ok = r.get('ok', 0)
                fail = r.get('fail', 0)
                if fail == 0:
                    self.app.helpers.on_log(
                        f"✅ 批量描述符完成：共 {total} 个文件，成功 {ok} 个（MW/LogP/TPSA 已写入表格对应列）",
                        "success")
                else:
                    self.app.helpers.on_log(
                        f"⚠️ 批量描述符完成：共 {total} 个文件，成功 {ok} / 失败 {fail}（详情见 WARNING 日志）",
                        "warning")
            self.app.after(0, _do)

        tm.run_async(_task, on_done=_on_done)

    def _get_paths_for_descriptor(self, only_selected: bool = False) -> list[tuple[str, str]]:
        """返回 [(iid, absolute_path)] 列表用于批量算描述符"""
        from pathlib import Path
        work = Path(self.app.work_dir_var.get()).resolve() if self.app.work_dir_var.get() else None
        if work is None:
            return []
        if only_selected:
            # 复选框多选模型：以勾选集合（文件名）为准，映射回当前可见行的 iid
            names = set(self.app.helpers.get_selected_filenames())
            items = [iid for iid in self.app.tree.get_children()
                     if str(self.app.tree.item(iid, "values")[1]) in names]
        else:
            items = list(self.app.tree.get_children())
        ret: list[tuple[str, str]] = []
        for iid in items:
            try:
                fname = str(self.app.tree.item(iid, "values")[0])
            except Exception:
                continue
            fp = work / fname
            if not fp.is_file():
                continue
            if fp.suffix.lower() not in ('.mol', '.sdf', '.xyz', '.cml', '.inchi', '.smiles', '.smi'):
                continue
            ret.append((iid, str(fp)))
        return ret

    # ================ O3：分子式 / 元素分析弹窗 ================
    def show_formula_dialog(self):
        self.app.dialogs.show_formula_dialog()

    # ================ O6：导出几何参数 CSV ================
    def export_geometry_csv(self):
        self.app.dialogs.export_geometry_csv()

    # ----- 对话框调用 -----
    def show_psi4_dialog(self):
        self.app.dialogs.show_psi4_dialog()

    def show_openbabel_dialog(self):
        self.app.dialogs.show_openbabel_dialog()

    def show_ext_filter_dialog(self):
        self.app.dialogs.show_ext_filter_dialog()

    def show_mapping_manager_dialog(self):
        self.app.dialogs.show_mapping_manager_dialog()

    def show_history_dialog(self):
        self.app.dialogs.show_history_dialog()

    def show_results_browser_dialog(self):
        self.app.dialogs.show_results_browser_dialog()

    def show_diff_sync_dialog(self):
        self.app.dialogs.show_diff_sync_dialog()

    def show_mapping_editor_dialog(self):
        self.app.dialogs.show_mapping_editor_dialog()

    def show_reaction_animation_dialog(self):
        self.app.dialogs.show_reaction_animation_dialog()

    # ================ 🛠️ 高级工具箱 ================
    def show_advanced_tools_dialog(self):
        self.app.dialogs.show_advanced_tools_dialog()