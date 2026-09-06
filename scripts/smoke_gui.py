#!/usr/bin/env python3
"""MolManager GUI 冒烟测试（无显示器环境可用）。

用法：
    xvfb-run -a python scripts/smoke_gui.py                    # 纯冒烟
    xvfb-run -a python scripts/smoke_gui.py --screenshot out.png
    xvfb-run -a python scripts/smoke_gui.py --screenshot a.png --theme light

流程：实例化 MainView（全链路装配 helpers/controller/build_ui）→ 真实
mainloop 事件循环中遍历 6 个页面 → 依次调起一组对话框（定时销毁）→
汇总失败清单（有失败退出码 1）。

⚠️ 刻意避开会阻塞事件循环的对话框：mapping_dialog 的 diff 预览
（wait_window）与 update_dialog（app.wait_window）。messagebox 全套
被替换为记录桩，防止环境检查类弹窗在无人值守环境下永久阻塞。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import traceback

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

_MB_CALLS: list[tuple[str, str]] = []


def _stub_messagebox() -> None:
    """把 tkinter.messagebox 的模态弹窗替换为记录桩（无人值守防阻塞）。"""
    import tkinter.messagebox as mb

    for name in ("showinfo", "showwarning", "showerror", "askokcancel", "askyesno", "askyesnocancel", "askquestion"):
        def _make(n: str):
            def _record(title="", message="", **kw):
                _MB_CALLS.append((n, str(message)[:80]))
                return True  # ask* 一律回答"是"
            return _record
        setattr(mb, name, _make(name))


def screenshot(tk_root, path: str) -> bool:
    """截取当前屏幕。优先 PIL（支持 xdisplay），回退 xwd+convert。"""
    display = os.environ.get("DISPLAY", "")
    try:
        from PIL import ImageGrab

        img = ImageGrab.grab(xdisplay=display or None)
        img.save(path)
        return True
    except Exception:
        pass
    try:
        tmp = path + ".xwd"
        r = subprocess.run(
            ["xwd", "-root", "-display", display, "-out", tmp],
            check=False, capture_output=True, timeout=10,
        )
        if r.returncode != 0:
            return False
        r2 = subprocess.run(["convert", tmp, path], check=False, capture_output=True, timeout=10)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return r2.returncode == 0
    except Exception:
        return False


def _new_toplevels(app, before: set) -> list:
    found = []
    try:
        for w in app.winfo_children():
            try:
                if str(w.winfo_class()) == "Toplevel" and w not in before:
                    found.append(w)
            except Exception:
                continue
    except Exception:
        pass
    return found


def smoke() -> list[str]:
    from core.view import MainView

    failures: list[str] = []
    dialog_report: list[str] = []

    def step_dialogs(app, cases, idx: int) -> None:
        if idx >= len(cases):
            app.after(200, app.destroy)
            return
        name, fn, expect = cases[idx]
        try:
            before = set(app.winfo_children())
            fn()
            toplevels = _new_toplevels(app, before)
            if toplevels:
                dialog_report.append(f"  ✔ {name}: Toplevel ×{len(toplevels)}")
                for dlg in toplevels:
                    app.after(600, dlg.destroy)
            elif _MB_CALLS:
                # 无 Toplevel 但弹了 messagebox（如环境缺失提示）→ 走到了提示分支，可接受
                dialog_report.append(f"  ✔ {name}: messagebox 提示 ×{len(_MB_CALLS)}（无独立窗口）")
                _MB_CALLS.clear()
            elif expect == "window_or_log":
                dialog_report.append(f"  ✔ {name}: 无窗口（提前返回，属预期分支）")
            else:
                failures.append(f"对话框 {name}: 未产生 Toplevel 也无 messagebox（函数可能提前返回）")
        except Exception as e:
            failures.append(f"对话框 {name}: {e}\n{traceback.format_exc()}")
        app.after(700, lambda: step_dialogs(app, cases, idx + 1))

    def step_pages(app, cases) -> None:
        for i in range(6):
            try:
                app._show_page(i)
            except Exception as e:
                failures.append(f"页面 {i} 切换失败: {e}\n{traceback.format_exc()}")
        app.after(300, lambda: step_dialogs(app, cases, 0))

    _stub_messagebox()
    app = MainView()
    app.update_idletasks()

    from ui.dialogs.error_diagnosis import show_error_diagnosis

    cases = [
        ("history", lambda: app.dialogs.show_history_dialog(), "toplevel"),
        ("backup", lambda: app.dialogs.show_backup_manager_dialog(), "toplevel"),
        ("sync", lambda: app.dialogs.show_diff_sync_dialog(), "toplevel"),
        ("mapping_manager", lambda: app.dialogs.show_mapping_manager_dialog(), "toplevel"),
        (
            "error_diagnosis",
            lambda: show_error_diagnosis(app, "SmokeTest: FileNotFoundError: demo.xyz", "大白话：文件不见了"),
            "toplevel",
        ),
        # psi4：无选中文件时按设计走日志提示后提前返回（不弹窗），属正确行为
        ("psi4", lambda: app.dialogs.show_psi4_dialog(), "window_or_log"),
        ("openbabel", lambda: app.dialogs.show_openbabel_dialog(), "toplevel"),
    ]
    app.after(500, lambda: step_pages(app, cases))
    app.mainloop()

    print("对话框调起报告：")
    print("\n".join(dialog_report) if dialog_report else "  （无）")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="MolManager GUI 冒烟测试")
    parser.add_argument("--screenshot", metavar="OUT", help="截图输出路径（PNG）")
    parser.add_argument("--theme", choices=["dark", "light"], default="dark", help="截图前切换主题")
    args = parser.parse_args()

    failures = smoke()

    # 独立截图进程（smoke 已 destroy 主窗口，需要重新实例化一次拿干净画面）
    if args.screenshot:
        try:
            from core.view import MainView

            if args.theme == "light":
                from ui import ui_theme

                ui_theme.set_current_theme("light")
                ui_theme.save_theme_preference("light")
            app = MainView()
            app.update_idletasks()
            app.update()
            app._show_page(1)  # 文件管理页（内容最丰富）
            app.update()
            ok = screenshot(app, args.screenshot)
            app.destroy()
            if not ok:
                failures.append(f"截图失败：{args.screenshot}")
            else:
                print(f"📸 截图已保存: {args.screenshot}")
        except Exception as e:
            failures.append(f"截图流程异常: {e}\n{traceback.format_exc()}")

    if failures:
        print(f"\n❌ 冒烟失败 {len(failures)} 项：")
        for f in failures:
            print("─" * 60)
            print(f)
        return 1
    print("\n✅ GUI 冒烟全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
