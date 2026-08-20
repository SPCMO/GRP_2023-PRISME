@echo off
cd /d "%~dp0"
setlocal enabledelayedexpansion

echo.
echo ============================================================
echo   GRP_2023-PRISME - Verification / installation de l'environnement
echo ============================================================

set "PYTHON_EXE="

if exist python_exe.txt (
    set /p PYTHON_EXE=<python_exe.txt
    echo.
    echo   Python deja memorise : !PYTHON_EXE!
    "!PYTHON_EXE!" --version >nul 2>&1
    if !errorlevel! neq 0 (
        echo   Ce chemin n'est plus valide, nouvelle detection...
        set "PYTHON_EXE="
    )
)

if not "!PYTHON_EXE!"=="" goto lancer_test

rem -- Detection automatique (py launcher, puis python), sans demander de version -----
where py >nul 2>&1
if %errorlevel%==0 (
    for /f "delims=" %%V in ('py -c "import sys; print(1 if sys.version_info[:2] >= (3,9) else 0)" 2^>nul') do set "PY_OK=%%V"
    if "!PY_OK!"=="1" (
        for /f "delims=" %%P in ('py -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%P"
    )
)

if not "!PYTHON_EXE!"=="" goto trouve

where python >nul 2>&1
if %errorlevel%==0 (
    for /f "delims=" %%V in ('python -c "import sys; print(1 if sys.version_info[:2] >= (3,9) else 0)" 2^>nul') do set "PY_OK=%%V"
    if "!PY_OK!"=="1" (
        for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%P"
    )
)

if not "!PYTHON_EXE!"=="" goto trouve

:saisie_manuelle
echo.
echo   Aucune version de Python 3.9+ n'a ete detectee automatiquement.
where py >nul 2>&1
if %errorlevel%==0 (
    echo.
    echo   Versions Python detectees par le Python Launcher :
    echo   -------------------------------------------
    py -0
    echo   -------------------------------------------
)
echo.
echo   Entrez le chemin complet vers python.exe (3.9 ou plus recent) :
set /p PYTHON_EXE="   Chemin : "
set PYTHON_EXE=!PYTHON_EXE:"=!
if "!PYTHON_EXE!"=="" (
    echo   [ERR] Aucun chemin saisi.
    pause
    exit /b 1
)
goto valider

:trouve
for /f "delims=" %%V in ('"!PYTHON_EXE!" --version 2^>^&1') do echo.   Python detecte automatiquement : %%V
echo   ^(!PYTHON_EXE!^)

:valider
"!PYTHON_EXE!" --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   [ERR] Impossible d'executer : !PYTHON_EXE!
    goto saisie_manuelle
)
echo !PYTHON_EXE!>python_exe.txt
echo   Choix enregistre dans python_exe.txt (Lancer_GRP_2023-PRISME.bat l'utilisera aussi).

:lancer_test
echo.
echo   Lancement de la verification...
echo.
"!PYTHON_EXE!" Test_pr_install.py
