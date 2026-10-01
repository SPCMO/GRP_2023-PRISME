@echo off
rem GRP_2023-PRISME - verification / installation de l'environnement. pushd : fonctionne aussi depuis un dossier reseau.
pushd "%~dp0"
setlocal enabledelayedexpansion

echo.
echo ============================================================
echo   GRP_2023-PRISME - Verification / installation de l'environnement
echo ============================================================
echo.
echo   Ne sont jamais retenus : le python.exe de WAPT ^(interpreteur embarque,
echo   inutilisable pour cet outil^) et l'alias du Microsoft Store ^(WindowsApps^).

set "PYTHON_EXE="
set "TENTATIVES=0"

rem Choix deja memorise : refuse s'il designe WAPT / le Store, ou s'il n'existe plus ou ne s'execute plus
if not exist python_exe.txt goto :detection
set /p PYTHON_EXE=<python_exe.txt
echo !PYTHON_EXE!| findstr /i /c:"\wapt" /c:"\WindowsApps" >nul
if errorlevel 1 goto :memo_pas_wapt
echo.
echo   Le Python memorise est celui de WAPT ou du Microsoft Store : IGNORE.
echo   ^(!PYTHON_EXE!^)
set "PYTHON_EXE="
goto :detection

:memo_pas_wapt
echo.
echo   Python deja memorise : !PYTHON_EXE!
"!PYTHON_EXE!" --version >nul 2>&1
if errorlevel 1 (
    echo   Ce chemin n'est plus valide, nouvelle detection...
    set "PYTHON_EXE="
)
if not "!PYTHON_EXE!"=="" goto :valider

:detection
rem -- Detection automatique : lanceur py (ne liste que de vrais Python), puis premier python du PATH hors WAPT / Store --
set "PY_OK="
where py >nul 2>&1
if errorlevel 1 goto :detection_path
for /f "delims=" %%V in ('py -3 -c "import sys; print(1 if sys.version_info[:2] >= (3,9) else 0)" 2^>nul') do set "PY_OK=%%V"
if not "!PY_OK!"=="1" goto :detection_path
for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%P"
if not "!PYTHON_EXE!"=="" goto :trouve

:detection_path
echo.
echo   Pythons trouves dans le PATH :
set "DEFAUT="
for /f "delims=" %%P in ('where python 2^>nul') do (
    echo %%P| findstr /i /c:"\wapt" /c:"\WindowsApps" >nul
    if errorlevel 1 (
        echo     %%P   [utilisable]
        if not defined DEFAUT set "DEFAUT=%%P"
    ) else (
        echo     %%P   [IGNORE : WAPT ou alias du Microsoft Store]
    )
)
if not defined DEFAUT echo     aucun utilisable
if not defined DEFAUT goto :saisie_manuelle
"!DEFAUT!" -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3,9) else 1)" >nul 2>&1
if not errorlevel 1 set "PYTHON_EXE=!DEFAUT!"
if not "!PYTHON_EXE!"=="" goto :trouve

:saisie_manuelle
set /a TENTATIVES+=1
if !TENTATIVES! gtr 5 (
    echo.
    echo   [ERR] Trop de tentatives. Installez Python 3.9 ou plus recent depuis https://www.python.org/downloads/
    popd
    pause
    exit /b 1
)
echo.
echo   Aucune version de Python 3.9+ n'a ete detectee automatiquement.
where py >nul 2>&1
if errorlevel 1 goto :saisie_chemin
echo.
echo   Versions Python detectees par le Python Launcher :
echo   -------------------------------------------
py -0
echo   -------------------------------------------
:saisie_chemin
echo.
echo   Entrez le chemin complet vers python.exe ^(3.9 ou plus recent^) :
set "PYTHON_EXE="
set /p PYTHON_EXE="   Chemin : "
if defined PYTHON_EXE set PYTHON_EXE=!PYTHON_EXE:"=!
if not "!PYTHON_EXE!"=="" goto :valider
echo   [ERR] Aucun chemin saisi.
popd
pause
exit /b 1

:trouve
for /f "delims=" %%V in ('"!PYTHON_EXE!" --version 2^>^&1') do echo.   Python detecte automatiquement : %%V
echo   ^(!PYTHON_EXE!^)

:valider
rem Refuser WAPT / Microsoft Store, meme choisi a la main
echo !PYTHON_EXE!| findstr /i /c:"\wapt" /c:"\WindowsApps" >nul
if errorlevel 1 goto :valider_exe
echo.
echo   [ERR] !PYTHON_EXE! est le Python de WAPT ou l'alias du Microsoft Store : inutilisable.
set "PYTHON_EXE="
goto :saisie_manuelle

:valider_exe
"!PYTHON_EXE!" --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo   [ERR] Impossible d'executer : !PYTHON_EXE!
    set "PYTHON_EXE="
    goto :saisie_manuelle
)
echo !PYTHON_EXE!>python_exe.txt
echo   Choix enregistre dans python_exe.txt ^(Lancer_GRP_2023-PRISME.bat l'utilisera aussi^).

:lancer_test
echo.
echo   Lancement de la verification...
echo.
"!PYTHON_EXE!" Test_pr_install.py
popd
