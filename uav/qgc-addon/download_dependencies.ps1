param([string]$Root = "$PSScriptRoot/../../desktop/.data/qgc-deps")
$ErrorActionPreference='Stop'
$directory=[IO.Path]::GetFullPath($Root)
New-Item -ItemType Directory -Force -Path $directory | Out-Null
$manifest=Get-Content -LiteralPath (Join-Path $directory 'manifest.json') -Raw | ConvertFrom-Json
$results=@($manifest | ForEach-Object -ThrottleLimit 3 -Parallel {
  $ErrorActionPreference='Stop'; $package=$_
  function Invoke-DependencyGit([string[]]$GitArguments) {
    $info=[Diagnostics.ProcessStartInfo]::new((Get-Command git -CommandType Application | Select-Object -First 1).Source)
    $info.UseShellExecute=$false; $info.CreateNoWindow=$true; $info.RedirectStandardOutput=$true; $info.RedirectStandardError=$true
    foreach($argument in $GitArguments){$info.ArgumentList.Add($argument)}
    $process=[Diagnostics.Process]::Start($info)
    $output=$process.StandardOutput.ReadToEndAsync(); $errors=$process.StandardError.ReadToEndAsync()
    $process.WaitForExit(); $text=$output.GetAwaiter().GetResult(); $errorText=$errors.GetAwaiter().GetResult()
    if($process.ExitCode -ne 0){throw "Dependency git failed: $errorText"}
    return $text.Trim()
  }
  if($package.Name -notmatch '^[A-Za-z0-9_.-]+$' -or $package.Repository -notmatch '^https://github.com/[\w.-]+/[\w.-]+\.git$'){throw 'Invalid dependency manifest'}
  $target=Join-Path $using:directory $package.Name
  if(-not(Test-Path -LiteralPath (Join-Path $target '.git'))){
    if($package.Ref -match '^[a-f0-9]{40}$'){
      $null=Invoke-DependencyGit @('init','-q',$target)
      $null=Invoke-DependencyGit @('-C',$target,'remote','add','origin',$package.Repository)
      $null=Invoke-DependencyGit @('-C',$target,'fetch','-q','--depth','1','origin',$package.Ref)
      $null=Invoke-DependencyGit @('-C',$target,'checkout','-q','--detach','FETCH_HEAD')
    }else{
      $null=Invoke-DependencyGit @('clone','-q','--depth','1','--branch',$package.Ref,$package.Repository,$target)
    }
  }
  $commit=Invoke-DependencyGit @('-C',$target,'rev-parse','HEAD')
  [pscustomobject]@{Name=$package.Name;Repository=$package.Repository;Ref=$package.Ref;Commit=$commit}
})
if($results.Count -ne @($manifest).Count){throw 'Some dependency downloads failed'}
$results | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $directory 'resolved.json') -Encoding utf8
Write-Output "Downloaded $($results.Count) pinned QGC dependencies."
