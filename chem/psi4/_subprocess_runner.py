#!/usr/bin/env python3
"""
PSI4 子进程运行器（被 run_psi4_task_cancellable 以 sys.executable 启动）。

用法：
    python _subprocess_runner.py <cmd.json> <result.json> <progress.jsonl>

该进程在**独立解释器**里调用真正的 run_psi4_task，把进度以 JSON 行追加写入
progress 文件，计算结束后把结果 JSON 写入 result 文件。这样主进程可以通过
杀死本进程（含 PSI4 起的子进程树）来强制取消一次长计算 —— 这是 F03 队列调度器
实现「取消 / 超时终止」的硬地基。
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
    if len(sys.argv) < 4:
        sys.stderr.write("usage: _subprocess_runner.py <cmd.json> <result.json> <progress.jsonl>\n")
        return 2
    cmd_path, result_path, progress_path = sys.argv[1], sys.argv[2], sys.argv[3]

    try:
        with open(cmd_path, encoding="utf-8") as f:
            cmd = json.load(f)
    except Exception as e:
        _write_error(result_path, f"无法读取命令文件: {e}")
        return 1

    # 子进程用自己的进度回调把进度落盘，主进程轮询该文件转发给 UI。
    cmd["_progress_callback"] = lambda p, m: _progress_to_file(progress_path, p, m)
    # 不把不可序列化的钩子透传给子进程（交互式调用均不传这两个）。
    cmd.pop("_extra_post_hook", None)

    try:
        from chem.psi4.core import run_psi4_task
    except Exception as e:
        _write_error(result_path, f"子进程导入 run_psi4_task 失败: {e}\n{traceback.format_exc()}")
        return 1

    try:
        result = run_psi4_task(**cmd)
    except Exception as e:
        result = {
            "success": False,
            "error": f"子进程计算异常: {e}",
            "trace": traceback.format_exc(),
        }

    def _default(o):
        try:
            return str(o)
        except Exception:
            return "<unserializable>"

    try:
        with open(result_path, "w", encoding="utf-8") as rf:
            json.dump(result, rf, default=_default, ensure_ascii=False)
    except Exception as e:
        sys.stderr.write(f"写入结果失败: {e}\n")
        return 1
    return 0


def _write_error(result_path, msg):
    try:
        with open(result_path, "w", encoding="utf-8") as rf:
            json.dump({"success": False, "error": msg}, rf, ensure_ascii=False)
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
