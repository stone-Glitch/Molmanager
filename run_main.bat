@echo off
chcp 65001 >nul
set "PY=C:/Users/lvdouzhijia82/miniconda3/envs/mol_manager_312/python.exe"
set "WS=%~dp0"
set "PATH=C:/Users/lvdouzhijia82/miniconda3/envs/mol_manager_312;C:/Users/lvdouzhijia82/miniconda3/envs/mol_manager_312/Library/bin;C:/Users/lvdouzhijia82/miniconda3/envs/mol_manager_312/Scripts;%PATH%"
set "PATH=%PATH:OpenBabel-3.1.1;=%"
set "PATH=%PATH:;OpenBabel-3.1.1=%"
set "PATH=%PATH:OpenBabel-3.1.1=%"
set "PATH=%PATH:OpenBabel-3.1.1\=%"
set "PATH=%PATH:;OpenBabel-3.1.1\=%"
cd /d "%WS%"
"%PY%" "%WS%main.py"
pause
