# pokechamp one-shot setup (Windows / PowerShell).
#   .\scripts\setup.ps1              # venv + install + tests
#   .\scripts\setup.ps1 -WithRL      # also PyTorch (CPU) for the 4b trainer
#   .\scripts\setup.ps1 -Model qwen3:14b   # also pull the Ollama model + run the routing eval

param(
    [switch]$WithRL,
    [string]$Model = ""
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path ".venv")) {
    Write-Host "creating venv..." -ForegroundColor Cyan
    python -m venv .venv
}
$py = ".\.venv\Scripts\python.exe"

Write-Host "installing pokechamp [dev,llm]..." -ForegroundColor Cyan
& $py -m pip install --quiet -e ".[dev,llm]"
if ($WithRL) {
    Write-Host "installing [rl] (PyTorch CPU)..." -ForegroundColor Cyan
    & $py -m pip install --quiet numpy torch --index-url https://download.pytorch.org/whl/cpu
}

Write-Host "running tests..." -ForegroundColor Cyan
& $py -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "tests failed" }

if ($Model) {
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        throw "ollama not found - install from https://ollama.com first"
    }
    Write-Host "pulling $Model..." -ForegroundColor Cyan
    ollama pull $Model
    Write-Host "routing eval (measures tool-choice accuracy)..." -ForegroundColor Cyan
    & $py -m pokechamp.llm.routing --model $Model
}

Write-Host "`ndone. next steps:" -ForegroundColor Green
Write-Host "  .\.venv\Scripts\pokechamp-chat --model <model>   # terminal assistant"
Write-Host "  .\.venv\Scripts\pokechamp-mcp                    # raw MCP server (stdio)"
