#!/usr/bin/env bash
# MolManager 容器入口
#   docker run ... molmanager:1.0.0            # GUI（需 X11 转发）
#   docker run ... molmanager:1.0.0 api        # 仅 FastAPI 接口层
#   docker run ... molmanager:1.0.0 bash       # 交互式调试
set -euo pipefail

# conda 环境未自动激活时手动激活（镜像里 PATH 已包含，这里做兜底）
if [ -f "/opt/conda/etc/profile.d/conda.sh" ]; then
    # shellcheck disable=SC1091
    source /opt/conda/etc/profile.d/conda.sh
    conda activate "${CONDA_ENV:-mol_manager_312}"
fi

mode="${1:-gui}"
case "$mode" in
    gui)
        # 无 DISPLAY 时用 Xvfb 兜底，避免容器里直接 crash
        if [ -z "${DISPLAY:-}" ]; then
            echo "[entrypoint] 未检测到 DISPLAY，改用 Xvfb 虚拟显示 ..." >&2
            Xvfb :99 -screen 0 1280x800x24 >/dev/null 2>&1 &
            export DISPLAY=:99
            sleep 1
        fi
        exec python main.py
        ;;
    api)
        exec uvicorn api.server:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}"
        ;;
    *)
        exec "$@"
        ;;
esac
