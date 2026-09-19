param(
  [Parameter(Mandatory = $true)]
  [string]$Version,
  [string]$OutputDir = ".\release"
)

$ErrorActionPreference = "Stop"
$releaseDir = Join-Path $OutputDir $Version
$imageDir = Join-Path $releaseDir "images"
New-Item -ItemType Directory -Force $imageDir | Out-Null

$images = @(
  @{ Name = "zhiyun-api:$Version"; Dockerfile = "apps/api/Dockerfile"; Context = "apps/api"; File = "api" },
  @{ Name = "zhiyun-worker:$Version"; Dockerfile = "apps/api/Dockerfile.worker"; Context = "apps/api"; File = "worker" },
  @{ Name = "zhiyun-web:$Version"; Dockerfile = "apps/web/Dockerfile"; Context = "apps/web"; File = "web" }
)
$imageIds = @{}

# 1) build linux/amd64 images locally; versioned tags only (no latest)
foreach ($item in $images) {
  $buildOk = $false
  for ($attempt = 1; $attempt -le 3 -and -not $buildOk; $attempt++) {
    docker buildx build --platform linux/amd64 --file $item.Dockerfile --tag $item.Name --load $item.Context
    if ($LASTEXITCODE -eq 0) { $buildOk = $true }
    else { Write-Host "retry $attempt for $($item.Name)"; Start-Sleep -Seconds 5 }
  }
  if (-not $buildOk) { throw "build failed: $($item.Name)" }
  docker save --output (Join-Path $imageDir "$($item.File).tar") $item.Name
  if ($LASTEXITCODE -ne 0) { throw "docker save failed: $($item.Name)" }
  if (-not (Test-Path (Join-Path $imageDir "$($item.File).tar"))) { throw "docker save produced no archive: $($item.File)" }
  $imageIds[$item.File] = (docker image inspect $item.Name --format '{{.Id}}').Trim()
  if ($LASTEXITCODE -ne 0 -or -not $imageIds[$item.File].StartsWith("sha256:")) { throw "cannot inspect image digest: $($item.Name)" }
  # gzip 直接压缩 docker save tar（不带外层 tar 包装，保证 gzip -dc | docker load 可用）
  $tarPath = Join-Path $imageDir "$($item.File).tar"
  $gzPath = Join-Path $imageDir "$($item.File).tar.gz"
  $inputStream = [System.IO.File]::OpenRead($tarPath)
  $outputStream = [System.IO.File]::Create($gzPath)
  $gzipStream = [System.IO.Compression.GZipStream]::new($outputStream, [System.IO.Compression.CompressionLevel]::Optimal)
  $inputStream.CopyTo($gzipStream)
  $gzipStream.Dispose()
  $outputStream.Dispose()
  $inputStream.Dispose()
  Remove-Item $tarPath -Force
}

# 2) deploy manifests and docs
Copy-Item "deploy/compose/compose.lite.yml" $releaseDir
Copy-Item ".env.example" $releaseDir
$envTemplate = Join-Path $releaseDir ".env.example"
(Get-Content $envTemplate) -replace "^IMAGE_VERSION=.*$", "IMAGE_VERSION=$Version" | Set-Content $envTemplate
Copy-Item "scripts/deploy.sh" $releaseDir
Copy-Item "scripts/rollback.sh" $releaseDir
New-Item -ItemType Directory -Force (Join-Path $releaseDir "docs/implementation/zhiyun") | Out-Null
Copy-Item "docs/implementation/zhiyun/*.md" (Join-Path $releaseDir "docs/implementation/zhiyun/")
Copy-Item "docs/operations/runbook.md" (Join-Path $releaseDir "docs/")

# 3) release-manifest.json: version, build time, platform, image tags/digests, db revision
$previousVersions = @(
  Get-ChildItem -Path $OutputDir -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -ne $Version } |
    Sort-Object Name -Descending |
    Select-Object -First 3 -ExpandProperty Name
)
$manifest = @{
  version = $Version
  built_at = (Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ")
  platform = "linux/amd64"
  images = @{
    api = "zhiyun-api:$Version"
    worker = "zhiyun-worker:$Version"
    web = "zhiyun-web:$Version"
  }
  image_digests = @{
    api = $imageIds.api
    worker = $imageIds.worker
    web = $imageIds.web
  }
  database_revision = "0012_model_run_org"
  previous_compatible_versions = $previousVersions
  docs_version = "2026-09-14"
} | ConvertTo-Json -Depth 4
Set-Content -Path (Join-Path $releaseDir "release-manifest.json") -Value $manifest

# 4) SHA256SUMS generated last; POSIX relative paths; excludes itself
$files = Get-ChildItem -Recurse -File $releaseDir | Where-Object { $_.Name -ne "SHA256SUMS" }
$lines = foreach ($file in $files) {
  $hash = (Get-FileHash -Algorithm SHA256 $file.FullName).Hash.ToLower()
  $relative = $file.FullName.Substring((Resolve-Path $releaseDir).Path.Length + 1).Replace("\", "/")
  "$hash  $relative"
}
# LF 行尾（Linux sha256sum -c 兼容），不以空行结尾
[System.IO.File]::WriteAllText(
  (Join-Path $releaseDir "SHA256SUMS"),
  (($lines -join "`n") + "`n"),
  [System.Text.UTF8Encoding]::new($false)
)

Write-Host "Release created at $releaseDir"
Write-Host "Verify: cd $releaseDir && sha256sum -c SHA256SUMS (Linux)"
