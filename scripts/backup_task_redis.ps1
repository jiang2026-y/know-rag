# 任务 Redis 备份脚本：生成 RDB 快照，并只清理项目备份目录中超过七天的文件。
# 定义：读取容器密码、生成备份、校验清理目录边界并删除到期备份。

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$containerName = 'llmcopy-task-redis'
$backupDirectory = [System.IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot '..\infra\redis\task-redis-backups')
)
$workspaceRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))

if (-not $backupDirectory.StartsWith($workspaceRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Task Redis backup directory is outside the project workspace.'
}

New-Item -ItemType Directory -Force -Path $backupDirectory | Out-Null
$fileName = 'task-redis-{0}.rdb' -f (Get-Date -Format 'yyyyMMdd-HHmmss')
$containerBackupPath = "/backups/$fileName"

& docker exec $containerName sh -c 'redis-cli -a "$TASK_REDIS_PASSWORD" --rdb "$1"' sh $containerBackupPath
if ($LASTEXITCODE -ne 0) {
    throw "Task Redis backup failed for container: $containerName"
}

$cutoff = (Get-Date).ToUniversalTime().AddDays(-7)
Get-ChildItem -LiteralPath $backupDirectory -File -Filter 'task-redis-*.rdb' |
    Where-Object { $_.LastWriteTimeUtc -lt $cutoff } |
    ForEach-Object {
        Remove-Item -LiteralPath $_.FullName -Force
    }

Write-Output "Task Redis backup completed: $backupDirectory\\$fileName"
