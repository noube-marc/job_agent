# Supprime la tâche planifiée Windows de job-agent.
#
# Utilisation, depuis le dossier du projet, dans PowerShell :
#   powershell -ExecutionPolicy Bypass -File .\scripts\uninstall_task.ps1
#
# Vos fichiers (config.yaml, .env, private/) ne sont pas touchés.

param([string]$TaskName = "job-agent")

$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $Task) {
    Write-Host "Aucune tâche '$TaskName' : rien à supprimer."
    exit 0
}
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Tâche '$TaskName' supprimée." -ForegroundColor Green
