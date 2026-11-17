param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_]+_(test|testing)$')]
    [string]$Database,

    [string]$HostName = 'localhost',

    [string]$Psql = 'psql'
)

$ErrorActionPreference = 'Stop'

if ($HostName -match '(?i)supabase\.(co|com)|pooler\.supabase') {
    throw 'CU31: se rechazo un host Supabase; este ensayo solo admite PostgreSQL de pruebas.'
}

$migrationPath = Resolve-Path (
    Join-Path $PSScriptRoot '..\..\db\cu31_pagos_administrativos_resend.sql'
)
$migrationSql = Get-Content -Raw -LiteralPath $migrationPath
$commitRegex = [regex]::new('(?im)^COMMIT;\s*$')
$commitMatches = $commitRegex.Matches($migrationSql)
if ($commitMatches.Count -ne 1) {
    throw "CU31: se esperaba exactamente un COMMIT final; encontrados: $($commitMatches.Count)."
}

$actualDatabase = (& $Psql -X --host $HostName --dbname $Database --tuples-only --no-align `
    --set ON_ERROR_STOP=1 --command 'SELECT current_database();').Trim()
if ($LASTEXITCODE -ne 0 -or $actualDatabase -ne $Database) {
    throw "CU31: no se pudo confirmar la base de pruebas solicitada ($Database)."
}

# Las postcondiciones y SELECT de verificacion del archivo original se ejecutan
# dentro de la misma transaccion. Solo el terminador final cambia en memoria.
$rollbackSql = $commitRegex.Replace($migrationSql, 'ROLLBACK;', 1)

$rollbackSql | & $Psql -X --host $HostName --dbname $Database `
    --set ON_ERROR_STOP=1 --file -
if ($LASTEXITCODE -ne 0) {
    throw 'CU31: el ensayo transaccional fallo; no aplique la migracion.'
}

Write-Host "CU31: validaciones superadas y cambios revertidos en $Database."
