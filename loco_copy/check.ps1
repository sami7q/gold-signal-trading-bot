# Windows equivalent of `make check`: ruff + mypy + tests (demo contract tests excluded).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = ".\.venv\Scripts\python.exe"
& $py -m ruff check .;  if ($LASTEXITCODE) { exit $LASTEXITCODE }
& $py -m mypy;          if ($LASTEXITCODE) { exit $LASTEXITCODE }
& $py -m pytest -q -m "not demo"; exit $LASTEXITCODE
