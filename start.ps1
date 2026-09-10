param([int]$Port = 8765, [string]$PythonPath = '')
$ErrorActionPreference = 'Stop'
$projectDirectory = $PSScriptRoot
if (-not $PythonPath) {
    $candidates = @(
        (Join-Path $projectDirectory '.venv/Scripts/python.exe'),
        (Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe')
    )
    $PythonPath = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $PythonPath) {
        $command = Get-Command python -ErrorAction SilentlyContinue
        if ($command -and $command.Source -notmatch 'WindowsApps') { $PythonPath = $command.Source }
    }
}
if (-not $PythonPath) { throw 'Python não encontrado. Instale Python 3.12 e as dependências do README, ou use -PythonPath.' }
& $PythonPath -X utf8 (Join-Path $projectDirectory 'vigia.py') serve --port $Port
exit $LASTEXITCODE
