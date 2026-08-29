@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

REM =====================================================================
REM  MolManager · Windows 环境重建脚本
REM ---------------------------------------------------------------------
REM  用途：conda 环境损坏 / 依赖冲突 / 升级 PSI4、OpenBabel 后，
REM        一键删掉旧环境并按 environment.yml 重建。
REM
REM  用法：双击运行，或在命令行执行 rebuild_env_windows.bat
REM =====================================================================

set "ENV_NAME=mol_manager_312"
set "ROOT=%~dp0"

echo.
echo ============================================================
echo   MolManager 环境重建
echo   环境名: %ENV_NAME%
echo   目录  : %ROOT%
echo ============================================================
echo.

REM ---------------------------------------------------------------- 1. 定位 conda
where conda >nul 2>&1
if errorlevel 1 (
    echo [错误] 未在 PATH 中找到 conda。
    echo        请先安装 Miniconda / Anaconda，或在下方手动指定路径后重试：
    echo        set "CONDA_EXE=C:\Users\你的用户名\miniconda3\Scripts\conda.exe"
    echo.
    pause
    exit /b 1
)

REM ---------------------------------------------------------------- 2. 初始化 conda 钩子
call conda --version >nul 2>&1
for /f "delims=" %%i in ('conda info --base 2^>nul') do set "CONDA_BASE=%%i"
if not defined CONDA_BASE (
    echo [错误] 无法获取 conda 安装根目录。
    pause
    exit /b 1
)
echo [1/4] conda 根目录: %CONDA_BASE%

if exist "%CONDA_BASE%\shell\condabin\conda_hook.bat" (
    call "%CONDA_BASE%\shell\condabin\conda_hook.bat"
) else (
    call "%CONDA_BASE%\Scripts\activate.bat" %CONDA_BASE%
)

REM ---------------------------------------------------------------- 3. 删除旧环境
echo.
echo [2/4] 检查旧环境 ...
call conda env list | findstr /i /c:"%ENV_NAME% " >nul 2>&1
if not errorlevel 1 (
    echo       发现已存在的 %ENV_NAME%，正在删除 ...
    call conda deactivate >nul 2>&1
    call conda env remove -n %ENV_NAME% -y
    if errorlevel 1 (
        echo [错误] 删除旧环境失败。请先关闭所有使用该环境的程序后重试。
        pause
        exit /b 1
    )
    echo       旧环境已删除。
) else (
    echo       未发现旧环境，跳过删除。
)

REM ---------------------------------------------------------------- 4. 创建新环境
echo.
echo [3/4] 按 environment.yml 创建环境（首次耗时较长，请耐心等待）...
cd /d "%ROOT%"
call conda env create -f environment.yml
if errorlevel 1 (
    echo.
    echo [错误] 环境创建失败。常见原因：
    echo        1. 网络问题 —— 建议 conda config --set remote_connect_timeout_secs 60 后重试；
    echo        2. 依赖冲突 —— 删除 environment.yml 中的 psi4 先建基础环境，再单独 conda install psi4；
    echo        3. 磁盘/权限 —— 确认 conda 安装目录可写。
    pause
    exit /b 1
)

REM ---------------------------------------------------------------- 5. 冒烟校验
echo.
echo [4/4] 校验关键依赖 ...
call conda run -n %ENV_NAME% python -c "import openbabel, pydantic; from utils.version import get_full_version; print('      MolManager', get_full_version(), '环境就绪')"
if errorlevel 1 (
    echo [警告] 依赖校验未通过，请留意上面的报错信息。
) else (
    echo.
    echo ============================================================
    echo   重建完成！之后可用 run_main.bat 启动，或执行：
    echo      conda activate %ENV_NAME%
    echo      python main.py
    echo ============================================================
)

echo.
pause
endlocal
