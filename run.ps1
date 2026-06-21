# Start the dev server with auto-reload (Windows / PowerShell).
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
& .\.venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port 8000 --reload
