# Pumpkin installer for Windows 10 and 11. Run in PowerShell:
#
#   irm https://raw.githubusercontent.com/geobellingham/pumpkin/main/install.ps1 | iex
#
# It downloads the latest release (Pumpkin with its own Python, nothing else needed), checks its SHA-256
# against the one GitHub publishes, installs it for your user only (no admin rights), and adds Pumpkin to
# the Start menu, the desktop, and Apps > Installed apps. Running it again updates Pumpkin; your settings,
# models and files stay.
#
# Options, as environment variables set before running it:
#   $env:PUMPKIN_TAG = 'v0.5.0'     a specific release instead of the latest
#   $env:PUMPKIN_NO_OPEN = '1'      do not open Pumpkin afterwards
#   $env:PUMPKIN_START = '1'        start it in the background afterwards (the Update button uses this)
#   $env:PUMPKIN_ZIP = 'C:\x.zip'   install from a local package (for testing; no download, no checksum)

& {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'          # Invoke-WebRequest is very slow with its progress bar
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072   # TLS 1.2

    $Repo = if ($env:PUMPKIN_REPO) { $env:PUMPKIN_REPO } else { 'geobellingham/pumpkin' }
    $Dir = Join-Path $env:LOCALAPPDATA 'Programs\Pumpkin'
    $Arch = 'x64'                                     # Windows on ARM runs the x64 build too
    $Headers = @{ 'User-Agent' = 'pumpkin-installer'; 'Accept' = 'application/vnd.github+json' }

    function Say($m) { Write-Host "  $m" }

    Write-Host ''
    Write-Host 'Pumpkin installer' -ForegroundColor DarkYellow

    if ([Environment]::OSVersion.Version.Major -lt 10) { throw 'Pumpkin needs Windows 10 or 11.' }
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'Pumpkin needs 64-bit Windows.' }

    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("pumpkin-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp | Out-Null
    try {
        # ---- the package
        if ($env:PUMPKIN_ZIP) {
            $zip = $env:PUMPKIN_ZIP
            Say "Installing from $zip"
        } else {
            $releases = @()
            if ($env:PUMPKIN_TAG) {
                $releases = @(Invoke-RestMethod -Headers $Headers "https://api.github.com/repos/$Repo/releases/tags/$($env:PUMPKIN_TAG)")
            } else {
                # the newest release that has the Windows package (right after a release is made it is still building)
                $releases = @(Invoke-RestMethod -Headers $Headers "https://api.github.com/repos/$Repo/releases?per_page=6") |
                    Where-Object { -not $_.draft -and -not $_.prerelease }
            }
            $asset = $null; $rel = $null
            foreach ($r in $releases) {
                $v = $r.tag_name.TrimStart('v')
                $a = $r.assets | Where-Object { $_.name -eq "pumpkin-$v-windows-$Arch.zip" } | Select-Object -First 1
                if ($a) { $asset = $a; $rel = $r; break }
            }
            if (-not $asset) { throw 'No Windows package found in the latest releases. Try again in a few minutes.' }
            Say "Downloading Pumpkin $($rel.tag_name)"
            $zip = Join-Path $tmp $asset.name
            Invoke-WebRequest -UseBasicParsing -Headers @{ 'User-Agent' = 'pumpkin-installer' } -Uri $asset.browser_download_url -OutFile $zip
            if ($asset.digest -and $asset.digest.StartsWith('sha256:')) {
                $want = $asset.digest.Substring(7).ToLower()
                $got = (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower()
                if ($got -ne $want) { throw "The download did not match its published checksum, so nothing was installed." }
                Say 'Checksum verified'
            } else {
                throw 'GitHub did not publish a checksum for this package, so Pumpkin will not install it.'
            }
        }

        # ---- stop a running Pumpkin (Windows cannot replace programs that are running)
        $cfg = Join-Path $env:APPDATA 'pumpkin'
        $port = 8090
        try {
            $s = Get-Content -Raw (Join-Path $cfg 'settings.json') | ConvertFrom-Json
            if ($s.port) { $port = [int]$s.port }
        } catch { }
        $tokenFile = Join-Path $cfg 'token'
        if (Test-Path $tokenFile) {
            try {
                $tok = (Get-Content -Raw $tokenFile).Trim()
                Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port/pumpkin/api/quit" -TimeoutSec 5 `
                    -Headers @{ 'Authorization' = "Bearer $tok"; 'X-Pumpkin' = '1'; 'Content-Type' = 'application/json' } `
                    -Body '{}' | Out-Null
                Say 'Stopped the running Pumpkin'
                Start-Sleep -Seconds 2
            } catch { }
        }
        # anything still running from Pumpkin's folders (Win32_Process lists paths without needing admin rights)
        $llamaDir = Join-Path $env:LOCALAPPDATA 'pumpkin\llama'
        $mine = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $_.ExecutablePath -and ($_.ExecutablePath.StartsWith($Dir, [StringComparison]::OrdinalIgnoreCase) -or
                                    $_.ExecutablePath.StartsWith($llamaDir, [StringComparison]::OrdinalIgnoreCase)) })
        foreach ($p in $mine) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
        if ($mine.Count) { Start-Sleep -Seconds 1 }

        # ---- unpack and put in place (python and app are replaced; nothing of yours lives here)
        Say "Installing to $Dir"
        $unz = Join-Path $tmp 'unzipped'
        Expand-Archive -Path $zip -DestinationPath $unz -Force
        $src = Join-Path $unz 'Pumpkin'
        if (-not (Test-Path (Join-Path $src 'python\python.exe'))) { throw 'The package is damaged (no python.exe).' }
        New-Item -ItemType Directory -Force -Path $Dir | Out-Null
        foreach ($sub in 'python', 'app', 'bin') {
            $p = Join-Path $Dir $sub
            if (Test-Path $p) { Remove-Item -Recurse -Force $p }
        }
        Copy-Item -Recurse -Force (Join-Path $src '*') $Dir

        $pyw = Join-Path $Dir 'python\pythonw.exe'
        $py = Join-Path $Dir 'python\python.exe'
        $ver = (& $py -X utf8 -c "import pumpkin; print(pumpkin.__version__)").Trim()
        if (-not $ver) { throw 'Pumpkin did not start after installing.' }

        # ---- shortcuts, the pumpkin command, and Apps > Installed apps
        $ico = Join-Path $Dir 'pumpkin.ico'
        $wsh = New-Object -ComObject WScript.Shell
        $places = @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop'))
        foreach ($folder in $places) {
            if (-not $folder) { continue }
            $lnk = $wsh.CreateShortcut((Join-Path $folder 'Pumpkin.lnk'))
            $lnk.TargetPath = $pyw
            $lnk.Arguments = '-X utf8 -m pumpkin open'
            $lnk.WorkingDirectory = $Dir
            $lnk.IconLocation = "$ico,0"
            $lnk.Description = 'Run AI models on your computer'
            $lnk.Save()
        }
        $bin = Join-Path $Dir 'bin'
        $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        if (-not $userPath) { $userPath = '' }
        if (-not (($userPath -split ';') -contains $bin)) {
            [Environment]::SetEnvironmentVariable('Path', ($userPath.TrimEnd(';') + ";$bin").TrimStart(';'), 'User')
        }
        $un = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Pumpkin'
        New-Item -Force -Path $un | Out-Null
        $uninstall = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $Dir 'uninstall.ps1')`""
        $vals = @{ DisplayName = 'Pumpkin'; DisplayVersion = $ver; Publisher = 'Pumpkin (open source)';
                   DisplayIcon = $ico; InstallLocation = $Dir; UninstallString = $uninstall;
                   URLInfoAbout = "https://github.com/$Repo"; NoModify = 1; NoRepair = 1 }
        foreach ($k in $vals.Keys) {
            $type = if ($vals[$k] -is [int]) { 'DWord' } else { 'String' }
            New-ItemProperty -Force -Path $un -Name $k -Value $vals[$k] -PropertyType $type | Out-Null
        }

        Write-Host ''
        Write-Host "Pumpkin $ver is installed." -ForegroundColor Green
        Say 'Open it from the Start menu or the desktop. In a new terminal, the pumpkin command works too.'

        if ($env:PUMPKIN_START -eq '1') {
            & $py -X utf8 -m pumpkin up | Out-Null
        } elseif ($env:PUMPKIN_NO_OPEN -ne '1') {
            Start-Process -FilePath $pyw -ArgumentList '-X', 'utf8', '-m', 'pumpkin', 'open' -WorkingDirectory $Dir
        }
    } finally {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
}
