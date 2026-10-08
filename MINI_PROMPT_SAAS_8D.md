PASO 8D — CONSOLA SAAS DE BACKUP/RESTORE

Inicia desde C:\SI2_Proyecto\clinica-oftalmologica-api

Lee completos:
- CONTEXTO_SAAS_8D_FRONTEND_BACKUP_RESTORE.md
- MASTER_PROMPT_SAAS_8D_FRONTEND_BACKUP_RESTORE.md

Trabaja FASE A BACKEND únicamente:
- audita endpoints SaaS actuales, NO dupliques backups/restores;
- si no existen endpoints para administrar backup_policy, crea GET /saas/backup-policies y PUT /saas/backup-policies/{empresa_id};
- usa seguridad SaaS Admin, validación timezone/frecuencia/retención, cálculo backend de proximo_backup y bitácora;
- no ejecutes ningún backup/restore real;
- tests focalizados + suite completa + git diff --check;
- entrega reporte Fase A y DETENTE.

Cuando autorice FASE B, cambiarás a:
C:\SI2_Proyecto\clinica-oftalmologica-web

Allí crearás UI SaaS de backups manuales/historial, políticas automáticas y restore protegido, siguiendo el MASTER_PROMPT.

No modificar 001–007 ni backups/restore existentes, no tocar datos reales ni .env, no commit/push/merge.
