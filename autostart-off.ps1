# Выключает автозапуск моста: убирает ярлык из автозагрузки
# и останавливает уже работающее слежение.

$startup = [Environment]::GetFolderPath('Startup')
$lnkPath = Join-Path $startup 'Мост достижений Hydra.lnk'

if (Test-Path -LiteralPath $lnkPath) {
    Remove-Item -LiteralPath $lnkPath -Force
    Write-Host "Ярлык из автозагрузки убран." -ForegroundColor Green
} else {
    Write-Host "Ярлыка в автозагрузке и не было."
}

# останавливаем работающее слежение (ищем именно наш bridge.py)
$stopped = 0
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*bridge.py*--watch*' } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        $stopped++
    }

if ($stopped -gt 0) {
    Write-Host "Остановил работающее слежение: $stopped шт." -ForegroundColor Green
} else {
    Write-Host "Работающего слежения не нашёл."
}

Write-Host ""
Write-Host "Мост выключен. Достижения, которые он уже перенёс, остались на месте."
Write-Host "Включить обратно — autostart-on.bat. Или просто запускай run.bat руками."
