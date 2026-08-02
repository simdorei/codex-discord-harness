[CmdletBinding()]
param(
    [string]$PythonExe = $env:PYTHON_EXE,
    [string]$CodexExe = $env:CODEX_EXE,
    [string]$CodexHome = $env:CODEX_HOME,
    [switch]$SkipDependencies,
    [switch]$SkipEnvFile,
    [switch]$SkipSteeringConfig,
    [switch]$SkipCodexPlugin,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RequirementsPath = Join-Path $ScriptDir 'requirements.txt'
$EnvExamplePath = Join-Path $ScriptDir '.env.example'
$EnvPath = Join-Path $ScriptDir '.env'
$PluginMarketplacePath = Join-Path $ScriptDir '.agents\plugins\marketplace.json'
$PluginManifestPath = Join-Path $ScriptDir 'plugins\codex-discord-remote\.codex-plugin\plugin.json'
$PluginInventoryVerifierPath = Join-Path $ScriptDir 'verify_codex_plugin_inventory.py'
$PluginMarketplaceName = 'codex-discord-remote'
$PluginRef = 'codex-discord-remote@codex-discord-remote'
$RequiredPythonMajor = 3
$RequiredPythonMinor = 12
$PortablePythonVersion = '3.12.1'
$PortablePythonDir = Join-Path $ScriptDir '.python-portable'
$PortablePythonExe = Join-Path $PortablePythonDir 'python.exe'
$PortablePythonZip = Join-Path $PortablePythonDir "python-${PortablePythonVersion}-embed-amd64.zip"
$PortablePythonUrl = "https://www.python.org/ftp/python/${PortablePythonVersion}/python-${PortablePythonVersion}-embed-amd64.zip"
$GetPipUrl = 'https://bootstrap.pypa.io/get-pip.py'
$GetPipPath = Join-Path $PortablePythonDir 'get-pip.py'
$GetPipLogPath = Join-Path $PortablePythonDir 'get-pip.log'

function Test-PythonCommand {
    param([string[]]$Command)

    $exe = $Command[0]
    $baseArgs = @()
    if ($Command.Count -gt 1) {
        $baseArgs = $Command[1..($Command.Count - 1)]
    }

    & $exe @baseArgs -c "import sys; raise SystemExit(0 if sys.version_info[:2] == ($RequiredPythonMajor, $RequiredPythonMinor) else 1)" >$null 2>$null
    return $LASTEXITCODE -eq 0
}

function Get-PythonExecutablePath {
    param([string[]]$Command)

    $exe = $Command[0]
    $baseArgs = @()
    if ($Command.Count -gt 1) {
        $baseArgs = $Command[1..($Command.Count - 1)]
    }

    $output = & $exe @baseArgs -c "import sys; print(sys.executable)"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($output)) {
        throw "Python command failed while resolving sys.executable: $exe $($baseArgs -join ' ')"
    }
    return ([string]$output).Trim()
}

function Find-PythonCommand {
    if (-not [string]::IsNullOrWhiteSpace($PythonExe)) {
        $candidate = @($PythonExe)
        if (Test-PythonCommand -Command $candidate) {
            return $candidate
        }
        throw "PYTHON_EXE must point to Python 3.12.x: $PythonExe"
    }

    if (Test-Path -LiteralPath $PortablePythonExe) {
        $candidate = @($PortablePythonExe)
        if (Test-PythonCommand -Command $candidate) {
            return $candidate
        }
    }

    return @()
}

function Enable-PortablePythonSite {
    $pthFiles = @(Get-ChildItem -LiteralPath $PortablePythonDir -Filter 'python*._pth' -File)
    if ($pthFiles.Count -eq 0) {
        throw "Portable Python _pth file was not found in $PortablePythonDir."
    }
    $pthPath = $pthFiles[0].FullName
    $lines = @(Get-Content -LiteralPath $pthPath)
    $updatedLines = [System.Collections.Generic.List[string]]::new()
    $hasRepoRoot = $false
    $hasImportSite = $false
    foreach ($lineValue in $lines) {
        $line = [string]$lineValue
        $trimmed = $line.Trim()
        if ($trimmed -eq '..') {
            $hasRepoRoot = $true
        }
        if ($trimmed -eq 'import site') {
            if (-not $hasRepoRoot) {
                $updatedLines.Add('..')
                $hasRepoRoot = $true
            }
            $hasImportSite = $true
            $updatedLines.Add($line)
            continue
        }
        if ($trimmed -eq '#import site') {
            if (-not $hasRepoRoot) {
                $updatedLines.Add('..')
                $hasRepoRoot = $true
            }
            $hasImportSite = $true
            $updatedLines.Add('import site')
            continue
        }
        $updatedLines.Add($line)
    }
    if (-not $hasRepoRoot) {
        $updatedLines.Add('..')
    }
    if (-not $hasImportSite) {
        $updatedLines.Add('import site')
    }
    Set-Content -LiteralPath $pthPath -Value $updatedLines -Encoding ASCII
}

function Install-PortablePython312 {
    if ($DryRun) {
        Write-Host "Would download portable Python $PortablePythonVersion to $PortablePythonDir"
        return
    }
    Write-Host "Portable Python $PortablePythonVersion was not found. Downloading to $PortablePythonDir."
    New-Item -ItemType Directory -Force -Path $PortablePythonDir | Out-Null
    Invoke-WebRequest -Uri $PortablePythonUrl -OutFile $PortablePythonZip
    Expand-Archive -LiteralPath $PortablePythonZip -DestinationPath $PortablePythonDir -Force
    Remove-Item -LiteralPath $PortablePythonZip -Force
    Enable-PortablePythonSite
    Invoke-WebRequest -Uri $GetPipUrl -OutFile $GetPipPath
    if (Test-Path -LiteralPath $GetPipLogPath) {
        Remove-Item -LiteralPath $GetPipLogPath -Force
    }
    & $PortablePythonExe $GetPipPath --no-warn-script-location *> $GetPipLogPath
    if ($LASTEXITCODE -ne 0) {
        throw "get-pip.py failed for portable Python with exit code ${LASTEXITCODE}. See $GetPipLogPath."
    }
    Remove-Item -LiteralPath $GetPipPath -Force
    Remove-Item -LiteralPath $GetPipLogPath -Force
}

function Resolve-PythonCommand {
    $command = @(Find-PythonCommand)
    if ($command.Count -gt 0) {
        return $command
    }

    Install-PortablePython312
    if ($DryRun) {
        return @($PortablePythonExe)
    }
    $command = @(Find-PythonCommand)
    if ($command.Count -gt 0) {
        return $command
    }

    throw "Portable Python 3.12.x was not found after bootstrap: $PortablePythonExe"
}

function Invoke-Python {
    param([string[]]$Arguments)

    $command = @(Resolve-PythonCommand)
    $exe = $command[0]
    $baseArgs = @()
    if ($command.Count -gt 1) {
        $baseArgs = $command[1..($command.Count - 1)]
    }

    if ($DryRun) {
        Write-Output "Would run: $exe $($baseArgs + $Arguments -join ' ')"
        return
    }

    & $exe @baseArgs @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code ${LASTEXITCODE}: $exe $($baseArgs + $Arguments -join ' ')"
    }
}

function Get-EnvFileValue {
    param([string]$Name)

    if (-not (Test-Path -LiteralPath $EnvPath)) {
        return ''
    }
    foreach ($line in Get-Content -LiteralPath $EnvPath) {
        $trimmed = ([string]$line).Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) {
            continue
        }
        $key, $value = $trimmed.Split('=', 2)
        if ($key -eq $Name) {
            return $value.Trim().Trim('"')
        }
    }
    return ''
}

function Set-EnvFileValue {
    param(
        [string]$Name,
        [string]$Value
    )

    $lines = @()
    if (Test-Path -LiteralPath $EnvPath) {
        $lines = @(Get-Content -LiteralPath $EnvPath)
    }

    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $line = [string]$lines[$i]
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#') -or -not $line.Contains('=')) {
            continue
        }
        $key, $oldValue = $line.Split('=', 2)
        $null = $oldValue
        if ($key.Trim() -eq $Name) {
            $lines[$i] = "$Name=$Value"
            $found = $true
            break
        }
    }

    if (-not $found) {
        $lines += "$Name=$Value"
    }

    Set-Content -LiteralPath $EnvPath -Value $lines -Encoding UTF8
}

function Get-DefaultCodexHomePath {
    return (Join-Path ([Environment]::GetFolderPath('UserProfile')) '.codex')
}

function Test-CodexRuntimeBinPath {
    param([string]$Path)

    $normalized = $Path.TrimEnd('\', '/').ToLowerInvariant()
    return $normalized.Contains('\.sandbox-bin') `
        -or $normalized.Contains('\plugins\.plugin-appserver') `
        -or $normalized.EndsWith('\appdata\local\openai\codex\bin') `
        -or $normalized.EndsWith('\app\resources')
}

function Resolve-CodexHomePath {
    $defaultCodexHome = Get-DefaultCodexHomePath
    if (-not [string]::IsNullOrWhiteSpace($CodexHome)) {
        $expanded = [Environment]::ExpandEnvironmentVariables($CodexHome.Trim().Trim('"').Trim("'"))
        $homePath = [Environment]::GetFolderPath('UserProfile')
        if ($expanded -eq '~') {
            return $homePath
        }
        if ($expanded.StartsWith('~/') -or $expanded.StartsWith('~\')) {
            $expanded = Join-Path $homePath $expanded.Substring(2)
        }
        $resolved = [System.IO.Path]::GetFullPath($expanded)
        if (Test-CodexRuntimeBinPath -Path $resolved) {
            return $defaultCodexHome
        }
        return $resolved
    }

    return $defaultCodexHome
}

function Find-CodexCommand {
    if (-not [string]::IsNullOrWhiteSpace($CodexExe)) {
        return $CodexExe.Trim().Trim('"').Trim("'")
    }

    $envCodexExe = Get-EnvFileValue -Name 'CODEX_EXE'
    if (-not [string]::IsNullOrWhiteSpace($envCodexExe)) {
        return $envCodexExe
    }

    $codex = Get-Command codex -ErrorAction SilentlyContinue
    if ($codex -ne $null) {
        return $codex.Source
    }

    return ''
}

function Resolve-CodexCommand {
    $command = Find-CodexCommand
    if (-not [string]::IsNullOrWhiteSpace($command)) {
        return $command
    }

    throw 'Codex CLI was not found. Set CODEX_EXE or install/enable the codex command.'
}

function Invoke-Codex {
    param([string[]]$Arguments)

    $exe = Resolve-CodexCommand
    if ($DryRun) {
        Write-Output "Would run: $exe $($Arguments -join ' ')"
        return
    }

    & $exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Codex CLI failed with exit code ${LASTEXITCODE}: $exe $($Arguments -join ' ')"
    }
}

function Test-CodexPluginInventory {
    if ($DryRun) {
        Write-Output "Would verify Codex marketplace and plugin inventory for $PluginRef"
        return
    }

    $marketplaceInventoryPath = [System.IO.Path]::GetTempFileName()
    $pluginInventoryPath = [System.IO.Path]::GetTempFileName()
    $utf8 = [System.Text.UTF8Encoding]::new($false)
    try {
        $marketplaceOutput = @(Invoke-Codex -Arguments @('plugin', 'marketplace', 'list', '--json'))
        $pluginOutput = @(Invoke-Codex -Arguments @('plugin', 'list', '--json'))
        [System.IO.File]::WriteAllText(
            $marketplaceInventoryPath,
            ($marketplaceOutput -join [Environment]::NewLine),
            $utf8
        )
        [System.IO.File]::WriteAllText(
            $pluginInventoryPath,
            ($pluginOutput -join [Environment]::NewLine),
            $utf8
        )
        Invoke-Python -Arguments @(
            $PluginInventoryVerifierPath,
            '--marketplace-inventory', $marketplaceInventoryPath,
            '--plugin-inventory', $pluginInventoryPath,
            '--plugin-manifest', $PluginManifestPath,
            '--expected-root', $ScriptDir,
            '--marketplace-name', $PluginMarketplaceName,
            '--plugin-id', $PluginRef
        )
    } catch {
        throw "INSTALL_INCOMPLETE: Codex plugin inventory verification failed. $($_.Exception.Message)"
    } finally {
        Remove-Item -LiteralPath $marketplaceInventoryPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $pluginInventoryPath -Force -ErrorAction SilentlyContinue
    }
}

if (-not $SkipDependencies) {
    if (-not (Test-Path -LiteralPath $RequirementsPath)) {
        throw "requirements.txt was not found: $RequirementsPath"
    }
    Write-Output "Installing Python dependencies from requirements.txt"
    Invoke-Python -Arguments @('-m', 'pip', 'install', '-r', $RequirementsPath)
}

if (-not $SkipEnvFile) {
    if (Test-Path -LiteralPath $EnvPath) {
        Write-Output ".env already exists: $EnvPath"
    } elseif (Test-Path -LiteralPath $EnvExamplePath) {
        if ($DryRun) {
            Write-Output "Would create: $EnvPath from .env.example"
        } else {
            Copy-Item -LiteralPath $EnvExamplePath -Destination $EnvPath
            Write-Output "Created: $EnvPath"
        }
    } else {
        Write-Output ".env.example was not found; skipping .env creation."
    }

    if ($DryRun) {
        Write-Output "Would set PYTHON_EXE to the resolved Python 3.12 executable in .env"
        $codexHomePath = Resolve-CodexHomePath
        Write-Output "Would set CODEX_HOME=$codexHomePath in .env"
        Write-Output "Would set CODEX_EXE in .env when the codex command is available"
    } else {
        $pythonCommand = @(Resolve-PythonCommand)
        $pythonExePath = Get-PythonExecutablePath -Command $pythonCommand
        Set-EnvFileValue -Name 'PYTHON_EXE' -Value $pythonExePath
        Write-Output "Configured PYTHON_EXE=$pythonExePath"

        $codexHomePath = Resolve-CodexHomePath
        Set-EnvFileValue -Name 'CODEX_HOME' -Value $codexHomePath
        Write-Output "Configured CODEX_HOME=$codexHomePath"

        $codexCommand = Find-CodexCommand
        if (-not [string]::IsNullOrWhiteSpace($codexCommand)) {
            Set-EnvFileValue -Name 'CODEX_EXE' -Value $codexCommand
            Write-Output "Configured CODEX_EXE=$codexCommand"
        }
    }
}

Write-Output 'Discovering Codex Desktop executable.'
Invoke-Python -Arguments @('codex_desktop_bridge.py', 'discover_codex')

if ($SkipSteeringConfig) {
    Write-Output 'Skipping steering config: installer no longer changes Codex Desktop follow-up mode.'
}

if ($SkipCodexPlugin) {
    Write-Output 'Skipping Codex plugin install.'
} else {
    if (-not (Test-Path -LiteralPath $PluginMarketplacePath)) {
        throw "Codex plugin marketplace was not found: $PluginMarketplacePath"
    }
    if (-not (Test-Path -LiteralPath $PluginManifestPath)) {
        throw "INSTALL_INCOMPLETE: Codex plugin manifest was not found: $PluginManifestPath"
    }
    if (-not (Test-Path -LiteralPath $PluginInventoryVerifierPath)) {
        throw "INSTALL_INCOMPLETE: Codex plugin inventory verifier was not found: $PluginInventoryVerifierPath"
    }
    try {
        Write-Output 'Installing Codex plugin marketplace from this repository.'
        Invoke-Codex -Arguments @('plugin', 'marketplace', 'add', $ScriptDir)
        Write-Output "Installing Codex plugin: $PluginRef"
        Invoke-Codex -Arguments @('plugin', 'add', $PluginRef)
    } catch {
        throw "INSTALL_INCOMPLETE: Codex plugin installation failed; !pro is not ready. $($_.Exception.Message)"
    }
    Test-CodexPluginInventory
}

if ($DryRun) {
    Write-Output 'Dry run complete.'
    Write-Output 'Plugin inventory was not verified.'
} else {
    Write-Output 'Install complete.'
    Write-Output 'Setup required: run .\setup-discord-bot.ps1 and paste the Discord bot token when prompted.'
    Write-Output 'After setup, restart Codex so bundled skills reload, then run .\codex-discord-bot.cmd'
}
