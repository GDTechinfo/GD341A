@echo off
REM Gera o instalador do Windows: dist\CH341Studio-<versao>-instalador-windows.exe
REM Requisitos:
REM   - Python 3.10+ 64 bits do python.org, no PATH
REM   - Inno Setup 6 (gratuito): https://jrsoftware.org/isdl.php
cd /d "%~dp0"

for /f "delims=" %%v in ('python -c "import ch341studio; print(ch341studio.__version__)"') do set VER=%%v
if "%VER%"=="" goto :erro
echo Versao: %VER%

python -m pip install -r requirements.txt pyinstaller || goto :erro
set WORK=%TEMP%\ch341studio_build
python -m PyInstaller --noconfirm --clean --windowed --name CH341Studio ^
  --icon "%~dp0ch341studio\assets\icon.ico" ^
  --workpath "%WORK%" --specpath "%WORK%" ^
  --add-data "%~dp0ch341studio\chips;ch341studio\chips" ^
  --add-data "%~dp0ch341studio\assets;ch341studio\assets" ^
  --collect-all libusb_package ^
  run.py || goto :erro
rmdir /s /q "%WORK%" 2>nul
if exist drivers\CH341DLLA64.DLL (
  mkdir dist\CH341Studio\drivers 2>nul
  copy /Y drivers\CH341DLLA64.DLL dist\CH341Studio\drivers\ >nul
)

REM procura o compilador do Inno Setup
set ISCC=
where iscc >nul 2>nul && set ISCC=iscc
if "%ISCC%"=="" if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if "%ISCC%"=="" if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set ISCC="%ProgramFiles%\Inno Setup 6\ISCC.exe"
if "%ISCC%"=="" if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set ISCC="%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if "%ISCC%"=="" (
  echo.
  echo Inno Setup 6 nao encontrado. Instale em https://jrsoftware.org/isdl.php e rode de novo.
  echo O programa ja esta pronto em dist\CH341Studio, so falta o instalador.
  if not defined CI pause
  exit /b 1
)
%ISCC% /Qp /DAppVersion=%VER% installer\ch341studio.iss || goto :erro
echo.
echo Pronto: dist\CH341Studio-%VER%-instalador-windows.exe
if not defined CI explorer dist
goto :eof
:erro
echo Falha ao gerar o instalador.
if not defined CI pause
exit /b 1
