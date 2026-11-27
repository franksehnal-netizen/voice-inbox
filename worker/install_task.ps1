# Voice Inbox – registrace naplánované úlohy (aktuální uživatel, bez admin práv, bez okna).
# Spouští se při přihlášení a pak každých 5 min. Odinstalace: Unregister-ScheduledTask -TaskName 'Voice Inbox Worker' -Confirm:$false
$ErrorActionPreference = 'Stop'
$name   = 'Voice Inbox Worker'
$worker = Join-Path $PSScriptRoot 'voice_inbox_worker.py'
$pyw    = (Get-Command pythonw).Source
$user   = "$env:USERDOMAIN\$env:USERNAME"

$action = New-ScheduledTaskAction -Execute $pyw -Argument "`"$worker`"" -WorkingDirectory $PSScriptRoot
$every  = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$logon  = New-ScheduledTaskTrigger -AtLogOn -User $user
$logon.Delay = 'PT2M'   # počkat, než Drive for desktop připojí G:
$logon.Repetition = $every.Repetition
$set = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$prin = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $name -Action $action -Trigger @($every, $logon) -Settings $set -Principal $prin `
    -Description 'Voice Inbox: přepis + shrnutí hlasových poznámek z G:\My Drive\voice_inbox\incoming do Obsidianu. Repo: Documents\voice-inbox (github franksehnal-netizen/voice-inbox)' -Force | Out-Null
$t = Get-ScheduledTask -TaskName $name
"REGISTERED $name | state=$($t.State) | triggers=$($t.Triggers.Count) | every=$($t.Triggers[0].Repetition.Interval) | exe=$($t.Actions[0].Execute)"
