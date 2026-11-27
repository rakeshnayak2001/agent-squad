@echo off
REM Use your Google AI Pro plan for the Gemini worker, through Google's Antigravity CLI (agy).
REM One Pro account at a time. When its quota runs out, tasks fall back to the next worker (e.g. local).
cd /d "%~dp0"
python -m pip install -e ".[mcp]" || goto :err
where agy >nul 2>nul || powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://antigravity.google/cli/install.ps1 | iex" || goto :err
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
agy --version || goto :err
python -m agent_squad init
python -m agent_squad gemini use agy
echo.
echo Sign in: a browser opens. Use your Google AI Pro account. When agy shows its prompt, type /quit.
python -m agent_squad gemini login
python -m agent_squad gemini models
python -m agent_squad test gemini "Say hi in five words"
pause
exit /b 0
:err
echo Setup failed. See the message above.
pause
exit /b 1
