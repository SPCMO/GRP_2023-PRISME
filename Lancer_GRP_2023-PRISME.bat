@echo off
rem GRP_2023-PRISME - lancement. pushd et non cd : fonctionne aussi depuis un dossier reseau.
pushd "%~dp0"
setlocal enabledelayedexpansion

rem Python memorise par Test_pr_install.bat (python_exe.txt). Sont REFUSES le python.exe de WAPT (interpreteur embarque qui
rem n'ajoute pas le dossier du script a sys.path) et l'alias du Microsoft Store (WindowsApps) : on cherche alors un vrai
rem Python (lanceur py, puis PATH hors WAPT / Store).
set "PYTHON_EXE="
if exist python_exe.txt set /p PYTHON_EXE=<python_exe.txt
if not defined PYTHON_EXE goto :chercher

echo !PYTHON_EXE!| findstr /i /c:"\wapt" /c:"\WindowsApps" >nul
if errorlevel 1 goto :memo_pas_wapt
echo.
echo   [ATTENTION] Le Python memorise dans python_exe.txt est celui de WAPT ou du Microsoft Store : il est ignore.
echo   Relancez Test_pr_install.bat pour memoriser un vrai Python.
set "PYTHON_EXE="
goto :chercher

:memo_pas_wapt
if exist "!PYTHON_EXE!" goto :lancer
echo.
echo   [ATTENTION] Le Python memorise dans python_exe.txt est introuvable : !PYTHON_EXE!
set "PYTHON_EXE="

:chercher
where py >nul 2>nul
if not errorlevel 1 for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%P"
if defined PYTHON_EXE goto :lancer
for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined PYTHON_EXE (
        echo %%P| findstr /i /c:"\wapt" /c:"\WindowsApps" >nul
        if errorlevel 1 set "PYTHON_EXE=%%P"
    )
)
if defined PYTHON_EXE goto :lancer

echo.
echo   [ERREUR] Aucun vrai Python trouve. Le python de WAPT et l'alias du Microsoft Store ne conviennent pas.
echo   Lancez Test_pr_install.bat pour verifier l'environnement.
popd
pause
exit /b 1

:lancer
"!PYTHON_EXE!" main.py
set "CODE_RETOUR=!errorlevel!"
popd
if not "!CODE_RETOUR!"=="0" (
    echo.
    echo   [ERREUR] L'outil n'a pas pu demarrer.
    echo   Lancez d'abord Test_pr_install.bat pour verifier l'environnement.
    pause
)
