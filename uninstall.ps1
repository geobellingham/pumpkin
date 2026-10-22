# Removes Pumpkin from Windows: the program, its shortcuts, the pumpkin command, starting at sign-in, and its
# entry in Apps > Installed apps. Your models, files and reports in %USERPROFILE%\Pumpkin and your settings
# stay, unless you run it with $env:PUMPKIN_PURGE = '1' (then settings and downloaded llama.cpp go too;
# the Pumpkin folder with your models and files always stays).
#
#   Apps > Installed apps > Pumpkin > Uninstall, or:
#   powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\Programs\Pumpkin\uninstall.ps1"

& {
    $ErrorActionPreference = 'Continue'
    $Dir = Join-Path $env:LOCALAPPDATA 'Programs\Pumpkin'
    $cfg = Join-Path $env:APPDATA 'pumpkin'

    # stop it
    $port = 8090
    try { $s = Get-Content -Raw (Join-Path $cfg 'settings.json') | ConvertFrom-Json; if ($s.port) { $port = [int]$s.port } } catch { }
    try {
        $tok = (Get-Content -Raw (Join-Path $cfg 'token')).Trim()
        Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port/pumpkin/api/quit" -TimeoutSec 5 `
            -Headers @{ 'Authorization' = "Bearer $tok"; 'X-Pumpkin' = '1'; 'Content-Type' = 'application/json' } -Body '{}' | Out-Null
        Start-Sleep -Seconds 2
    } catch { }
    $llamaDir = Join-Path $env:LOCALAPPDATA 'pumpkin\llama'
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ExecutablePath -and ($_.ExecutablePath.StartsWith($Dir, [StringComparison]::OrdinalIgnoreCase) -or
                                $_.ExecutablePath.StartsWith($llamaDir, [StringComparison]::OrdinalIgnoreCase)) } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

    # shortcuts, start at sign-in, the installed-apps entry, the PATH entry
    foreach ($folder in [Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop')) {
        if ($folder) { Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $folder 'Pumpkin.lnk') }
    }
    Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'Pumpkin' -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Pumpkin'
    $bin = Join-Path $Dir 'bin'
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($userPath) {
        $kept = ($userPath -split ';' | Where-Object { $_ -and $_ -ne $bin }) -join ';'
        [Environment]::SetEnvironmentVariable('Path', $kept, 'User')
    }

    if ($env:PUMPKIN_PURGE -eq '1') {
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $cfg
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $env:LOCALAPPDATA 'pumpkin')
    }

    # the program folder last; this script lives in it, which PowerShell allows once it is loaded
    Set-Location $env:USERPROFILE
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $Dir

    Write-Host 'Pumpkin is removed.' -ForegroundColor Green
    Write-Host "  Your models and files are still in $(Join-Path $env:USERPROFILE 'Pumpkin'). Delete that folder if you do not need them."
    if ($env:PUMPKIN_PURGE -ne '1') { Write-Host "  Settings are in $cfg." }
}
