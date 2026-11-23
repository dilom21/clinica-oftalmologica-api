# Aplicar desde la raíz del backend en la rama dev-josias.
# No elimina archivos ni ejecuta migraciones, git commit ni git push.
$ErrorActionPreference = 'Stop'
$repo = (Get-Location).Path
if (-not (Test-Path (Join-Path $repo 'app/main.py'))) { throw 'Ejecuta este script en la raíz del backend (app/main.py no existe).' }
$rama = (git branch --show-current).Trim()
if ($rama -ne 'dev-josias') { throw "Rama actual '$rama'; se exige dev-josias." }
$src = Join-Path $PSScriptRoot 'app/modules/gestion_inventario_proveedores'
$dest = Join-Path $repo 'app/modules/gestion_inventario_proveedores'
$archivos = @('models/catalogo.py','schemas/catalogo.py','services/catalogo.py','api/router.py')
foreach ($rel in $archivos) {
  $origen = Join-Path $src $rel
  $destino = Join-Path $dest $rel
  $carpeta = Split-Path $destino -Parent
  New-Item -ItemType Directory -Path $carpeta -Force | Out-Null
  if (Test-Path $destino) {
    $respaldo = "$destino.backup-cu26"
    if (-not (Test-Path $respaldo)) { Copy-Item $destino $respaldo }
  }
  Copy-Item $origen $destino -Force
  Write-Host "Copiado: $rel"
}
$main = Join-Path $repo 'app/main.py'
$contenido = [System.IO.File]::ReadAllText($main)
$importacion = 'from app.modules.gestion_inventario_proveedores.api.router import router as inventario_router'
$inclusion = 'app.include_router(inventario_router)'
if (-not $contenido.Contains($importacion)) {
  $contenido = $importacion + "`r`n" + $contenido
}
if (-not $contenido.Contains($inclusion)) {
  $ancla = 'app.include_router(saas_router)'
  if (-not $contenido.Contains($ancla)) { throw 'No se encontró app.include_router(saas_router) en app/main.py. Requiere integración manual.' }
  $contenido = $contenido.Replace($ancla, "$ancla`r`n$inclusion")
}
[System.IO.File]::WriteAllText($main, $contenido, [System.Text.UTF8Encoding]::new($false))
Write-Host 'CU26 instalado. No se ha modificado ninguna base de datos.'
Write-Host 'Verifica: python -m compileall -q app/modules/gestion_inventario_proveedores'
Write-Host 'Ejecuta: python -m pytest -q tests/test_cu26_backend.py (si pytest está instalado)'
