<#
.SYNOPSIS
启动本地 llama.cpp 模型服务（Qwen3-4B-Instruct-2507），供 Agent 通过 OpenAI 兼容接口调用。

.EXAMPLE
pwsh -File scripts/serve_llama.ps1

.EXAMPLE
pwsh -File scripts/serve_llama.ps1 -Port 8081 -Ctx 16384
#>
[CmdletBinding()]
param(
    [string]$Model = 'D:\llms\Qwen3-4B-Instruct-2507-GGUF\Qwen3-4B-Instruct-2507-Q4_K_M.gguf',
    [int]$Port = 8080,
    [int]$Ctx = 8192,
    [string[]]$ExtraArgs = @()
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $Model)) {
    throw "模型文件不存在：$Model（可用 -Model 指定其他 GGUF 路径）"
}

# 优先用 PATH 里的 llama-server；winget 安装后未新开终端时 PATH 尚未刷新，退回到 winGet 包目录。
$server = (Get-Command llama-server -ErrorAction SilentlyContinue).Source
if (-not $server) {
    $server = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Directory -Filter '*llamacpp*' -ErrorAction SilentlyContinue |
        ForEach-Object { Join-Path $_.FullName 'llama-server.exe' } |
        Where-Object { Test-Path -LiteralPath $_ } |
        Select-Object -First 1
}
if (-not $server) {
    throw '未找到 llama-server，请先执行 winget install llama.cpp，并新开一个终端'
}

$running = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($running) {
    throw "端口 $Port 已被占用，请先停止占用进程或用 -Port 指定其他端口"
}

Write-Host "llama-server : $server"
Write-Host "模型         : $Model"
Write-Host "接口地址     : http://127.0.0.1:$Port/v1/chat/completions"
Write-Host "上下文 / 槽位: $Ctx / 1"
Write-Host '按 Ctrl+C 停止模型服务。'

# --jinja 是工具调用（function calling）的前提；-c 必须显式指定，否则按模型原生 262144
# 上下文分配 KV cache 直接吃爆内存；-np 1 只保留一个槽位，单页面对话足够且更省内存。
& $server -m $Model --jinja -c $Ctx -np 1 --port $Port @ExtraArgs
