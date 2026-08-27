#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
E-09 HPC 作业脚本生成器（SLURM / PBS）· 纯逻辑层

根据一份结构化「作业规格」生成 SLURM / PBS 作业脚本文本。
只负责渲染模板字符串，不提交作业、不触碰调度器，可在沙箱单测。

支持两种调度器：
  - SLURM：sbatch（#SBATCH 指令）
  - PBS/Torque：qsub（#PBS 指令）
"""


def _sanitize_name(name: str) -> str:
    # 作业名不能含空格/特殊字符，替换为下划线
    out = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in (name or "job"))
    return out or "job"


def generate_slurm(job: dict) -> str:
    """
    job 字段（均可选）：
      name, partition, nodes, ntasks, cpus_per_task, gres, walltime,
      memory_gb, output, error, modules(list), commands(list[str])
    """
    name = _sanitize_name(job.get("name", "molmanager_job"))
    lines: list[str] = ["#!/bin/bash"]
    lines.append(f"#SBATCH --job-name={name}")
    if job.get("partition"):
        lines.append(f"#SBATCH --partition={job['partition']}")
    if job.get("nodes"):
        lines.append(f"#SBATCH --nodes={job['nodes']}")
    if job.get("ntasks"):
        lines.append(f"#SBATCH --ntasks={job['ntasks']}")
    if job.get("cpus_per_task"):
        lines.append(f"#SBATCH --cpus-per-task={job['cpus_per_task']}")
    if job.get("gres"):
        lines.append(f"#SBATCH --gres={job['gres']}")
    if job.get("walltime"):
        lines.append(f"#SBATCH --time={job['walltime']}")
    if job.get("memory_gb"):
        lines.append(f"#SBATCH --mem={job['memory_gb']}G")
    lines.append(f"#SBATCH --output={job.get('output') or f'{name}.out'}")
    lines.append(f"#SBATCH --error={job.get('error') or f'{name}.err'}")
    lines.append("")
    for mod in job.get("modules", []) or []:
        lines.append(f"module load {mod}")
    if job.get("modules"):
        lines.append("")
    lines.append("set -e")
    lines.append("")
    cmds = job.get("commands") or []
    if not cmds:
        cmds = ["# 在此填写要运行的命令"]
    for c in cmds:
        lines.append(c)
    lines.append("")
    return "\n".join(lines)


def generate_pbs(job: dict) -> str:
    """PBS/Torque 版本，字段同上（partition→queue）。"""
    name = _sanitize_name(job.get("name", "molmanager_job"))
    lines: list[str] = ["#!/bin/bash"]
    lines.append(f"#PBS -N {name}")
    queue = job.get("partition") or job.get("queue")
    if queue:
        lines.append(f"#PBS -q {queue}")
    if job.get("nodes") and job.get("cpus_per_task"):
        # PBS 用 -l nodes=n:ppn=c
        lines.append(f"#PBS -l nodes={job['nodes']}:ppn={job['cpus_per_task']}")
    if job.get("walltime"):
        lines.append(f"#PBS -l walltime={job['walltime']}")
    if job.get("memory_gb"):
        lines.append(f"#PBS -l mem={job['memory_gb']}gb")
    lines.append(f"#PBS -o {job.get('output') or f'{name}.out'}")
    lines.append(f"#PBS -e {job.get('error') or f'{name}.err'}")
    lines.append("")
    for mod in job.get("modules", []) or []:
        lines.append(f"module load {mod}")
    if job.get("modules"):
        lines.append("")
    lines.append("set -e")
    lines.append("")
    cmds = job.get("commands") or ["# 在此填写要运行的命令"]
    for c in cmds:
        lines.append(c)
    lines.append("")
    return "\n".join(lines)


def generate_script(scheduler: str, job: dict) -> str:
    """按调度器名（slurm/pbs，大小写不敏感）分发。"""
    s = (scheduler or "").strip().lower()
    if s == "pbs" or s == "torque":
        return generate_pbs(job)
    return generate_slurm(job)


__all__ = ["generate_slurm", "generate_pbs", "generate_script", "_sanitize_name"]
