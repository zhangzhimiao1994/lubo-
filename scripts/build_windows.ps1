[CmdletBinding()]
param(
    [switch]$SkipInstall,
    [switch]$PrepareOnly,
    [string]$PythonExe
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$env:KIVY_LOG_MODE = "PYTHON"
$env:KIVY_NO_FILELOG = "1"

function Write-BuildPhase {
    param([Parameter(Mandatory = $true)][string]$Message)

    Write-Host "==> $Message"
    if ($env:GITHUB_ACTIONS -eq "true") {
        Write-Host "::notice title=Windows build phase::$Message"
    }
}

function Resolve-PythonCommand {
    param([Parameter(Mandatory = $true)][string]$Candidate)

    if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
        return (Resolve-Path -LiteralPath $Candidate).Path
    }

    $Command = Get-Command -Name $Candidate -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $Command) {
        throw "Python executable was not found: $Candidate"
    }

    return $Command.Source
}

function Get-PythonInfo {
    param([Parameter(Mandatory = $true)][string]$Candidate)

    $Output = @(& $Candidate -c 'import sys; print(sys.version_info.major, sys.version_info.minor, sys.version_info.micro, sep=chr(46))')
    if ($LASTEXITCODE -ne 0 -or $Output.Count -lt 1) {
        throw "Failed to inspect Python executable '$Candidate'."
    }

    if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
        throw "Python executable path is invalid: $Candidate"
    }

    $VersionText = ([string]$Output[-1]).Trim()
    try {
        $Version = [version]::Parse($VersionText)
    }
    catch {
        throw "Python reported an invalid version: $VersionText"
    }

    return [PSCustomObject]@{
        Executable = (Resolve-Path -LiteralPath $Candidate).Path
        Version = $Version
    }
}

function Test-KivyCompatiblePython {
    param([Parameter(Mandatory = $true)][version]$Version)

    return $Version.Major -eq 3 -and $Version.Minor -ge 10 -and $Version.Minor -le 13
}

if ($PythonExe) {
    $PythonInfo = Get-PythonInfo (Resolve-PythonCommand $PythonExe)
    if (-not (Test-KivyCompatiblePython $PythonInfo.Version)) {
        throw "Python 3.10-3.13 is required for Kivy 2.3.1; '$PythonExe' is Python $($PythonInfo.Version)."
    }
}
else {
    $CurrentPythonInfo = $null
    try {
        $CurrentPythonInfo = Get-PythonInfo (Resolve-PythonCommand "python")
    }
    catch {
        Write-Warning "The current python command could not be used: $($_.Exception.Message)"
    }

    if ($CurrentPythonInfo -and (Test-KivyCompatiblePython $CurrentPythonInfo.Version)) {
        $PythonInfo = $CurrentPythonInfo
    }
    else {
        if ($CurrentPythonInfo) {
            Write-Warning "The current python command uses Python $($CurrentPythonInfo.Version), which is not compatible with Kivy 2.3.1."
        }

        $UvCommand = Get-Command -Name "uv" -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if (-not $UvCommand) {
            throw "Python 3.10-3.13 is required. Install a compatible Python or pass -PythonExe with its executable path."
        }

        $UvOutput = @(& $UvCommand.Source python find 3.13)
        if ($LASTEXITCODE -ne 0 -or $UvOutput.Count -eq 0) {
            throw "uv could not find Python 3.13. Install Python 3.10-3.13 or pass -PythonExe with its executable path."
        }

        $UvPython = ([string]$UvOutput[-1]).Trim()
        $PythonInfo = Get-PythonInfo (Resolve-PythonCommand $UvPython)
        if (-not (Test-KivyCompatiblePython $PythonInfo.Version)) {
            throw "uv returned Python $($PythonInfo.Version), but Python 3.10-3.13 is required for Kivy 2.3.1."
        }
    }
}

$BasePython = $PythonInfo.Executable
$CanonicalBasePython = [IO.Path]::GetFullPath($BasePython).ToLowerInvariant()
$FingerprintSource = "$CanonicalBasePython|$($PythonInfo.Version)"
$Sha256 = [Security.Cryptography.SHA256]::Create()
try {
    $BaseFingerprint = ([BitConverter]::ToString(
        $Sha256.ComputeHash([Text.Encoding]::UTF8.GetBytes($FingerprintSource))
    )).Replace("-", "").ToLowerInvariant()
}
finally {
    $Sha256.Dispose()
}
Write-Host "Using base Python $($PythonInfo.Version) at $BasePython"

Push-Location -LiteralPath $RepoRoot
try {
    Write-BuildPhase "Preparing isolated build environment"
    $EntryPoint = "lubo/apps/desktop/main.py"
    $ResourceDirectories = @("config", "i18n", "src/javascript")
    $BuildVenvRoot = Join-Path $RepoRoot ".build-venv"
    $ExpectedBuildVenv = [IO.Path]::GetFullPath((Join-Path $RepoRoot ".build-venv/windows"))
    $BuildVenv = $ExpectedBuildVenv
    $BuildPython = Join-Path $BuildVenv "Scripts/python.exe"
    $FingerprintPath = Join-Path $BuildVenv "base-python.fingerprint"

    if (-not (Test-Path -LiteralPath $EntryPoint -PathType Leaf)) {
        throw "Desktop entry point not found: $EntryPoint"
    }

    foreach ($Directory in $ResourceDirectories) {
        if (-not (Test-Path -LiteralPath $Directory -PathType Container)) {
            throw "Resource directory not found: $Directory"
        }
    }

    $VenvIsReusable = $false
    if (
        (Test-Path -LiteralPath $BuildPython -PathType Leaf) -and
        (Test-Path -LiteralPath $FingerprintPath -PathType Leaf)
    ) {
        try {
            $BuildPythonInfo = Get-PythonInfo $BuildPython
            if (Test-KivyCompatiblePython $BuildPythonInfo.Version) {
                $StoredFingerprint = [IO.File]::ReadAllText(
                    $FingerprintPath,
                    [Text.Encoding]::ASCII
                ).Trim()
                $VenvIsReusable = [string]::Equals(
                    $StoredFingerprint,
                    $BaseFingerprint,
                    [StringComparison]::Ordinal
                )
            }
        }
        catch {
            Write-Warning "The existing build virtual environment is invalid: $($_.Exception.Message)"
        }
    }

    if (-not $VenvIsReusable) {
        $GuardedBuildVenv = [IO.Path]::GetFullPath($BuildVenv)
        if (-not [string]::Equals(
            $GuardedBuildVenv,
            $ExpectedBuildVenv,
            [StringComparison]::OrdinalIgnoreCase
        )) {
            throw "Refusing to remove unexpected build virtual environment path: $GuardedBuildVenv"
        }

        foreach ($GuardPath in @($BuildVenvRoot, $BuildVenv)) {
            $GuardItem = Get-Item -LiteralPath $GuardPath -Force -ErrorAction SilentlyContinue
            if (
                $GuardItem -and
                (($GuardItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)
            ) {
                throw "Refusing to use reparse point for build virtual environment: $GuardPath"
            }
        }

        if (Test-Path -LiteralPath $BuildVenv) {
            $RealRepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
            $RealBuildVenvRoot = (Resolve-Path -LiteralPath $BuildVenvRoot).Path
            $RealBuildVenv = (Resolve-Path -LiteralPath $BuildVenv).Path
            $ExpectedRealBuildVenvRoot = [IO.Path]::GetFullPath(
                (Join-Path $RealRepoRoot ".build-venv")
            )
            $ExpectedRealBuildVenv = [IO.Path]::GetFullPath(
                (Join-Path $ExpectedRealBuildVenvRoot "windows")
            )
            if (
                -not [string]::Equals(
                    $RealBuildVenvRoot,
                    $ExpectedRealBuildVenvRoot,
                    [StringComparison]::OrdinalIgnoreCase
                ) -or
                -not [string]::Equals(
                    $RealBuildVenv,
                    $ExpectedRealBuildVenv,
                    [StringComparison]::OrdinalIgnoreCase
                )
            ) {
                throw "Refusing to remove build virtual environment outside the repository: $RealBuildVenv"
            }

            Remove-Item -LiteralPath $BuildVenv -Recurse -Force
        }

        & $BasePython -m venv $BuildVenv
        if ($LASTEXITCODE -ne 0) {
            throw "Build virtual environment creation failed with exit code $LASTEXITCODE."
        }

        if (-not (Test-Path -LiteralPath $BuildPython -PathType Leaf)) {
            throw "Build Python executable not found: $BuildPython"
        }

        $BuildPythonInfo = Get-PythonInfo $BuildPython
        if (-not (Test-KivyCompatiblePython $BuildPythonInfo.Version)) {
            throw "Build Python $($BuildPythonInfo.Version) is not compatible with Kivy 2.3.1."
        }

        $FingerprintTemp = Join-Path $BuildVenv (
            "base-python.fingerprint.tmp.{0}" -f [Guid]::NewGuid().ToString("N")
        )
        try {
            [IO.File]::WriteAllText(
                $FingerprintTemp,
                $BaseFingerprint + [Environment]::NewLine,
                [Text.Encoding]::ASCII
            )
            Move-Item -LiteralPath $FingerprintTemp -Destination $FingerprintPath -Force
        }
        finally {
            if (Test-Path -LiteralPath $FingerprintTemp -PathType Leaf) {
                Remove-Item -LiteralPath $FingerprintTemp -Force
            }
        }
    }

    $BuildPython = $BuildPythonInfo.Executable
    Write-Host "Using build Python $($BuildPythonInfo.Version) at $BuildPython"

    if (-not $SkipInstall) {
        Write-BuildPhase "Installing Python dependencies"
        if (-not (Test-Path -LiteralPath "requirements-gui.txt" -PathType Leaf)) {
            throw "Requirements file not found: requirements-gui.txt"
        }

        & $BuildPython -m pip install -r requirements-gui.txt
        if ($LASTEXITCODE -ne 0) {
            throw "Dependency installation failed with exit code $LASTEXITCODE."
        }
    }

    if ($PrepareOnly) {
        Write-BuildPhase "Build environment ready"
        return
    }

    if ($env:FFMPEG_PATH) {
        if (-not (Test-Path -LiteralPath $env:FFMPEG_PATH -PathType Leaf)) {
            throw "FFMPEG_PATH does not point to a file: $($env:FFMPEG_PATH)"
        }
        $FFmpegPath = (Resolve-Path -LiteralPath $env:FFMPEG_PATH).Path
    }
    else {
        $FFmpegCommand = Get-Command ffmpeg -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if (-not $FFmpegCommand) {
            throw "FFmpeg was not found on PATH. Install FFmpeg before building the application."
        }
        $FFmpegPath = $FFmpegCommand.Source
    }
    Write-Host "Bundling FFmpeg from $FFmpegPath"
    Write-BuildPhase "Preparing packaged configuration"
    $PackagedConfig = "build/package-config"
    & $BuildPython scripts/prepare_packaged_config.py `
        --source config `
        --output $PackagedConfig
    if ($LASTEXITCODE -ne 0) {
        throw "Packaged configuration preparation failed with exit code $LASTEXITCODE."
    }

    Write-BuildPhase "Running PyInstaller"
    $PreviousKivyDoc = [Environment]::GetEnvironmentVariable("KIVY_DOC", "Process")
    try {
        # PyInstaller imports Kivy modules while scanning DLLs. Documentation
        # mode prevents those imports from opening a window on headless runners.
        $env:KIVY_DOC = "1"
        & $BuildPython -m PyInstaller `
            --noconfirm `
            --clean `
            --log-level INFO `
            --name DouyinLiveRecorder `
            --onedir `
            --windowed `
            --additional-hooks-dir "packaging/pyinstaller-hooks" `
            --collect-data kivy `
            --add-data "$FFmpegPath;." `
            --add-data "$PackagedConfig;config" `
            --add-data "i18n;i18n" `
            --add-data "src/javascript;src/javascript" `
            "lubo/apps/desktop/main.py"
        $PyInstallerExitCode = $LASTEXITCODE
    }
    finally {
        if ($null -eq $PreviousKivyDoc) {
            Remove-Item Env:KIVY_DOC -ErrorAction SilentlyContinue
        }
        else {
            $env:KIVY_DOC = $PreviousKivyDoc
        }
    }

    if ($PyInstallerExitCode -ne 0) {
        throw "PyInstaller failed with exit code $PyInstallerExitCode."
    }

    Write-BuildPhase "Verifying packaged output"
    $DistPath = Join-Path $RepoRoot "dist/DouyinLiveRecorder"
    if (-not (Test-Path -LiteralPath $DistPath -PathType Container)) {
        throw "Expected build output not found: $DistPath"
    }

    Write-Host "Build complete: $DistPath"
}
finally {
    Pop-Location
}
