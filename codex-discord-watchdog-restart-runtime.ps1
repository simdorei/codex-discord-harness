function Get-CodexThreadUpdatedAt {
    param([string]$UpdatedAtText)

    if ([string]::IsNullOrWhiteSpace($UpdatedAtText)) {
        return $null
    }

    [datetime]$updatedAt = [datetime]::MinValue
    $formats = @(
        'yyyy-MM-dd HH:mm:ss',
        'yyyy-MM-ddTHH:mm:ssK',
        'yyyy-MM-ddTHH:mm:ss'
    )
    $parsed = [datetime]::TryParseExact(
        $UpdatedAtText,
        $formats,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::AssumeLocal,
        [ref]$updatedAt
    )
    if (-not $parsed) {
        $parsed = [datetime]::TryParse(
            $UpdatedAtText,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::AssumeLocal,
            [ref]$updatedAt
        )
    }
    if (-not $parsed) {
        return $null
    }
    return $updatedAt
}

function Get-CodexThreadRestartBlockers {
    param([int]$QuietSeconds = $RestartQuietSeconds)

    if (-not (Test-Path -LiteralPath $BridgePath)) {
        throw "Cannot verify Codex thread state before restart; bridge script not found: $BridgePath"
    }

    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $bridgeOutput = & py -3 $BridgePath list --db-root --limit 0 2>&1
        $bridgeExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($bridgeExitCode -ne 0) {
        throw "Cannot verify Codex thread state before restart.`n$($bridgeOutput -join "`n")"
    }

    $blockers = @()
    $now = Get-Date
    foreach ($line in $bridgeOutput) {
        if ($line -notmatch '\|') {
            continue
        }
        $parts = [string]$line -split '\|'
        if ($parts.Count -lt 3) {
            continue
        }
        $state = $parts[2].Trim()
        if ($state -and $state -ne 'idle') {
            $blockers += [pscustomobject]@{
                Reason = 'busy'
                Detail = ([string]$line).Trim()
            }
            continue
        }

        if ($QuietSeconds -gt 0 -and $parts.Count -ge 9) {
            $updatedAt = Get-CodexThreadUpdatedAt -UpdatedAtText $parts[8].Trim()
            if ($updatedAt -ne $null) {
                $ageSeconds = [math]::Floor(($now - $updatedAt).TotalSeconds)
                if ($ageSeconds -lt $QuietSeconds) {
                    Write-LauncherLog (
                        "watchdog_restart_idle_recent_allowed quiet_seconds=$QuietSeconds " +
                        ("detail={0} | updated_age_seconds={1}" -f ([string]$line).Trim(), $ageSeconds)
                    )
                }
            }
        }
    }

    return $blockers
}

function Format-CodexThreadRestartBlockers {
    param($Blockers)

    $lines = @()
    foreach ($blocker in $Blockers) {
        $lines += ("{0}: {1}" -f $blocker.Reason, $blocker.Detail)
    }
    return $lines
}

function Assert-CodexThreadsQuietForRestart {
    $blockers = @(Get-CodexThreadRestartBlockers -QuietSeconds $RestartQuietSeconds)
    if ($blockers.Count -gt 0) {
        $lines = Format-CodexThreadRestartBlockers -Blockers $blockers
        throw "Refusing to restart Codex Discord bot because Codex threads are busy.`n$($lines -join "`n")"
    }
}

function Wait-CodexThreadsQuietForRestart {
    if ($RestartWaitTimeoutSeconds -le 0) {
        Assert-CodexThreadsQuietForRestart
        return
    }

    $deadline = (Get-Date).AddSeconds($RestartWaitTimeoutSeconds)
    $lastLogAt = [datetime]::MinValue
    while ($true) {
        $blockers = @(Get-CodexThreadRestartBlockers -QuietSeconds $RestartQuietSeconds)
        if ($blockers.Count -eq 0) {
            Write-LauncherLog "watchdog_restart_quiet quiet_seconds=$RestartQuietSeconds"
            return
        }

        $now = Get-Date
        if ($now -ge $deadline) {
            $lines = Format-CodexThreadRestartBlockers -Blockers $blockers
            throw "Timed out waiting for Codex threads to become quiet before restart.`n$($lines -join "`n")"
        }

        if (($now - $lastLogAt).TotalSeconds -ge 15) {
            $sample = ($blockers | Select-Object -First 1).Detail
            Write-LauncherLog "watchdog_restart_waiting quiet_seconds=$RestartQuietSeconds blockers=$($blockers.Count) sample=$sample"
            $lastLogAt = $now
        }
        Start-Sleep -Seconds $RestartWaitPollSeconds
    }
}

function Claim-RestartRequest {
    if (-not (Test-Path -LiteralPath $RestartRequestPath)) {
        return ""
    }
    try {
        Move-Item -LiteralPath $RestartRequestPath -Destination $RestartClaimPath -Force -ErrorAction Stop
        Write-LauncherLog "watchdog_restart_claimed marker=$RestartRequestPath claim=$RestartClaimPath"
        return $RestartClaimPath
    } catch {
        if (-not (Test-Path -LiteralPath $RestartRequestPath)) {
            Write-LauncherLog "watchdog_restart_claim_lost marker=$RestartRequestPath"
            return ""
        }
        Write-LauncherLog "watchdog_restart_claim_failed marker=$RestartRequestPath error=$($_.Exception.Message)"
        throw
    }
}

function Restore-RestartRequest {
    param([string]$ClaimPath)

    if ([string]::IsNullOrWhiteSpace($ClaimPath) -or -not (Test-Path -LiteralPath $ClaimPath)) {
        return
    }
    [System.IO.File]::WriteAllText($RestartRequestPath, '')
    Remove-Item -LiteralPath $ClaimPath -Force -ErrorAction Stop
    Write-LauncherLog "watchdog_restart_restored marker=$RestartRequestPath claim=$ClaimPath"
}

function Restore-OrphanedRestartClaims {
    foreach ($claim in @(Get-ChildItem -Path $RestartClaimPattern -File -ErrorAction SilentlyContinue)) {
        $ownerAlive = $false
        if ($claim.Name -match '\.(\d+)$') {
            $ownerAlive = $null -ne (Get-Process -Id ([int]$Matches[1]) -ErrorAction SilentlyContinue)
        }
        if (-not $ownerAlive) {
            Restore-RestartRequest -ClaimPath $claim.FullName
        }
    }
}
