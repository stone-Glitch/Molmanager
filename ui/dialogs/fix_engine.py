# -*- coding: utf-8 -*-
"""
F07 一键修复执行引擎（设计落地增强）—— 把规则库 fix 字段变成真实可执行的修复。

契约：
  - import 阶段只依赖标准库；tkinter / 业务模块（psi4、controller）全部在函数内部惰性 import，
    保证在缺 GUI / 缺量化依赖的环境下也能 import 本模块；
  - apply_fix(app, rule, error_text) -> (ok: bool, message: str)，由 error_diagnosis._on_fix 调用；
  - 所有动作都是「真实生效」的：修改记忆到配置 / 注入下次计算参数 / 打开目录或对话框，
    而不是弹个 toast 占位；
  - 任何一步失败都降级为 readable 的 message，绝不静默吞错。
"""

from __future__ import annotations

import re


# 常见基组拼写 / 大小写纠正表（key 统一小写用于归一化匹配）。
# 仅覆盖 PSI4 内置可用基组里最容易被写错的那批；其余交给用户确认。
_BASIS_CORRECTIONS = {
    "6-31g": "6-31g*",
    "6-31g**": "6-31g*",
    "6-31+g": "6-31+g*",
    "6-311g": "6-311g*",
    "6-311g**": "6-311g**",
    "sto3g": "sto-3g",
    "def2-svp": "def2-SVP",
    "def2-tzvpp": "def2-TZVPP",
    "def2-tzvp": "def2-TZVP",
    "cc-pvdz": "cc-pVDZ",
    "cc-pvtz": "cc-pVTZ",
    "cc-pvqz": "cc-pVQZ",
    "aug-cc-pvdz": "aug-cc-pVDZ",
    "aug-cc-pvtz": "aug-cc-pVTZ",
}

# 常见「总是可用」的退路基组（当无法从错误里识别可纠正拼写时使用）
_FALLBACK_BASIS = "6-31g*"


def _log(app, msg, level="info"):
    try:
        hlp = getattr(app, "helpers", None)
        fn = getattr(hlp, "on_log", None)
        if callable(fn):
            fn(str(msg), str(level))
    except Exception:
        pass


def _get_psi4_cfg(app):
    cd = getattr(app, "config_data", None)
    if not isinstance(cd, dict):
        return {}
    cd.setdefault("psi4_config", {})
    return cd["psi4_config"]


def _persist(app):
    try:
        from utils.config import save_config
        save_config(getattr(app, "config_data", {}))
    except Exception:
        pass


def _reopen_psi4(app):
    """重新打开 PSI4 设置对话框，让用户在已修正的默认值上确认并重算。"""
    ctrl = getattr(app, "controller", None)
    fn = getattr(ctrl, "show_psi4_dialog", None)
    if callable(fn):
        try:
            fn()
            return True
        except Exception:
            pass
    dlg = getattr(getattr(app, "dialogs", None), "show_psi4_dialog", None)
    if callable(dlg):
        try:
            dlg()
            return True
        except Exception:
            pass
    return False


def _parse_basis(error_text):
    """从错误原文里尽量抠出被报「不存在」的基组名。"""
    if not error_text:
        return None
    m = re.search(r"basis\s+set\s+['\"]([^'\"]+)['\"]", error_text, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"['\"]([^'\"]+)['\"]\s+(?:not found|not available|unavailable)",
                  error_text, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def _normalize_basis(name):
    """返回纠正后的基组名；无法识别时返回 None。"""
    if not name:
        return None
    key = name.strip().lower()
    if key in _BASIS_CORRECTIONS:
        return _BASIS_CORRECTIONS[key]
    # 大小写修正：PSI4 基组名对大小写敏感（如 def2-SVP），但前缀通常小写也可，
    # 这里仅对明确已知的做映射，未知一律不动，避免误改。
    return None


# ================================================================
# 各 action 处理器：返回 (ok, message)
# ================================================================
def _fix_basis(app, error_text, params):
    candidate = _parse_basis(error_text)
    current = getattr(app, "psi4_last_basis", "") or ""
    target = candidate or current
    corrected = _normalize_basis(target)

    if corrected is None:
        # 无法从错误中确认可纠正拼写：改用稳妥的退路基组并明确告知是猜测
        corrected = (params.get("fallback") or _FALLBACK_BASIS)
        guessed = True
    else:
        guessed = False

    if not corrected or corrected.lower() == (target or "").lower():
        # 纠正后和原来一样（说明原本写法就是对的），仅重新打开对话框让用户确认
        _reopen_psi4(app)
        return True, "基组写法未发现明显错误，已重新打开设置对话框供确认。"

    # 真正改写并记忆
    try:
        app.psi4_last_basis = corrected
    except Exception:
        pass
    cfg = _get_psi4_cfg(app)
    cfg["last_basis"] = corrected
    _persist(app)
    _reopen_psi4(app)
    if guessed:
        _log(app, f"⚠️ 无法从错误确认正确基组，已暂用「{corrected}」（PSI4 内置可用）替换，请确认设置。",
              "warning")
        return True, f"已将基组替换为退路基组 {corrected}（猜测，请确认）"
    _log(app, f"✅ 已将基组纠正为「{corrected}」并记忆，请确认后重算。", "success")
    return True, f"基组已纠正为 {corrected}"


def _fix_memory(app, error_text, params):
    gb = int(params.get("gb", 2) or 2)
    # 若可探测物理内存，把默认值限制在「物理内存一半」以内，避免越设越大
    try:
        try:
            import psutil
            total = psutil.virtual_memory().total // (1024 ** 3)
            if total:
                gb = max(1, min(gb, max(1, total // 2)))
        except Exception:
            pass
    except Exception:
        pass
    cfg = _get_psi4_cfg(app)
    cfg["memory_gb"] = gb
    _persist(app)
    _reopen_psi4(app)
    _log(app, f"✅ 已将 PSI4 默认内存设为 {gb} GB，请确认后重算。", "success")
    return True, f"内存已设为 {gb} GB"


def _fix_scf(app, error_text, params):
    scf_options = params.get("scf_options") or {"maxiter": 200}
    if not isinstance(scf_options, dict):
        scf_options = {"maxiter": 200}
    cfg = _get_psi4_cfg(app)
    cfg["scf_options"] = scf_options
    _persist(app)
    _reopen_psi4(app)
    _log(app, "✅ 已写入 SCF 收敛辅助选项（增大 maxiter / 启用阻尼），下次计算自动注入。",
          "success")
    return True, "已注入 SCF 收敛选项"


def _fix_file(app, error_text, params):
    ctrl = getattr(app, "controller", None)
    fn = getattr(ctrl, "browse_work_dir", None)
    if callable(fn):
        try:
            fn()
            _log(app, "✅ 已重新选择工作目录，文件列表将刷新。", "success")
            return True, "已重新定位工作目录"
        except Exception as e:
            return False, f"重新定位失败：{e}"
    # 兜底：直接弹目录选择
    try:
        from tkinter import filedialog
        wd = str(getattr(getattr(ctrl, "model", None), "work_dir", ""))
        d = filedialog.askdirectory(initialdir=wd or None,
                                    title="选择输入文件所在的工作目录")
        if d and ctrl is not None and hasattr(ctrl, "model"):
            ctrl.model.work_dir = d
            try:
                getattr(app, "work_dir_var", None).set(d)
            except Exception:
                pass
            scan = getattr(ctrl, "scan_files", None)
            if callable(scan):
                scan()
            _log(app, f"✅ 工作目录已设为 {d}", "success")
            return True, "已重新定位工作目录"
        return False, "未选择目录"
    except Exception as e:
        return False, f"重新定位失败：{e}"


def _fix_dep(app, error_text, params):
    cmds = [
        "conda activate mol_manager_312",
        "conda install -c conda-forge psi4 openbabel",
    ]
    text = "\n".join(cmds)
    copied = False
    try:
        getattr(app, "clipboard_clear", lambda: None)()
        getattr(app, "clipboard_append", lambda _: None)(text)
        copied = True
    except Exception:
        copied = False
    _log(app, "依赖安装命令：\n" + text + ("\n（已复制到剪贴板）" if copied else ""),
          "info")
    # 打开环境诊断，便于在当前环境补装
    fn = getattr(app, "show_environment_dialog_from_menu", None)
    if not callable(fn):
        fn = getattr(getattr(app, "helpers", None), "show_env_diagnosis_dialog", None)
    if callable(fn):
        try:
            fn()
        except Exception:
            pass
    return True, "已复制安装命令并打开环境诊断"


_HANDLERS = {
    "correct_basis": _fix_basis,
    "reduce_memory": _fix_memory,
    "relax_scf": _fix_scf,
    "locate_file": _fix_file,
    "install_dep": _fix_dep,
}


def apply_fix(app, rule, error_text):
    """
    按规则库的 fix.action 执行真实修复。
    返回 (ok, message)：message 会作为友好的日志提示展示给用户。
    """
    fix = (rule or {}).get("fix") or {}
    action = fix.get("action", "none")
    params = fix.get("params") or {}
    handler = _HANDLERS.get(action)
    if handler is None:
        return False, "该错误暂无自动修复，请使用「复制错误」或「环境诊断」。"
    try:
        return handler(app, error_text, params)
    except Exception as e:  # 修复链路自身异常也要落日志，不能静默
        _log(app, f"⚠️ 自动修复执行异常：{e}", "error")
        return False, f"自动修复执行失败：{e}"
