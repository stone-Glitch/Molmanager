#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
F17 备份管理对话框（T11 / Phase 1）
──────────────────────────────────
入口：菜单栏「⚙️ 设置 → 🗂️ 备份管理…」

界面结构：
    ┌─ 设置行：[✔ 启用自动备份]  每类保留 [10] 份   [💾 保存设置]
    ├─ 过滤行：类型 [全部 ▾]     备份目录：<path>   共 N 份 / 占用 X MB
    ├─ 左：快照列表（时间 / 类型 / 描述 / 文件数 / 大小）
    ├─ 右：选中快照的文件明细（文件名 / 大小 / 原位置是否还在）
    └─ 底部：[🔄 刷新] [⏮️ 回滚] [📤 导出到…] [🗑️ 删除] [🧹 清理超额] [关闭]

⚠️ 契约（架构 §6.4）：
  - 备份链路**只警告不抛异常**。本对话框所有对 BackupManager 的调用都包 try/except，
    失败只弹提示 + 写日志，绝不把异常冒泡到 Tk 事件循环。
  - 快照读写的都是 mapping/export/config 这类小文件（单文件上限 64MB），
    实测单次回滚 < 100ms，因此同步执行、不占用 TaskManager；
    这样也避免了「回滚跑一半用户点了别的按钮」的竞态。
  - 回滚前会自动生成 prerestore 保险快照（由 BackupManager 内部完成），
    用户回滚错版本仍有退路。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Any, List, Optional

from utils.dialog_geom import fit_dialog_geometry
from utils.logger import default_logger as logger
from utils import backup_manager as bm

#: 类型过滤下拉：显示名 → trigger 值（None 表示不过滤）
_TRIGGER_FILTER_ITEMS: List[tuple] = [
    ("全部", None),
    (bm.trigger_label(bm.TRIGGER_MAPPING), bm.TRIGGER_MAPPING),
    (bm.trigger_label(bm.TRIGGER_EXPORT), bm.TRIGGER_EXPORT),
    (bm.trigger_label(bm.TRIGGER_CONFIG), bm.TRIGGER_CONFIG),
    (bm.trigger_label(bm.TRIGGER_PRERESTORE), bm.TRIGGER_PRERESTORE),
]


def _log(app: Any, msg: str, level: str = "info") -> None:
    """统一日志出口：优先走主界面日志面板，失败退回 logger。"""
    try:
        helpers = getattr(app, "helpers", None)
        if helpers is not None and hasattr(helpers, "on_log"):
            helpers.on_log(msg, level)
            return
    except Exception:
        pass
    try:
        getattr(logger, "warning" if level in ("warning", "error") else "info")(msg)
    except Exception:
        pass


def show_backup_manager_dialog(app: Any, controller: Any) -> None:
    """打开备份管理对话框。任何异常都转成弹窗提示，不向上冒泡。"""
    try:
        _build_dialog(app, controller)
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️ 打开备份管理对话框失败: %s", exc)
        try:
            messagebox.showerror("打开失败", f"无法打开备份管理：\n{exc}", parent=app)
        except Exception:
            pass


def _build_dialog(app: Any, controller: Any) -> None:
    model = getattr(controller, "model", None)
    if model is None:
        messagebox.showerror("打开失败", "内部错误：未找到数据模型。", parent=app)
        return

    dialog = tk.Toplevel(app)
    dialog.title("🗂️ 备份管理（自动快照 / 回滚）")
    dialog.geometry(fit_dialog_geometry(dialog, 980, 620, min_w=760, min_h=460))
    dialog.resizable(True, True)
    dialog.transient(app)
    dialog.grab_set()

    cfg = getattr(app, "config_data", None)
    if not isinstance(cfg, dict):
        cfg = {}
    backup_cfg = cfg.get("backup") if isinstance(cfg.get("backup"), dict) else {}

    enabled_var = tk.BooleanVar(value=bool(backup_cfg.get("enabled", True)))
    try:
        keep_init = int(backup_cfg.get("keep_per_type", bm.DEFAULT_KEEP_PER_TYPE) or bm.DEFAULT_KEEP_PER_TYPE)
    except (TypeError, ValueError):
        keep_init = bm.DEFAULT_KEEP_PER_TYPE
    keep_var = tk.StringVar(value=str(max(1, keep_init)))
    filter_var = tk.StringVar(value=_TRIGGER_FILTER_ITEMS[0][0])
    summary_var = tk.StringVar(value="统计中…")
    root_var = tk.StringVar(value="备份目录：-")

    # 当前列表里的快照（索引与 Treeview iid 对应）
    snapshots: List[Any] = []

    # ---------------------------------------------------------- 顶部：设置行
    setting_row = ttk.LabelFrame(dialog, text="⚙️ 备份设置", padding=8)
    setting_row.pack(fill=tk.X, padx=10, pady=(10, 4))

    ttk.Checkbutton(setting_row, text="启用自动备份（保存映射表 / 导出 / 改配置前自动快照）",
                    variable=enabled_var).pack(side=tk.LEFT)
    ttk.Label(setting_row, text="  每类保留").pack(side=tk.LEFT, padx=(16, 2))
    ttk.Spinbox(setting_row, from_=1, to=100, width=5, textvariable=keep_var).pack(side=tk.LEFT)
    ttk.Label(setting_row, text="份").pack(side=tk.LEFT, padx=(2, 10))

    def _save_settings() -> None:
        """把开关 / 保留份数写回 config 并即时生效。"""
        try:
            keep = int(keep_var.get().strip() or bm.DEFAULT_KEEP_PER_TYPE)
        except (TypeError, ValueError):
            keep = bm.DEFAULT_KEEP_PER_TYPE
        keep = max(1, min(100, keep))
        keep_var.set(str(keep))
        try:
            section = cfg.get("backup")
            if not isinstance(section, dict):
                section = {}
                cfg["backup"] = section
            section["enabled"] = bool(enabled_var.get())
            section["keep_per_type"] = keep
            from utils.config import save_config
            save_config(cfg)
            model.configure_backup(section)
            _log(app, f"🗂️ 备份设置已保存：{'启用' if section['enabled'] else '停用'}，每类保留 {keep} 份", "success")
        except Exception as exc:  # noqa: BLE001
            logger.warning("⚠️ 保存备份设置失败: %s", exc)
            messagebox.showwarning("保存失败", f"备份设置未能保存：\n{exc}", parent=dialog)
            return
        _refresh()

    ttk.Button(setting_row, text="💾 保存设置", command=_save_settings).pack(side=tk.LEFT)

    # ---------------------------------------------------------- 过滤 / 概览行
    filter_row = ttk.Frame(dialog)
    filter_row.pack(fill=tk.X, padx=10, pady=(0, 4))
    ttk.Label(filter_row, text="类型：").pack(side=tk.LEFT)
    filter_combo = ttk.Combobox(
        filter_row, textvariable=filter_var, state="readonly", width=14,
        values=[name for name, _v in _TRIGGER_FILTER_ITEMS],
    )
    filter_combo.pack(side=tk.LEFT, padx=(0, 12))
    ttk.Label(filter_row, textvariable=summary_var).pack(side=tk.LEFT)
    ttk.Label(filter_row, textvariable=root_var, foreground="#7A8699").pack(side=tk.RIGHT)

    # ---------------------------------------------------------- 主体：双栏
    main = ttk.Frame(dialog)
    main.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)

    left = ttk.LabelFrame(main, text="📜 快照列表（新→旧）", padding=4)
    left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

    cols = ("time", "type", "desc", "count", "size")
    tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
    tree.heading("time", text="时间")
    tree.heading("type", text="类型")
    tree.heading("desc", text="描述")
    tree.heading("count", text="文件数")
    tree.heading("size", text="大小")
    tree.column("time", width=150, anchor=tk.W, stretch=False)
    tree.column("type", width=90, anchor=tk.W, stretch=False)
    tree.column("desc", width=220, anchor=tk.W)
    tree.column("count", width=60, anchor=tk.CENTER, stretch=False)
    tree.column("size", width=80, anchor=tk.E, stretch=False)
    tree_scroll = ttk.Scrollbar(left, orient=tk.VERTICAL, command=tree.yview)
    tree.configure(yscrollcommand=tree_scroll.set)
    tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
    tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    right = ttk.LabelFrame(main, text="📄 快照内容", padding=4)
    right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 0))

    dcols = ("name", "size", "exists")
    detail = ttk.Treeview(right, columns=dcols, show="headings", selectmode="browse")
    detail.heading("name", text="文件")
    detail.heading("size", text="大小")
    detail.heading("exists", text="原位置")
    detail.column("name", width=210, anchor=tk.W)
    detail.column("size", width=80, anchor=tk.E, stretch=False)
    detail.column("exists", width=90, anchor=tk.CENTER, stretch=False)
    detail_scroll = ttk.Scrollbar(right, orient=tk.VERTICAL, command=detail.yview)
    detail.configure(yscrollcommand=detail_scroll.set)
    detail_scroll.pack(side=tk.RIGHT, fill=tk.Y)
    detail.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    path_var = tk.StringVar(value="选中左侧快照可查看其包含的文件。")
    ttk.Label(dialog, textvariable=path_var, foreground="#7A8699",
              anchor=tk.W, wraplength=940).pack(fill=tk.X, padx=12, pady=(0, 2))

    # ---------------------------------------------------------- 数据操作
    def _manager():
        """取 BackupManager；取不到时提示并返回 None。"""
        try:
            mgr = model.backup_manager
        except Exception as exc:  # noqa: BLE001
            logger.warning("⚠️ 获取备份管理器失败: %s", exc)
            mgr = None
        if mgr is None:
            messagebox.showwarning(
                "备份不可用",
                "备份管理器初始化失败，可能是工作目录不可写。\n请检查日志面板中的警告信息。",
                parent=dialog,
            )
        return mgr

    def _current_trigger() -> Optional[str]:
        label = filter_var.get()
        for name, value in _TRIGGER_FILTER_ITEMS:
            if name == label:
                return value
        return None

    def _selected_meta():
        sel = tree.selection()
        if not sel:
            return None
        try:
            idx = int(sel[0])
        except (TypeError, ValueError):
            return None
        if 0 <= idx < len(snapshots):
            return snapshots[idx]
        return None

    def _clear_detail() -> None:
        for iid in detail.get_children():
            detail.delete(iid)

    def _refresh(*_args: Any) -> None:
        """重新拉取快照列表 + 概览统计。"""
        nonlocal snapshots
        for iid in tree.get_children():
            tree.delete(iid)
        _clear_detail()
        path_var.set("选中左侧快照可查看其包含的文件。")
        snapshots = []

        mgr = None
        try:
            mgr = model.backup_manager
        except Exception as exc:  # noqa: BLE001
            logger.warning("⚠️ 获取备份管理器失败: %s", exc)
        if mgr is None:
            summary_var.set("备份不可用")
            root_var.set("备份目录：-")
            return

        try:
            root_var.set(f"备份目录：{mgr.backup_root}")
        except Exception:
            root_var.set("备份目录：-")

        try:
            snapshots = list(mgr.list_snapshots(_current_trigger()))
        except Exception as exc:  # noqa: BLE001
            logger.warning("⚠️ 读取快照列表失败: %s", exc)
            snapshots = []

        for idx, meta in enumerate(snapshots):
            try:
                tree.insert(
                    "", tk.END, iid=str(idx),
                    values=(meta.display_time(), meta.trigger_label,
                            meta.description or "-", meta.file_count, meta.size_text),
                )
            except Exception:
                continue

        try:
            total_txt = bm.format_size(mgr.total_size())
        except Exception:
            total_txt = "-"
        state = "启用" if enabled_var.get() else "已停用"
        summary_var.set(f"共 {len(snapshots)} 份快照 / 占用 {total_txt} / 自动备份{state}")

    def _on_select(_event: Any = None) -> None:
        meta = _selected_meta()
        _clear_detail()
        if meta is None:
            path_var.set("选中左侧快照可查看其包含的文件。")
            return
        mgr = None
        try:
            mgr = model.backup_manager
        except Exception:
            mgr = None
        if mgr is None:
            return
        try:
            rows = mgr.preview_snapshot(meta.snapshot_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("⚠️ 读取快照内容失败: %s", exc)
            rows = []
        for i, row in enumerate(rows):
            mark = "仍存在" if row.get("exists_now") else "已丢失"
            if not row.get("stored_exists"):
                mark = "备份缺失"
            try:
                detail.insert("", tk.END, iid=str(i),
                              values=(row.get("orig_name", "?"), row.get("size_text", "-"), mark))
            except Exception:
                continue
        try:
            path_var.set(f"快照 ID：{meta.snapshot_id}    应用版本：{meta.app_version or '-'}")
        except Exception:
            pass

    tree.bind("<<TreeviewSelect>>", _on_select)
    filter_combo.bind("<<ComboboxSelected>>", _refresh)

    # ---------------------------------------------------------- 动作按钮
    def _do_restore() -> None:
        meta = _selected_meta()
        if meta is None:
            messagebox.showinfo("请先选择", "请先在左侧选中一份快照。", parent=dialog)
            return
        mgr = _manager()
        if mgr is None:
            return
        try:
            rows = mgr.preview_snapshot(meta.snapshot_id)
        except Exception:
            rows = []
        overwrite = sum(1 for r in rows if r.get("exists_now"))
        if not messagebox.askyesno(
            "确认回滚",
            f"即将把快照【{meta.display_time()} · {meta.trigger_label}】还原回原位置：\n\n"
            f"  • 快照内文件：{meta.file_count} 个（{meta.size_text}）\n"
            f"  • 将被覆盖的现有文件：{overwrite} 个\n\n"
            f"回滚前会自动为被覆盖的文件生成一份保险快照，可再次回滚撤销本次操作。\n"
            f"确定继续吗？",
            parent=dialog,
        ):
            return
        try:
            restored, errors = mgr.restore_snapshot(meta.snapshot_id, pre_snapshot=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("⚠️ 回滚失败: %s", exc)
            messagebox.showerror("回滚失败", f"回滚过程中出错：\n{exc}", parent=dialog)
            return

        if errors:
            _log(app, f"⚠️ 回滚完成但有 {len(errors)} 项失败：{errors[0]}", "warning")
            messagebox.showwarning(
                "部分回滚失败",
                f"成功还原 {restored} 个文件，{len(errors)} 个失败：\n\n" + "\n".join(errors[:8]),
                parent=dialog,
            )
        else:
            _log(app, f"⏮️ 已从快照 {meta.snapshot_id} 还原 {restored} 个文件", "success")
            messagebox.showinfo("回滚完成", f"已成功还原 {restored} 个文件。", parent=dialog)

        # 映射表被还原后要让主界面重新加载，否则内存里还是旧数据
        if restored and meta.trigger == bm.TRIGGER_MAPPING:
            _reload_mapping_after_restore()
        _refresh()

    def _reload_mapping_after_restore() -> None:
        """映射表回滚后重新载入并刷新文件列表（失败只记日志）。"""
        try:
            mapping_path = model.default_mapping_path()
            if mapping_path.is_file() and hasattr(model, "load_mapping_file"):
                model.load_mapping_file(str(mapping_path))
                _log(app, f"🔄 已重新载入映射表：{mapping_path.name}", "info")
        except Exception as exc:  # noqa: BLE001
            logger.warning("⚠️ 回滚后重新载入映射表失败: %s", exc)
        try:
            if hasattr(controller, "scan_files"):
                controller.scan_files()
        except Exception as exc:  # noqa: BLE001
            logger.debug("回滚后刷新文件列表失败: %s", exc)

    def _do_export() -> None:
        """把快照内容导出（另存）到用户指定目录，不覆盖原位置。"""
        meta = _selected_meta()
        if meta is None:
            messagebox.showinfo("请先选择", "请先在左侧选中一份快照。", parent=dialog)
            return
        mgr = _manager()
        if mgr is None:
            return
        target = filedialog.askdirectory(title="选择导出目录（快照文件将按原文件名写入）", parent=dialog)
        if not target:
            return
        try:
            restored, errors = mgr.restore_snapshot(meta.snapshot_id, target_dir=target, pre_snapshot=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("⚠️ 导出快照失败: %s", exc)
            messagebox.showerror("导出失败", f"导出过程中出错：\n{exc}", parent=dialog)
            return
        if errors:
            messagebox.showwarning(
                "部分导出失败",
                f"成功导出 {restored} 个文件，{len(errors)} 个失败：\n\n" + "\n".join(errors[:8]),
                parent=dialog,
            )
        else:
            messagebox.showinfo("导出完成", f"已导出 {restored} 个文件到：\n{target}", parent=dialog)
        _log(app, f"📤 快照 {meta.snapshot_id} 已导出 {restored} 个文件到 {target}", "info")

    def _do_delete() -> None:
        meta = _selected_meta()
        if meta is None:
            messagebox.showinfo("请先选择", "请先在左侧选中一份快照。", parent=dialog)
            return
        mgr = _manager()
        if mgr is None:
            return
        if not messagebox.askyesno(
            "确认删除",
            f"确定删除快照【{meta.display_time()} · {meta.trigger_label}】吗？\n"
            f"删除后该版本将无法恢复。",
            parent=dialog,
        ):
            return
        ok = False
        try:
            ok = mgr.delete_snapshot(meta.snapshot_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("⚠️ 删除快照失败: %s", exc)
        if ok:
            _log(app, f"🗑️ 已删除快照 {meta.snapshot_id}", "info")
        else:
            messagebox.showwarning("删除失败", "快照未能删除，可能文件被占用。", parent=dialog)
        _refresh()

    def _do_prune() -> None:
        mgr = _manager()
        if mgr is None:
            return
        try:
            keep = int(keep_var.get().strip() or bm.DEFAULT_KEEP_PER_TYPE)
        except (TypeError, ValueError):
            keep = bm.DEFAULT_KEEP_PER_TYPE
        if not messagebox.askyesno(
            "确认清理",
            f"将按保留策略清理超额快照：每种类型只保留最近 {keep} 份。\n确定继续吗？",
            parent=dialog,
        ):
            return
        removed = 0
        try:
            removed = mgr.prune()
        except Exception as exc:  # noqa: BLE001
            logger.warning("⚠️ 清理快照失败: %s", exc)
        _log(app, f"🧹 已清理 {removed} 份超额快照", "info")
        messagebox.showinfo("清理完成", f"已清理 {removed} 份超额快照。", parent=dialog)
        _refresh()

    btn_row = ttk.Frame(dialog)
    btn_row.pack(fill=tk.X, padx=10, pady=(4, 10))
    ttk.Button(btn_row, text="🔄 刷新", command=_refresh).pack(side=tk.LEFT, padx=3)
    ttk.Button(btn_row, text="⏮️ 回滚到此快照", command=_do_restore).pack(side=tk.LEFT, padx=3)
    ttk.Button(btn_row, text="📤 导出到…", command=_do_export).pack(side=tk.LEFT, padx=3)
    ttk.Button(btn_row, text="🗑️ 删除快照", command=_do_delete).pack(side=tk.LEFT, padx=3)
    ttk.Button(btn_row, text="🧹 清理超额", command=_do_prune).pack(side=tk.LEFT, padx=3)
    ttk.Button(btn_row, text="关闭", command=dialog.destroy).pack(side=tk.RIGHT, padx=3)

    _refresh()


__all__ = ["show_backup_manager_dialog"]
