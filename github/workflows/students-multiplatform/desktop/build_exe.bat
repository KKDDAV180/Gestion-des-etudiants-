@echo off
REM ---------------------------------------------------------------
REM  Construit GestionEtudiants.exe (Windows 10/11, Python 3.10+)
REM  Double-cliquez ce fichier, ou lancez-le depuis le dossier du projet.
REM  Resultat : dist\GestionEtudiants.exe
REM ---------------------------------------------------------------
cd /d "%~dp0.."
python -m pip install --upgrade pip || goto :error
python -m pip install -r requirements.txt -r desktop\requirements-desktop.txt || goto :error

python -m PyInstaller --noconfirm --clean --onefile --noconsole ^
  --name GestionEtudiants ^
  --icon desktop\app.ico ^
  --paths . ^
  --add-data "templates;templates" ^
  --add-data "static;static" ^
  --hidden-import app ^
  --collect-submodules webview ^
  desktop\launcher.py || goto :error

echo.
echo Termine : dist\GestionEtudiants.exe
pause
exit /b 0

:error
echo.
echo ECHEC de la construction.
pause
exit /b 1
