# CU3C.7.1 — Proveedor OAuth 2.0 para FCM mediante ADC

## Alcance

Esta fase conecta el adaptador FCM HTTP v1 existente con Application Default
Credentials (ADC). No configura credenciales reales, no realiza envíos, no abre
bases, no aplica migraciones y no modifica el worker, sus leases, el fanout ni
la orquestación tenant.

## Configuración

- `FCM_PROJECT_ID`: ID no secreto del proyecto Firebase destino.
- ADC: se resuelve con `google.auth.default` y el scope exclusivo
  `https://www.googleapis.com/auth/firebase.messaging`.
- En Google Cloud se debe preferir una identidad de servicio adjunta o Workload
  Identity con privilegios mínimos para enviar mensajes FCM.
- Fuera de Google Cloud, `GOOGLE_APPLICATION_CREDENTIALS` puede apuntar a un
  archivo administrado fuera del repositorio. El backend no crea ni persiste
  ese archivo.

La API Firebase Cloud Messaging debe habilitarse y la identidad debe recibir el
rol/permiso mínimo mediante administración externa. El código no crea cuentas,
claves, roles IAM ni recursos Google Cloud.

## Diseño

`ProveedorAccessTokenAdc` implementa el contrato `ProveedorAccessToken`:

1. conserva las credenciales ADC únicamente en memoria;
2. reutiliza el access token mientras tenga más de 60 segundos de vigencia;
3. refresca con `google.auth.transport.requests.Request`;
4. serializa el refresh con un lock para evitar renovaciones concurrentes;
5. devuelve solo el token de corta duración;
6. oculta credenciales en `repr` y elimina la causa de errores potencialmente
   sensibles.

`obtener_proveedor_fcm()` falla cerrado. Si falta `FCM_PROJECT_ID` o ADC no se
puede resolver, devuelve un `FcmHttpV1` no configurado. En ese estado el worker
no reclama entregas ni consume intentos.

## Seguridad y límites

- Los access tokens no se guardan en PostgreSQL/Supabase, logs, métricas ni
  respuestas.
- No se añadió Firebase Admin SDK ni otra base de datos.
- Las respuestas HTTP 401/403 siguen siendo errores globales de configuración;
  no invalidan dispositivos.
- Las pruebas usan credenciales y transporte HTTP simulados, sin red externa.
- Push real sigue bloqueado hasta configurar ADC/IAM externamente y autorizar
  una prueba controlada con un dispositivo de prueba.
