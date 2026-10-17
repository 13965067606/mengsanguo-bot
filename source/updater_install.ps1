param([Parameter(Mandatory=$true)][string]$Stage,
      [Parameter(Mandatory=$true)][string]$Target,
      [Parameter(Mandatory=$true)][int]$ProcessId,
      [switch]$NoRestart)
$ErrorActionPreference = 'Stop'
$changes = @()
$mutated = $false
function Assert-NoLinks([string]$Path) {
    $itemPath = $Path
    while ($itemPath) {
        if ((Test-Path -LiteralPath $itemPath) -and ((Get-Item -LiteralPath $itemPath -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw '不允许通过目录链接更新' }
        $parentPath = Split-Path -Parent $itemPath
        if ($parentPath -eq $itemPath) { break }
        $itemPath = $parentPath
    }
}
function Get-Sha256([string]$Path) {
    $hasher = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($Path)
    try { return ([BitConverter]::ToString($hasher.ComputeHash($stream))).Replace('-', '').ToLowerInvariant() }
    finally { $stream.Dispose(); $hasher.Dispose() }
}
try {
    $Stage = [IO.Path]::GetFullPath($Stage)
    $Target = [IO.Path]::GetFullPath($Target)
    if ($Target.TrimEnd('\') -eq [IO.Path]::GetPathRoot($Target).TrimEnd('\')) { throw '不允许更新磁盘根目录' }
    Assert-NoLinks $Stage
    Assert-NoLinks $Target
    $payload = Join-Path $Stage 'payload'
    $manifest = Get-Content -LiteralPath (Join-Path $payload 'update.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.executable -cne 'MengSanGuoWindowBot.exe') { throw '程序名无效' }
    $waitUntil = (Get-Date).AddSeconds(45)
    while (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) {
        if ((Get-Date) -gt $waitUntil) { throw '旧程序未退出，未替换任何文件' }
        Start-Sleep -Milliseconds 250
    }
    $backup = Join-Path $Stage 'backup'
    New-Item -ItemType Directory -Path $backup | Out-Null
    foreach ($prop in $manifest.files.PSObject.Properties) {
        $name = $prop.Name
        if ($name -notmatch '^(MengSanGuoWindowBot\.exe|config\.example\.json|使用说明\.md|(?:templates|assets|ocr)/.+)$' -or $name -match '\\|:|(^|/)\.\.(/|$)') { throw '安装文件越界' }
        $sourcePath = [IO.Path]::GetFullPath((Join-Path $payload $name))
        $destPath = [IO.Path]::GetFullPath((Join-Path $Target $name))
        if (-not $sourcePath.StartsWith($payload + '\', [StringComparison]::OrdinalIgnoreCase) -or -not $destPath.StartsWith($Target.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw '安装路径越界' }
        Assert-NoLinks $sourcePath
        Assert-NoLinks $destPath
        if ((Get-Sha256 $sourcePath) -cne $prop.Value) { throw '安装前校验失败' }
        $oldPath = Join-Path $backup $name
        $existed = Test-Path -LiteralPath $destPath
        if ($existed -and (Get-Item -LiteralPath $destPath).PSIsContainer) { throw '目标文件被同名文件夹占用' }
        if ($existed) {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $oldPath) | Out-Null
            Copy-Item -LiteralPath $destPath -Destination $oldPath
        }
        $changes += @{Source=$sourcePath; Destination=$destPath; Backup=$oldPath; Existed=$existed; Applied=$false}
    }
    foreach ($change in $changes) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $change.Destination) | Out-Null
        $pendingPath = $change.Destination + '.update-' + [Guid]::NewGuid().ToString('N')
        Copy-Item -LiteralPath $change.Source -Destination $pendingPath
        $copyUntil = (Get-Date).AddSeconds(15)
        try {
            while ($true) {
                try { Move-Item -LiteralPath $pendingPath -Destination $change.Destination -Force; break }
                catch { if ((Get-Date) -gt $copyUntil) { throw }; Start-Sleep -Milliseconds 500 }
            }
            $change.Applied = $true
            $mutated = $true
        } finally {
            if (Test-Path -LiteralPath $pendingPath) { Remove-Item -LiteralPath $pendingPath -Force }
        }
    }
    Set-Content -LiteralPath (Join-Path $Target 'update_success.txt') -Value ("更新完成：" + $manifest.version + "`n备份：" + $backup) -Encoding UTF8
    if (-not $NoRestart) { Start-Process -FilePath (Join-Path $Target 'MengSanGuoWindowBot.exe') -WorkingDirectory $Target -WindowStyle Normal }
} catch {
    $failure = $_.Exception.Message
    $rollbackFailures = @()
    if ($mutated) {
        [array]::Reverse($changes)
        foreach ($change in $changes) {
            if (-not $change.Applied) { continue }
            try {
                if ($change.Existed) { Copy-Item -LiteralPath $change.Backup -Destination $change.Destination -Force }
                elseif (Test-Path -LiteralPath $change.Destination) { Remove-Item -LiteralPath $change.Destination -Force }
            } catch { $rollbackFailures += $change.Destination }
        }
    }
    $report = "更新失败：$failure`n备份保留在：$Stage`n恢复失败文件：$($rollbackFailures -join ', ')"
    if (Test-Path -LiteralPath $Target) { Set-Content -LiteralPath (Join-Path $Target 'update_error.txt') -Value $report -Encoding UTF8 }
    if (-not $NoRestart) {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show($report, '更新失败，请检查 update_error.txt') | Out-Null
    }
    exit 1
}
