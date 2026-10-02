# Arranca el Estudio en Windows, en esta maquina y solo para esta maquina.
#
#     powershell -NoProfile -ExecutionPolicy Bypass -File arrancar.ps1
#     powershell -NoProfile -ExecutionPolicy Bypass -File arrancar.ps1 -Puerto 8030 -SinNavegador
#
# instalar.sh es solo para Ubuntu; esto es su equivalente en local: fija las
# variables de entorno (README.md, «Donde viven los datos») y lanza app.py.
#
# LOS DATOS VAN FUERA DEL CODIGO, en E:\ASVideoStudio\datos (o -Datos). Asi
# actualizar o volver a clonar la app no toca ni un video ni una clave, y las
# claves no pueden acabar en un commit por despiste.
#
# SOLO 127.0.0.1, y no es configurable a proposito: la API no autentica nada
# (README.md, «Quien esta dentro»). Escuchar en la red seria darle el estudio,
# y las claves que gasta, a cualquiera de la wifi.

param(
  [int]$Puerto = 8020,
  [string]$Datos = "E:\ASVideoStudio\datos",
  [string]$Python = "E:\ASVideoStudio\venv\Scripts\python.exe",
  [int]$Lotes = 6,
  [switch]$SinNavegador
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if (-not (Test-Path $Python)) {
  Write-Output "no encuentro python en $Python"
  Write-Output "crea el entorno:  python -m venv E:\ASVideoStudio\venv"
  Write-Output "y sus librerias:  E:\ASVideoStudio\venv\Scripts\python.exe -m pip install -r requirements.txt"
  exit 2
}
foreach ($exe in 'ffmpeg', 'ffprobe') {
  if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) {
    Write-Output "AVISO  falta $exe en el PATH: la voz, la musica y el MP4 no funcionaran"
  }
}

$Temporal = Join-Path (Split-Path $Datos -Parent) "temp"
$Modelos  = Join-Path (Split-Path $Datos -Parent) "modelos"
foreach ($d in @($Datos, "$Datos\proyectos", "$Datos\banco", "$Datos\banco\presets", "$Datos\secretos", $Temporal, $Modelos)) {
  New-Item -ItemType Directory -Force $d | Out-Null
}

# LOS TEMPORALES TAMBIEN EN E:. Python (tempfile), los Edge headless del render
# y el CLI de Claude sacan su carpeta de trabajo de TEMP/TMP; sin esto irian a
# C:\Users\...\AppData\Local\Temp. Los hijos heredan estas variables.
$env:TEMP = $Temporal
$env:TMP  = $Temporal
# Y los modelos que descarga el alineador de voz (Whisper, wav2vec2): ~1-2 GB que
# sin esto irian a C:\Users\...\.cache.
$env:ESTUDIO_MODELOS = $Modelos
$env:HF_HOME         = "$Modelos\huggingface"
$env:TORCH_HOME      = "$Modelos\torch"

$env:ESTUDIO_PROYECTOS       = "$Datos\proyectos"
$env:ESTUDIO_PRESETS         = "$Datos\presets.json"
$env:ESTUDIO_BANCO           = "$Datos\banco"
$env:ESTUDIO_BANCO_PRESETS   = "$Datos\banco\presets"
$env:ESTUDIO_SECRETOS        = "$Datos\secretos"
$env:ESTUDIO_AJUSTES         = "$Datos\ajustes.json"
$env:ESTUDIO_RECETAS         = "$Datos\recetas.json"
$env:ESTUDIO_ESTADISTICAS    = "$Datos\estadisticas.json"
$env:ESTUDIO_COSTE_GLOBAL    = "$Datos\coste_global.jsonl"
$env:ESTUDIO_BITACORA_GLOBAL = "$Datos\bitacora_global.jsonl"
# 6 procesos de render: con 16 GB de RAM, mas satura la maquina (CLAUDE.md
# tiene la tabla medida en 8 vCPU; aqui se vuelve a medir en la fase 4).
$env:ESTUDIO_LOTES           = "$Lotes"
# Las fuentes de Windows: las mismas con las que el autor midio.
$env:ESTUDIO_FUENTES         = "C:\Windows\Fonts"
# Por si quedo puesta de una tanda de pruebas: aqui se genera de verdad.
Remove-Item Env:ESTUDIO_SIMULAR -ErrorAction SilentlyContinue

$url = "http://127.0.0.1:$Puerto/"
Write-Output "AS Video Studio en $url   (datos en $Datos, $Lotes procesos de render)"
Write-Output "Ctrl+C para pararlo."
if (-not $SinNavegador) {
  # Se abre un poco despues, cuando el servicio ya contesta.
  Start-Job -ScriptBlock { param($u) Start-Sleep -Seconds 3; Start-Process $u } -ArgumentList $url | Out-Null
}

& $Python app.py --host 127.0.0.1 --puerto $Puerto
