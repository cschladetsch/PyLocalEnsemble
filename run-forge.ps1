# Navigate to the Forge directory
cd C:\Users\chris\local\repos\PyAlice-private\server\stable-diffusion-webui-forge

# Write clean run.ps1 using single-quoted here-string (no expansion bugs)
@'
$ErrorActionPreference = "Stop"

$SharedModels = "$env:USERPROFILE\.models\shared"
$VenvPython   = "$PSScriptRoot\venv\Scripts\python.exe"

$env:COMMANDLINE_ARGS = "--ckpt-dir `"$SharedModels`" --vae-dir `"$SharedModels\VAE`" --lora-dir `"$SharedModels\Lora`" --controlnet-dir `"$SharedModels\ControlNet`""
$env:PYTHON = $VenvPython

Set-Location $PSScriptRoot

& $VenvPython launch.py
'@ | Out-File -FilePath "run.ps1" -Encoding utf8

# Launch Forge
.\run.ps1
