@echo off
REM agent-squad setup on Windows: installs the package and saves your Gemini API key(s).
cd /d "%~dp0"
python -m pip install -e ".[mcp]" || goto :err
python -m agent_squad init
echo.
echo Gemini key 1: create a key at https://aistudio.google.com/apikey (sign in with your Google account), then paste it.
python -m agent_squad gemini set-key acc1
echo.
echo Gemini key 2 (optional, from your second Google account; just press Enter to skip):
python -m agent_squad gemini set-key acc2
python -m agent_squad test gemini "Say hi in five words"
python -m agent_squad accounts
pause
exit /b 0
:err
echo Setup failed. See the message above.
pause
exit /b 1
