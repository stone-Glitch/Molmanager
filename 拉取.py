#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pull.py（拉取.py）
从远程拉取最新代码的安全脚本，与 更新.py（强制推送）对称：

- 先 ``git fetch`` 下载远程更新，但不动工作区
- 展示本地与远程的分歧（待拉取 / 待推送的提交）
- 确认后再执行 ``git pull``，默认用 ``--ff-only``（快进优先，绝不产生合并提交；
  非快进时直接报错让你先处理，避免悄悄制造 merge commit 或冲突）

⚠️ 本脚本只拉取，不会推送、不会强制覆盖。

用法:
    python pull.py              # 拉取当前分支的跟踪远程
    python pull.py main         # 显式指定分支
"""

import subprocess
import sys


def run_git_command(args, check=True, capture=True):
    """执行 Git 命令（参数列表形式，绝不使用 shell=True）。"""
    try:
        if capture:
            result = subprocess.run(
                args,
                check=check,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            return result.stdout.strip(), result.stderr.strip()
        subprocess.run(args, check=check)
        return "", ""
    except subprocess.CalledProcessError as e:
        print(f"❌ Git 命令失败: {' '.join(args)}")
        print(e.stderr)
        sys.exit(1)


def get_current_branch():
    out, _ = run_git_command(["git", "branch", "--show-current"])
    return out


def show_divergence(branch: str) -> None:
    """展示本地与远程的分歧。"""
    # 远程待拉取的提交（远程有、本地没有）
    incoming, _ = run_git_command(
        ["git", "log", "--oneline", f"HEAD..origin/{branch}"], check=False
    )
    # 本地待推送的提交（本地有、远程没有）
    outgoing, _ = run_git_command(
        ["git", "log", "--oneline", f"origin/{branch}..HEAD"], check=False
    )
    if incoming:
        n = len(incoming.splitlines())
        print(f"\n📥 远程有 {n} 个待拉取提交（origin/{branch} 领先本地）：")
        print(incoming)
    else:
        print("\n✅ 远程无新提交，本地已是最新。")
    if outgoing:
        n = len(outgoing.splitlines())
        print(f"\n📤 本地有 {n} 个待推送提交（本地领先 origin/{branch}）：")
        print(outgoing)
        print("   （本脚本只拉取，不会推送这些提交；推送请用 更新.py）")


def confirm_pull(branch: str) -> bool:
    print(f"\n⬇️  准备对 origin/{branch} 执行 git pull --ff-only。")
    print("    仅快进合并；若产生分叉会报错，需要你先 rebase 或处理。")
    try:
        answer = input("    确认拉取？输入 y 继续，其他任意键取消：").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消。")
        return False
    return answer == "y"


def main():
    # 1. 是否在仓库内
    out, _ = run_git_command(["git", "rev-parse", "--is-inside-work-tree"])
    if out != "true":
        print("❌ 错误: 当前目录不是 Git 仓库，请进入仓库目录后运行。")
        sys.exit(1)

    # 2. 解析分支（默认当前分支）
    branch = sys.argv[1] if len(sys.argv) > 1 else get_current_branch()
    if not branch:
        print("❌ 无法获取当前分支，请显式指定：python pull.py <分支名>")
        sys.exit(1)

    # 3. 先 fetch（不动工作区）
    print(f"🔄 正在 fetch origin/{branch} ...")
    run_git_command(["git", "fetch", "origin", branch], capture=False)

    # 4. 展示分歧
    show_divergence(branch)

    # 5. 快进拉取（需确认）
    if not confirm_pull(branch):
        print("🛑 已取消拉取，工作区未改动。")
        sys.exit(0)

    print(f"⬇️  执行 git pull --ff-only origin {branch} ...")
    run_git_command(["git", "pull", "--ff-only", "origin", branch], capture=False)
    print(f"✅ 拉取完成，本地 {branch} 已与 origin/{branch} 同步（快进）。")


if __name__ == "__main__":
    main()
