#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
E-02 分层目录树构造器（纯逻辑层）

把扁平的文件/目录相对路径列表，构建成嵌套树结构，供 UI 在
「扁平列表 ↔ 分层树视图」之间切换时复用。

设计原则：
  - 零外部依赖，可在无 tkinter 的沙箱里直接单测。
  - 纯函数：相同输入永远得到相同结构（确定性、可重放）。
  - 不触碰任何文件系统；入参只接受字符串路径。
"""
from collections.abc import Iterable


def _split(path: str, sep: str | None = None) -> list[str]:
    """按分隔符切分路径，去掉空段（兼容 'a//b' 与首尾 '/'）。"""
    if sep is None:
        # 自动识别：优先用系统分隔符，其次 '/'
        sep = "\\" if "\\" in path else "/"
    raw = path.split(sep)
    return [p for p in raw if p not in ("", ".", "..")]


def build_tree(paths: Iterable[str], sep: str | None = None) -> dict:
    """
    从扁平路径列表构建嵌套树。

    返回结构示例（每个节点都是一个 dict）：
        {
          "benzene": {
            "_is_file": True, "_children": {}
          },
          "proj": {
            "_is_file": False,
            "_children": {
              "a.xyz": {"_is_file": True, "_children": {}},
              "sub":  {"_is_file": False, "_children": {...}}
            }
          }
        }

    - 同一名字既作为目录又作为文件出现时（如 'a' 与 'a/b'），
      `_is_file` 取并集（标记为该节点既可是文件也可是目录容器）。
    """
    root: dict = {}
    for p in paths:
        parts = _split(p, sep)
        if not parts:
            continue
        node = root
        for i, part in enumerate(parts):
            is_last = i == len(parts) - 1
            child = node.get(part)
            if child is None:
                child = {"_is_file": is_last, "_children": {}}
                node[part] = child
            else:
                # 已存在：若该段这次是末端文件，则提升为文件节点
                if is_last:
                    child["_is_file"] = True
            node = child["_children"]
    return root


def iter_tree(tree: dict, parent: str = "", sep: str = "/") -> list[tuple[str, bool]]:
    """
    深度优先遍历树，按目录在前、文件在后的稳定顺序产出
    (完整相对路径, 是否为文件) 列表，供 ttk.Treeview 插入。

    顺序规则：子节点按名字排序；每个名字下先递归其目录子树，
    再（若该节点自身是文件）产出文件项——即「目录优先展示、文件随后」。
    """
    out: list[tuple[str, bool]] = []
    # 目录子节点先处理（递归），文件节点后产出
    names = sorted(tree.keys())
    for name in names:
        node = tree[name]
        full = f"{parent}{sep}{name}" if parent else name
        children = node.get("_children", {})
        if children:
            out.extend(iter_tree(children, full, sep))
        if node.get("_is_file", False):
            out.append((full, True))
    return out


def count_nodes(tree: dict) -> tuple[int, int]:
    """返回 (目录节点数, 文件节点数)。"""
    dirs = 0
    files = 0
    for name, node in tree.items():
        children = node.get("_children", {})
        if children:
            dirs += 1
            d, f = count_nodes(children)
            dirs += d
            files += f
        if node.get("_is_file", False):
            files += 1
    return dirs, files


__all__ = ["build_tree", "iter_tree", "count_nodes", "_split"]
