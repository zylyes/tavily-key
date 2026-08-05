# Tavily Key Pool — Windows 本地版开机自启安装脚本
# 用法: 以管理员身份运行 PowerShell，然后执行
#   .\deploy\windows-local\install_service.ps1 [-Uninstall]
#
# 使用「任务计划程序」实现开机自启，无需安装第三方服务工具。
# 如需注册为真正的 Windows 服务，可改用 NSSM（见 README）。

param(
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$TaskName = "TavilyDashboard"
$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

# 定位 Python
$Py = ""
if (Test-Path (Join-Path $ProjectDir ".venv\Scripts\python.exe")) {
    $Py = Join-Path $ProjectDir ".venv\Scripts\python.exe"
} else {
    $Py = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $Py) { Write-Host "未找到 Python，请先安装并加入 PATH。"; exit 1 }
}

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "已移除开机自启任务: $TaskName"
    exit 0
}

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "请以管理员身份运行 PowerShell 再执行本脚本。"
    exit 1
}

$Action  = New-ScheduledTaskAction -Execute $Py -Argument "app\dashboard.py" -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "Tavily API Key Pool Dashboard (local)" -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Write-Host "已安装开机自启并启动: $TaskName"
Write-Host "  项目目录: $ProjectDir"
Write-Host "  访问地址: 见设置页或 http://127.0.0.1:8000"
Write-Host "  卸载: 以管理员运行 .\deploy\windows-local\install_service.ps1 -Uninstall"
