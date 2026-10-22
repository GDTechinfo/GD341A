@echo off
REM Gera dist\CH341Studio\CH341Studio.exe  (use SEMPRE o .exe da pasta dist)
REM Requisitos: Python 3.10+ (64 bits) instalado pelo python.org, no PATH
cd /d "%~dp0"
python -m pip install -r requirements.txt pyinstaller || goto :erro
REM a pasta de trabalho do PyInstaller vai para %TEMP% e e apagada no fim,
REM para nao sobrar um .exe incompleto em "build" que nao abre
set WORK=%TEMP%\ch341studio_build
python -m PyInstaller --noconfirm --clean --windowed --name CH341Studio ^
  --workpath "%WORK%" --specpath "%WORK%" ^
  --add-data "%~dp0ch341studio\chips;ch341studio\chips" ^
  --add-data "%~dp0ch341studio\assets;ch341studio\assets" ^
  --icon "%~dp0ch341studio\assets\icon.ico" ^
  --collect-all libusb_package ^
  run.py || goto :erro
rmdir /s /q "%WORK%" 2>nul
if exist build\CH341Studio rmdir /s /q build 2>nul
if exist drivers\CH341DLLA64.DLL (
  mkdir dist\CH341Studio\drivers 2>nul
  copy /Y drivers\CH341DLLA64.DLL dist\CH341Studio\drivers\ >nul
)
echo.
echo Pronto: dist\CH341Studio\CH341Studio.exe
echo (para levar para outro PC, copie a pasta dist\CH341Studio inteira)
explorer "dist\CH341Studio"
goto :eof
:erro
echo Falha na compilacao.
pause
exit /b 1
