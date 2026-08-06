#Requires -Version 7
$ErrorActionPreference = 'Stop'

$modelDir = Join-Path $PSScriptRoot '..\models\bge-small-en-v1.5'
New-Item -ItemType Directory -Force -Path $modelDir | Out-Null

$base = 'https://huggingface.co/BAAI/bge-small-en-v1.5/resolve/main'
$files = @{
    'onnx/model.onnx' = 'model.onnx'
    'vocab.txt'       = 'vocab.txt'
    'tokenizer.json'  = 'tokenizer.json'
    'config.json'     = 'config.json'
}

foreach ($remote in $files.Keys) {
    $target = Join-Path $modelDir $files[$remote]
    if (Test-Path $target) {
        Write-Host "already present: $($files[$remote])"
        continue
    }
    Write-Host "downloading $remote"
    Invoke-WebRequest -Uri "$base/$remote" -OutFile $target
}

$size = (Get-Item (Join-Path $modelDir 'model.onnx')).Length
if ($size -lt 50MB) {
    throw "model.onnx is only $size bytes - the download almost certainly returned an error page."
}
Write-Host "model ready in $modelDir ($([math]::Round($size / 1MB)) MB)"
