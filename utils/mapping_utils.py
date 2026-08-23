#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
映射增强工具（M-03 / M-04 / M-06 / M-02 的纯逻辑层）。

把「模糊拼写 / 搜索过滤 / 模板生成 / 文件名反向提取」等可单测的逻辑从 tkinter
UI 中剥离到这里，UI 层只负责调用；这样无需 GUI 即可在 managed python 下验证正确性
（遵循本项目「验证 > 承诺」：先确认逻辑再接线）。
"""
import csv
import os
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


def levenshtein(a: str, b: str) -> int:
    """
    经典 Levenshtein 编辑距离（DP，O(n*m) 时间与空间，行向量优化）。
    空串 / 相等 / 单字符替换·插入·删除 均已被单测覆盖。
    """
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for ca in a:
        cur = [prev[0] + 1]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[lb]


def _dedupe(names: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: set = set()
    for n in names:
        n = (n or "").strip()
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def fuzzy_suggestions(name: str, candidates: Iterable[str],
                      max_dist: int = 2, limit: int = 8) -> List[str]:
    """
    返回与 name 编辑距离 ≤ max_dist 的候选（去重、按距离升序、截断到 limit）。
    用于编辑中文名时主动「建议」已有近似写法，避免拼写重复（如 乙醇/乙醚）。

    ⚠️ 长度守卫：仅当 name 与候选「长度均 ≥ 2」时才比较。
    原因：单字中文名（苯/水/氧…）彼此恒为 1 次替换，Levenshtein≤2 会把
    几乎所有单字都判为「近似」，产生纯噪声建议。真正有意义的是多字名
    （乙醇/乙醚/乙醛 这类易混化学名）的近似检测。
    """
    name = (name or "").strip()
    if not name or len(name) < 2:
        return []
    scored: List[Tuple[int, str]] = []
    seen: set = set()
    for c in candidates:
        c = (c or "").strip()
        if not c or c == name or c in seen:
            continue
        if len(c) < 2:
            continue
        d = levenshtein(name, c)
        if d <= max_dist:
            scored.append((d, c))
            seen.add(c)
    scored.sort(key=lambda x: (x[0], x[1]))
    return [c for _, c in scored[:limit]]


def find_fuzzy_pairs(names: Iterable[str], max_dist: int = 2) -> List[Tuple[str, str, int]]:
    """
    在去重后的名称集合中，找出所有「编辑距离 ≤ max_dist 且非完全相同」的配对。
    用于保存映射表时扫描中文名（或英文名）的近似重复，给出警示。
    返回 [(a, b, dist), ...]。

    ⚠️ 长度守卫：任一名长度 < 2 的配对直接跳过（见 fuzzy_suggestions 说明）。
    """
    uniq = _dedupe(names)
    pairs: List[Tuple[str, str, int]] = []
    n = len(uniq)
    for i in range(n):
        if len(uniq[i]) < 2:
            continue
        for j in range(i + 1, n):
            if len(uniq[j]) < 2:
                continue
            d = levenshtein(uniq[i], uniq[j])
            if d <= max_dist:
                pairs.append((uniq[i], uniq[j], d))
    return pairs


def filter_mapping_rows(rows: List[Tuple[str, str]], keyword: str) -> List[Tuple[str, str]]:
    """
    M-04 底层谓词：按 keyword（不区分大小写，匹配英文名或中文名）过滤 (eng, chn) 行。
    UI 层拿到结果后决定 detach/reattach 哪些 treeview 项。
    """
    kw = (keyword or "").strip().lower()
    if not kw:
        return list(rows)
    return [
        (eng, chn) for eng, chn in rows
        if kw in str(eng).lower() or kw in str(chn).lower()
    ]


def generate_blank_template(path: str,
                            existing_english: Iterable[str] | None = None,
                            blank_rows: int = 10,
                            delimiter: str = "\t") -> int:
    """
    M-06：生成空白映射模板。
      - 表头：english<delimiter>chinese
      - 每个现有英文名占一行（中文留空），方便用户照着填
      - 末尾补 blank_rows 个全空行
    用 utf-8-sig 写，保证 Excel 打开中文不乱码。返回实际写出行数（不含表头）。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    rows: List[Tuple[str, str]] = []
    if existing_english:
        for eng in existing_english:
            eng = (eng or "").strip()
            if eng:
                rows.append((eng, ""))
    for _ in range(max(0, int(blank_rows))):
        rows.append(("", ""))
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        f.write(f"english{delimiter}chinese\n")
        writer = csv.writer(f, delimiter=delimiter, lineterminator="\n")
        for eng, chn in rows:
            writer.writerow([eng, chn])
    return len(rows)


# ----------------------------------------------------------------------------
# M-02：从文件名反向提取映射
# ----------------------------------------------------------------------------
# 文件名里常见的「限定符」词尾，剥离后更容易得到干净的分子英文名。
# 仅匹配整词（行尾），不误伤中间出现同样字母的词（如 "opt" 在 "optical" 中）。
_QUALIFIER_RE = re.compile(
    r"^(opt|optim|optimized|opt1|min|sp|scan|scanned|conf|conformer|"
    r"conformers|geo|geometry|geom|nmr|ir|raman|freq|frequency|vib|vibr|"
    r"ts|rxn|ph\d+|calc|calc\d+|run\d+|job\d+|molecule|mol|final|init|"
    r"initial|rerun|v\d+|copy|new|bak|tmp|old|fix|fixed|chk|fchk|out|log|"
    r"xyz|sdf|pdb|mol2|cif|dat|gbw|wfn|wfx|dump|print|screen|snapshot)$",
    re.IGNORECASE,
)


def clean_filename_stem(stem: str) -> str:
    """
    把一个文件名「干」（不含扩展名）清洗成候选英文名。

    策略（保守、可解释）：
      1. 按 ``[\\s_\\-./]+`` 切分为若干 token；
      2. 从尾部剥掉「限定符」（opt/conf/scan…）与「纯数字」词尾；
      3. 从头部剥掉「纯数字」与「单字符」噪声头；
      4. 剩余 token 用 ``_`` 连接；若清洗后为空，则回退到原始 stem。

    例如：
      - ``ethanol_opt``        → ``ethanol``
      - ``benzene_conf_003``   → ``benzene``
      - ``H2O_min_sp``         → ``H2O``
      - ``complex_1``          → ``complex``
      - ``ethanol_water``      → ``ethanol_water``（无尾部限定符，原样保留）
      - ``mol_001``            → ``mol_001``（剥数字后只剩 mol，单字符头被剥 → 空 → 回退原始）

    这是「建议」而非「自动应用」：即使用户觉得清洗得不对，也能在编辑器里改。
    """
    stem = (stem or "").strip()
    if not stem:
        return ""
    tokens = [t for t in re.split(r"[\s_\-./]+", stem) if t]
    if not tokens:
        return stem
    # 剥尾部限定符 / 纯数字
    while tokens and (_QUALIFIER_RE.match(tokens[-1]) or tokens[-1].isdigit()):
        tokens.pop()
    # 剥头部纯数字 / 单字符噪声
    while tokens and (tokens[0].isdigit() or len(tokens[0]) <= 1):
        tokens.pop(0)
    if not tokens:
        return stem
    return "_".join(tokens)


def suggest_mapping_from_dir(dir_path: str | os.PathLike,
                             existing_english: Optional[Iterable[str]] = None,
                             extensions: Optional[Iterable[str]] = None,
                             recursive: bool = False,
                             max_items: int = 500) -> List[Tuple[str, str]]:
    """
    M-02：扫描目录，返回「尚未映射」的候选 (english, "") 列表，供用户在编辑器中批量建议。

    逻辑：
      - 遍历 ``dir_path``（``recursive=False`` 只扫顶层，避免一次性吞进成千上万文件）；
      - 仅考虑「有扩展名」的文件（跳过 README / Makefile 等无扩展名项）；
      - 可用 ``extensions`` 限定后缀（如 ``[".xyz", ".sdf", ".log", ".out"]``，大小写不敏感）；
      - 对每个文件取 stem → ``clean_filename_stem`` → 候选英文名；
      - 跳过：清洗后为空的、已在 existing_english（大小写不敏感）中的、与已建议项重复的；
      - 结果按英文名升序，最多 ``max_items`` 条。

    返回 ``[(english, ""), ...]``，中文名留空由用户填写。
    """
    d = Path(dir_path)
    if not d.is_dir():
        return []

    exts: Optional[set] = None
    if extensions:
        exts = {str(e).lower().lstrip(".") for e in extensions if e}

    have: set = set()
    if existing_english:
        for e in existing_english:
            e = (e or "").strip()
            if e:
                have.add(e.lower())

    seen: set = set()
    results: List[Tuple[str, str]] = []

    def _walk(directory: Path):
        try:
            with os.scandir(directory) as it:
                for entry in it:
                    if entry.is_dir(follow_symlinks=False):
                        if recursive:
                            _walk(entry.path)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    name = entry.name
                    suffix = Path(name).suffix.lower()
                    if not suffix:
                        continue  # 无扩展名，跳过
                    if exts is not None and suffix.lstrip(".") not in exts:
                        continue
                    cleaned = clean_filename_stem(Path(name).stem)
                    if not cleaned:
                        continue
                    if cleaned.lower() in have:
                        continue
                    if cleaned.lower() in seen:
                        continue
                    seen.add(cleaned.lower())
                    results.append((cleaned, ""))
                    if len(results) >= max_items:
                        return
        except (OSError, PermissionError):
            return

    _walk(d)
    results.sort(key=lambda x: x[0].lower())
    return results


def diff_mappings(old: Dict[str, str], new: Dict[str, str]) -> Dict[str, "object"]:
    """
    M-05：对比「当前内存映射」与「待载入映射」，分类为三类变更。

    返回结构（均为可 JSON 化的纯数据，便于单测与 UI 渲染）：
        {
          "added":   {eng: chn, ...}        # 新文件有、当前没有的英文名
          "changed": {eng: (old_chn, new_chn), ...}  # 同名但中文名不同的
          "removed": {eng: old_chn, ...}    # 当前有、新文件没有的英文名
          "counts":  {"added": int, "changed": int, "removed": int, "unchanged": int}
        }

    注意：仅按「英文名」比较键；中文名冲突（多英文名→同一中文名）不在本函数职责内，
    由 model.load_mapping_file 的 dup_chn 负责（科学红线 S-06）。
    """
    old_keys = set(old.keys())
    new_keys = set(new.keys())
    added = {k: new[k] for k in (new_keys - old_keys)}
    removed = {k: old[k] for k in (old_keys - new_keys)}
    changed = {}
    for k in (new_keys & old_keys):
        if old[k] != new[k]:
            changed[k] = (old[k], new[k])
    unchanged = len(new_keys & old_keys) - len(changed)
    return {
        "added": added,
        "changed": changed,
        "removed": removed,
        "counts": {
            "added": len(added),
            "changed": len(changed),
            "removed": len(removed),
            "unchanged": unchanged,
        },
    }
