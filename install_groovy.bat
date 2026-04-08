@echo off
echo === Groovy Install for NoteCraft CPI Simulator ===
where groovy >nul 2>nul && echo Groovy already installed! && groovy --version && exit /b 0
echo.
echo Please install Groovy using one of:
echo   1. Chocolatey:  choco install groovy
echo   2. SDKMAN (WSL): sdk install groovy
echo   3. Manual: https://groovy.apache.org/download.html
echo.
echo After install, restart your terminal and run the app.
pause
