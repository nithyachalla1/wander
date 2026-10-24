# Tiny static file server for Wander (no Python/Node needed).
# Usage:  powershell -ExecutionPolicy Bypass -File serve.ps1 [-Port 8000]
param([int]$Port = 8000)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("http://localhost:$Port/")
$listener.Start()
Write-Host "Wander is live at http://localhost:$Port/  (Ctrl+C to stop)"

$types = @{
  '.html' = 'text/html; charset=utf-8'; '.js' = 'text/javascript; charset=utf-8'
  '.mjs'  = 'text/javascript; charset=utf-8'; '.css' = 'text/css; charset=utf-8'
  '.json' = 'application/json'; '.png' = 'image/png'; '.svg' = 'image/svg+xml'
  '.ico'  = 'image/x-icon'; '.wasm' = 'application/wasm'
}

while ($listener.IsListening) {
  $ctx = $listener.GetContext()
  $path = [Uri]::UnescapeDataString($ctx.Request.Url.AbsolutePath)
  if ($path -eq '/') { $path = '/index.html' }
  $file = [IO.Path]::GetFullPath((Join-Path $root $path.TrimStart('/')))
  $res = $ctx.Response
  try {
    if ($file.StartsWith($root) -and (Test-Path $file -PathType Leaf)) {
      $bytes = [IO.File]::ReadAllBytes($file)
      $ext = [IO.Path]::GetExtension($file).ToLower()
      $res.ContentType = if ($types.ContainsKey($ext)) { $types[$ext] } else { 'application/octet-stream' }
      $res.Headers.Add('Cache-Control', 'no-store')
      $res.OutputStream.Write($bytes, 0, $bytes.Length)
    } else {
      $res.StatusCode = 404
    }
  } catch { $res.StatusCode = 500 }
  $res.Close()
}
