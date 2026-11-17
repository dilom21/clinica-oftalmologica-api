# Local non-destructive smoke: never uses shared Supabase or real Resend.
# Requires Docker Desktop (Linux containers), Docker Compose V2 and PowerShell.
param([int]$HttpPort = 8088, [int]$HttpsPort = 8443)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
Push-Location $repo
try {
    $smokeEnv = Join-Path $repo '.env.docker-smoke'
    if (Test-Path $smokeEnv) {
        throw '.env.docker-smoke ya existe. Revisarlo manualmente antes de sobrescribirlo.'
    }
    $random = [Guid]::NewGuid().ToString('N') + [Guid]::NewGuid().ToString('N')
    @"
APP_ENV=local
DATABASE_URL=postgresql+psycopg://smoke:smoke@127.0.0.1:5432/smoke?sslmode=disable
JWT_SECRET_KEY=$random
JWT_ALGORITHM=HS256
PASSWORD_RESET_URL_BASE=http://localhost:4200/password/restablecer
GMAIL_CLIENT_ID=smoke
GMAIL_CLIENT_SECRET=smoke
GMAIL_REFRESH_TOKEN=smoke
GMAIL_SENDER_EMAIL=smoke@example.test
STRIPE_SECRET_KEY=sk_test_smoke_no_network
STRIPE_WEBHOOK_SECRET=whsec_smoke_no_network
RESEND_API_KEY=
RESEND_FROM_EMAIL=onboarding@resend.dev
RESEND_DEMO_MODE=false
EMAIL_TEST=
SAAS_BACKUP_PRIVATE_DIR=/opt/app/private-backups
SAAS_BACKUP_STORAGE_PROVIDER=local
APP_TIMEZONE=America/La_Paz
NGINX_MODE=http
DUCKDNS_FQDN=localhost
HTTP_PORT=$HttpPort
HTTPS_PORT=$HttpsPort
DOCKER_IMAGE_TAG=smoke
"@ | ForEach-Object { [System.IO.File]::WriteAllText($smokeEnv, $_, (New-Object System.Text.UTF8Encoding($false))) }

    $env:COMPOSE_PROJECT_NAME = 'clinica-sprint3-smoke'
    $env:APP_ENV_FILE = '.env.docker-smoke'
    $env:HTTP_PORT = "$HttpPort"
    $env:HTTPS_PORT = "$HttpsPort"
    $env:NGINX_MODE = 'http'
    $env:DUCKDNS_FQDN = 'localhost'
    $env:DOCKER_IMAGE_TAG = 'smoke'

    docker compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'docker compose config falló' }

    docker compose up --build -d
    if ($LASTEXITCODE -ne 0) { throw 'docker compose up falló' }

    docker compose ps
    if ($LASTEXITCODE -ne 0) { throw 'docker compose ps falló' }

    $url = "http://127.0.0.1:$HttpPort/"
    $respuesta = $null
    for ($intento = 1; $intento -le 15; $intento++) {
        try {
            $respuesta = Invoke-RestMethod -Uri $url -TimeoutSec 5
            break
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    if (-not $respuesta -or -not $respuesta.mensaje) {
        throw "No responde FastAPI a traves de Nginx: $url. Revisar docker compose logs."
    }
    Write-Host "[OK] FastAPI via Nginx: $($respuesta.mensaje)" -ForegroundColor Green

    $spec = Invoke-RestMethod -Uri "http://127.0.0.1:$HttpPort/openapi.json" -TimeoutSec 30
    $rutas = @($spec.paths.PSObject.Properties.Name)
    foreach ($ruta in @('/proveedores/', '/pagos/stripe/webhook', '/pagos/resend/webhook')) {
        if ($rutas -notcontains $ruta) { throw "No existe ruta: $ruta" }
        Write-Host "[OK] $ruta" -ForegroundColor Green
    }

    Write-Host 'Smoke estructural OK. ADVERTENCIA: el worker no puede procesar DB ficticia.' -ForegroundColor Yellow
    Write-Host 'Revisar logs: docker compose logs --tail=80 api worker nginx'
    Write-Host 'Apagar: docker compose down (NO usar -v para conservar backups).' -ForegroundColor Yellow
}
finally {
    Pop-Location
    Remove-Item Env:COMPOSE_PROJECT_NAME -ErrorAction SilentlyContinue
    Remove-Item Env:APP_ENV_FILE -ErrorAction SilentlyContinue
    Remove-Item Env:HTTP_PORT -ErrorAction SilentlyContinue
    Remove-Item Env:HTTPS_PORT -ErrorAction SilentlyContinue
    Remove-Item Env:NGINX_MODE -ErrorAction SilentlyContinue
    Remove-Item Env:DUCKDNS_FQDN -ErrorAction SilentlyContinue
    Remove-Item Env:DOCKER_IMAGE_TAG -ErrorAction SilentlyContinue
}
