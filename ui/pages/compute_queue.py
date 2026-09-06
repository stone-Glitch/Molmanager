"""📊 任务队列页（自 ui_builder._tabs.py 机械拆分，行为不变）。"""

import time
import tkinter as tk
from tkinter import ttk

import ui.ui_theme as ui_theme
from ui.ui_theme import COLORS, dark_card, themed_button
from ui.ui_builder._theme import add_tooltip

# ===========================================================
# 📊 Tab4：任务队列（设计落地 Phase 5）
# ===========================================================


def _status_cn(st):
    return {"running": "运行中", "success": "成功", "failed": "失败", "cancelled": "已取消", "queued": "排队"}.get(
        st, st
    )


def _open_queue_log_drawer(app, job):
    """队列任务日志右侧滑出面板（设计落地 Phase 5）。"""
    # 防重复：同一任务已开则置顶
    for w in app.winfo_children():
        if getattr(w, "_is_log_drawer", False) and getattr(w, "_drawer_job_id", None) == job.get("id"):
            try:
                w.lift()
            except Exception:
                pass
            return
    dlg = tk.Toplevel(app)
    dlg._is_log_drawer = True
    dlg._drawer_job_id = job.get("id")
    dlg.transient(app)
    dlg.overrideredirect(True)
    dlg.title("任务日志")
    P = ui_theme.get_palette()
    dlg.configure(bg=P["border_strong"])

    try:
        sw = app.winfo_screenwidth()
        sh = app.winfo_screenheight()
    except Exception:
        sw, sh = 1920, 1080
    H = min(600, sh - 60)
    W = 420
    x = max(0, sw - W - 10)
    y = 30
    dlg.geometry(f"{W}x{H}+{x}+{y}")

    # 头部
    head = tk.Frame(dlg, bg=P["surface"], bd=0)
    head.pack(fill=tk.X, padx=1, pady=1)
    tk.Label(
        head,
        text="📜 任务日志 · %s" % job.get("name", ""),
        bg=P["surface"],
        fg=P["text"],
        font=("Microsoft YaHei", 12, "bold"),
        anchor="w",
        padx=12,
        pady=8,
    ).pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _close():
        try:
            dlg.destroy()
        except Exception:
            pass

    tk.Button(
        head,
        text="✕",
        command=_close,
        relief=tk.FLAT,
        bd=0,
        bg=P["surface"],
        fg=P["text_secondary"],
        activebackground=P["border"],
        activeforeground=P["accent"],
        font=("Microsoft YaHei", 12),
        cursor="hand2",
        width=3,
        padx=6,
        pady=4,
    ).pack(side=tk.RIGHT, padx=6, pady=4)

    # 日志正文
    body = tk.Frame(dlg, bg=P["input"], bd=1, relief=tk.SOLID, highlightbackground=P["border"], highlightthickness=1)
    body.pack(fill=tk.BOTH, expand=True, padx=1, pady=(0, 1))
    txt = tk.Text(
        body, bg=P["input"], fg=P["text"], relief=tk.FLAT, bd=0, font=("Consolas", 10), wrap=tk.WORD, state=tk.DISABLED
    )
    txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=8, pady=8)
    sb = tk.Scrollbar(body, command=txt.yview, bg=P["surface"], troughcolor=P["bg"], bd=0, relief=tk.FLAT)
    sb.pack(side=tk.RIGHT, fill=tk.Y)
    txt.config(yscrollcommand=sb.set)
    logs = job.get("log") or []
    txt.configure(state=tk.NORMAL)
    if logs:
        for ln in logs:
            txt.insert(tk.END, ln + "\n")
    else:
        txt.insert(tk.END, "（暂无日志输出）")
    txt.configure(state=tk.DISABLED)

    dlg.bind("<Escape>", lambda e: _close())


def build_tab_compute_queue(app, parent):
    """
    任务队列页（统一后台任务可视化，设计落地 Phase 5）：
      - 工具条：取消当前任务 / 清除已完成 / 并发度下拉（1/2/4/8，持久化）
      - 任务表：# / 名称 / 类型 / 方法-基组 / 状态 / 进度 / 耗时 / 操作
      - 每行操作：日志（右侧滑出抽屉）、失败行额外「诊断」（F07）
      - 无任务时显示空状态引导
    数据来自 app.task_manager.jobs（由 run_task→submit 接入，Phase 5 包装）。
    """
    parent.grid_rowconfigure(1, weight=1)
    parent.grid_columnconfigure(0, weight=1)
    F = getattr(app, "_fonts", {}) or {}

    # —— 工具条 ——
    tool = tk.Frame(
        parent,
        bg=COLORS["card_bg"],
        bd=1,
        relief=tk.SOLID,
        highlightbackground=COLORS["card_border"],
        highlightthickness=1,
    )
    tool.grid(row=0, column=0, sticky="ew", padx=8, pady=(10, 6))
    tool.grid_columnconfigure(5, weight=1)

    def _cancel_current():
        try:
            app.task_manager.request_cancel()
            app.helpers.on_log("⏹ 已请求取消当前任务", "warning")
        except Exception:
            pass

    def _clear_finished():
        try:
            with app.task_manager._jobs_lock:
                app.task_manager.jobs = [j for j in app.task_manager.jobs if j.get("status") == "running"]
            refresh_queue()
            app.helpers.on_log("🧹 已清除已完成任务", "info")
        except Exception:
            pass

    themed_button(
        tool, "⏹ 取消当前任务", _cancel_current, "warning", tip="请求取消正在运行的任务（协作式，下次进度上报时中止）"
    ).pack(side=tk.LEFT, padx=4, pady=6)
    themed_button(
        tool, "🧹 清除已完成", _clear_finished, "secondary", tip="从列表中移除成功 / 失败 / 已取消的任务"
    ).pack(side=tk.LEFT, padx=4, pady=6)

    # 并发度下拉（持久化；当前常驻 worker 串行执行，此值为规划档位）
    tk.Label(
        tool,
        text="并发度:",
        bg=COLORS["card_bg"],
        fg=COLORS["text_light"],
        font=F.get("SMALL", ("Microsoft YaHei", 11)),
    ).pack(side=tk.LEFT, padx=(16, 4), pady=6)
    _conc_var = tk.StringVar(value=str(int(app.config_data.get("queue_concurrency", 2) or 2)))
    _conc = ttk.Combobox(
        tool,
        textvariable=_conc_var,
        values=["1", "2", "4", "8"],
        width=5,
        state="readonly",
        font=F.get("BASE", ("Microsoft YaHei", 12)),
    )
    _conc.pack(side=tk.LEFT, padx=2, pady=6)
    add_tooltip(_conc, "同时运行的任务数（1=串行，2/4/8=并行）。实时生效并保存到配置。")

    def _on_conc(_e=None):
        try:
            v = int(_conc_var.get())
            app.config_data["queue_concurrency"] = v
            from utils.config import save_config

            save_config(app.config_data)
            # 实时驱动常驻 worker 池并发度（无需重启）
            tm = getattr(app, "task_manager", None)
            if tm is not None and hasattr(tm, "set_concurrency"):
                tm.set_concurrency(v)
            app.helpers.on_log("🔧 并发度已设为 %d（实时生效，已保存）" % v, "info")
        except Exception:
            pass

    _conc.bind("<<ComboboxSelected>>", _on_conc)

    # —— 任务表 ——
    tbl_card = dark_card(parent)
    tbl_card.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
    tbl_card.grid_rowconfigure(0, weight=1)
    tbl_card.grid_columnconfigure(0, weight=1)

    cols = ("#", "名称", "类型", "方法-基组", "状态", "进度", "耗时", "操作")
    tree = ttk.Treeview(tbl_card, columns=cols, show="headings", height=14)
    for c, w in zip(cols, (4, 22, 12, 18, 10, 10, 10, 22), strict=False):
        tree.heading(c, text=c)
        tree.column(c, width=w, anchor=tk.W if c in ("名称", "类型", "方法-基组") else tk.CENTER)
    tree.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
    vsb = ttk.Scrollbar(tbl_card, command=tree.yview)
    vsb.grid(row=0, column=1, sticky="ns", pady=10)
    tree.configure(yscrollcommand=vsb.set)

    # 状态色标签
    P = ui_theme.get_palette()
    tree.tag_configure("st_running", foreground=P["link"])
    tree.tag_configure("st_success", foreground=P["success"])
    tree.tag_configure("st_failed", foreground=P["danger"])
    tree.tag_configure("st_cancelled", foreground=P["text_muted"])

    app._queue_tree = tree

    # —— 空状态 ——
    es = tk.Frame(tbl_card, bg=COLORS["surface"])
    es.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
    es.grid_remove()
    tk.Label(
        es, text="📭  暂无任务", bg=COLORS["surface"], fg=COLORS["text"], font=("Microsoft YaHei", 15, "bold")
    ).pack(anchor="center", pady=(40, 6))
    tk.Label(
        es,
        text="去「计算与动画」提交 PSI4 计算，或运行文件整理 / OpenBabel 工具，\n"
        "任务会自动出现在这里并实时显示进度与日志。",
        bg=COLORS["surface"],
        fg=COLORS["text_secondary"],
        font=("Microsoft YaHei", 11),
        justify="center",
    ).pack(anchor="center")
    app._queue_empty = es

    def _fmt_dur(j):
        try:
            s = (j.get("finished") or time.time()) - j.get("started", time.time())
            return "%.0fs" % max(0, s)
        except Exception:
            return "—"

    def _open_diag(job):
        try:
            app.show_error_diagnosis(job.get("error", ""), summary="任务失败：%s" % job.get("name", ""))
        except Exception:
            pass

    def _on_click(event):
        region = tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        row = tree.identify_row(event.y)
        col = tree.identify_column(event.x)
        if not row or col != "#8":  # 仅「操作」列响应
            return
        job = getattr(tree, "_job_map", {}).get(row)
        if not job:
            return
        if job.get("status") == "failed" and "诊断" in tree.set(row, "操作"):
            _open_diag(job)
        else:
            _open_queue_log_drawer(app, job)

    tree.bind("<Button-1>", _on_click)

    def refresh_queue():
        jobs = getattr(app.task_manager, "jobs", [])
        with app.task_manager._jobs_lock:
            snapshot = list(jobs)
        tree.delete(*tree.get_children())
        tree._job_map = {}
        if not snapshot:
            es.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
            return
        es.grid_remove()
        for j in snapshot:
            st = j.get("status", "running")
            tag = {
                "running": "st_running",
                "success": "st_success",
                "failed": "st_failed",
                "cancelled": "st_cancelled",
            }.get(st, "st_running")
            op = "日志 · 诊断" if st == "failed" else "日志"
            vals = (
                j.get("id", ""),
                j.get("name", ""),
                j.get("kind", ""),
                j.get("spec", "—"),
                _status_cn(st),
                "%d%%" % j.get("progress", 0),
                _fmt_dur(j),
                op,
            )
            iid = tree.insert("", tk.END, values=vals, tags=(tag,))
            tree._job_map[iid] = j

    app.refresh_queue = refresh_queue

    # 周期性刷新（仅队列页可见时刷新，省开销）
    def _poll():
        try:
            if getattr(app, "_cur_page", 0) == 5:  # 任务队列（工作台/文件管理/分子映射/计算/高级已占前 5 页）
                refresh_queue()
        except Exception:
            pass
        try:
            app.after(700, _poll)
        except Exception:
            pass

    refresh_queue()
    app.after(700, _poll)
