@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

REM ============================================================
REM  MolManager - 一键强制推送到 GitHub (Windows)
REM
REM  用途：把本地 main 分支强制同步到远程（覆盖远程历史）
REM  前提：
REM    1) 已安装 Git 并配置好 SSH key（github.com 已添加公钥）
REM    2) 本脚本需放在仓库根目录下运行
REM
REM  安全设计：
REM    - 使用 --force-with-lease（比 --force 安全，远端有他人新提交时会拒绝）
REM    - 推送前列出待推送提交 + 远端/本地差异，需显式输入 Y 才继续
REM ============================================================

cd /d "%~dp0"

echo.
echo ============================================================
echo   MolManager 强制推送
echo ============================================================
echo.

REM ---- 1. 检查 git 可用 ----
where git >nul 2>nul
if errorlevel 1 (
    echo [错误] 未检测到 git，请先安装 Git for Windows。
    pause
    exit /b 1
)

REM ---- 2. 检查是否在 git 仓库内 ----
git rev-parse --is-inside-work-tree >nul 2>nul
if errorlevel 1 (
    echo [错误] 当前目录不是 git 仓库：%~dp0
    pause
    exit /b 1
)

REM ---- 3. 获取当前分支 ----
for /f "delims=" %%b in ('git rev-parse --abbrev-ref HEAD') do set BRANCH=%%b
if "%BRANCH%"=="HEAD" (
    echo [错误] 当前处于分离 HEAD 状态，请先切到具体分支。
    pause
    exit /b 1
)
echo 当前分支: %BRANCH%

REM ---- 4. 检查工作区是否干净 ----
git diff --quiet HEAD >nul 2>nul
if errorlevel 1 (
    echo.
    echo [警告] 工作区有未提交的改动：
    git status --short
    echo.
    echo 继续推送只会上传"已提交的"内容，未提交改动不会上传。
)

REM ---- 5. 显示 remote ----
for /f "delims=" %%r in ('git config --get remote.origin.url') do set REMOTEURL=%%r
echo 远程地址: %REMOTEURL%
echo.

REM ---- 6. 拉取远端最新（更新 lease 基准，避免误覆盖他人提交）----
echo [1/3] 正在 fetch 远端最新状态...
git fetch origin %BRANCH% 2>nul
if errorlevel 1 (
    echo       ^(fetch 失败或远端无此分支，可能是首次推送^)
) else (
    echo       fetch 完成
)

echo.
echo [2/3] 本地与远端差异：
echo ------------------------------------------------------------
echo   本地领先远端(待推送)：
for /f "delims=" %%c in ('git log --oneline origin/%BRANCH%..HEAD 2^>nul') do echo     + %%c
echo.
echo   远端领先本地(将被覆盖/丢弃)：
for /f "delims=" %%c in ('git log --oneline HEAD..origin/%BRANCH% 2^>nul') do echo     - %%c
echo ------------------------------------------------------------
echo.

REM ---- 7. 二次确认 ----
echo [!] 强制推送会用本地历史覆盖远程分支 %BRANCH%。
echo     上面标记为 "-" 的远程提交将永久丢失。
echo.
set /p CONFIRM=确认强制推送？请输入 Y 继续（其他任意键取消）: 
if /i not "%CONFIRM%"=="Y" (
    echo.
    echo 已取消，未做任何推送。
    pause
    exit /b 0
)

REM ---- 8. 执行强制推送 ----
echo.
echo [3/3] 正在强制推送...
git push --force-with-lease origin %BRANCH%
if errorlevel 1 (
    echo.
    echo [失败] 推送被拒绝或出错。
    echo   常见原因：
    echo     1) 远端有你本地没有的新提交（--force-with-lease 的保护机制，防止覆盖他人工作）
    echo     2) SSH key 未配置或未被 GitHub 授权
    echo   若确认远端内容可丢弃，可手动执行：
    echo     git push --force origin %BRANCH%
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   推送成功！
echo ============================================================
echo.
git log --oneline -5
echo.
pause
