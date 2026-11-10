# Crée (ou remplace) la tâche planifiée Windows qui lance job-agent.
#
# Utilisation, depuis le dossier du projet, dans PowerShell :
#   powershell -ExecutionPolicy Bypass -File .\scripts\install_task.ps1
#
# Par défaut : toutes les 2 heures de 8 h à 20 h, du lundi au samedi.
# Ces valeurs doivent correspondre à la section "schedule" de config.yaml :
# l'agent vérifie de toute façon lui-même la plage horaire à chaque lancement.
# La tâche tourne avec votre compte, seulement quand votre session est ouverte
# et qu'une connexion réseau est disponible, sans fenêtre visible. Un passage
# manqué (ordinateur éteint) est lancé dès que possible. Aucun droit
# administrateur n'est nécessaire.

param(
    [string]$TaskName = "job-agent",
    [int]$StartHour = 8,
    [int]$EndHour = 20,
    [int]$IntervalHours = 2,
    [string[]]$Days = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$Pythonw = Join-Path $ProjectDir ".venv\Scripts\pythonw.exe"

if (-not (Test-Path $Pythonw)) {
    Write-Host "Environnement Python introuvable : $Pythonw" -ForegroundColor Red
    Write-Host "Lancez d'abord 'uv sync' dans le dossier du projet."
    exit 1
}

$Action = New-ScheduledTaskAction -Execute $Pythonw -Argument "-m job_agent.cli run" -WorkingDirectory $ProjectDir

$Start = (Get-Date).Date.AddHours($StartHour)
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At $Start
$Repeat = New-ScheduledTaskTrigger -Once -At $Start `
    -RepetitionInterval (New-TimeSpan -Hours $IntervalHours) `
    -RepetitionDuration (New-TimeSpan -Hours ($EndHour - $StartHour) -Minutes 5)
$Trigger.Repetition = $Repeat.Repetition

$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Settings $Settings -Principal $Principal `
    -Description "job-agent : collecte des offres d'emploi" -Force | Out-Null

Write-Host "Tâche '$TaskName' créée : toutes les $IntervalHours h de ${StartHour} h à ${EndHour} h ($($Days -join ', '))." -ForegroundColor Green
Write-Host "Pour la lancer tout de suite : Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Journal des passages : $ProjectDir\private\logs\"
