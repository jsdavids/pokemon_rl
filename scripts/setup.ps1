# pokechamp one-shot setup (Windows / PowerShell).
#   .\scripts\setup.ps1              # venv + install + tests
#   .\scripts\setup.ps1 -WithRL      # also PyTorch (CPU) for the 4b trainer
#   .\scripts\setup.ps1 -Model qwen3:14b   # also pull the Ollama model + run the routing eval
#
# Bake-off tags (16GB GPU): qwen3:14b (Q4_K_M) / qwen3:14b-q8_0 (no Q5
# exists on Ollama for qwen3 — eval both to measure the quant tax),
# gpt-oss:20b, qwen3:30b-a3b. Tool calling degrades with quantization
# faster than chat, so pin quant tags explicitly in results.

param(
    [switch]$WithRL,
    [string]$Model = "",
    [int]$ContextLength = 16384
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
    # Blackwell GPUs (RTX 50xx) need a recent Ollama; old builds fall back
    # to CPU silently. The eval preflight verifies actual GPU offload.
    Write-Host "ollama version: $(ollama -v)" -ForegroundColor Cyan

    # Ollama's default context (4096) silently front-truncates the system
    # prompt + 13 tool schemas. Persist a bigger default for the server.
    $current = [Environment]::GetEnvironmentVariable("OLLAMA_CONTEXT_LENGTH", "User")
    if ($current -ne "$ContextLength") {
        [Environment]::SetEnvironmentVariable("OLLAMA_CONTEXT_LENGTH", "$ContextLength", "User")
        Write-Host "set OLLAMA_CONTEXT_LENGTH=$ContextLength (user env var)." -ForegroundColor Yellow
        Write-Host "RESTART the Ollama app/service so it takes effect - the eval preflight will confirm." -ForegroundColor Yellow
    }

    Write-Host "pulling $Model..." -ForegroundColor Cyan
    ollama pull $Model
    Write-Host "routing eval (measures tool-choice accuracy)..." -ForegroundColor Cyan
    & $py -m pokechamp.llm.routing --model $Model --json routing_results.jsonl
}

Write-Host "`ndone. next steps:" -ForegroundColor Green
Write-Host "  .\.venv\Scripts\pokechamp-chat --model <model>   # terminal assistant"
Write-Host "  .\.venv\Scripts\pokechamp-mcp                    # raw MCP server (stdio)"
if ($Model) {
    Write-Host "  bake-off: rerun with each model; results accumulate in routing_results.jsonl"
}
