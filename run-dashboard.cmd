@echo off
setlocal
cd /d "%~dp0"
where node >nul 2>nul
if errorlevel 1 (
  echo Node.js is required to build the React dashboard.
  pause
  exit /b 1
)
if not exist "frontend\node_modules\vite\package.json" (
  echo Installing project-local frontend dependencies from the lockfile...
  pushd frontend
  call npm ci --ignore-scripts
  if errorlevel 1 (
    popd
    echo Frontend dependency installation failed. No server started.
    pause
    exit /b 1
  )
  popd
)
pushd frontend
call npm run build
if errorlevel 1 (
  popd
  echo React build failed. No server started.
  pause
  exit /b 1
)
popd
echo.
echo Open http://127.0.0.1:8878 in your regular browser.
echo Keep this window open. Ctrl+C stops this dashboard.
echo If the address is already running, use that window rather than a second copy.
python -E -B -m ui.dashboard --port 8878
if errorlevel 1 pause
