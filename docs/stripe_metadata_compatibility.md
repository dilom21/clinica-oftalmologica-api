# Informe: compatibilidad con Stripe 15.6.1

## Alcance y causa raiz

- Base comprobada: `785415e`, rama `fix/stripe-metadata-compatibility`.
- Entorno comprobado: Python 3.12.13, Conda `clinica-backend`, `stripe==15.6.1`.
- En v15, `StripeObject` ya no hereda de `dict`. Los recursos y su metadata
  deben convertirse mediante `to_dict()` antes de usar metodos de diccionario.
- Reproduccion local con `PaymentIntent.construct_from(...)`: la metadata es
  un `StripeObject` y `dict(objeto.metadata)` produce el `TypeError` reportado.
- El fallo ocurre despues de `PaymentIntent.create`: Stripe puede haber creado
  la intencion aunque el backend devuelva 500 y no persista su identificador.
- La misma conversion incorrecta existia en la normalizacion de `Refund`.
- La revision con `ListObject` real detecto tambien que `Refund.list()` no
  admite `.get()`. Afectaba a la conciliacion de reembolsos sin referencia local.

## Cambios

`app/modules/gestion_pagos/providers/stripe_provider.py` (seis lineas nuevas):

- Convertir metadata con `to_dict()` solo cuando es `stripe.StripeObject`, en
  `_normalizar_intencion` y `_normalizar_reembolso`.
- Convertir la respuesta de `Refund.list()` con `to_dict(recursive=False)`.
  La conversion superficial mantiene los objetos `Refund` para el normalizador.
- Conservar el comportamiento de diccionarios convencionales y dobles de
  prueba: campos opcionales, claves/valores convertidos a texto y omision de `None`.

`tests/test_stripe_metadata_compatibility.py`:

- 29 casos de regresion con `PaymentIntent`, `Refund` y `ListObject` reales,
  construidos localmente, sin transacciones externas.
- Metadata presente, vacia, nula y ausente en dobles; normalizacion monetaria;
  PaymentIntent expandido en Refund; crear, recuperar, cancelar y listar.
- Endpoints de intenciones de pagos directos y cuotas.
- Fallo historico despues de crear una intencion: reintento y recuperacion por
  identificador, tanto legacy como tenant, sin duplicar pago, cuota ni intencion.
- Bloqueo de nuevo cobro ante una intencion vinculada en estado `succeeded`.
- Conciliacion de un refund creado antes del error de normalizacion; webhook
  de refund firmado y verificado localmente con el SDK, procesado dos veces.
- Recuperacion de metadata tenant desde PaymentIntent para refunds del dashboard.

## Revision de flujos y conversiones restantes

| Flujo | Resultado de revision |
| --- | --- |
| Pagos directos y cuotas | El normalizador corregido se usa al crear y recuperar. El pago pendiente se confirma en BD antes de llamar al proveedor. |
| Recuperacion sin referencia | Reutiliza el pago y exactamente los parametros y la clave de idempotencia originales, mientras Stripe conserve dicha clave. |
| Recuperacion con referencia | Consulta el identificador persistido, sin crear otra intencion. |
| Cancelacion | Recupera y cancela mediante el mismo normalizador; no se alteraron validaciones ni estados financieros. |
| Webhooks | `evento.to_dict()` ya era correcto y convierte recursivamente la metadata. Se conserva la verificacion de firma anterior al enrutamiento. |
| Reembolsos y conciliacion | Se corrigen normalizacion y listado; la atribucion sigue exigiendo coincidencia de ajuste/pago, importe y moneda. |
| Conversion en `webhook_tenancy.py` | Recibe `IntentoPagoProveedor.metadata`, un diccionario del adaptador, no un recurso crudo del SDK. |
| Conversion en `service.py` para refund.* | Recibe el evento convertido recursivamente a diccionario por el adaptador. |

No se modificaron claves de idempotencia, aislamiento multitenant, credenciales,
validaciones de seguridad, esquema ni logica de negocio. No se eliminaron pagos
pendientes. No hay cambios de Flutter, Angular, IAM, WIF, ADC, Firebase o workers.

## Verificacion aislada

Se utilizo un ejecutor temporal externo al repositorio:
`C:/Users/Harold/AppData/Local/Temp/opencode/stripe_isolated_run.py`.
Antes de importar la aplicacion sustituye la configuracion por valores sinteticos,
desactiva la carga de `.env`, bloquea PostgreSQL y conexiones SQLAlchemy no SQLite,
y bloquea conexiones de red y DNS. Solo permite el socketpair interno de asyncio
en Windows. Las operaciones de Stripe se sustituyen por respuestas locales.

Comandos de regresion y suite completa, desde la raiz del repositorio:

```powershell
python C:\Users\Harold\AppData\Local\Temp\opencode\stripe_isolated_run.py tests/test_stripe_metadata_compatibility.py -q --tb=short
python C:\Users\Harold\AppData\Local\Temp\opencode\stripe_isolated_run.py -q --tb=short -ra
```

- Pruebas nuevas contra el proveedor original: **17 fallan, 12 pasan**.
- Pruebas nuevas con la correccion: **29 pasan**.
- Suite especifica de pagos: **427 pasan**, 18 avisos de deprecacion de SQLite.
- Suite completa del backend: **2876 pasan, 46 omitidas, 0 fallos**, en
  **156.92 segundos**. Se uso el descubrimiento completo de `pytest.ini`, que
  excluye la copia historica `CU26_Backend_dev_josias`.
- Omisiones: 31 pruebas de PostgreSQL que requieren habilitacion expresa y
  una base sintetica; 15 validaciones externas facturables de IA. No se
  habilitaron para preservar el aislamiento solicitado.
- Avisos de suite completa: 1729 (1726 de adaptadores date/datetime de SQLite
  deprecados en Python 3.12; 3 de claves sinteticas de pruebas JWT con firma o
  algoritmo invalido). No se cambiaron configuraciones de seguridad para ocultarlos.
- `git diff --check`: correcto.

## Recuperacion operativa y riesgos pendientes

**La correccion no garantiza por si sola la recuperacion automatica de todos los
pendientes historicos.** No se consultaron ni modificaron datos de produccion.

1. Si existe referencia local, el flujo recupera la intencion por ID. Si esta en
   `succeeded`, bloquea otro cobro y requiere procesar/conciliar el webhook.
2. Sin referencia local, el reintento conserva la clave y recupera la respuesta
   original solo mientras Stripe mantenga la entrada de idempotencia.
3. Stripe puede purgar claves de API v1 cuando tengan al menos 24 horas. Reusar
   una clave purgada genera una peticion nueva. **No reintentar a ciegas pendientes
   antiguos o de antiguedad incierta sin referencia**: comprobar primero los
   registros del proveedor y su metadata tenant/pago/cuota, importe y moneda.
4. Un webhook de PaymentIntent cuyo ID todavia no esta vinculado localmente
   devuelve 404; no se vincula automaticamente solo por metadata. Una intencion
   ya exitosa sin referencia requiere conciliacion supervisada antes de reintentar.
5. Los refunds creados antes del error pueden conciliarse sin recrearlos mediante
   los flujos existentes de consulta/listado y atribucion con evidencia.
6. La retencion real de idempotencia, la concurrencia PostgreSQL y el estado de
   los intentos en AWS/Stripe no se verifican mediante simulaciones SQLite.

Estas limitaciones preexistentes se documentan sin introducir una migracion o un
mecanismo nuevo de conciliacion en este parche minimo. Cualquier recuperacion de
produccion requiere revision y autorizacion separadas.

## Fuentes

- [Documentacion del SDK en v15.6.1](https://github.com/stripe/stripe-python/blob/v15.6.1/README.md#working-with-api-resources).
- [Migracion v15: StripeObject deja de heredar de dict](https://github.com/stripe/stripe-python/wiki/Migration-guide-for-v15#stripeobject-no-longer-inherits-from-dict).
- [Idempotencia de Stripe y purga de claves](https://docs.stripe.com/api/idempotent_requests).

No se realizo commit, push, merge ni despliegue.
