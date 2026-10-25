# Desarrollo local: FastAPI y worker CU31

Desde la raiz de `clinica-oftalmologica-api`, activa el entorno Conda y usa:

```cmd
conda activate clinica-backend
python scripts\dev.py
```

El launcher utiliza el mismo `python` del entorno activo e inicia exactamente
dos procesos hermanos:

- `python -m uvicorn app.main:app --reload`
- `python -m app.modules.gestion_pagos.notificacion_runner`

Uvicorn conserva el hot reload. Sus reinicios afectan solo al proceso de la
API; el worker no vive dentro de FastAPI y no se duplica con cada recarga.
Los dos procesos heredan el entorno y cargan el `.env` mediante la
configuracion existente.

Los registros de ambos aparecen en la misma terminal. Si cualquiera termina
inesperadamente, el launcher informa el codigo y detiene al otro. Para cerrar
el entorno pulsa `Ctrl+C` una vez; el launcher solicita el cierre de ambos
grupos de procesos y aplica un cierre forzado y acotado solo si no responden.

## Comandos independientes

Para diagnosticar solamente la API:

```cmd
python -m uvicorn app.main:app --reload
```

Para diagnosticar solamente el worker:

```cmd
python -m app.modules.gestion_pagos.notificacion_runner
```

No ejecutes el worker independiente al mismo tiempo que `scripts\dev.py`.
Ejecutar dos launchers manualmente tambien crea dos workers; PostgreSQL y la
idempotencia de Resend protegen la cola, pero no es una configuracion local
recomendada.

## Destinatario unico para comprobantes de prueba

El override esta desactivado por defecto. Para probar CU31 sin modificar el
correo de los pacientes sinteticos, configura localmente:

```dotenv
RESEND_API_KEY=re_xxxxxxxxxxxxxxxxx
RESEND_FROM_EMAIL=onboarding@resend.dev
RESEND_FROM_NAME=Clinica Oftalmologica
EMAIL_TEST=correo-de-pruebas@example.com
```

Cuando `EMAIL_TEST` esta configurado, todos los comprobantes CU31 procesados
por el worker local (base legacy y tenants) se redirigen a esa direccion. No se
modifica el correo almacenado de ningun paciente. Si la variable esta ausente
o vacia, se utiliza normalmente el correo valido asociado al paciente.

Con el modo activo, una notificacion nueva queda `PENDIENTE` aunque ese
paciente sintetico no tenga correo valido. La outbox conserva la marca
`DESTINATARIO_REDIRECT_PRUEBAS`, y los logs aclaran que la aceptacion o entrega
no corresponde al paciente. El PDF, la idempotencia y los reintentos no
cambian.

El arranque falla de forma segura si `EMAIL_TEST` es invalido o si se detecta
Render/produccion mediante las variables del entorno de despliegue. Para volver
al comportamiento normal elimina o deja vacio `EMAIL_TEST`.

## Despliegue posterior

Este launcher es solo para desarrollo. En Render la API y el worker deben
continuar como servicios separados, con ciclos de vida, escalado, variables y
registros independientes. No se debe usar `--reload` en produccion.
