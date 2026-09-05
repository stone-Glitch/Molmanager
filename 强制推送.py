#!/usr/bin/env python3
"""MolManager —— 一键强制推送到 GitHub（跨平台）。

用途：把本地当前分支强制同步到远程分支（覆盖远程历史）。

安全设计：
    - 全程使用 ``subprocess`` 参数列表形式，无 ``shell=True``，
      杜绝 shell 元字符注入。
    - 使用 ``--force-with-lease`` 而非裸 ``--force``：远端存在本地未知的新
      提交时会主动拒绝，避免静默覆盖他人工作。
    - 推送前 fetch 远端、列出双方差异，并需显式输入 y 才继续。

用法：
    python 强制推送.py              # 推送当前分支
    python 强制推送.py main         # 指定分支
    python 强制推送.py main --yes   # 跳过确认（慎用，CI/脚本场景）

前置条件：
    1. 已安装 git；
    2. SSH key 已添加到 GitHub（remote 形如 git@github.com:...）；
    3. 在本仓库根目录下运行。
"""

from __future__ import annotations

import subprocess
import sys


def run_git(args: list[str]) -> tuple[int, str]:
    """执行 git 命令，返回 (退出码, 输出)。

    采用参数列表形式调用，不经 shell 解析，因此不存在命令注入风险。
    """
    try:
        proc = subprocess.run(  # noqa: S603
            ["git", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return 127, "未找到 git"
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out.strip()


def get_current_branch() -> str:
    code, out = run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    return out if code == 0 else ""


def confirm(branch: str) -> bool:
    """强制推送前的二次确认。"""
    print()
    print("[!] 强制推送会用本地历史覆盖远程分支 " + branch + "。")
    print("    上面标记为 '-' 的远程提交将永久丢失，且无法恢复。")
    try:
        answer = input("确认强制推送？请输入 y 继续（其他任意键取消）: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("y", "yes")


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    auto_yes = "--yes" in argv[1:]

    print("=" * 60)
    print("  MolManager 强制推送")
    print("=" * 60)
    print()

    # 1. 检查 git 可用
    code, _ = run_git(["--version"])
    if code != 0:
        print("[错误] 未检测到 git，请先安装。")
        return 1

    # 2. 检查在仓库内
    code, out = run_git(["rev-parse", "--is-inside-work-tree"])
    if code != 0 or out != "true":
        print("[错误] 当前目录不是 git 仓库。")
        return 1

    # 3. 当前分支
    branch = args[0] if args else get_current_branch()
    if not branch or branch == "HEAD":
        print("[错误] 当前处于分离 HEAD 状态，请先切到具体分支。")
        return 1
    print(f"当前分支: {branch}")

    # 4. 工作区状态（仅提示，不阻断）
    code, _ = run_git(["diff", "--quiet", "HEAD"])
    if code != 0:
        print()
        print("[警告] 工作区有未提交的改动（未提交内容不会被推送）：")
        _, status = run_git(["status", "--short"])
        print(status or "  (无)")

    # 5. remote
    _, remote_url = run_git(["config", "--get", "remote.origin.url"])
    print(f"远程地址: {remote_url or '(未配置 origin)'}")
    print()

    # 6. fetch，更新 lease 基准
    print("[1/3] 正在 fetch 远端最新状态...")
    code, err = run_git(["fetch", "origin", branch])
    if code != 0:
        print("      (fetch 失败或远端无此分支，可能是首次推送)")
        if err:
            print(f"      {err.splitlines()[0] if err.splitlines() else ''}")
    else:
        print("      fetch 完成")

    # 7. 展示差异
    print()
    print("[2/3] 本地与远端差异：")
    print("-" * 60)
    _, ahead = run_git(["log", "--oneline", f"origin/{branch}..HEAD"])
    print("  本地领先远端(待推送)：")
    print(("\n".join("    + " + ln for ln in ahead.splitlines()) if ahead else "    (无)"))
    print()
    _, behind = run_git(["log", "--oneline", f"HEAD..origin/{branch}"])
    print("  远端领先本地(将被覆盖/丢弃)：")
    print(("\n".join("    - " + ln for ln in behind.splitlines()) if behind else "    (无)"))
    print("-" * 60)

    # 8. 确认
    if not auto_yes and not confirm(branch):
        print()
        print("已取消，未做任何推送。")
        return 0

    # 9. 强制推送
    print()
    print("[3/3] 正在强制推送...")
    code, out = run_git(["push", "--force-with-lease", "origin", branch])
    if code != 0:
        print()
        print("[失败] 推送被拒绝或出错：")
        print(out)
        print()
        print("  常见原因：")
        print("    1) 远端有你本地没有的新提交（--force-with-lease 的保护机制）")
        print("    2) SSH key 未配置或未被 GitHub 授权")
        print(f"  若确认远端内容可丢弃，可手动执行：git push --force origin {branch}")
        return 1

    print()
    print("=" * 60)
    print("  推送成功！")
    print("=" * 60)
    print()
    _, log = run_git(["log", "--oneline", "-5"])
    print(log)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
