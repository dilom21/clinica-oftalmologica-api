# Clínica Oftalmológica - Docker para API + worker + Nginx

**Responsable Docker:** integrante que prepara esta rama. **Responsable AWS:** compañero que recibe el traspaso.
**Base de código revisada:** backend `main` `1d932e1c77` (02/11/2026).
**Alcance según acuerdo:** solo FastAPI, worker de Resend y Nginx en Docker Compose. Angular permanece en Vercel; Flutter es cliente externo; no se crea un PostgreSQL ni se modifican los siete tenants existentes en Supabase.

> Este paquete es una entrega **pendiente de smoke test Docker real**. La validación estática NO equivale a verificar imágenes, conexiones Supabase ni Resend. No publicar en `main` hasta ejecutar los controles de aceptación.

## 1. Archivos y arquitectura

- `Dockerfile`: imagen Python 3.12 sin `--reload` y con clientes PostgreSQL 18 (`pg_dump`, `pg_restore`, `psql`); proceso sin privilegios de root.
- `docker-compose.yml`: `api` (puerto 8000 solo interno), `worker` (un único proceso, sin puertos), `nginx` (80 y 443 públicos), restart policies y red de aplicación. API/worker usan la misma imagen y las mismas variables de backend, salvo las propias de Nginx.
- `.dockerignore`: impide que credenciales, llaves TLS, archivos locales o `.git` entren al build context.
- `nginx/configs/http.conf.template`: configuración inicial para bootstrap y desafío ACME (solo `GET /` y `/openapi.json`; el resto de la API retorna 403 hasta HTTPS).
- `nginx/configs/start.sh`: arranque validado y sustitución controlada de `DUCKDNS_FQDN` en el template elegido.
- `nginx/configs/https.conf.template`: HTTPS una vez emitido el certificado. Los webhooks `POST /pagos/stripe/webhook` y `POST /pagos/resend/webhook` pasan a FastAPI sin modificar URL ni cuerpo.
- `.env.example`: variables documentadas sin secretos válidos.
- `scripts/docker/smoke.ps1`: validación local segura con configuración ficticia.
- `patches/requirements.txt.patch` (solo en el paquete, no se copia al repositorio): agrega `httpx==0.28.1`, dependencia real de Resend omitida en el archivo base.
- `REPORTE-TRASPASO-AWS-BORRADOR.md`: lista de evidencias y pendientes para completar después del smoke en Docker Desktop.

La infraestructura esperada es:

```
Angular (Vercel) / Flutter
          |
       HTTPS 443
          |
      AWS EC2
          |
 Nginx (80/443 publicados)
          |
 api:8000 (solo red Compose) -- Supabase control + 7 tenants
          |
 worker (proceso separado) ----- Resend
          |
 Stripe test / webhooks firmados (HTTPS en Nginx)
```

## 2. Aplicar este paquete sin alterar `main`

1. En `C:\SI2_Proyecto\clinica-oftalmologica-api`, comprobar `git status --short` vacío y `git switch main && git pull --ff-only origin main`.
2. Crear rama `git switch -c infra/docker-aws-sprint3-20261102`. No trabajar sobre `main`.
3. Usar `aplicar-paquete.ps1` incluido en el ZIP, desde la carpeta descomprimida, con `-RepoPath` apuntando al repositorio. Ver `INSTRUCCIONES-PRIMER-PASO.md`. El script copia archivos nuevos, agrega reglas al `.gitignore` existente y aplica el patch a `requirements.txt`, sin realizar commit ni push. **No ejecutarlo en `main`.**
4. Verificar que `requirements.txt` contenga `httpx==0.28.1`, y que no se hayan modificado otros archivos de negocio. Si el patch no aplica por cambios de versión, detenerse y revisar manualmente.
5. `git status --short`, `git diff --check` y revisión de secretos: nunca añadir `.env`, archivos de `certbot/conf`, claves privadas, ni dumps al commit.
6. Hacer commit y push a **la rama de Docker**, únicamente después de pasar las pruebas. PR revisado y merge deliberado a `main` junto con el compañero AWS.

## 3. Prueba local segura en Windows (primera ejecución)

**Nunca reutilices el `.env` real de Supabase durante el smoke test.** Hay un worker que podría procesar una cola legítima en cuanto arranque. El script crea `.env.docker-smoke` con una URL de base de datos falsa y sin clave Resend; no envía correos, no realiza pagos y no ejecuta migraciones.

Requisitos: Docker Desktop en modo contenedores Linux, Compose V2 y puertos 8088/8443 libres.

```powershell
cd C:\SI2_Proyecto\clinica-oftalmologica-api
.\scripts\docker\smoke.ps1 -HttpPort 8088 -HttpsPort 8443
```

El script ejecuta `docker compose config --quiet`, `docker compose up --build -d`, `docker compose ps`, `GET /` y valida en OpenAPI las rutas de proveedores y ambos webhooks. Debe observarse `api`, `worker` y `nginx` en ejecución; `api` y `nginx` deberían aparecer saludables. El worker intentará leer una base de datos ficticia **inaccesible**, registrará errores de conexión esperables y permanecerá como proceso independiente: **no se ha probado su procesamiento de cola**.

Para inspeccionar logs/estado después del script, en la **misma terminal**:

```powershell
$env:COMPOSE_PROJECT_NAME = 'clinica-sprint3-smoke'
$env:APP_ENV_FILE = '.env.docker-smoke'
$env:NGINX_MODE = 'http'
$env:DUCKDNS_FQDN = 'localhost'
$env:HTTP_PORT = '8088'
$env:HTTPS_PORT = '8443'
$env:DOCKER_IMAGE_TAG = 'smoke'
docker compose ps
docker compose logs --tail=80 api worker nginx
docker compose exec -T api python -c "import httpx; print(httpx.__version__)"
docker compose exec -T api pg_dump --version
docker compose exec -T api pg_restore --version
docker compose exec -T api psql --version
docker compose exec -T nginx nginx -t
# Detener. NUNCA usar 'down -v' sin confirmar que ningún backup debe conservarse.
docker compose down
Remove-Item Env:COMPOSE_PROJECT_NAME,Env:APP_ENV_FILE,Env:NGINX_MODE,Env:DUCKDNS_FQDN,Env:HTTP_PORT,Env:HTTPS_PORT,Env:DOCKER_IMAGE_TAG
```

**Criterios:** no debe haber binding `8000:8000`; `docker compose ps` mostrará solo 80/443 de Nginx publicados; comando del worker debe ser `python -m app.modules.gestion_pagos.notificacion_runner`. Una configuración ficticia no demuestra lectura/escritura real ni funcionamiento de Resend, Stripe o Supabase.

## 4. Pruebas Python en host (realizadas previamente; volver a ejecutar en rama Docker)

Desde Conda `clinica-api` con dependencias instaladas:

```powershell
python -m pip check
python -m pytest -q tests/test_cu28_proveedores.py
python -m pytest -q `
  tests/test_cu26_backend.py `
  tests/test_cu27_movimientos.py `
  tests/test_cu28_proveedores.py `
  tests/test_cu30_flujo_integral.py `
  tests/test_cu31_pagos_administrativos.py `
  tests/test_cu32_cu33_planes_cuotas.py
```

Evidencia previa aportada por el equipo (antes de crear Docker): `171 passed`, `7 passed` CU28, `pip check` sin conflictos y 135 rutas FastAPI. No se debe presentar esta evidencia como pruebas Docker.

## 5. Entrega al responsable de AWS: paso a paso en Ubuntu EC2

**Previo:** EC2 Ubuntu, puertos 80 y 443 abiertos, SSH/22 restringido a su IP, NO abrir 8000/5432, Docker Engine + Compose V2 instalados, DuckDNS apuntando a una IPv4 estable y presupuesto controlado. Estas tareas pertenecen al responsable AWS.

1. Clonar `main` después de merge aprobado de la rama Docker.
2. Crear `.env` directamente en EC2 y protegerlo (`umask 077; nano .env; chmod 600 .env`); NO enviarlo por chat ni por Git. Partir de `.env.example`, sustituyendo placeholders por configuración real. No poner comillas incorrectas en la URL de PostgreSQL; usar SSL y codificar caracteres especiales en la contraseña del URI.
3. Mantener `NGINX_MODE=http` y `DUCKDNS_FQDN=subdominio.duckdns.org` durante el bootstrap. `docker compose config --quiet && docker compose up -d --build && docker compose ps`.
4. Confirmar `http://subdominio.duckdns.org/` y el desafío ACME sin tocar rutas de negocio. En este modo **login y webhooks devuelven 403** para impedir transmisión de credenciales por HTTP.
5. Emitir certificado con webroot y los dos volúmenes compartidos (ejecutar desde raíz del repo):

```bash
mkdir -p certbot/conf certbot/www
# Sustituir por el FQDN y el correo del responsable del certificado:
FQDN=subdominio.duckdns.org
CERT_EMAIL=operador@example.com
docker run --rm \
  -v "$(pwd)/certbot/conf:/etc/letsencrypt" \
  -v "$(pwd)/certbot/www:/var/www/certbot" \
  certbot/certbot certonly --webroot -w /var/www/certbot \
  -d "$FQDN" --email "$CERT_EMAIL" --agree-tos --no-eff-email
```

6. Tras emisión exitosa, cambiar `NGINX_MODE=https` en `.env` y ejecutar `docker compose up -d --force-recreate nginx`. Probar `curl -I https://subdominio.duckdns.org/` y `curl -I http://subdominio.duckdns.org/` (este último debe redirigir a HTTPS). Verificar ambos webhooks exclusivamente por HTTPS, con firma válida al hacer pruebas de Stripe/Resend; nunca usar secretos falsos en producción.
7. Configurar una tarea programada (systemd timer o cron) de `certbot renew --webroot ...` con los mismos montajes y `docker compose exec -T nginx nginx -s reload` solo tras renovación. Registrar su resultado; una prueba de renovación en modo ensayo es obligatoria.
8. Validar que el worker `docker compose logs -f worker` continúa funcionando, que no hay un segundo worker (`docker compose ps`), y que ambos procesos retienen su estado tras `docker compose restart` y reinicio de EC2.
9. Configurar Stripe en modo **test**: endpoint `https://FQDN/pagos/stripe/webhook`, y `STRIPE_WEBHOOK_SECRET` específico de ese endpoint. Configurar webhook Resend `https://FQDN/pagos/resend/webhook` y su secreto de firma cuando corresponda.
10. Actualizar URL de API en Angular (`environment.ts`) y cliente Flutter después de HTTPS verificado; desplegar cada cliente por su responsable.

Para renovación en EC2 (ejemplo, ejecutar desde carpeta del proyecto):

```bash
docker run --rm \
  -v "$(pwd)/certbot/conf:/etc/letsencrypt" \
  -v "$(pwd)/certbot/www:/var/www/certbot" \
  certbot/certbot renew --webroot -w /var/www/certbot --quiet
# Solo después de comprobar resultado:
docker compose exec -T nginx nginx -s reload
```

**Nota:** poner `NGINX_MODE=https` antes de que exista el certificado hará fallar Nginx. No habilitar Stripe/Resend público mientras esté en HTTP de bootstrap.

## 6. Variables que debe configurar AWS

**Obligatorias en el código actual para arrancar:** `DATABASE_URL`, `JWT_SECRET_KEY`, `PASSWORD_RESET_URL_BASE`, `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`, `GMAIL_SENDER_EMAIL`. La conexión `DATABASE_URL` apunta al control SaaS/legacy, y el código deriva conexiones de los tenants usando las reglas del backend (no inventar siete URLs hardcodeadas).

**Para pagos en pruebas:** `STRIPE_SECRET_KEY=sk_test_...`, `STRIPE_WEBHOOK_SECRET` asignado al endpoint HTTPS AWS y `STRIPE_CURRENCY`. Mantener idempotencia sin cambios de código.

**Resend:** `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `RESEND_FROM_NAME`, `RESEND_WEBHOOK_SECRET`, `RESEND_DEMO_MODE=true`, `EMAIL_TEST` configurado exclusivamente con el correo autorizado en el acuerdo, y valores opcionales de timeout/batch/interval. Las mismas variables deben llegar a API y worker.

**Backups:** `SAAS_BACKUP_PRIVATE_DIR` queda en un volumen Docker privado; para permanencia fuera de la EC2 se recomienda configurar proveedor privado `supabase` con un bucket **separado y no público**, `SAAS_BACKUP_SUPABASE_URL`, `SAAS_BACKUP_SUPABASE_BUCKET`, `SAAS_BACKUP_SUPABASE_SERVICE_ROLE_KEY`. Nunca el bucket público `product-images`. Configurar mecanismo externo de ejecución programada, **no ejecutar migraciones ni backups automáticamente al iniciar contenedores**.

**Nginx/Compose:** `DUCKDNS_FQDN`, `NGINX_MODE`, `HTTP_PORT`, `HTTPS_PORT`, `DOCKER_IMAGE_TAG`. Revisar `.env.example` completo.

## 7. Backup, restore, migrations: límites explícitos

- La imagen incluye `pg_dump`, `pg_restore`, `psql`. No crea ni modifica bases de datos por sí sola.
- Para diagnosticar clientes: `docker compose exec -T api pg_dump --version` y equivalentes. Es necesario comprobar compatibilidad con la versión de PostgreSQL de Supabase antes de backups reales.
- La herramienta `scripts/saas/run_automatic_backups.py` existe en el backend; la ejecución automática debe calendarizarse deliberadamente por AWS, solo después de validar permisos, bucket y restaurabilidad. Un `--dry-run` requiere revisar el código y la configuración; **no ejecutarlo contra las bases del proyecto sin aprobación expresa**.
- El restore requiere credenciales con permisos administrativos y se debe practicar solo sobre una base efímera, nunca sobre los siete tenants compartidos.
- Nunca agregar migraciones CU32/CU33 como paso de `Dockerfile`, `entrypoint` ni `docker compose up`; verificar ledger y aplicar solo las pendientes con procedimiento independiente.
- Evitar `docker compose down -v` porque elimina volúmenes y podría destruir copias locales.

## 8. Observabilidad y recuperación

```bash
docker compose ps
docker compose logs --tail=150 api
docker compose logs --tail=150 worker
docker compose logs --tail=150 nginx
docker compose restart api
docker compose restart worker
docker compose exec -T nginx nginx -t
```

Para rollback, conservar el SHA anterior y el `.env` intacto; detener cambios, volver a checkout de un commit aprobado en una carpeta de despliegue y reconstruir la versión anterior. **No ejecutar migraciones inversas ni restaurar bases automáticamente**. Capturar `docker compose ps`, logs redactados, resultados HTTPS y estado de Stripe/Resend antes/después; revisar el proceso con el equipo.

## 9. Criterios de aceptación - transferencia Docker

- [ ] `Dockerfile` construye sin errores en Linux/amd64.
- [ ] `docker compose config --quiet` válido con un `.env` ficticio.
- [ ] `docker compose up -d --build` inicia `api`, `worker` y `nginx`.
- [ ] `/` y `/openapi.json` responden por Nginx y las rutas de webhooks están presentes.
- [ ] API puerto 8000 no está publicado; worker no abre puertos.
- [ ] API, worker y nginx reinician limpiamente; un solo worker activo.
- [ ] `pg_dump`, `pg_restore`, `psql`, `httpx` presentes en la imagen.
- [ ] Todas las pruebas Python relevantes pasan y no se exponen secretos.
- [ ] Evidencias (comandos, capturas y SHA) guardadas en el reporte de entrega.

**Criterio distinto de aceptación AWS:** HTTPS válido, siete tenants operativos y aislados, Stripe test firmado, Resend entrega PDF al correo autorizado, worker persistente y reboot EC2 probado. Esto debe validarlo el responsable AWS junto con Docker una vez publicado el sistema.
