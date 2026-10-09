@echo off
cd /d "%~dp0.."
"%SystemRoot%\py.exe" -3 -m desk.link reseal --config "%~dp0desk.env"
exit /b %ERRORLEVEL%
