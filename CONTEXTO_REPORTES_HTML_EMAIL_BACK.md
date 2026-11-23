# CONTEXTO_REPORTES_HTML_EMAIL_BACK.md

## 1. Identificación

**Proyecto:** Clínica Oftalmológica  
**Sprint:** Sprint 2  
**Bloque:** Completar reportes con HTML + envío por eMail  
**Capa:** Backend FastAPI  
**Repositorio objetivo:** `C:\SI2_Proyecto\clinica-oftalmologica-api`

Este bloque amplía el motor de reportes YA IMPLEMENTADO. No se debe crear un segundo motor ni duplicar lógica.

---

## 2. Objetivo

Completar el requisito académico de exportación de reportes incorporando:

1. **Exportación HTML**
2. **Envío de reportes por eMail**

Ya existen y funcionan:

- XLSX
- PDF
- CSV
- reportes estáticos
- reportes dinámicos
- filtros
- selección de columnas
- ordenamiento
- previsualización
- bitácora de exportaciones

Este bloque debe reutilizar exactamente el mismo motor y validaciones.

---

## 3. Arquitectura existente a reutilizar

Inspeccionar primero:

```text
app/modules/gestion_reportes/
├── api/router.py
├── schemas/schemas.py
├── services/service.py
├── services/exporters/
├── repositories/repository.py
└── registry/datasets.py
```

También revisar:

```text
app/core/config.py
app/core/dependencies.py
app/modules/gestion_usuarios_seguridad/
```

Y buscar infraestructura de correo ya existente en el proyecto:

```text
SMTP
email
mail
correo
recuperacion
password reset
```

Si ya existe un servicio de correo, REUTILIZARLO.

No crear dos sistemas de email paralelos.

---

## 4. Exportación HTML

Agregar formato:

```text
html
```

a reportes estáticos y dinámicos.

Endpoints existentes deben aceptar:

```http
POST /reportes/dinamicos/exportar/html
POST /reportes/estaticos/{reporte_key}/exportar/html
```

Content-Type:

```text
text/html; charset=utf-8
```

Content-Disposition:

```text
attachment; filename="reporte_....html"
```

---

## 5. HTML generado

Generar un documento HTML autocontenido y legible.

Debe incluir:

- `<!doctype html>`
- `<html lang="es">`
- `<meta charset="utf-8">`
- título del reporte
- fecha/hora de generación
- tabla
- encabezados
- filas
- estilos CSS inline o `<style>` interno
- pie con cantidad de registros

No incluir scripts.

No cargar recursos externos.

Escapar correctamente todos los valores provenientes de BD usando `html.escape` o equivalente seguro.

No insertar contenido sin escapar.

---

## 6. Diseño HTML

Debe ser sobrio y portable:

- encabezado azul oscuro;
- tabla clara;
- encabezados con fondo suave;
- tipografía del sistema;
- bordes simples;
- ancho responsivo;
- impresión razonable.

No necesita gráficos.

---

## 7. Email

Implementar envío de reportes por correo.

No enviar automáticamente.

Debe existir una acción explícita del administrador.

El reporte se debe generar usando el MISMO motor existente y adjuntar como archivo.

Formatos de adjunto permitidos:

```text
xlsx
pdf
csv
html
```

No admitir extensiones arbitrarias.

---

## 8. Endpoint de email

Implementar una ruta clara y coherente. Opción recomendada:

### Dinámico

```http
POST /reportes/dinamicos/enviar-email
```

### Estático

```http
POST /reportes/estaticos/{reporte_key}/enviar-email
```

Body conceptual:

```json
{
  "destinatario": "correo@ejemplo.com",
  "formato": "pdf",
  "asunto": "Reporte solicitado",
  "mensaje": "Adjunto se encuentra el reporte solicitado.",
  "...configuracionReporte": "según esquema existente"
}
```

No duplicar el schema de configuración si puede componerse/reutilizarse.

---

## 9. Validación email

Usar `EmailStr` de Pydantic.

Un solo destinatario por solicitud en este bloque.

No implementar:

- CC
- BCC
- listas masivas

Longitudes sugeridas:

```text
asunto: 1..150
mensaje: 0..1000
```

`extra="forbid"`.

---

## 10. Seguridad

Reutilizar exactamente:

```text
JWT válido
rol Administrador
permiso "Generar reportes" con ACCION_LECTURA
```

El frontend nunca debe aportar:

- SQL
- nombres físicos de tabla
- rutas de archivos
- headers SMTP
- credenciales SMTP

---

## 11. Configuración de correo

Primero inspeccionar si ya existen variables y servicio SMTP.

Si NO existe infraestructura reutilizable, agregar soporte configurable en backend, sin secretos hardcodeados:

```text
SMTP_HOST
SMTP_PORT
SMTP_USERNAME
SMTP_PASSWORD
SMTP_FROM_EMAIL
SMTP_FROM_NAME
SMTP_USE_TLS
```

No modificar `.env`.

La app debe poder iniciar aunque SMTP no esté configurado.

Si se intenta enviar email sin configuración:

```text
503
Servicio de correo no configurado
```

No romper otros módulos.

---

## 12. Servicio de correo

Si hace falta crear uno, ubicarlo en un lugar reutilizable, por ejemplo:

```text
app/core/email_service.py
```

o seguir estructura existente.

Usar librería estándar `smtplib` + `email.message.EmailMessage` si es suficiente.

No agregar una dependencia externa solo para enviar SMTP salvo necesidad real.

El envío debe:

- conectar con timeout;
- TLS si está configurado;
- autenticar si corresponde;
- adjuntar bytes del reporte;
- poner filename seguro;
- incluir body de texto;
- no imprimir credenciales.

---

## 13. Manejo de errores email

Mapear:

```text
SMTP no configurado     → 503
timeout/conexión        → 503
auth SMTP               → 503
destinatario inválido   → 422
formato inválido        → 422
error generación reporte→ conservar códigos actuales
```

No devolver traceback.

No devolver credenciales/config interna.

---

## 14. Límites

Reutilizar el límite de exportación ya existente:

```text
máximo 5000 filas
```

No permitir que email bypassée límites.

No generar dos veces el mismo dataset si se puede reutilizar el resultado de una ejecución.

---

## 15. Bitácora

### Export HTML exitosa

Registrar:

```text
EXPORTAR_REPORTE_HTML
```

Entidad:

```text
reporte
```

### Email exitoso

Registrar:

```text
ENVIAR_REPORTE_EMAIL
```

Descripción genérica, por ejemplo:

```text
Envío por correo de reporte dinámico en formato PDF
Envío por correo de reporte estático pacientes_activos en formato XLSX
```

NO registrar:

- destinatario completo si se considera sensible;
- contenido del email;
- filas;
- filtros;
- adjunto;
- credenciales SMTP.

Si se requiere trazabilidad del destinatario, enmascararlo o documentar la decisión. Preferencia: no guardarlo en bitácora académica.

---

## 16. HTML + email y privacidad

Los reportes pueden incluir datos clínicos.

No persistir archivos temporales si no es necesario.

Generar en memoria:

```text
query
→ bytes/string
→ attachment
→ enviar
```

No escribir archivos permanentes en disco.

No guardar contenido en logs.

---

## 17. Tests

Extender `tests/test_gestion_reportes.py` o crear archivo complementario si queda más claro.

No enviar emails reales en tests.

Mockear SMTP/servicio de correo.

Cubrir mínimo:

1. export dinámico HTML → 200;
2. export estático HTML → 200;
3. Content-Type HTML correcto;
4. Content-Disposition `.html`;
5. HTML contiene doctype;
6. HTML escapa `<script>`/HTML de datos;
7. HTML no contiene scripts ejecutables provenientes de datos;
8. bitácora `EXPORTAR_REPORTE_HTML`;

Email:
9. dinámico email exitoso mock;
10. estático email exitoso mock;
11. formato PDF;
12. formato XLSX;
13. formato CSV;
14. formato HTML;
15. destinatario inválido → 422;
16. formato inválido → 422;
17. SMTP no configurado → 503;
18. fallo SMTP → 503;
19. bitácora `ENVIAR_REPORTE_EMAIL`;
20. email fallido NO genera bitácora exitosa;
21. no excede límite 5000;
22. JWT requerido;
23. rol admin requerido;
24. permiso requerido.

Suite completa debe permanecer verde.

---

## 18. No hacer

NO:

- frontend todavía;
- voz;
- DeepSeek para reportes;
- backup;
- restore;
- SaaS;
- migraciones;
- tablas nuevas;
- cambios Supabase;
- commit;
- push;
- merge;
- modificar `.env`.

---

## 19. Criterio de terminado

```text
[ ] HTML dinámico
[ ] HTML estático
[ ] HTML seguro/escapado
[ ] MIME + filename correctos
[ ] email dinámico
[ ] email estático
[ ] adjuntos XLSX/PDF/CSV/HTML
[ ] SMTP reutilizado o configurable
[ ] no secretos en código
[ ] app inicia sin SMTP
[ ] 503 si email no configurado
[ ] bitácora HTML
[ ] bitácora email
[ ] tests sin correo real
[ ] suite completa pasa
[ ] reporte final del agente
```
