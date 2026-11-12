@echo off
REM Builds dist\VPB-windows.zip : unzip anywhere and run VPB.exe (no Python needed).
REM For a build you can vouch for, create requirements-build.lock first (see docs\security.md).
cd /d "%~dp0"

if not exist .venv (
  py -3 -m venv .venv || goto :err
)

if exist requirements-build.lock (
  echo Installing from requirements-build.lock with hash checking
  .venv\Scripts\python -m pip install --require-hashes -r requirements-build.lock || goto :err
) else (
  echo WARNING: no requirements-build.lock found, so unpinned packages will be installed.
  echo          See docs\security.md to create one.
  .venv\Scripts\python -m pip install -r requirements-build.txt || goto :err
)

.venv\Scripts\pyinstaller --noconfirm --clean --noconsole --onedir --noupx --name VPB ^
  --icon assets\icon.ico --version-file version_info.txt ^
  --paths . --hidden-import keyring.backends.Windows --hidden-import websocket ^
  vpb.py || goto :err

REM Optional code signing: set VPB_SIGN_CERT to a .pfx file and VPB_SIGN_PASSWORD to its password first.
if defined VPB_SIGN_CERT (
  echo Signing VPB.exe
  signtool sign /fd SHA256 /td SHA256 /tr http://timestamp.digicert.com /f "%VPB_SIGN_CERT%" /p "%VPB_SIGN_PASSWORD%" dist\VPB\VPB.exe || goto :err
)

if not exist dist\VPB\templates mkdir dist\VPB\templates
copy /y templates\README.md dist\VPB\templates\ >nul
xcopy /e /i /y templates\defaults dist\VPB\templates\defaults >nul
if not exist dist\VPB\assets mkdir dist\VPB\assets
copy /y assets\icon.ico dist\VPB\assets\ >nul
xcopy /e /i /y docs dist\VPB\docs >nul
copy /y README.md dist\VPB\README.md >nul
copy /y .env.example dist\VPB\.env.example >nul
powershell -NoProfile -Command "Compress-Archive -Path dist\VPB\* -DestinationPath dist\VPB-windows.zip -Force" || goto :err
echo.
echo Done: dist\VPB-windows.zip
echo SHA-256 of the zip (put this in the release notes):
certutil -hashfile dist\VPB-windows.zip SHA256
echo Test it: unzip somewhere else, run VPB.exe, walk through the Setup Guide, use Dry-Run.
exit /b 0
:err
echo BUILD FAILED
exit /b 1
