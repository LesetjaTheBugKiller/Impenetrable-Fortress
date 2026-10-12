@echo off
rem Run every test suite in Impenetrable-Fortress and write desk\test_results.log.
rem Order: check the tree and git pull --ff-only on main, then every suite.
rem It never forces, stashes, resets, commits or pushes, and never reads desk.env or any key.
rem Exit 0 only when every suite that ran passed. A SKIPPED suite does not fail the run,
rem except desk-python, which must run, and mql5 when REQUIRE_MQL5_TESTS=1.
rem
rem Usage: desk\run_all_tests.cmd [--no-pull] [--allow-dirty]
rem   default        main branch only, refuse tracked local changes, git pull --ff-only.
rem   --no-pull      do not pull and do not check the branch. Still refuses tracked local changes.
rem   --allow-dirty  only together with --no-pull: also skip the tracked-changes check.
rem   CI=1, CI=true or GITHUB_ACTIONS=true: no git checks and no pull.
rem   RUN_LIVE_TESTS=1 also runs desk\tests\test_tle_live.py, which uses the network.
rem   REQUIRE_MQL5_TESTS=1 counts the MQL5 suite as FAIL instead of SKIPPED. For now that always
rem   fails, because MQL5 results are not read in yet.
rem The log never contains the option name of the dirty-tree escape; deploy.cmd refuses such logs.
setlocal EnableExtensions EnableDelayedExpansion
rem Save the script folder before :parse_args; plain SHIFT also shifts %0.
set "SCRIPT_DIR=%~dp0"

set "NO_PULL="
set "ALLOW_DIRTY="
set "IS_CI="
set "ARG_ERROR="
:parse_args
if "%~1"=="" goto args_done
if /i "%~1"=="--no-pull" (
    set "NO_PULL=1"
    shift
    goto parse_args
)
if /i "%~1"=="--allow-dirty" (
    set "ALLOW_DIRTY=1"
    shift
    goto parse_args
)
if /i "%~1"=="--help" goto usage_ok
if /i "%~1"=="-h" goto usage_ok
if "%~1"=="/?" goto usage_ok
echo Unknown argument: "%~1"
set "ARG_ERROR=1"
goto args_done
:usage_ok
call :usage
exit /b 0
:args_done
if /i "%CI%"=="1" set "IS_CI=1"
if /i "%CI%"=="true" set "IS_CI=1"
if /i "%GITHUB_ACTIONS%"=="true" set "IS_CI=1"
if defined ALLOW_DIRTY if not defined NO_PULL if not defined IS_CI (
    echo Refused: --allow-dirty works only together with --no-pull. A pull is never made on a dirty tree.
    set "ARG_ERROR=1"
)

set "GIT_MODE=pull"
if defined NO_PULL set "GIT_MODE=nopull"
if defined IS_CI set "GIT_MODE=ci"
set "TREE_SUFFIX="
if "%GIT_MODE%"=="ci" set "TREE_SUFFIX= (tree not checked: CI)"
if "%GIT_MODE%"=="nopull" if defined ALLOW_DIRTY set "TREE_SUFFIX= (tree not checked: dirty allowed)"

cd /d "%SCRIPT_DIR%.." || (echo Could not open the project folder. & exit /b 1)
set "ROOT=%CD%"
set "LOG=%ROOT%\desk\test_results.log"
set "OUT=%ROOT%\desk\test_output"
set "OVERALL=PASS"
set "SKIPPED_LIST="
if not exist "%OUT%" mkdir "%OUT%"
del /q "%OUT%\*.txt" >nul 2>&1

> "%LOG%" echo Impenetrable-Fortress tests  %DATE% %TIME%
>>"%LOG%" echo Folder: %ROOT%
>>"%LOG%" echo Raw output per suite: desk\test_output\SUITE.txt
>>"%LOG%" echo.

rem Bad options: say so on the console only, and leave a log that cannot pass.
if defined ARG_ERROR (
    call :usage
    >>"%LOG%" echo STOPPED: invalid command-line options, see the console. Nothing was pulled or tested.
    >>"%LOG%" echo OVERALL FAIL
    exit /b 1
)

rem ---------- git ----------
if "%GIT_MODE%"=="ci" goto git_ci
where git >nul 2>&1 || (call :stop "git is not on PATH. Nothing was pulled or tested." & exit /b 1)
if "%GIT_MODE%"=="nopull" goto git_nopull

set "BRANCH="
for /f "delims=" %%b in ('git rev-parse --abbrev-ref HEAD 2^>nul') do set "BRANCH=%%b"
if /i not "%BRANCH%"=="main" (
    call :stop "Current branch is '%BRANCH%', not main. Nothing was pulled or tested."
    exit /b 1
)
>>"%LOG%" echo git: pull mode, branch main
call :check_clean
if errorlevel 1 exit /b 1

echo Pulling main with git pull --ff-only ...
git pull --ff-only > "%OUT%\git-pull.txt" 2>&1
set "PULL_CODE=%ERRORLEVEL%"
type "%OUT%\git-pull.txt"
if not "%PULL_CODE%"=="0" (
    >>"%LOG%" echo git pull --ff-only: FAILED, exit code %PULL_CODE%
    type "%OUT%\git-pull.txt" >> "%LOG%"
    call :stop "git pull failed. Nothing was tested."
    exit /b 1
)
set "PULL_LAST="
for /f "usebackq delims=" %%l in ("%OUT%\git-pull.txt") do set "PULL_LAST=%%l"
>>"%LOG%" echo git pull --ff-only: OK - !PULL_LAST!
>>"%LOG%" echo.
goto suites

:git_nopull
>>"%LOG%" echo git: no pull (--no-pull), branch not checked.
if defined ALLOW_DIRTY goto git_nopull_dirty
call :check_clean
if errorlevel 1 exit /b 1
>>"%LOG%" echo.
goto suites

:git_nopull_dirty
echo git: tracked-changes check skipped, dirty tree allowed.
>>"%LOG%" echo git: tree not checked, dirty tree allowed. Tested the folder as it is.
>>"%LOG%" echo.
goto suites

:git_ci
echo git: skipped (CI)
>>"%LOG%" echo git: skipped (CI). No tracked-changes check, no pull. Tested the folder as it is.
>>"%LOG%" echo.

rem ---------- suites ----------
:suites
call :suite_python
call :suite_maven
call :suite_deploy
call :suite_mql5

>>"%LOG%" echo.
>>"%LOG%" echo NOTE: desk\tests\test_tle_live.py is skipped unless RUN_LIVE_TESTS=1. With it set, it contacts the public drand beacon at api.drand.sh and uses desk\bin\tle.exe.
>>"%LOG%" echo NOTE: Maven may download dependencies from Maven Central on its first run.
>>"%LOG%" echo NOTE: legacy\python\s.py has no tests.
if defined SKIPPED_LIST (>>"%LOG%" echo SKIPPED, not counted as failures:!SKIPPED_LIST!)
>>"%LOG%" echo OVERALL %OVERALL%%TREE_SUFFIX%

echo.
type "%LOG%"
if "%OVERALL%"=="PASS" (exit /b 0) else (exit /b 1)


rem ---------- refuse tracked local changes ----------
:check_clean
git status --porcelain --untracked-files=no > "%OUT%\git-status.txt" 2>&1
if errorlevel 1 (
    type "%OUT%\git-status.txt"
    type "%OUT%\git-status.txt" >> "%LOG%"
    call :stop "git status failed. Nothing was pulled or tested."
    exit /b 1
)
set "STATUS_SIZE=0"
for %%s in ("%OUT%\git-status.txt") do set "STATUS_SIZE=%%~zs"
if not "%STATUS_SIZE%"=="0" (
    echo Tracked files have local changes:
    type "%OUT%\git-status.txt"
    >>"%LOG%" echo Tracked files have local changes:
    type "%OUT%\git-status.txt" >> "%LOG%"
    call :stop "Commit or undo these changes first. Nothing was pulled or tested. This script never stashes, resets or forces."
    exit /b 1
)
>>"%LOG%" echo git: tracked files are clean.
exit /b 0


rem ---------- Python unittest suite in desk\tests, required ----------
:suite_python
set "NAME=desk-python"
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (where python >nul 2>&1 && set "PY=python")
if not defined PY (
    call :record 9 "not run: Python not found. Neither py nor python is on PATH. This suite is required."
    goto :eof
)
echo Running %NAME%: %PY% -m unittest discover -s desk\tests -t .
%PY% -m unittest discover -s desk\tests -t . > "%OUT%\%NAME%.txt" 2>&1
set "CODE=%ERRORLEVEL%"
call :parse_unittest "%OUT%\%NAME%.txt"
if "!RAN!"=="0" set "CODE=9"
if "!RAN!"=="?" set "CODE=9"
call :record !CODE! "ran=!RAN! failures=!FAILS! errors=!ERRS! skipped=!SKIPS!"
goto :eof

:parse_unittest
set "RAN=?"
set "FAILS=0"
set "ERRS=0"
set "SKIPS=0"
set "LAST="
for /f "tokens=2" %%a in ('findstr /b /c:"Ran " "%~1"') do set "RAN=%%a"
for /f "delims=" %%l in ('findstr /b /c:"OK" /c:"FAILED (" "%~1"') do set "LAST=%%l"
if not defined LAST goto :eof
set "L=!LAST:FAILED (=!"
set "L=!L:OK (=!"
set "L=!L:)=!"
for /f "tokens=1-6 delims=,= " %%a in ("!L!") do (
    call :count "%%a" "%%b"
    call :count "%%c" "%%d"
    call :count "%%e" "%%f"
)
goto :eof

:count
if /i "%~1"=="failures" set "FAILS=%~2"
if /i "%~1"=="errors" set "ERRS=%~2"
if /i "%~1"=="skipped" set "SKIPS=%~2"
goto :eof


rem ---------- Java server, Maven and JUnit 5 ----------
:suite_maven
set "NAME=java-maven"
if not exist "pom.xml" (call :skip "No pom.xml at the project root." & goto :eof)
where mvn >nul 2>&1 || (call :skip "Maven is not on PATH." & goto :eof)
where java >nul 2>&1 || (call :skip "Java is not on PATH." & goto :eof)
echo Running %NAME%: mvn -B test
call mvn -B test > "%OUT%\%NAME%.txt" 2>&1
set "CODE=%ERRORLEVEL%"
set "RAN=?"
set "FAILS=?"
set "ERRS=?"
set "SKIPS=?"
for /f "tokens=4,6,8,10 delims=:, " %%a in ('findstr /c:"Tests run:" "%OUT%\%NAME%.txt" ^| findstr /v /c:"Time elapsed"') do (
    set "RAN=%%a"
    set "FAILS=%%b"
    set "ERRS=%%c"
    set "SKIPS=%%d"
)
call :record %CODE% "ran=!RAN! failures=!FAILS! errors=!ERRS! skipped=!SKIPS!"
goto :eof


rem ---------- deploy\test-deploy.sh, fake docker, no registry ----------
:suite_deploy
set "NAME=deploy-bash"
if not exist "deploy\test-deploy.sh" (call :skip "No deploy\test-deploy.sh." & goto :eof)
set "GITBASH=%ProgramFiles%\Git\bin\bash.exe"
if not exist "%GITBASH%" (call :skip "Git Bash not found at %ProgramFiles%\Git\bin\bash.exe. The WSL bash is not used." & goto :eof)
echo Running %NAME%: bash deploy/test-deploy.sh
"%GITBASH%" deploy/test-deploy.sh > "%OUT%\%NAME%.txt" 2>&1
set "CODE=%ERRORLEVEL%"
set "RAN=?"
set "FAILS=?"
set "PASSED="
for /f "tokens=2,4 delims== " %%a in ('findstr /b /c:"passed=" "%OUT%\%NAME%.txt"') do (
    set "PASSED=%%a"
    set "FAILS=%%b"
)
if defined PASSED set /a RAN=PASSED+FAILS >nul 2>&1
call :record %CODE% "ran=!RAN! failures=!FAILS! errors=n/a"
goto :eof


rem ---------- MQL5 tests need MetaTrader 5 ----------
:suite_mql5
set "NAME=mql5"
if not exist "mql5\Scripts\Tests\ReceiverProtocolTests.mq5" (call :skip "No MQL5 test script found." & goto :eof)
if "%REQUIRE_MQL5_TESTS%"=="1" (
    call :record 9 "not run: REQUIRE_MQL5_TESTS=1, but MQL5 results are not read in yet, so this always fails for now."
    goto :eof
)
call :skip "mql5\Scripts\Tests\ReceiverProtocolTests.mq5 runs only inside MetaTrader 5. Compile it in MetaEditor, run it on a chart, and read PASS and FAIL in the Experts tab."
goto :eof


rem ---------- helpers ----------
:record
if "%~1"=="0" (set "RESULT=PASS") else (set "RESULT=FAIL" & set "OVERALL=FAIL")
>>"%LOG%" echo %NAME%  %RESULT%  %~2  exit=%~1  raw: desk\test_output\%NAME%.txt
echo %NAME%: %RESULT%  %~2
goto :eof

:skip
>>"%LOG%" echo %NAME%  SKIPPED  %~1
echo %NAME%: SKIPPED  %~1
set "SKIPPED_LIST=!SKIPPED_LIST! %NAME%"
goto :eof

:usage
echo Usage: desk\run_all_tests.cmd [--no-pull] [--allow-dirty]
echo   default        main branch only, refuse tracked local changes, git pull --ff-only.
echo   --no-pull      do not pull and do not check the branch. Still refuses tracked local changes.
echo   --allow-dirty  only together with --no-pull: also skip the tracked-changes check.
echo   CI=1, CI=true or GITHUB_ACTIONS=true: no git checks and no pull.
echo   RUN_LIVE_TESTS=1 also runs the live time-lock test, which uses the network.
echo   REQUIRE_MQL5_TESTS=1 counts the MQL5 suite as FAIL. For now that always fails.
goto :eof

:stop
echo STOPPED: %~1
>>"%LOG%" echo STOPPED: %~1
>>"%LOG%" echo OVERALL FAIL
goto :eof
