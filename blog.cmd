@echo off
setlocal
set "ROOT=%~dp0"
set "PYEXE="
if exist "C:\Python313\python.exe" set "PYEXE=C:\Python313\python.exe"
if not defined PYEXE if exist "C:\Users\ChowHW\.workbuddy\binaries\python\versions\3.13.12\python.exe" set "PYEXE=C:\Users\ChowHW\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not defined PYEXE set "PYEXE=python"
"%PYEXE%" "%ROOT%tools\cli.py" %*
endlocal
