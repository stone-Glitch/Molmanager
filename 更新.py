#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
full_force_sync.py
大范围全覆盖同步脚本：
- 自动添加所有变更（新增、修改、删除）
- 提交全部改动
- 安全强制推送（--force-with-lease）完全覆盖远程分支

⚠️ 注意：本脚本会执行 `git push --force-with-lease`，可能覆盖远程分支。
仅在你确信要全覆盖远程时使用，且推送前必须显式输入 y 确认。

用法: python full_force_sync.py ["自定义提交信息"]
"""

import subprocess
import sys
from datetime import datetime


def run_git_command(args, check=True, capture=True):
    """执行 Git 命令（以参数列表形式，绝不使用 shell=True）。

    ``args`` 必须是字符串列表，例如 ``["git", "status", "--porcelain"]``。
    这样命令与参数严格分离，杜绝 shell 元字符注入。
    """
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
        # 实时输出模式（用于 push 显示进度）
        subprocess.run(args, check=check)
        return "", ""
    except subprocess.CalledProcessError as e:
        print(f"❌ Git 命令失败: {' '.join(args)}")
        print(e.stderr)
        sys.exit(1)


def get_current_branch():
    """获取当前分支名"""
    out, _ = run_git_command(["git", "branch", "--show-current"])
    return out


def has_changes():
    """检查工作区是否有任何变更（包括未跟踪文件）"""
    out, _ = run_git_command(["git", "status", "--porcelain"])
    return bool(out.strip())


def confirm_force_push(branch: str) -> bool:
    """推送前二次确认，避免误覆盖远程。"""
    print(
        f"\n⚠️  即将对远程分支 origin/{branch} 执行「安全强制推送」"
        f"（git push --force-with-lease）。"
    )
    print("    该操作可能覆盖远程历史，请确保你确实要全覆盖远程。")
    try:
        answer = input("    确认强制推送？输入 y 继续，其他任意键取消：").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消。")
        return False
    return answer == "y"


def main():
    # 1. 检查是否在 Git 仓库内
    out, _ = run_git_command(["git", "rev-parse", "--is-inside-work-tree"])
    if out != "true":
        print("❌ 错误: 当前目录不是 Git 仓库，请进入仓库目录后运行。")
        sys.exit(1)

    # 2. 检查是否有变更需要提交
    if not has_changes():
        print("ℹ️  工作区是干净的，没有任何变更。无需提交，直接进行强制覆盖推送。")
    else:
        print("📦 检测到工作区变更，执行全量暂存 (git add -A)...")
        run_git_command(["git", "add", "-A"], capture=False)
        print("✅ 已暂存所有变更（新增/修改/删除）")

        # 3. 提交（commit_msg 作为独立 argv 元素，无 shell 注入风险）
        commit_msg = (
            sys.argv[1]
            if len(sys.argv) > 1
            else f"大范围全覆盖自动同步于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        run_git_command(["git", "commit", "-m", commit_msg], capture=False)
        print(f"✅ 已提交: {commit_msg}")

    # 4. 获取当前分支
    branch = get_current_branch()
    if not branch:
        print("❌ 无法获取当前分支，请确保已切换到有效分支。")
        sys.exit(1)

    # 5. 二次确认后再强制推送
    if not confirm_force_push(branch):
        print("🛑 已取消强制推送，本地改动已提交但远程未变。")
        sys.exit(0)

    print(f"🚀 正在执行安全强制推送 (git push --force-with-lease) 到 origin/{branch} ...")
    run_git_command(["git", "push", "--force-with-lease", "origin", branch], capture=False)
    print(f"✅ 大范围全覆盖完成！本地分支 {branch} 已完全覆盖远程仓库。")


if __name__ == "__main__":
    main()
