param([string]$OutputDirectory = "$PSScriptRoot/../../desktop/.data/qt-archives", [string]$DownloadMirror = 'https://mirrors.tuna.tsinghua.edu.cn/qt')
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$directory = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $directory | Out-Null
$base = 'https://download.qt.io/online/qtsdkrepository/linux_x64/desktop/qt6_6111/qt6_6111'
$cache = Join-Path $directory 'Updates.xml'
if(Test-Path -LiteralPath $cache){[xml]$metadata=Get-Content -LiteralPath $cache -Raw}
else{[xml]$metadata = (Invoke-WebRequest "$base/Updates.xml" -TimeoutSec 30 -MaximumRetryCount 2 -RetryIntervalSec 2).Content}
$modules = @('qtgraphs','qtlocation','qtpositioning','qtspeech','qtmultimedia','qtserialport','qtimageformats','qtshadertools','qtconnectivity','qtquick3d','qtsensors','qtscxml','qtwebsockets','qthttpserver','qttasktree','qtlanguageserver','qtquicktimeline')
$names = @('qt.qt6.6111.linux_gcc_64') + @($modules | ForEach-Object { "qt.qt6.6111.addons.$_.linux_gcc_64" })
$packages = @($metadata.Updates.PackageUpdate | Where-Object { $_.Name -in $names })
if($packages.Count -ne $names.Count){ throw 'Official Qt package metadata is incomplete' }
$archives = @($packages | ForEach-Object {
  $package = $_
  foreach($archive in $package.DownloadableArchives.Split(',')) {
    $name = $archive.Trim()
    if($name -notmatch '^[A-Za-z0-9._-]+\.7z$'){ throw 'Invalid Qt archive name' }
    $official="$base/$($package.Name)/$($package.Version)$name"
    [pscustomobject]@{Name=$name;Url=$official;DownloadUrl=$official.Replace('https://download.qt.io',$DownloadMirror);File=[IO.Path]::Combine($directory,$name)}
  }
})
$results = @($archives | ForEach-Object -ThrottleLimit 4 -Parallel {
  $ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; $archive=$_
  $checksum = (Invoke-WebRequest ($archive.DownloadUrl + '.sha1') -TimeoutSec 30 -MaximumRetryCount 2 -RetryIntervalSec 2).Content
  if($checksum -is [byte[]]){$checksum=[Text.Encoding]::UTF8.GetString($checksum)}
  $expected=[regex]::Match([string]$checksum,'\b[a-fA-F0-9]{40}\b').Value.ToLower()
  if(-not $expected){throw "Missing checksum: $($archive.Name)"}
  $baseCache=Join-Path $using:directory 'qtbase.7z'
  if($archive.Name -like 'qtbase-*' -and (Test-Path -LiteralPath $baseCache) -and -not(Test-Path -LiteralPath $archive.File)) { Copy-Item -LiteralPath $baseCache -Destination $archive.File }
  if(-not(Test-Path -LiteralPath $archive.File) -or (Get-FileHash -LiteralPath $archive.File -Algorithm SHA1).Hash.ToLower() -ne $expected){
    Invoke-WebRequest $archive.DownloadUrl -OutFile ($archive.File + '.part') -TimeoutSec 180 -MaximumRetryCount 2 -RetryIntervalSec 2
    if((Get-FileHash -LiteralPath ($archive.File + '.part') -Algorithm SHA1).Hash.ToLower() -ne $expected){throw "Checksum mismatch: $($archive.Name)"}
    Move-Item -LiteralPath ($archive.File + '.part') -Destination $archive.File -Force
  }
  [pscustomobject]@{Name=$archive.Name;Url=$archive.Url;File=$archive.File;Sha1=$expected}
})
if($results.Count -ne $archives.Count){throw 'One or more Qt archives did not download'}
$results | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $directory 'manifest.json') -Encoding utf8
Write-Output "Verified $($results.Count) official Qt archives."
