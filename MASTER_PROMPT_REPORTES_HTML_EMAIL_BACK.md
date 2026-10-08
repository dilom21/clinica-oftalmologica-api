# MASTER_PROMPT_REPORTES_HTML_EMAIL_BACK.md

## DÓNDE EJECUTAR

Trabaja EXCLUSIVAMENTE en:

```text
C:\SI2_Proyecto\clinica-oftalmologica-api
```

Estamos completando el requisito de REPORTES antes de pasar a Voz.

Lee completos:

```text
CONTEXTO_REPORTES_HTML_EMAIL_BACK.md
MASTER_PROMPT_REPORTES_HTML_EMAIL_BACK.md
```

Después inspecciona el código LOCAL actual.

# OBJETIVO

Ampliar el motor de reportes YA EXISTENTE con:

```text
1. Exportación HTML
2. Envío por eMail
```

NO crear un segundo motor.

Ya funcionan:

```text
XLSX
PDF
CSV
reportes estáticos
reportes dinámicos
preview
filtros
columnas
orden
límites
seguridad
bitácora
```

Debes reutilizar todo eso.

# FASE 1 — INSPECCIÓN EMAIL EXISTENTE

Antes de crear infraestructura nueva busca en TODO el backend:

```text
SMTP
smtplib
EmailMessage
email
correo
mail
recuperacion
password reset
```

Si existe un servicio/configuración reutilizable:
REUTILÍZALO.

No dupliques email.

# FASE 2 — HTML

Agregar `html` a formatos válidos.

Deben funcionar:

```http
POST /reportes/dinamicos/exportar/html
POST /reportes/estaticos/{reporte_key}/exportar/html
```

Headers:

```text
Content-Type: text/html; charset=utf-8
Content-Disposition: attachment; filename="....html"
```

HTML:
- doctype;
- lang es;
- charset utf-8;
- título;
- fecha de generación;
- tabla;
- cantidad de registros;
- CSS embebido;
- sin JavaScript;
- sin recursos externos.

CRÍTICO:
Escapa TODOS los valores de BD.
Prueba explícitamente XSS/HTML injection.

# FASE 3 — EMAIL

Crear endpoints coherentes:

```http
POST /reportes/dinamicos/enviar-email
POST /reportes/estaticos/{reporte_key}/enviar-email
```

El email debe adjuntar el reporte generado por el motor actual.

Formatos permitidos:

```text
xlsx
pdf
csv
html
```

Body incluye:
- destinatario EmailStr;
- formato;
- asunto;
- mensaje;
- configuración del reporte según sea dinámico/estático.

Reutiliza/compon schemas existentes; no copies lógica innecesariamente.

# SMTP

Si NO existe servicio reutilizable, crea configuración opcional:

```text
SMTP_HOST
SMTP_PORT
SMTP_USERNAME
SMTP_PASSWORD
SMTP_FROM_EMAIL
SMTP_FROM_NAME
SMTP_USE_TLS
```

NO toques `.env`.
NO hardcodees secretos.

La app debe iniciar sin SMTP.

Si se invoca email sin configuración:

```text
503 Servicio de correo no configurado
```

Puedes usar:

```text
smtplib
email.message.EmailMessage
```

sin dependencia nueva si resulta suficiente.

# SEGURIDAD

Mismos controles actuales:

```text
JWT
Administrador
permiso Generar reportes / LECTURA
```

Email NO puede saltarse límites ni whitelist.

# BITÁCORA

HTML exitoso:

```text
EXPORTAR_REPORTE_HTML
```

Email exitoso:

```text
ENVIAR_REPORTE_EMAIL
```

No guardar:
- datos del reporte;
- filtros completos;
- mensaje;
- adjunto;
- credenciales;
- contenido clínico.

# TESTS

NO enviar correos reales.

Mockear el servicio SMTP.

Extender tests para cubrir:

HTML:
- dinámico;
- estático;
- content-type;
- filename;
- doctype;
- escape XSS;
- bitácora.

Email:
- dinámico;
- estático;
- XLSX;
- PDF;
- CSV;
- HTML;
- destinatario inválido;
- formato inválido;
- no configurado 503;
- fallo SMTP 503;
- bitácora éxito;
- sin bitácora éxito cuando falla.

Ejecuta:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_gestion_reportes.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

No debilites tests existentes.

# NO HACER

NO:
- frontend;
- voz;
- IA de voz;
- backup;
- Supabase;
- migraciones;
- commit;
- push;
- merge;
- tocar .env.

# REPORTE FINAL

Entrega:

```text
1. ESTADO
2. INFRAESTRUCTURA EMAIL ENCONTRADA / CREADA
3. ARCHIVOS CREADOS
4. ARCHIVOS MODIFICADOS
5. HTML
6. ENDPOINTS EMAIL
7. FORMATOS DE ADJUNTO
8. CONFIGURACIÓN SMTP
9. SEGURIDAD
10. BITÁCORA
11. PRIVACIDAD
12. TESTS DEL BLOQUE
13. SUITE COMPLETA
14. DEUDAS / DECISIONES
15. GIT STATUS
```

No hagas commit.
