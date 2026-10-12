@echo off
rem ==========================================================================
rem  deploy.cmd - Impenetrable-Fortress Receiver hotfix deploy (DRAFT v4.3, review only)
rem
rem  ######################################################################
rem  ##  DEMO ONLY until BOTH are true:                                  ##
rem  ##   (a) the recompile input-preservation test passed on demo, or   ##
rem  ##       the external JSON config has landed; and                   ##
rem  ##   (b) Lesetja has created the LIVE_OK marker for that terminal.  ##
rem  ##  Marker folder (outside the repo, created by Lesetja by hand):   ##
rem  ##     %LOCALAPPDATA%\ImpenetrableFortress\deploy-markers\           ##
rem  ##  <terminal-folder-name>.DEMO_OK or .LIVE_OK with lines:         ##
rem  ##     login=<account number MT5 shows>                             ##
rem  ##     mode=DEMO        (DEMO_OK)   or   mode=REAL   (LIVE_OK)      ##
rem  ##     server=<broker server>   (optional; checked if present)      ##
rem  ##  plus guard.sha256 (the reviewed guard hash, 64 hex, one line).  ##
rem  ##  Save marker and pin files as plain ASCII WITHOUT a BOM          ##
rem  ##  (Notepad: Save as, Encoding "ANSI"; a BOM makes them refused).  ##
rem  ######################################################################
rem
rem  Account check: if the running EA wrote MQL5\Files\RX_account.txt
rem  (login=, mode=DEMO|REAL, server=, ts=<unix seconds>) it must match the marker and
rem  be fresher than 10 minutes. LIVE requires that file. DEMO falls back to typing the
rem  login and mode with a warning. The MQL5 writer is NOT implemented yet (proposal: Zuko).
rem  The account is checked at the start and AGAIN just before the copy.
rem  RX_account.txt only binds the deploy to an account. It is NOT proof that the Receiver
rem  is running, attached to the right chart, or trading.
rem
rem  Drift (N1): if the terminal's sources differ from the lock, DRIFT-OK is offered only
rem  when every DRIFT path (guard --porcelain) is a file this deploy overwrites. The backup
rem  then records drift=1. Restoring a backup whose drift is 1 or unknown (after a failed
rem  deploy or with --rollback) puts ONLY the backed-up receiver.ex5 back, without
rem  recompiling, unless you type RECOMPILE-DRIFTED. The terminal's current (reviewed)
rem  sources stay in place; the backup's drifted sources are moved into its quarantine\
rem  folder. Then remove and re-attach the Receiver (or restart MT5).
rem  drift= values: 0 (live matched the lock), 1 (DRIFT-OK accepted), unknown (rollback
rem  snapshot without a usable guard run, or a backup from v4.1 or older with no drift= line).
rem
rem  Usage
rem    deploy.cmd                              deploy main to the default terminal
rem    deploy.cmd --terminal "<data folder>"   deploy to another terminal (data folder or its MQL5)
rem    deploy.cmd --metaeditor "<exe>"         use that terminal's MetaEditor64.exe
rem    deploy.cmd --rollback [backup-name]     restore this terminal's newest (or named) backup
rem
rem  Self-update safety: the script copies itself into a new private folder
rem  %TEMP%\if-deploy-<digits>\, checks the copy byte for byte, runs it, and deletes the
rem  folder at exit, so `git pull` can never rewrite the file cmd.exe is reading. It must be
rem  started from the repo. If the pull changed deploy.cmd: "deploy.cmd was updated by the
rem  pull. Run it again." On a fresh start every IF_* variable inherited from the
rem  environment is cleared. Options are parsed and validated BEFORE the relaunch and passed
rem  to the copy only as IF_OPT_* variables; the copy gets no raw command-line arguments.
rem  Paths containing "!" are not supported (delayed expansion).
rem
rem  Remote operations: ONLY `git pull --ff-only origin main` (hooks disabled with
rem  -c core.hooksPath=NUL). Lesetja explicitly allowed git pull on main as the one
rem  remote operation. Never runs: git push, git clean, git reset --hard, git stash,
rem  git checkout. Tags deploy-<terminal>-YYYYMMDD-HHMM are LOCAL ONLY.
rem  Never run `git push --tags`: it would publish every local deploy tag.
rem  Never copies or reads: config includes (ReceiverConfig.mqh, SenderConfig.mqh,
rem  ReceiverLimits.mqh), desk\, desk\desk.env, desk\bin\, *.set, presets, app.env, .env.
rem
rem  Guard: tools\config_guard.py (committed). Its SHA-256 must match the pin in
rem  %MARKER_DIR%\guard.sha256 (outside the repo). GUARD_SHA256 below is a second copy:
rem  if it differs from the pin file, the script refuses. Lesetja reviews the guard and
rem  writes the pin file himself (never automatic):
rem      certutil -hashfile tools\config_guard.py SHA256
rem  Only the fast-forward pull happens before this check; backup, tag, copy and
rem  compile all come after it.
rem
rem  Clean tree: deploy.cmd's own `git status --porcelain --untracked-files=no` check is the
rem  ONLY clean-tree gate. The test runner's --no-pull skips the runner's own check.
rem
rem  Test gate: desk\run_all_tests.cmd is called with the hard-coded argument
rem  --no-pull ONLY. deploy.cmd never passes --allow-dirty: it clears inherited test-argument
rem  variables, refuses if any environment variable contains "allow-dirty", deletes the old
rem  test log first, and refuses if the new log shows the uncommitted-changes check was
rem  skipped (the runner writes "...check skipped (--allow-dirty)").
rem
rem  TO VERIFY ON DEMO: the MetaEditor "Result:" log line, whether chart inputs survive
rem  a recompile, where MT5 shows the login and DEMO/REAL, and core.hooksPath=NUL.
rem ==========================================================================
setlocal EnableExtensions EnableDelayedExpansion

rem ---------- run from a private temporary copy (git pull may rewrite this file) ----------
if defined IF_DEPLOY_TMP if /i "%~f0"=="!IF_DEPLOY_TMP!" goto :relaunch_check
goto :fresh_start
:relaunch_check
if not defined IF_DEPLOY_ORIGIN (echo REFUSED: temporary copy started without IF_DEPLOY_ORIGIN. & pause & exit /b 1)
call :check_tmp_path || (echo REFUSED: this copy is not inside a private %%TEMP%%\if-deploy- folder. & pause & exit /b 1)
if not "%~1"=="" (echo REFUSED: the temporary copy must not get raw arguments. & pause & exit /b 1)
goto :relaunched

:fresh_start
rem Nothing inherited from the environment is trusted.
set "IF_DEPLOY_ORIGIN="
set "IF_DEPLOY_TMP="
set "IF_DEPLOY_DIR="
set "IF_OPT_MODE=deploy"
set "IF_OPT_RB="
set "IF_OPT_TERMINAL="
set "IF_OPT_METAEDITOR="
:args
if "%~1"=="" goto :args_done
if /i "%~1"=="--rollback" goto :arg_rollback
if /i "%~1"=="--terminal" goto :arg_terminal
if /i "%~1"=="--metaeditor" goto :arg_metaeditor
echo Unknown option "%~1". Use --terminal ^<path^>, --metaeditor ^<exe^> or --rollback [name].
goto :fail
:arg_rollback
if /i "!IF_OPT_MODE!"=="rollback" (echo --rollback given twice. & goto :fail)
set "IF_OPT_MODE=rollback"
shift
if "%~1"=="" goto :args_done
set "A=%~1"
if "!A:~0,2!"=="--" goto :args
set "IF_OPT_RB=%~1"
shift
goto :args
:arg_terminal
if defined IF_OPT_TERMINAL (echo --terminal given twice. & goto :fail)
shift
if "%~1"=="" (echo --terminal needs a folder. & goto :fail)
set "IF_OPT_TERMINAL=%~f1"
shift
goto :args
:arg_metaeditor
if defined IF_OPT_METAEDITOR (echo --metaeditor given twice. & goto :fail)
shift
if "%~1"=="" (echo --metaeditor needs a path. & goto :fail)
set "IF_OPT_METAEDITOR=%~f1"
shift
goto :args
:args_done
if defined IF_OPT_RB (
  call :check_backup_name "!IF_OPT_RB!" || goto :fail
)
if defined IF_OPT_TERMINAL (
  if not exist "!IF_OPT_TERMINAL!\MQL5\Experts\" if not exist "!IF_OPT_TERMINAL!\Experts\" (
    echo REFUSED: no MQL5\Experts folder under "!IF_OPT_TERMINAL!".
    goto :fail
  )
)
if defined IF_OPT_METAEDITOR (
  if not exist "!IF_OPT_METAEDITOR!" (echo REFUSED: MetaEditor not found: !IF_OPT_METAEDITOR! & goto :fail)
  for %%m in ("!IF_OPT_METAEDITOR!") do if /i not "%%~nxm"=="MetaEditor64.exe" (echo REFUSED: --metaeditor must point to MetaEditor64.exe. & goto :fail)
)
set "IF_DEPLOY_ORIGIN=%~f0"
set "IF_TRY=0"
:mk_tmp
set /a IF_TRY+=1
if !IF_TRY! GTR 50 (echo REFUSED: cannot create a private folder under %TEMP%. & pause & exit /b 1)
set "IF_DEPLOY_DIR=%TEMP%\if-deploy-!RANDOM!!RANDOM!"
mkdir "!IF_DEPLOY_DIR!" 2>nul || goto :mk_tmp
set "IF_DEPLOY_TMP=!IF_DEPLOY_DIR!\deploy.cmd"
copy /y "%~f0" "!IF_DEPLOY_TMP!" >nul 2>&1 || (rd /s /q "!IF_DEPLOY_DIR!" >nul 2>&1 & echo REFUSED: cannot copy deploy.cmd to %TEMP%. & pause & exit /b 1)
fc /b "%~f0" "!IF_DEPLOY_TMP!" >nul 2>&1 || (rd /s /q "!IF_DEPLOY_DIR!" >nul 2>&1 & echo REFUSED: the temporary copy is not identical. & pause & exit /b 1)
call "!IF_DEPLOY_TMP!" & set "IF_RC=!errorlevel!" & rd /s /q "!IF_DEPLOY_DIR!" >nul 2>&1 & exit /b !IF_RC!
:relaunched

rem ---------- paths and settings (edit here) ----------
set "REPO=C:\Users\micca\personal_projects\Impenetrable-Fortress"
set "TERMINAL_DATA=%APPDATA%\MetaQuotes\Terminal\010E047102812FC0C18890992854220E"
set "METAEDITOR=C:\Program Files\MetaTrader 5 IC Markets Global\MetaEditor64.exe"
set "BACKUP_ROOT=%LOCALAPPDATA%\ImpenetrableFortress\deploy-backups"
set "SNAPSHOT_ROOT=%LOCALAPPDATA%\ImpenetrableFortress\pre-rollback-snapshots"
set "MARKER_DIR=%LOCALAPPDATA%\ImpenetrableFortress\deploy-markers"
set "PIN_FILE=%MARKER_DIR%\guard.sha256"
set "GUARD=%REPO%\tools\config_guard.py"
set "GUARD_SHA256=bfa2a623cd3debccf13f5c9b4b6b3795c3f4a93ba65c480b6e4362e681e2fb1c"
set "LOCK=%REPO%\config.lock.json"
set "PY=py -3 -B"
set "TESTS=%REPO%\desk\run_all_tests.cmd"
set "TEST_LOG=%REPO%\desk\test_results.log"
set "BRANCH=main"
rem Set to 1 after the first green MetaTrader-in-the-loop (M1) test run.
rem When 1, the deploy needs a positive "mql5  PASS" line in desk\test_results.log.
set "REQUIRE_MQL5_TESTS=0"

rem ---------- allowlist: logic files only ----------
set "SRC_EA=%REPO%\mql5\Experts\Receiver.mq5"
set "LOGIC_INCLUDES=ReceiverDailyState.mqh ReceiverDecisions.mqh ReceiverOrderExecution.mqh ReceiverPositionManagement.mqh ReceiverProtocol.mqh ReceiverSignalWorkflow.mqh"
set "CONFIG_INCLUDES=ReceiverConfig.mqh ReceiverLimits.mqh"

rem ---------- SEAL tripwire (existence only, contents never read) ----------
set "SEAL_ENV=%REPO%\desk\desk.env"
set "SEAL_TASK=%REPO%\desk\bin\reseal-task.cmd"

rem ---------- options (parsed and validated before the relaunch) ----------
set "MODE=!IF_OPT_MODE!"
set "RB=!IF_OPT_RB!"
set "TERMINAL_ARG=!IF_OPT_TERMINAL!"
if defined IF_OPT_METAEDITOR set "METAEDITOR=!IF_OPT_METAEDITOR!"
if /i not "!MODE!"=="deploy" if /i not "!MODE!"=="rollback" (echo REFUSED: unknown mode. & goto :fail)
if defined RB (
  call :check_backup_name "!RB!" || goto :fail
)
set "DRIFTED=0"

if defined TERMINAL_ARG (
  if exist "!TERMINAL_ARG!\MQL5\Experts\" (
    set "MQL5_DIR=!TERMINAL_ARG!\MQL5"
  ) else if exist "!TERMINAL_ARG!\Experts\" (
    set "MQL5_DIR=!TERMINAL_ARG!"
  ) else (
    echo REFUSED: no MQL5\Experts folder under "!TERMINAL_ARG!".
    goto :fail
  )
) else (
  set "MQL5_DIR=%TERMINAL_DATA%\MQL5"
)
for %%d in ("!MQL5_DIR!\..") do set "TERMINAL_ROOT=%%~fd"
for %%d in ("!TERMINAL_ROOT!") do set "TERMINAL_ID=%%~nxd"
set "TID8=!TERMINAL_ID: =!"
set "TID8=!TID8:~0,8!"
set "LIVE_EA=!MQL5_DIR!\Experts\receiver.mq5"
set "LIVE_EX5=!MQL5_DIR!\Experts\receiver.ex5"
set "LIVE_LOG=!MQL5_DIR!\Experts\receiver.log"
set "BK_DIR=%BACKUP_ROOT%\!TERMINAL_ID!"
set "SNAP_DIR=%SNAPSHOT_ROOT%\!TERMINAL_ID!"

call :preflight || goto :fail
call :confirm_target || goto :fail
if /i "%MODE%"=="rollback" goto :rollback

rem ==========================================================================
:deploy
if /i not "%IF_DEPLOY_ORIGIN%"=="%REPO%\deploy.cmd" (
  echo REFUSED: start the repo's own deploy.cmd ^(%REPO%\deploy.cmd^), not a copy elsewhere.
  goto :fail
)
cd /d "%REPO%" || (echo REFUSED: cannot open %REPO% & goto :fail)

set "CUR="
for /f "delims=" %%b in ('git rev-parse --abbrev-ref HEAD') do set "CUR=%%b"
if /i not "!CUR!"=="%BRANCH%" (
  echo REFUSED: the repo is on branch "!CUR!", not %BRANCH%. Nothing deployed.
  goto :fail
)
set "DIRTY="
for /f "delims=" %%l in ('git status --porcelain --untracked-files^=no') do set "DIRTY=1"
if defined DIRTY (
  echo REFUSED: tracked files have local changes. Commit or discard them yourself first.
  goto :fail
)

echo Pulling %BRANCH% ^(fast-forward only, hooks off; the one allowed remote operation^)...
git -c core.hooksPath=NUL pull --ff-only origin %BRANCH%
if errorlevel 1 (
  echo REFUSED: the pull failed or was not a fast-forward. Nothing deployed.
  goto :fail
)
fc /b "%~f0" "%REPO%\deploy.cmd" >nul 2>&1
if errorlevel 1 (
  echo STOPPED: deploy.cmd was updated by the pull. Run it again.
  echo ^(Or you started a copy that differs from the repo's deploy.cmd.^) Nothing deployed.
  goto :fail
)
set "SHA="
for /f %%s in ('git rev-parse --short HEAD') do set "SHA=%%s"

call :verify_guard || goto :fail

echo.
echo Running config guard on the repo and the live terminal...
%PY% "%GUARD%" --root "%REPO%" --lock "%LOCK%" --live "!MQL5_DIR!" --live-roots Receiver
set "GRC=!errorlevel!"
if "!GRC!"=="3" (
  echo.
  echo The live terminal differs from the lock ^(DRIFT lines above^).
  call :drift_in_copy_list || goto :fail
  echo Every drifted file is one this deploy overwrites.
  echo This is expected only if the change waiting to go live was reviewed.
  echo The backup will be marked drift=1: restoring it puts the old receiver.ex5 back without recompiling.
  set "ANS="
  set /p "ANS=Type DRIFT-OK to continue, anything else stops: "
  if not "!ANS!"=="DRIFT-OK" (echo Stopped. Nothing deployed. & goto :fail)
  set "DRIFTED=1"
  echo The guard runs --live again after the copy and must then PASS.
) else if not "!GRC!"=="0" (
  echo STOPPED: config guard failed ^(exit !GRC!; MISSING-LIVE is a hard failure^). Nothing deployed.
  goto :fail
)

call :check_includes || goto :fail

echo.
rem Never --allow-dirty: clear inherited argument variables and refuse if anything carries it.
set "IF_TEST_ARGS="
set "TEST_ARGS="
set "RUN_ALL_TESTS_ARGS="
set "ALLOW_DIRTY="
set | findstr /i /c:"allow-dirty" >nul 2>&1
if not errorlevel 1 (
  echo REFUSED: an environment variable contains "allow-dirty". deploy.cmd never skips the clean-tree check. Nothing deployed.
  goto :fail
)
if exist "%TEST_LOG%" del /q "%TEST_LOG%"
if exist "%TEST_LOG%" (echo REFUSED: could not remove the old desk\test_results.log. Nothing deployed. & goto :fail)
echo Running all test suites ^(desk\run_all_tests.cmd --no-pull, REQUIRE_MQL5_TESTS=%REQUIRE_MQL5_TESTS%^)...
call "%TESTS%" --no-pull
set "TRC=!errorlevel!"
cd /d "%REPO%"
if not "!TRC!"=="0" (
  echo STOPPED: tests failed ^(see desk\test_results.log^). Nothing deployed.
  goto :fail
)
if not exist "%TEST_LOG%" (echo STOPPED: the test runner wrote no desk\test_results.log. Nothing deployed. & goto :fail)
findstr /i /c:"allow-dirty" /c:"uncommitted-changes check skipped" "%TEST_LOG%" >nul 2>&1
if not errorlevel 1 (
  echo STOPPED: the test log shows the uncommitted-changes check was skipped ^(allow-dirty^). Nothing deployed.
  goto :fail
)
if "%REQUIRE_MQL5_TESTS%"=="1" (
  findstr /b /c:"mql5  PASS" "%TEST_LOG%" >nul 2>&1
  if errorlevel 1 (
    echo STOPPED: REQUIRE_MQL5_TESTS=1 but desk\test_results.log has no "mql5  PASS" line. Nothing deployed.
    goto :fail
  )
)

call :seal_snapshot BEFORE
call :confirm_flat || goto :fail
call :inputs_readback || goto :fail

call :timestamp || goto :fail
set "BK=!BK_DIR!\!TS!"
if exist "!BK!" (
  echo A backup for this minute already exists. Wait a minute and run again.
  goto :fail
)
call :backup "!BK!" || (echo STOPPED: backup failed or is incomplete. Nothing deployed. & goto :fail)

set "TAG=deploy-!TID8!-!TS!"
git tag "!TAG!"
if errorlevel 1 (echo STOPPED: could not create local tag !TAG!. Nothing deployed. & goto :fail)
>>"!BK!\deploy-info.txt" echo tag=!TAG!

call :recheck_account
if errorlevel 1 (
  git tag -d "!TAG!" >nul 2>&1
  echo STOPPED before the copy. Nothing in the terminal changed. Local tag !TAG! was removed.
  goto :fail
)

echo Copying logic files to the terminal ^(each copy is verified^)...
set "COPYFAIL="
call :copy_checked "%SRC_EA%" "!LIVE_EA!" || set "COPYFAIL=1"
for %%f in (%LOGIC_INCLUDES%) do (
  call :copy_checked "%REPO%\mql5\Include\%%f" "!MQL5_DIR!\Include\%%f" || set "COPYFAIL=1"
)
if defined COPYFAIL (
  echo COPY FAILED.
  goto :restore_after_failure
)

echo.
echo Re-running config guard --live on the copied files ^(must PASS^)...
%PY% "%GUARD%" --root "%REPO%" --lock "%LOCK%" --live "!MQL5_DIR!" --live-roots Receiver
if not "!errorlevel!"=="0" (
  echo STOPPED: the terminal does not match the lock after the copy.
  goto :restore_after_failure
)

call :compile
if errorlevel 1 goto :restore_after_failure

>"!BK!\deployed.txt" echo sha=!SHA!
>>"!BK!\deployed.txt" echo tag=!TAG!

call :seal_snapshot AFTER
if not "!SEAL_BEFORE!"=="!SEAL_AFTER!" echo WARNING: SEAL file presence changed during deploy. Check desk\ now.

echo.
echo DEPLOYED !SHA! to !TARGET_KIND! terminal !TERMINAL_ID!  ^(local tag !TAG!, backup !BK!^)
echo Now open the Receiver chart's Inputs tab again and compare the inputs listed
echo above with the values you noted. Check the Experts log for "[RX] Ready".
echo Reminder: the tag is local. Never run git push --tags.
goto :end

:restore_after_failure
echo Restoring the backup from !BK! ...
call :restore_from "!BK!"
set "RRC=!errorlevel!"
git tag -d "!TAG!" >nul 2>&1
if "!RRC!"=="0" (
  echo DEPLOY FAILED. The previous version was restored ^(see above^). Local tag !TAG! was removed.
) else (
  echo DEPLOY FAILED AND THE AUTOMATIC RESTORE DID NOT COMPLETE ^(code !RRC!^). Follow the message above.
  echo Local tag !TAG! was removed. Backup folder: !BK!
)
goto :fail

rem ==========================================================================
:rollback
if not defined RB (
  rem Prefer the newest backup of a deploy that succeeded, marked by deployed.txt.
  for /f "delims=" %%d in ('dir /b /ad /o-n "!BK_DIR!" 2^>nul') do if not defined RB if exist "!BK_DIR!\%%d\deployed.txt" set "RB=%%d"
)
if not defined RB (
  for /f "delims=" %%d in ('dir /b /ad /o-n "!BK_DIR!" 2^>nul') do if not defined RB set "RB=%%d"
  if defined RB echo WARNING: no backup of a successful deploy found; using the newest backup !RB!.
)
if not defined RB (echo No backup found for this terminal in !BK_DIR!. & goto :fail)
set "RBDIR=!BK_DIR!\!RB!"
call :check_backup "!RBDIR!" || goto :fail
echo Rolling back terminal !TERMINAL_ID! to backup !RB!.
if exist "!RBDIR!\deploy-info.txt" type "!RBDIR!\deploy-info.txt"
call :seal_snapshot BEFORE
call :confirm_flat || goto :fail

call :timestamp || goto :fail
call :probe_drift
set "DRIFTED=!PROBE!"
echo Current terminal drift for the pre-rollback snapshot: !DRIFTED!
call :backup "!SNAP_DIR!\!TS!" || (echo STOPPED: could not snapshot the current state. Nothing changed. & goto :fail)
call :recheck_account || (echo STOPPED before the restore. Nothing changed. & goto :fail)
call :restore_from "!RBDIR!"
if errorlevel 1 goto :fail
>"!BK_DIR!\last-rollback.txt" echo !TS!
call :seal_snapshot AFTER
if not "!SEAL_BEFORE!"=="!SEAL_AFTER!" echo WARNING: SEAL file presence changed. Check desk\ now.
echo ROLLED BACK to !RB!. The repo is unchanged; fix forward with a normal reviewed change.
goto :end

rem ==========================================================================
rem  Subroutines
rem ==========================================================================
:preflight
if not exist "%REPO%\.git" (echo REFUSED: %REPO% is not a git checkout. & exit /b 1)
if not exist "!MQL5_DIR!\Experts" (echo REFUSED: terminal folder not found: !MQL5_DIR! & exit /b 1)
if not exist "%METAEDITOR%" (echo REFUSED: MetaEditor not found: %METAEDITOR% & exit /b 1)
if not exist "%GUARD%" (echo REFUSED: config guard not found: %GUARD% & exit /b 1)
if not exist "%TESTS%" (echo REFUSED: test runner not found: %TESTS% & exit /b 1)
if not exist "!LIVE_EA!" (echo REFUSED: live receiver.mq5 not found. Install the Receiver by hand first. & exit /b 1)
exit /b 0

:confirm_target
rem Marker <TERMINAL_ID>.LIVE_OK (mode=REAL) or .DEMO_OK (mode=DEMO) with login=<n>.
set "TARGET_KIND="
set "MARKER="
if exist "%MARKER_DIR%\!TERMINAL_ID!.LIVE_OK" (set "TARGET_KIND=LIVE" & set "WANT_MODE=REAL" & set "MARKER=%MARKER_DIR%\!TERMINAL_ID!.LIVE_OK")
if not defined TARGET_KIND if exist "%MARKER_DIR%\!TERMINAL_ID!.DEMO_OK" (set "TARGET_KIND=DEMO" & set "WANT_MODE=DEMO" & set "MARKER=%MARKER_DIR%\!TERMINAL_ID!.DEMO_OK")
echo.
echo  Target terminal data folder: !TERMINAL_ROOT!
echo  Target MQL5 folder:          !MQL5_DIR!
echo  MetaEditor:                  %METAEDITOR%
echo  Mode:                        %MODE%
if not defined TARGET_KIND (
  echo REFUSED: no approval marker for this terminal.
  echo  Only Lesetja creates it, by hand, with two lines login=^<number^> and mode=DEMO or mode=REAL:
  echo    %MARKER_DIR%\!TERMINAL_ID!.DEMO_OK   ^(demo account, mode=DEMO^)
  echo    %MARKER_DIR%\!TERMINAL_ID!.LIVE_OK   ^(real account, mode=REAL; only after the input test passed or JSON config landed^)
  exit /b 1
)
set "M_LOGIN="
set "M_MODE="
set "M_SERVER="
for /f "usebackq tokens=1,* delims==" %%a in ("!MARKER!") do (
  if /i "%%a"=="login" set "M_LOGIN=%%b"
  if /i "%%a"=="mode" set "M_MODE=%%b"
  if /i "%%a"=="server" set "M_SERVER=%%b"
)
if not defined M_LOGIN (echo REFUSED: the marker file has no login= line ^(ASCII, no BOM^). & exit /b 1)
if /i not "!M_MODE!"=="!WANT_MODE!" (echo REFUSED: a !TARGET_KIND! marker must say mode=!WANT_MODE!. & exit /b 1)
echo  Approved as:                 !TARGET_KIND! ^(marker file found; expected account not shown^)
set "ACC_FILE=!MQL5_DIR!\Files\RX_account.txt"
set "ACC_SRC=typed"
if exist "!ACC_FILE!" (
  call :check_account_file || exit /b 1
  set "ACC_SRC=file"
  echo Account confirmed from MQL5\Files\RX_account.txt ^(written by the running EA^).
  echo  It binds the account only; it does not prove the Receiver is trading.
  exit /b 0
)
if "!TARGET_KIND!"=="LIVE" (
  echo REFUSED: LIVE needs MQL5\Files\RX_account.txt written by the running EA ^(not implemented yet^).
  exit /b 1
)
echo.
echo WARNING: no MQL5\Files\RX_account.txt from the EA. Falling back to a typed check ^(DEMO only^).
echo  In this MT5 terminal, read the account number and whether it is a demo or real account.
set "T_LOGIN="
set /p "T_LOGIN=Type the account login MT5 shows: "
set "T_MODE="
set /p "T_MODE=Type the trade mode MT5 shows (DEMO or REAL): "
if not "!T_LOGIN!"=="!M_LOGIN!" (echo REFUSED: the login does not match the marker. Nothing changed. & exit /b 1)
if /i not "!T_MODE!"=="!M_MODE!" (echo REFUSED: the trade mode does not match the marker. Nothing changed. & exit /b 1)
echo Account confirmed for this terminal.
exit /b 0

:check_account_file
rem RX_account.txt: login=, mode=DEMO|REAL, server=, ts=<unix seconds, UTC>. Values never printed.
set "A_LOGIN="
set "A_MODE="
set "A_SERVER="
set "A_TS="
for /f "tokens=1,* delims==" %%a in ('type "%ACC_FILE%"') do (
  if /i "%%a"=="login" set "A_LOGIN=%%b"
  if /i "%%a"=="mode" set "A_MODE=%%b"
  if /i "%%a"=="server" set "A_SERVER=%%b"
  if /i "%%a"=="ts" set "A_TS=%%b"
)
if not defined A_LOGIN (echo REFUSED: RX_account.txt has no login= line. & exit /b 1)
if not defined A_MODE (echo REFUSED: RX_account.txt has no mode= line. & exit /b 1)
if not defined A_TS (echo REFUSED: RX_account.txt has no ts= line. & exit /b 1)
if not "!A_LOGIN!"=="!M_LOGIN!" (echo REFUSED: the EA's account login does not match the marker. Nothing changed. & exit /b 1)
if /i not "!A_MODE!"=="!M_MODE!" (echo REFUSED: the EA's trade mode does not match the marker. Nothing changed. & exit /b 1)
if defined M_SERVER if /i not "!A_SERVER!"=="!M_SERVER!" (echo REFUSED: the EA's server does not match the marker. Nothing changed. & exit /b 1)
set "IF_TS=!A_TS!"
set "AGE="
for /f %%x in ('powershell -NoProfile -Command "$t=[long]0; if([long]::TryParse($env:IF_TS,[ref]$t)){[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()-$t}else{-99999}"') do set "AGE=%%x"
if not defined AGE (echo REFUSED: could not read the clock. & exit /b 1)
if !AGE! LSS -60 (echo REFUSED: RX_account.txt has an invalid or future ts=. & exit /b 1)
if !AGE! GTR 600 (echo REFUSED: RX_account.txt is older than 10 minutes. Is the EA running on this terminal? & exit /b 1)
exit /b 0

:check_backup_name
rem %1 must look like YYYYMMDD-HHMM: digits, one dash, digits.
set "IF_CHK=%~1"
if "!IF_CHK:~0,1!"=="-" (echo REFUSED: a backup name looks like YYYYMMDD-HHMM. & exit /b 1)
if "!IF_CHK:~-1!"=="-" (echo REFUSED: a backup name looks like YYYYMMDD-HHMM. & exit /b 1)
for %%c in (0 1 2 3 4 5 6 7 8 9) do set "IF_CHK=!IF_CHK:%%c=!"
if not "!IF_CHK!"=="-" (echo REFUSED: a backup name looks like YYYYMMDD-HHMM. & exit /b 1)
exit /b 0

:recheck_account
rem N2: the account is checked again just before files change.
if exist "!ACC_FILE!" (
  call :check_account_file || exit /b 1
  echo Account re-checked from RX_account.txt just before the change.
  exit /b 0
)
if "!ACC_SRC!"=="file" (echo REFUSED: MQL5\Files\RX_account.txt disappeared since the start. & exit /b 1)
if "!TARGET_KIND!"=="LIVE" (echo REFUSED: LIVE needs MQL5\Files\RX_account.txt. & exit /b 1)
echo NOTE: still no RX_account.txt; the typed DEMO check from the start applies.
exit /b 0

:check_tmp_path
rem The running copy must be %TEMP%\if-deploy-<digits>\deploy.cmd (full-path compare).
powershell -NoProfile -Command "$p=[IO.Path]::GetFullPath($env:IF_DEPLOY_TMP); $t=[IO.Path]::GetFullPath($env:TEMP).TrimEnd('\')+'\if-deploy-'; if($p.StartsWith($t,[StringComparison]::OrdinalIgnoreCase) -and ($p.Substring($t.Length) -match '^[0-9]+\\deploy\.cmd$')){exit 0}else{exit 1}"
exit /b %errorlevel%

:drift_in_copy_list
rem N1: DRIFT-OK only if every DRIFT path is a file this deploy overwrites (guard --porcelain).
set "IF_DBAD="
set "IF_DN=0"
set "IF_DRES="
for /f "tokens=1,2" %%a in ('%PY% "%GUARD%" --root "%REPO%" --lock "%LOCK%" --live "!MQL5_DIR!" --live-roots Receiver --porcelain') do (
  if "%%a"=="DRIFT" (
    set /a IF_DN+=1
    call :in_copy_list "%%b" || (echo   DRIFT outside the deploy copy list: %%b & set "IF_DBAD=1")
  ) else if "%%a"=="RESULT" (
    set "IF_DRES=%%b"
  ) else (
    echo   unexpected guard line: %%a %%b
    set "IF_DBAD=1"
  )
)
if not "!IF_DRES!"=="LIVE-DRIFT" (echo REFUSED: the machine-readable guard run did not report LIVE-DRIFT. Nothing deployed. & exit /b 1)
if !IF_DN! EQU 0 (echo REFUSED: the guard reported drift but listed no DRIFT path. Nothing deployed. & exit /b 1)
if defined IF_DBAD (echo REFUSED: DRIFT-OK needs every drifted file to be one this deploy overwrites. Nothing deployed. & exit /b 1)
exit /b 0

:in_copy_list
if /i "%~1"=="mql5/Experts/Receiver.mq5" exit /b 0
for %%f in (%LOGIC_INCLUDES%) do if /i "%~1"=="mql5/Include/%%f" exit /b 0
exit /b 1

:verify_guard
git ls-files --error-unmatch "tools/config_guard.py" >nul 2>&1 || (echo REFUSED: tools\config_guard.py is not committed. & exit /b 1)
git ls-files --error-unmatch "config.lock.json" >nul 2>&1 || (echo REFUSED: config.lock.json is not committed. & exit /b 1)
if not exist "%PIN_FILE%" (
  echo REFUSED: no guard pin file. After reviewing the guard, Lesetja creates it himself:
  echo     certutil -hashfile "%GUARD%" SHA256
  echo and saves the 64 hex characters as the only line of %PIN_FILE%
  exit /b 1
)
set "PIN="
for /f "usebackq tokens=1" %%p in ("%PIN_FILE%") do if not defined PIN set "PIN=%%p"
if not defined PIN (echo REFUSED: %PIN_FILE% is empty. & exit /b 1)
if "!PIN:~63,1!"=="" (echo REFUSED: the pin file must hold exactly 64 hex characters ^(ASCII, no BOM^). & exit /b 1)
if not "!PIN:~64!"=="" (echo REFUSED: the pin file must hold exactly 64 hex characters ^(ASCII, no BOM^). & exit /b 1)
if /i not "%GUARD_SHA256%"=="PASTE-REVIEWED-SHA256-HERE" if /i not "%GUARD_SHA256%"=="!PIN!" (
  echo REFUSED: GUARD_SHA256 in deploy.cmd differs from %PIN_FILE%.
  echo  Review the guard change, then write the pin file yourself ^(ASCII, no BOM^). Nothing deployed.
  exit /b 1
)
set "IF_FILE=%GUARD%"
set "GH="
for /f %%h in ('powershell -NoProfile -Command "(Get-FileHash -LiteralPath $env:IF_FILE -Algorithm SHA256).Hash.ToLower()"') do set "GH=%%h"
if /i not "!GH!"=="!PIN!" (
  echo REFUSED: tools\config_guard.py does not match the pinned SHA-256. Review the guard change.
  exit /b 1
)
echo Guard hash matches the pin.
exit /b 0

:check_includes
rem The Receiver's include chain (from the guard) must equal LOGIC_INCLUDES (config includes excluded).
%PY% "%GUARD%" --root "%REPO%" --print-chain --live-roots Receiver >nul 2>&1 || (echo REFUSED: the guard could not list the Receiver include chain. & exit /b 1)
set "IF_HAVE="
for /f "tokens=1,2" %%a in ('%PY% "%GUARD%" --root "%REPO%" --print-chain --live-roots Receiver') do if "%%a"=="include" set "IF_HAVE=!IF_HAVE! %%b"
set "IF_LOGIC=%LOGIC_INCLUDES%"
set "IF_CONFIG=%CONFIG_INCLUDES%"
powershell -NoProfile -Command "$want=@($env:IF_LOGIC -split ' ' | Where-Object { $_ }); $cfg=@($env:IF_CONFIG -split ' ' | Where-Object { $_ }); $have=@($env:IF_HAVE -split ' ' | Where-Object { $_ -and $cfg -notcontains $_ } | Sort-Object -Unique); $bad=0; foreach($h in $have){ if($want -notcontains $h){ Write-Output ('NOT IN LOGIC_INCLUDES: ' + $h); $bad=1 } }; foreach($w in $want){ if($have -notcontains $w){ Write-Output ('IN LOGIC_INCLUDES BUT NOT IN THE CHAIN: ' + $w); $bad=1 } }; exit $bad"
if errorlevel 1 (echo REFUSED: the Receiver include chain and LOGIC_INCLUDES differ. Update the allowlist after review. & exit /b 1)
echo Include allowlist matches the Receiver include chain.
exit /b 0

:confirm_flat
echo.
echo  The Receiver keeps silent ^(virtual^) stops. While it reloads, positions rely only on the broker backstop.
echo  This script cannot see positions: no read-only way to query the terminal is installed.
echo  In MT5, check the Trade tab: no open positions or pending orders for the Receiver.
set "ANS="
set /p "ANS=Type FLAT (capitals) to confirm the Receiver is flat: "
if "!ANS!"=="FLAT" exit /b 0
echo Not confirmed. Nothing changed.
exit /b 1

:inputs_readback
rem Compares with the sha of THIS terminal's newest successful deploy (deployed.txt).
echo.
echo ---------- Settings read-back ----------
set "LASTDIR="
set "LASTSHA="
for /f "delims=" %%d in ('dir /b /ad /o-n "!BK_DIR!" 2^>nul') do (
  if not defined LASTDIR if exist "!BK_DIR!\%%d\deployed.txt" set "LASTDIR=%%d"
)
if defined LASTDIR (
  for /f "usebackq tokens=1,* delims==" %%a in ("!BK_DIR!\!LASTDIR!\deployed.txt") do if /i "%%a"=="sha" set "LASTSHA=%%b"
)
set "RBTS="
if exist "!BK_DIR!\last-rollback.txt" for /f "usebackq delims=" %%t in ("!BK_DIR!\last-rollback.txt") do set "RBTS=%%t"
if defined LASTSHA (
  echo Inputs changed since this terminal's last deploy ^(!LASTSHA!, !LASTDIR!^), names only:
  %PY% "%GUARD%" --root "%REPO%" --changed-inputs "!LASTSHA!"
  if errorlevel 1 echo WARNING: could not compare with !LASTSHA!. Check EVERY input.
  if defined RBTS if "!RBTS!" GTR "!LASTDIR!" echo WARNING: a rollback ran after that deploy. Check EVERY input.
) else (
  echo No earlier deploy recorded for this terminal. Check EVERY input.
)
echo.
echo  UNVERIFIED: that a recompile keeps the values already set on the chart.
echo  New inputs take the SOURCE default; a renamed input loses its chart value.
echo  Open the Receiver chart's Inputs tab now. Note the current values ^(or save your
echo  own preset^) so you can compare them after the reload. This script never reads them.
set "ANS="
set /p "ANS=Type INPUTS-CHECKED once you have checked the chart's Inputs tab: "
if "!ANS!"=="INPUTS-CHECKED" exit /b 0
echo Not confirmed. Nothing changed.
exit /b 1

:timestamp
set "TS="
for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmm"') do set "TS=%%t"
if not defined TS (echo Could not read the clock. & exit /b 1)
exit /b 0

:copy_checked
rem %1 source, %2 destination. Copies and byte-compares; non-zero on any failure.
copy /y "%~1" "%~2" >nul 2>&1 || (echo   copy failed: %~nx1 & exit /b 1)
fc /b "%~1" "%~2" >nul 2>&1 || (echo   copy not identical: %~nx1 & exit /b 1)
exit /b 0

:backup
rem %1 = destination. manifest.txt: terminal=, mql5_dir=, sha=, file=<rel to MQL5> lines, COMPLETE.
set "DST=%~1"
mkdir "%DST%\Experts" "%DST%\Include" 2>nul
if not exist "%DST%\Experts" exit /b 1
>"%DST%\manifest.txt" echo terminal=!TERMINAL_ID!
>>"%DST%\manifest.txt" echo mql5_dir=!MQL5_DIR!
>>"%DST%\manifest.txt" echo sha=!SHA!
>>"%DST%\manifest.txt" echo drift=!DRIFTED!
call :copy_checked "!LIVE_EA!" "%DST%\Experts\receiver.mq5" || exit /b 1
>>"%DST%\manifest.txt" echo file=Experts\receiver.mq5
if exist "!LIVE_EX5!" (
  call :copy_checked "!LIVE_EX5!" "%DST%\Experts\receiver.ex5" || exit /b 1
  >>"%DST%\manifest.txt" echo file=Experts\receiver.ex5
) else (
  echo NOTE: no live receiver.ex5 to back up.
)
for %%f in (%LOGIC_INCLUDES%) do (
  if exist "!MQL5_DIR!\Include\%%f" (
    call :copy_checked "!MQL5_DIR!\Include\%%f" "%DST%\Include\%%f" || exit /b 1
    >>"%DST%\manifest.txt" echo file=Include\%%f
  ) else (
    >>"%DST%\absent.txt" echo Include\%%f
  )
)
>>"%DST%\manifest.txt" echo COMPLETE
>"%DST%\deploy-info.txt" echo backup_of_terminal=!TERMINAL_ID!
>>"%DST%\deploy-info.txt" echo created=%DATE% %TIME%
call :check_backup "%DST%" || exit /b 1
echo Backup saved and verified: %DST%
exit /b 0

:check_backup
rem %1 = backup folder. Refuses unless it belongs to THIS terminal, is COMPLETE, and every file exists.
set "CB=%~1"
if not exist "%CB%\manifest.txt" (echo REFUSED: backup %~nx1 has no manifest ^(incomplete or made by an older script^). & exit /b 1)
findstr /x /c:"COMPLETE" "%CB%\manifest.txt" >nul || (echo REFUSED: backup %~nx1 is incomplete. & exit /b 1)
set "CBTERM="
set "CBEA="
set "CBMISS="
for /f "usebackq tokens=1,* delims==" %%a in ("%CB%\manifest.txt") do (
  if /i "%%a"=="terminal" set "CBTERM=%%b"
  if /i "%%a"=="file" (
    if /i "%%b"=="Experts\receiver.mq5" set "CBEA=1"
    if not exist "%CB%\%%b" if not exist "%CB%\quarantine\%%b" (echo   missing in backup: %%b & set "CBMISS=1")
  )
)
if /i not "!CBTERM!"=="!TERMINAL_ID!" (echo REFUSED: backup %~nx1 was made for another terminal. & exit /b 1)
if not defined CBEA (echo REFUSED: backup %~nx1 has no receiver.mq5. & exit /b 1)
if defined CBMISS (echo REFUSED: backup %~nx1 is incomplete. & exit /b 1)
exit /b 0

:restore_from
rem %1 = backup folder. 0 = restored (compiled, or for drift 1/unknown the old .ex5 only);
rem 1 = compile failed; 2 = refused; 3 = copy/delete failed.
set "SRC=%~1"
call :check_backup "%SRC%" || exit /b 2
rem No drift= line (v4.1 or older backup) means unknown.
set "RDRIFT=unknown"
for /f "usebackq tokens=1,* delims==" %%a in ("%SRC%\manifest.txt") do if /i "%%a"=="drift" set "RDRIFT=%%b"
if not "!RDRIFT!"=="0" (
  echo.
  echo This backup's drift is "!RDRIFT!": its sources may not be the reviewed ones.
  echo Default: put ONLY the backed-up receiver.ex5 back. The terminal's current sources stay;
  echo the backup's sources are moved to its quarantine folder. Nothing is recompiled.
  set "ANS="
  set /p "ANS=Press Enter for that, or type RECOMPILE-DRIFTED to restore and recompile the backup's sources: "
  if not "!ANS!"=="RECOMPILE-DRIFTED" goto :restore_ex5_only
  echo Restoring and recompiling the backup's sources as requested.
)
set "RFAIL="
for /f "usebackq tokens=1,* delims==" %%a in ("%SRC%\manifest.txt") do (
  if /i "%%a"=="file" if /i not "%%~xb"==".ex5" (
    call :bk_path "%%b"
    call :copy_checked "!BKF!" "!MQL5_DIR!\%%b" || set "RFAIL=1"
  )
)
if exist "%SRC%\absent.txt" (
  rem Files that did not exist before the deploy are removed; only allowlisted names.
  for /f "usebackq delims=" %%l in ("%SRC%\absent.txt") do (
    for %%f in (%LOGIC_INCLUDES%) do (
      if /i "%%l"=="Include\%%f" if exist "!MQL5_DIR!\Include\%%f" (
        del /q "!MQL5_DIR!\Include\%%f" || set "RFAIL=1"
        if exist "!MQL5_DIR!\Include\%%f" set "RFAIL=1"
      )
    )
  )
)
if defined RFAIL (
  echo RESTORE INCOMPLETE: at least one copy or delete failed. The terminal may hold mixed files.
  echo Copy the files from %SRC% into !MQL5_DIR! by hand, then compile in MetaEditor.
  exit /b 3
)
call :compile
if not errorlevel 1 exit /b 0
if exist "%SRC%\Experts\receiver.ex5" (
  call :copy_checked "%SRC%\Experts\receiver.ex5" "!LIVE_EX5!" && (
    echo Recompiling the backup failed. The backed-up receiver.ex5 was put back.
    echo Remove and re-attach the Receiver on its chart ^(or restart MT5^) so it loads.
  )
)
exit /b 1

:restore_ex5_only
rem Nothing in the terminal changes except receiver.ex5. Checked before anything is touched.
if not exist "%SRC%\Experts\receiver.ex5" (
  echo REFUSED: this backup has no receiver.ex5, so an .ex5-only restore is impossible. Nothing changed.
  echo Run it again and type RECOMPILE-DRIFTED only after reviewing the backup's sources.
  exit /b 2
)
call :copy_checked "%SRC%\Experts\receiver.ex5" "!LIVE_EX5!" || (echo RESTORE INCOMPLETE: could not put receiver.ex5 back. Copy it by hand from %SRC%\Experts. & exit /b 3)
set "QFAIL="
mkdir "%SRC%\quarantine\Experts" "%SRC%\quarantine\Include" 2>nul
for /f "usebackq tokens=1,* delims==" %%a in ("%SRC%\manifest.txt") do (
  if /i "%%a"=="file" if /i not "%%~xb"==".ex5" if exist "%SRC%\%%b" (
    move /y "%SRC%\%%b" "%SRC%\quarantine\%%b" >nul 2>&1 || set "QFAIL=1"
  )
)
findstr /x /c:"quarantined=1" "%SRC%\manifest.txt" >nul 2>&1 || (>>"%SRC%\manifest.txt" echo quarantined=1)
echo.
echo The backed-up receiver.ex5 was put back WITHOUT recompiling.
echo NOTE: the terminal's .mq5/.mqh files were NOT changed. They are the current reviewed sources,
echo  so the running receiver.ex5 is an OLDER build than the sources next to it. The next normal
echo  deploy recompiles from reviewed sources. Do not press Compile in MetaEditor before that
echo  unless you mean to replace this old build.
echo The backup's drift-"!RDRIFT!" sources are in %SRC%\quarantine\ ^(review them before any reuse^).
if defined QFAIL echo WARNING: some backup sources could not be moved to quarantine; they are still in %SRC%.
echo Remove and re-attach the Receiver on its chart ^(or restart MT5^) so it loads.
exit /b 0

:bk_path
rem Sets BKF to the backup copy of %1: the backup folder, or its quarantine\ if moved there.
if exist "%SRC%\%~1" (set "BKF=%SRC%\%~1") else (set "BKF=%SRC%\quarantine\%~1")
exit /b 0

:probe_drift
rem Sets PROBE to 0, 1 or unknown from a pinned guard run (--live --porcelain); output hidden.
rem It compares the terminal with the CURRENT repo lock, which may be newer than the last deploy.
set "PROBE=unknown"
pushd "%REPO%" 2>nul || exit /b 0
call :verify_guard >nul 2>&1
if errorlevel 1 (popd & exit /b 0)
%PY% "%GUARD%" --root "%REPO%" --lock "%LOCK%" --live "!MQL5_DIR!" --live-roots Receiver --porcelain >nul 2>&1
set "PRC=!errorlevel!"
popd
if "!PRC!"=="0" set "PROBE=0"
if "!PRC!"=="3" set "PROBE=1"
exit /b 0
:compile
rem Paths go to PowerShell through environment variables (no quoting problems).
rem TO VERIFY ON DEMO: the log is UTF-16 and its last line reads "Result: 0 errors, N warnings".
echo Compiling receiver.mq5 with MetaEditor...
set "IF_EX5=!LIVE_EX5!"
set "IF_LOG=!LIVE_LOG!"
if exist "!LIVE_LOG!" del /q "!LIVE_LOG!"
set "T0="
for /f %%x in ('powershell -NoProfile -Command "if(Test-Path -LiteralPath $env:IF_EX5){(Get-Item -LiteralPath $env:IF_EX5).LastWriteTimeUtc.Ticks}else{0}"') do set "T0=%%x"
start "" /wait "%METAEDITOR%" /compile:"!LIVE_EA!" /log
powershell -NoProfile -Command "$l=$env:IF_LOG; if(-not (Test-Path -LiteralPath $l)){Write-Output 'No compile log was written.'; exit 2}; $r = Select-String -LiteralPath $l -Pattern 'Result:' | Select-Object -Last 1; if($r){Write-Output $r.Line.Trim()}; if($r -and $r.Line -match 'Result:\s*0\s+error'){exit 0}else{exit 1}"
if errorlevel 1 (echo COMPILE FAILED ^(see !LIVE_LOG!^). & exit /b 1)
set "T1="
for /f %%x in ('powershell -NoProfile -Command "if(Test-Path -LiteralPath $env:IF_EX5){(Get-Item -LiteralPath $env:IF_EX5).LastWriteTimeUtc.Ticks}else{0}"') do set "T1=%%x"
if "!T1!"=="0" (echo COMPILE FAILED: no receiver.ex5 was produced. & exit /b 1)
if "!T1!"=="!T0!" (echo COMPILE FAILED: receiver.ex5 was not updated. & exit /b 1)
echo Compile OK: 0 errors.
exit /b 0

:seal_snapshot
rem %1 = BEFORE or AFTER. Records presence only; never opens the files.
set "S="
if exist "%SEAL_ENV%" (set "S=!S!E1") else (set "S=!S!E0")
if exist "%SEAL_TASK%" (set "S=!S!T1") else (set "S=!S!T0")
set "SEAL_%~1=!S!"
exit /b 0

:fail
echo.
echo Finished with errors.
pause
endlocal
exit /b 1

:end
echo.
pause
endlocal
exit /b 0
