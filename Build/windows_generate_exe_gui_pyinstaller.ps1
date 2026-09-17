# 用 PyInstaller 把 GUI 版打成本地部署包，两种模式：
#
#   onefile（默认）：单文件 exe，随拷随用，启动要先解压（约 2~3 秒）
#   onedir        ：一个文件夹（exe + _internal），启动快，适合「拷文件夹到
#                    另一台电脑」的便携部署 —— 整个文件夹拷走即可，目标机器
#                    不需要装 Python 和任何依赖。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File Build\windows_generate_exe_gui_pyinstaller.ps1            # onefile
#   powershell -ExecutionPolicy Bypass -File Build\windows_generate_exe_gui_pyinstaller.ps1 -Mode onedir
#
# 产物：
#   onefile -> Build\Output\onefile\shmtu_auth_gui.exe
#   onedir  -> Build\Output\onedir\shmtu_auth_gui\（整个文件夹就是部署包）
#
# 说明：
#   - ddddocr 的 onnx 模型随包数据一起打进包（--collect-all）。
#   - scipy 在源码中无人 import，显式排除以减小体积。
#   - 运行时数据（data/logs/config）自动生成在 exe 同目录，拷贝时无需预置。

param(
    [ValidateSet("onefile", "onedir")]
    [string]$Mode = "onefile"
)

$ErrorActionPreference = "Stop"

# 定位仓库根（本脚本在 Build\ 下，往上一级即是）
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$python = Join-Path $repoRoot ".venv\Scripts\pyinstaller.exe"
if (-not (Test-Path $python)) {
    $python = (Get-Command pyinstaller -ErrorAction SilentlyContinue).Path
}
if (-not $python) {
    Write-Host "pyinstaller not found. Install it first: pip install pyinstaller"
    exit 1
}

$icon = Join-Path $repoRoot "Assets\Icon\icons\Icon.ico"

Write-Host "Repo root  : $repoRoot"
Write-Host "PyInstaller: $python"
Write-Host "Mode       : $Mode"
Write-Host "Building the executable..."

& $python `
    --noconfirm --clean `
    --$Mode --windowed `
    --name shmtu_auth_gui `
    --paths src `
    --icon "$icon" `
    --collect-all ddddocr `
    --exclude-module scipy `
    --exclude-module tkinter `
    --distpath "Build/Output/$Mode" `
    --workpath Build/Output/work `
    --specpath Build/Output `
    src/shmtu_auth/main_gui.py

if ($LASTEXITCODE -ne 0) {
    Write-Host "Build FAILED!"
    exit 1
}

Write-Host "Executable is located at $repoRoot\Build\Output\$Mode\shmtu_auth_gui"
if ($Mode -eq "onedir") {
    Write-Host "把 Build\Output\onedir\shmtu_auth_gui 整个文件夹拷到目标电脑即可运行。"
}
Write-Host "Build Completed!!!"
