"""💡 动作提示注入（自 ui_builder._tabs.py 机械拆分，行为不变）。"""

# ===========================================================
# 📊 底部状态栏（新版：状态 + 进度 + 操作提示 + OB 指示灯）
# ===========================================================


def _inject_action_tips(app):
    """
    把常见 controller 动作包一层「动作完成后写提示到 action_tip_var」。
    非侵入式：用 try/except，失败不影响功能。
    """

    def _tip(msg: str):
        try:
            app.action_tip_var.set("💡 " + msg)
        except Exception:
            pass

    # 给几个最常用的控制器函数包装
    pairs = [
        ("scan_files", "已扫描文件列表，下一步：点「🔧 一键修复全部」自动处理命名问题"),
        ("run_fix_by_mode", "修复已完成。下一步：点「📂 按类型整理」或「📁 按文件名分组」归档"),
        ("organize_by_type", "已按扩展名整理归档。下一步：选文件 → 切到「🔬 计算与动画」运行预设"),
        ("organize_by_basename", "已按基本名分组（每个分子一个子目录）。下一步：点「生成缺失映射表」批量补名"),
        ("load_mapping_file", "映射已加载！列表里中文名已更新。下一步：点「一键修复全部」执行映射重命名"),
        ("generate_missing", "缺失的文件名已导出 CSV。填完中文名后，用「映射管理器」导入即可"),
        ("undo_last", "已撤销上一步。需要前进？点工具栏「↪ 重做」"),
        ("remove_duplicate_files", "重复文件清理完成。建议先点「扫描文件」确认结果"),
    ]
    for name, tip in pairs:
        try:
            original = getattr(app.controller, name)

            def _wrap(fn, t):
                def _w(*a, **kw):
                    try:
                        ret = fn(*a, **kw)
                    finally:
                        try:
                            _tip(t)
                        except Exception:
                            pass
                    return ret

                return _w

            setattr(app.controller, name, _wrap(original, tip))
        except Exception:
            pass
