#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PSI4 持久热 worker（被 run_psi4_task_cancellable 以 `python -m chem.psi4._worker` 启动）。

与 _subprocess_runner 的区别：本进程**只启动一次**，在启动时把昂贵的 psi4 导入做完，
之后通过 stdin 逐行接收 JSON 命令（每个命令 = 一次 run_psi4_task 调用），把结果写到命令里
指定的 result 文件、进度写到 progress 文件，然后继续读下一行。这样一批计算只需承担一次
psi4 导入开销 —— 这是消除「每次计算都重导 psi4 约 10~15s」的关键优化。

取消 / 超时由父进程通过杀掉本进程树实现（PSI4 C++ 调用本身不可中断）；强杀后父进程会按需
重启本 worker（下一次计算再次承担一次导入，仅取消时付出该代价）。
"""
import json
import sys
import traceback


def _progress_to_file(progress_path, percent, msg):
    try:
        with open(progress_path, "a", encoding="utf-8") as pf:
            pf.write(json.dumps({"p": percent, "m": msg}, ensure_ascii=False) + "\n")
            pf.flush()
    except Exception:
        pass


def main():
    # —— 启动时一次性导入 psi4（最贵的步骤，之后常驻复用）——
    try:
        import psi4  # noqa: F401  触发 PSI4 加载，失败则本 worker 不可用
    except Exception as e:
        sys.stderr.write(f"worker: psi4 导入失败，worker 退出: {e}\n")
        return 1

    try:
        from chem.psi4.core import run_psi4_task
    except Exception as e:
        sys.stderr.write(f"worker: 导入 run_psi4_task 失败: {e}\n")
        return 1

    def _default(o):
        try:
            return str(o)
        except Exception:
            return "<unserializable>"

    # —— 逐行读取命令；父进程关闭 stdin（EOF）时自然退出 ——
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
        except Exception:
            continue

        result_path = cmd.pop("result_path", None)
        progress_path = cmd.pop("progress_path", None)
        cmd.pop("_extra_post_hook", None)

        def _prog(p, m, _pp=progress_path):
            if _pp:
                _progress_to_file(_pp, p, m)

        cmd["_progress_callback"] = _prog

        try:
            result = run_psi4_task(**cmd)
        except Exception as e:
            result = {
                "success": False,
                "error": f"worker 计算异常: {e}",
                "trace": traceback.format_exc(),
            }

        if result_path:
            try:
                with open(result_path, "w", encoding="utf-8") as rf:
                    json.dump(result, rf, default=_default, ensure_ascii=False)
            except Exception:
                pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
