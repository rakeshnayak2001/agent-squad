@echo off
REM agent-squad setup on Windows: installs the package, Gemini CLI, and signs in both Google AI Pro accounts.
cd /d "%~dp0"
python -m pip install -e ".[mcp]" || goto :err
where gemini >nul 2>nul || call npm install -g @google/gemini-cli || goto :err
python -m agent_squad init
echo.
echo Sign in account 1: choose "Sign in with Google", use your FIRST Google AI Pro account, then type /quit
python -m agent_squad gemini login acc1
echo.
echo Sign in account 2: same, with your SECOND Google AI Pro account
python -m agent_squad gemini login acc2
python -m agent_squad test gemini "Say hi in five words"
python -m agent_squad accounts
pause
exit /b 0
:err
echo Setup failed. See the message above.
pause
exit /b 1
