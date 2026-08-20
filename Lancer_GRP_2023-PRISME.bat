@echo off
cd /d "%~dp0"

:: Utilise le Python memorise par Test_pr_install.bat si disponible
set PYTHON_EXE=python
if exist python_exe.txt (
    set /p PYTHON_EXE=<python_exe.txt
)

"%PYTHON_EXE%" main.py
if errorlevel 1 (
    echo.
    echo   [ERREUR] L'outil n'a pas pu demarrer.
    echo   Lancez d'abord Test_pr_install.bat pour verifier l'environnement.
    pause
)
