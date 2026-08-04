function Request-BotRestart {
    $expectedIdentity = Get-CodexBotProcessIdentity `
        -BotScript $BotScript `
        -RuntimeLockPath $RuntimeLockPath
    if (-not $expectedIdentity) {
        throw 'running bot process identity could not be verified'
    }
    Publish-AtomicTextFile `
        -Path $RestartRequestPath `
        -Content "identity=$expectedIdentity"
    $task = Get-ScheduledTask -TaskName 'Codex Discord Bot' -ErrorAction SilentlyContinue
    if ($task -ne $null) {
        if (-not $task.Settings.Enabled) {
            Enable-ScheduledTask -TaskName 'Codex Discord Bot' | Out-Null
        }
        Start-ScheduledTask -TaskName 'Codex Discord Bot'
        Write-LauncherLog "tray_restart_requested task='Codex Discord Bot'"
        return
    }
    if (Test-Path -LiteralPath $HeadlessLauncher) {
        Start-Process `
            -FilePath 'wscript.exe' `
            -ArgumentList @("`"$HeadlessLauncher`"") `
            -WindowStyle Hidden
        Write-LauncherLog "tray_restart_requested launcher=$HeadlessLauncher"
    }
}
