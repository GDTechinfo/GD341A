@echo off
REM Gera dist\CH341Studio-portable\CH341Studio.exe (um unico .exe) + portable.txt
REM Configuracoes, historico e banco de chips ficam na pasta "dados" ao lado do .exe
cd /d "%~dp0"
python -m pip install -r requirements.txt pyinstaller || goto :erro
set WORK=%TEMP%\ch341studio_build_portable
python -m PyInstaller --noconfirm --clean --windowed --onefile --name CH341Studio ^
  --workpath "%WORK%" --specpath "%WORK%" ^
  --distpath dist\CH341Studio-portable ^
  --add-data "%~dp0ch341studio\chips;ch341studio\chips" ^
  --add-data "%~dp0ch341studio\assets;ch341studio\assets" ^
  --icon "%~dp0ch341studio\assets\icon.ico" ^
  --collect-all libusb_package ^
  run.py || goto :erro
rmdir /s /q "%WORK%" 2>nul
if exist build\CH341Studio rmdir /s /q build 2>nul
echo Este arquivo ativa o modo portatil. Apague-o para usar a pasta do usuario.> dist\CH341Studio-portable\portable.txt
if exist drivers\CH341DLLA64.DLL copy /Y drivers\CH341DLLA64.DLL dist\CH341Studio-portable\ >nul
echo.
echo Pronto: dist\CH341Studio-portable\CH341Studio.exe  (copie a pasta inteira para um pendrive)
explorer "dist\CH341Studio-portable"
goto :eof
:erro
echo Falha na compilacao.
pause
exit /b 1
