Set-Location $PSScriptRoot
$py = $null
if (Get-Command python -ErrorAction SilentlyContinue) { $py = "python" }
elseif (Get-Command py -ErrorAction SilentlyContinue) { $py = "py", "-3" }
else {
    Write-Error "未找到 Python。请安装 Python 3.10+ 并加入 PATH。"
    exit 1
}
& @py -c "import PIL" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "正在安装 Pillow ..."
    & @py -m pip install -r requirements.txt
}
& @py annotate.py @args
exit $LASTEXITCODE
