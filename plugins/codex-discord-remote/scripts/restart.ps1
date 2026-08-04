[CmdletBinding()]
param(
    [string]$RepoRoot,
    [switch]$DryRun,
    [switch]$Immediate,
    [switch]$Deferred,
    [string]$ExpectedBotIdentity,
    [int]$DelaySeconds = 10,
    [int]$QuietSeconds = 90,
    [int]$WaitTimeoutSeconds = 900
)

$ErrorActionPreference = 'Stop'
$MinimumDelaySeconds = 15
$MinimumQuietSeconds = 15

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Join-Path $PSScriptRoot '..\..\..'
}

$RepoRoot = [IO.Path]::GetFullPath($RepoRoot)
$RestartMarker = Join-Path $RepoRoot '.codex_discord_bot.restart'
$Watchdog = Join-Path $RepoRoot 'codex-discord-watchdog.ps1'
$AtomicFileRuntime = Join-Path $RepoRoot 'codex-discord-atomic-file-runtime.ps1'
$IdentityRuntime = Join-Path $RepoRoot 'codex-discord-watchdog-identity-runtime.ps1'
$BotScript = Join-Path $RepoRoot 'codex_discord_bot.py'
$RuntimeLockPath = Join-Path $RepoRoot '.codex_discord_bot.runtime.lock'
$EffectiveQuietSeconds = [math]::Max($MinimumQuietSeconds, $QuietSeconds)
$EffectiveDelaySeconds = [math]::Max($MinimumDelaySeconds, $DelaySeconds)

if (-not (Test-Path -LiteralPath $Watchdog)) {
    throw "watchdog script not found: $Watchdog"
}
foreach ($runtimePath in @($AtomicFileRuntime, $IdentityRuntime)) {
    if (-not (Test-Path -LiteralPath $runtimePath)) {
        throw "restart runtime script not found: $runtimePath"
    }
}
. $AtomicFileRuntime
. $IdentityRuntime

function Invoke-BoundRestart {
    param([string]$Identity)

    if ([string]::IsNullOrWhiteSpace($Identity)) {
        throw 'restart is missing the expected bot identity'
    }
    Start-Sleep -Seconds $EffectiveDelaySeconds
    $currentIdentity = Get-CodexBotProcessIdentity `
        -BotScript $BotScript `
        -RuntimeLockPath $RuntimeLockPath
    if ($currentIdentity -ne $Identity) {
        throw 'restart no longer matches the requested bot process'
    }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Watchdog `
        -CheckRestartReady `
        -RestartQuietSeconds $EffectiveQuietSeconds `
        -RestartWaitTimeoutSeconds $WaitTimeoutSeconds
    if ($LASTEXITCODE -ne 0) {
        throw "restart readiness check failed with exit code $LASTEXITCODE"
    }
    $currentIdentity = Get-CodexBotProcessIdentity `
        -BotScript $BotScript `
        -RuntimeLockPath $RuntimeLockPath
    if ($currentIdentity -ne $Identity) {
        throw 'bot process changed while validating restart readiness'
    }
    Publish-AtomicTextFile `
        -Path $RestartMarker `
        -Content "identity=$Identity"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Watchdog `
        -RestartQuietSeconds $EffectiveQuietSeconds `
        -RestartWaitTimeoutSeconds $WaitTimeoutSeconds
    if ($LASTEXITCODE -ne 0) {
        throw "watchdog restart failed with exit code $LASTEXITCODE"
    }
}

if ($DryRun) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Watchdog `
        -CheckRestartReady `
        -RestartQuietSeconds $EffectiveQuietSeconds `
        -RestartWaitTimeoutSeconds 0
    exit $LASTEXITCODE
}

if ($Deferred) {
    Invoke-BoundRestart -Identity $ExpectedBotIdentity
    exit 0
}

$expectedIdentity = Get-CodexBotProcessIdentity `
    -BotScript $BotScript `
    -RuntimeLockPath $RuntimeLockPath
if ([string]::IsNullOrWhiteSpace($expectedIdentity)) {
    throw 'running bot process identity could not be verified'
}

if ($Immediate) {
    Invoke-BoundRestart -Identity $expectedIdentity
    Write-Output (
        "restart_completed: quiet_seconds=$EffectiveQuietSeconds " +
        "wait_timeout_seconds=$WaitTimeoutSeconds"
    )
    exit 0
}

$escapedRestartScript = $PSCommandPath.Replace("'", "''")
$escapedRepoRoot = $RepoRoot.Replace("'", "''")
$command = (
    "& '$escapedRestartScript' -RepoRoot '$escapedRepoRoot' -Deferred " +
    "-ExpectedBotIdentity '$expectedIdentity' -DelaySeconds $EffectiveDelaySeconds " +
    "-QuietSeconds $EffectiveQuietSeconds -WaitTimeoutSeconds $WaitTimeoutSeconds"
)
Start-Process -FilePath 'powershell.exe' `
    -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $command) `
    -WindowStyle Hidden
Write-Output (
    "restart_queued: delay_seconds=$EffectiveDelaySeconds " +
    "quiet_seconds=$EffectiveQuietSeconds wait_timeout_seconds=$WaitTimeoutSeconds " +
    "requested_immediate=$([bool]$Immediate)"
)
