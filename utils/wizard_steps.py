#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
U-07 新手任务向导（6 个高频场景）· 纯数据/逻辑层

把「新手任务向导」的 6 个高频场景定义成结构化数据（场景 → 步骤序列），
供 UI 的向导面板/欢迎页逐条渲染。纯数据 + 查找函数，无 tkinter 依赖，
可沙箱单测。

⚠️ 场景与步骤是「默认模板」，UI 可增删改；本层只提供稳定的读取接口，
不绑定任何具体控件。
"""


WIZARD_SCENARIOS: list[dict] = [
    {
        "id": "import",
        "title": "导入分子文件",
        "description": "把结构文件（.xyz/.mol/.sdf/.pdb 等）加入工作区。",
        "steps": [
            {"title": "准备文件", "detail": "把结构文件放进工作目录，或直接拖入窗口。",
             "hint": "支持 8 种结构格式（M-01 已扩展）。"},
            {"title": "拖放导入", "detail": "从资源管理器把文件拖进文件列表。",
             "hint": "可在配置里设扩展名白名单。"},
            {"title": "确认扫描", "detail": "确认文件出现在列表并带上了状态标记。",
             "hint": "状态列：⏳ 待重命名 / ❌ 无映射 / ✅ 已命名。"},
        ],
    },
    {
        "id": "mapping",
        "title": "建立中文名映射",
        "description": "为英文名建立中文名对照，供批量重命名。",
        "steps": [
            {"title": "打开映射编辑器", "detail": "进入映射编辑界面。",
             "hint": "顶部有搜索框，可按英文/中文过滤。"},
            {"title": "填写中文名", "detail": "为每个英文名填中文名，或用模板/文件名建议。",
             "hint": "支持空白模板生成、从文件名反向建议（M-02/M-06）。"},
            {"title": "保存映射", "detail": "保存；保存前会提示拼写相近的重复项（M-03）。",
             "hint": "中文名冲突会被检测并告警（S-06）。"},
        ],
    },
    {
        "id": "rename",
        "title": "批量重命名",
        "description": "按映射把文件重命名为「英文名（中文名）.ext」。",
        "steps": [
            {"title": "预览", "detail": "先预览重命名计划，确认无误。",
             "hint": "预览里可取消个别项。"},
            {"title": "执行", "detail": "执行重命名，成功后映射表同步更新（D-02）。",
             "hint": "采用原子事务，失败整体回滚（D-01）。"},
        ],
    },
    {
        "id": "energy",
        "title": "运行单点能计算",
        "description": "用 PSI4 计算一个分子的电子能量。",
        "steps": [
            {"title": "选择分子", "detail": "选中一个结构文件。",
             "hint": "确认文件已正确命名/有映射。"},
            {"title": "打开 PSI4 对话框", "detail": "选择「单点能」任务与预设方法/基组。",
             "hint": "可用「快速（HF/STO-3G）」先跑通。"},
            {"title": "查看结果", "detail": "结果区显示能量与耗时；通俗结论区给出一句话解释（U-09）。",
             "hint": "若溶剂回退/热化学失败，会有红色醒目警示（S-04/05）。"},
        ],
    },
    {
        "id": "optimize",
        "title": "运行几何优化",
        "description": "优化分子几何，得到稳定构象。",
        "steps": [
            {"title": "选任务", "detail": "PSI4 对话框选「几何优化」。",
             "hint": "先用 B3LYP/6-31G* 起步。"},
            {"title": "等待收敛", "detail": "观察日志；对话保持打开可取消（U-02）。",
             "hint": "结果含优化后坐标。"},
            {"title": "保存构象", "detail": "把优化结果导出为结构文件。",
             "hint": "可继续做频率/扫描。"},
        ],
    },
    {
        "id": "organize",
        "title": "整理文件与查看结果",
        "description": "按类型归档文件，或查看关联的计算输出。",
        "steps": [
            {"title": "按类型整理", "detail": "把结构/计算文件分目录归档。",
             "hint": "支持 dry-run 预览。"},
            {"title": "反向追溯", "detail": "查看某结构文件关联的 .log/.fchk（E-06）。",
             "hint": "动态元数据列可展示能量/方法等（E-01）。"},
            {"title": "导出/备份", "detail": "导出映射或打包项目（.molproj，E-05）。",
             "hint": "自动备份已启用时可回滚（D-06/F17）。"},
        ],
    },
]


def get_scenarios() -> list[dict]:
    return list(WIZARD_SCENARIOS)


def get_scenario(scenario_id: str) -> dict | None:
    key = (scenario_id or "").strip().lower()
    for s in WIZARD_SCENARIOS:
        if s["id"].lower() == key:
            return s
    return None


def total_steps() -> int:
    return sum(len(s.get("steps", [])) for s in WIZARD_SCENARIOS)


def scenario_ids() -> list[str]:
    return [s["id"] for s in WIZARD_SCENARIOS]


__all__ = ["WIZARD_SCENARIOS", "get_scenarios", "get_scenario",
           "total_steps", "scenario_ids"]
