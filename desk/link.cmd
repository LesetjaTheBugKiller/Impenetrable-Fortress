@echo off
cd /d "%~dp0.."
"%SystemRoot%\py.exe" -3 -m desk.link link --config "%~dp0desk.env"
exit /b %ERRORLEVEL%
