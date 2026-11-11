# Включает автозапуск моста: тихий Python без окна следит за достижениями
# и переносит новое каждую минуту. Права администратора не нужны.
#
# Лежит ярлык здесь: Автозагрузка (Startup). Убрать — autostart-off.bat.

$here = Split-Path -Parent $MyInvocation.MyCommand.Path

# где взять pythonw.exe (это тот же Python, только без чёрного окна)
$target = $null
$onPath = Get-Command 'pythonw.exe' -ErrorAction SilentlyContinue
if ($onPath) {
    $target = $onPath.Source
} else {
    $python = Get-Command 'python.exe' -ErrorAction SilentlyContinue
    if ($python) {
        $candidate = Join-Path (Split-Path -Parent $python.Source) 'pythonw.exe'
        if (Test-Path -LiteralPath $candidate) { $target = $candidate }
    }
}
if (-not $target) {
    # Python не прописан в PATH — ищем его там, куда он обычно ставится.
    $patterns = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python*\pythonw.exe'),
        (Join-Path $env:ProgramFiles 'Python*\pythonw.exe'),
        'C:\Python*\pythonw.exe',
        'D:\Python*\pythonw.exe',
        'D:\Python\pythonw.exe'
    )
    foreach ($p in $patterns) {
        $hit = Get-ChildItem -Path $p -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($hit) { $target = $hit.FullName; break }
    }
}

if (-not $target -or -not (Test-Path -LiteralPath $target)) {
    Write-Host "Не нашёл pythonw.exe — автозапуск не включён." -ForegroundColor Yellow
    Write-Host "Поставь Python с python.org (при установке галочка «Add Python to PATH»),"
    Write-Host "либо впиши полный путь к pythonw.exe в строку `$target выше в этом файле."
    exit 1
}

$startup = [Environment]::GetFolderPath('Startup')
$lnkPath = Join-Path $startup 'Мост достижений Hydra.lnk'

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($lnkPath)
$lnk.TargetPath = $target
$lnk.Arguments = '"' + (Join-Path $here 'bridge.py') + '" --watch 60'
$lnk.WorkingDirectory = $here
$lnk.Description = 'Тихо переносит достижения эмуляторов туда, где их видит Hydra Launcher'
$lnk.Save()

Write-Host "Автозапуск включён." -ForegroundColor Green
Write-Host "Ярлык: $lnkPath"
Write-Host "Мост включится сам при следующем входе в Windows."
Write-Host "Прямо сейчас запускаю его вручную..."

Start-Process -FilePath $target -ArgumentList ('"' + (Join-Path $here 'bridge.py') + '" --watch 60') -WorkingDirectory $here
Write-Host "Готово. Он работает тихо, окна не будет." -ForegroundColor Green
