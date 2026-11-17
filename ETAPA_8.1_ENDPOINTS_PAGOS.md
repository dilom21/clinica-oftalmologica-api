# ETAPA 8.1 — Historial y comprobantes de pago (Backend)

Contratos de los endpoints nuevos para su consumo desde Flutter (ETAPA 8.2).

- Backend: FastAPI + SQLAlchemy + PostgreSQL (Supabase) + Stripe Test Mode.
- Autenticación: **JWT Bearer** (`Authorization: Bearer <token>`), rol **Paciente**.
- Prefijo del módulo: `/pagos`.
- El paciente se resuelve **siempre desde el JWT**: no se envía `paciente_id`.

---

## 1) Historial de pagos

**Método / URL**

```
GET /pagos/mis-pagos
```

**Autenticación:** `Authorization: Bearer <JWT>` (paciente).

**Content-Type de respuesta:** `application/json`.

**Descripción:** devuelve los pagos del paciente autenticado ordenados del más
reciente al más antiguo (`fecha_creacion` desc, `id` desc). Solo se incluyen
pagos cuyos **detalles** (PagoDetalle → ServicioRealizado) pertenecen al
paciente. Los pagos con relaciones inconsistentes (o compartidos con otros
pacientes) se omiten por completo.

**Respuesta 200 (ejemplo realista):**

```json
[
  {
    "pago_id": 505,
    "consulta_clinica_id": 100,
    "fecha_creacion": "2026-10-07T08:15:00",
    "fecha_hora_pago": "2026-10-07T08:15:00",
    "monto": "240.00",
    "moneda": "BOB",
    "metodo_pago": "TARJETA",
    "estado_pago": "APROBADO",
    "pasarela": "STRIPE",
    "servicios": [
      {
        "servicio_realizado_id": 1010,
        "nombre_servicio": "Consulta General Oftalmológica",
        "monto_aplicado": "150.00"
      },
      {
        "servicio_realizado_id": 1011,
        "nombre_servicio": "Medición de Lentes",
        "monto_aplicado": "60.00"
      },
      {
        "servicio_realizado_id": 1012,
        "nombre_servicio": "Consulta General Oftalmológica",
        "monto_aplicado": "30.00"
      }
    ]
  },
  {
    "pago_id": 2,
    "consulta_clinica_id": 4,
    "fecha_creacion": "2026-10-06T10:00:00",
    "fecha_hora_pago": null,
    "monto": "210.00",
    "moneda": "BOB",
    "metodo_pago": "TARJETA",
    "estado_pago": "PENDIENTE",
    "pasarela": "STRIPE",
    "servicios": [
      {
        "servicio_realizado_id": 5,
        "nombre_servicio": "Consulta General Oftalmológica",
        "monto_aplicado": "150.00"
      },
      {
        "servicio_realizado_id": 6,
        "nombre_servicio": "Medición de Lentes",
        "monto_aplicado": "60.00"
      }
    ]
  }
]
```

**Notas de tipos (importante para el parser de Flutter):**

- `monto` y `monto_aplicado` se serializan como **String** decimal con dos
  decimales (ej. `"210.00"`). Parsear con `double.parse(...)`.
- `fecha_creacion` y `fecha_hora_pago` son ISO-8601 sin zona (UTC).
  `fecha_hora_pago` puede ser `null` (pago aún no confirmado).
- `consulta_clinica_id` puede ser `null` si los servicios del pago provienen de
  consultas distintas.
- `pasarela` puede ser `null`.
- `estado_pago` ∈ `PENDIENTE | APROBADO | RECHAZADO | ANULADO | REEMBOLSADO`.

**Errores:** `401/403` sin JWT válido. `200 []` cuando el paciente no tiene pagos.

**Reglas de UI:**

- Mostrar todos los estados.
- Habilitar el botón "Descargar comprobante" **solo** cuando
  `estado_pago == "APROBADO"`.

---

## 2) Comprobante de pago (PDF)

**Método / URL**

```
GET /pagos/mis-pagos/{pago_id}/comprobante
```

**Autenticación:** `Authorization: Bearer <JWT>` (paciente).

**Parámetros de ruta:** `pago_id` (entero > 0).

**Content-Type de respuesta:** `application/pdf`.

**Nombre de archivo (header `Content-Disposition`):**

```
attachment; filename="comprobante_pago_{pago_id}.pdf"
```

**Descripción:** genera bajo demanda (sin guardar en disco ni en base de datos)
el comprobante PDF de un pago **APROBADO** perteneciente al paciente
autenticado. Es una operación de **solo lectura**.

**Cuerpo de respuesta:** bytes del PDF (empieza con `%PDF`).

**Contenido del PDF:**

- Encabezado "CLÍNICA OFTALMOLÓGICA" / "COMPROBANTE DE PAGO" y N° `CP-000XXX`.
- Información del pago: N° interno, fecha/hora del pago, fecha de emisión,
  estado, método, pasarela, moneda y referencia de transacción.
- Paciente: nombres y apellidos (sin datos clínicos).
- Consulta: identificador y fecha (sin información clínica sensible).
- Detalle de servicios con importe tomado de `pago_detalle.monto_aplicado`
  (precio histórico, NO el precio actual del catálogo) y `TOTAL` desde
  `pago.monto`.
- Fechas mostradas en zona horaria **America/La_Paz (UTC-4)**.
- Pie: "Este documento es una constancia de pago y no constituye factura
  fiscal." (No es factura tributaria; sin NIT/CUF/SIN.)

**Códigos de respuesta:**

| Código | Situación |
|--------|-----------|
| `200`  | PDF generado correctamente. |
| `401/403` | JWT ausente, inválido o vencido. |
| `404`  | `pago_id` inexistente **o** pago de otro paciente (mismo mensaje; no revela existencia). |
| `409`  | Pago propio en estado `PENDIENTE`, `RECHAZADO`, `ANULADO` o `REEMBOLSADO`, o pago con inconsistencia entre total y detalle. |

**Ejemplo de error 409 (pago propio no aprobado):**

```json
{
  "detail": "El comprobante solo esta disponible para pagos APROBADOS (estado actual: PENDIENTE)"
}
```

**Restricciones de autorización:**

- No se puede descargar el comprobante de otro paciente (responde `404`).
- No se emite comprobante para pagos no aprobados (`409`).
- No se modifican datos ni el estado del pago al generar el comprobante.
- No se exponen secretos de Stripe ni datos de tarjetas.

---

## Cómo descargar en Flutter (referencia ETAPA 8.2)

```http
GET /pagos/mis-pagos/2/comprobante
Authorization: Bearer <token>
```

La respuesta es binaria (`application/pdf`); guardar los bytes con la extensión
`.pdf` y abrir con un visor o `share_plus`.
