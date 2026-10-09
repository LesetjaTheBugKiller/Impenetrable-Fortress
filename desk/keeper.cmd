@echo off
cd /d "%~dp0.."
"%SystemRoot%\py.exe" -3 -m desk.keeper run --config "%~dp0desk.env"
exit /b %ERRORLEVEL%
