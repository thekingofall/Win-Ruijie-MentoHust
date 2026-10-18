param(
    [string]$WorkRoot = (Join-Path ([IO.Path]::GetTempPath()) "XinRuijie-build"),
    [string]$ExecutableName = "新锐捷-0.6.0"
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonPath)) { throw "请先建立项目 .venv 并安装 build 依赖。" }
New-Item -ItemType Directory -Path $WorkRoot -Force | Out-Null
$distPath = Join-Path $projectRoot "dist"
$sourceRoot = Join-Path $projectRoot "mentohust-modern\src"
$assetsPath = Join-Path $sourceRoot "mentohust_modern\assets"
$buildArgs = @("-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
    "--name", $ExecutableName, "--distpath", $distPath, "--workpath", (Join-Path $WorkRoot "work"),
    "--specpath", $WorkRoot, "--paths", $sourceRoot,
    "--icon", (Join-Path $assetsPath "app-icon.ico"),
    "--add-data", ($assetsPath + ";mentohust_modern/assets"),
    "--add-data", ((Join-Path $projectRoot "LICENSE") + ";."),
    "--add-data", ((Join-Path $projectRoot "NOTICE.md") + ";."),
    (Join-Path $projectRoot "mentohust-modern\launcher.py"))
& $pythonPath @buildArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败。" }
Copy-Item -LiteralPath (Join-Path $projectRoot "LICENSE"), (Join-Path $projectRoot "NOTICE.md"), (Join-Path $projectRoot "README.md") -Destination $distPath -Force
$previewDir = Join-Path $distPath "reports"
New-Item -ItemType Directory -Path $previewDir -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot "reports\gui-preview.png") -Destination $previewDir -Force
Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $distPath ($ExecutableName + ".exe"))
