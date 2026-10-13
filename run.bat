@echo off
REM Charlie's Camera Studio - Windows start script. Needs only uv (https://docs.astral.sh/uv/).
cd /d "%~dp0"
REM uv installs to %USERPROFILE%\.local\bin; add it in case PATH was not refreshed.
set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"
where uv >nul 2>nul
if errorlevel 1 (
  echo uv is not installed. Install it in PowerShell with:
  echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  echo then open a new terminal and run run.bat again.
  pause
  exit /b 1
)
echo Installing / checking Python and dependencies (first run takes a minute)...
uv sync --frozen --quiet
REM One-time Claude sign-in for the AI assistant (asks only if you're not signed in yet).
uv run python -m backend.app.claude_login --ensure
uv run python -m backend.app.main --open %*
pause
