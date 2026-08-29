# MolManager · 分子管理器 —— 容器镜像
#
# 构建：
#   docker build -t molmanager:1.0.0 .
#
# 运行 GUI（需 X11 转发，Linux 宿主机）：
#   xhost +local:docker
#   docker run --rm -it \
#       -e DISPLAY=$DISPLAY -v /tmp/.X11-unix:/tmp/.X11-unix \
#       molmanager:1.0.0
#
# 仅跑 API（无 GUI）：
#   docker run --rm -p 8000:8000 molmanager:1.0.0 api
#   # 文档： http://127.0.0.1:8000/docs
#
# 说明：PSI4 / OpenBabel 是 C++ 扩展，必须走 conda-forge；基础镜像直接用
#       miniforge3，避免 pip 轮子在 glibc 上踩坑。
FROM condaforge/miniforge3:latest

LABEL org.opencontainers.image.title="MolManager"
LABEL org.opencontainers.image.description="分子管理器：计算产物归档 / 格式转换 / PSI4 量化计算 / 分子检索"
LABEL org.opencontainers.image.source="https://github.com/stone-Glitch/-"

ENV CONDA_ENV=mol_manager_312 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    PATH=/opt/conda/envs/mol_manager_312/bin:$PATH

# Tkinter / Matplotlib / OpenBabel 运行时需要的系统库 + 无头 GUI 用的 Xvfb
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
        libx11-6 \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依赖先行：environment.yml 不变时可复用镜像层
COPY environment.yml ./
RUN conda env create -f environment.yml \
    && conda clean -afy \
    && conda run -n ${CONDA_ENV} python -c "import sys; print('python', sys.version)"

COPY . .

# --no-deps：运行时依赖已由 conda 提供，避免 pip 覆盖 conda 的 C++ 扩展
RUN /opt/conda/envs/${CONDA_ENV}/bin/python -m pip install --no-cache-dir --no-deps -e . \
    && /opt/conda/envs/${CONDA_ENV}/bin/python -m pip install --no-cache-dir \
        "fastapi>=0.110" "uvicorn[standard]>=0.27"

# 冒烟：确保依赖装全（缺 openbabel / pydantic 会在这里失败而不是等到运行时）
RUN conda run -n ${CONDA_ENV} python -c "\
from utils.version import get_full_version; \
import utils.config, utils.chem_query, core.domain, core.storage_sqlite; \
print('MolManager', get_full_version(), 'smoke OK')"

EXPOSE 8000

# 启动脚本：传 "api" 走 FastAPI，默认走 GUI
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["gui"]
