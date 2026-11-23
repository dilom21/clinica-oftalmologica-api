# Integración rápida CU26 en dev-josias

1. Confirma rama `git branch --show-current` → `dev-josias`.
2. Descarga/extráe el ZIP dentro de `C:\SI2_Proyecto\clinica-oftalmologica-api`.
3. Abre PowerShell en la raíz del backend y ejecuta:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\CU26_Backend_dev_josias\instalar_cu26.ps1
```

4. Copia las pruebas **sin pisar las demás**:

```powershell
Copy-Item .\CU26_Backend_dev_josias\tests\test_cu26_backend.py .\tests\ -Force
```

5. Verifica sintaxis y pruebas:

```powershell
.\.venv\Scripts\python.exe -m compileall -q .\app\modules\gestion_inventario_proveedores
.\.venv\Scripts\python.exe -m pytest -q .\tests\test_cu26_backend.py
```

Si aparece `No module named pytest`, instala las herramientas de prueba en el entorno virtual con ` .\.venv\Scripts\python.exe -m pip install pytest httpx ` (solo dependencias de pruebas).

6. Ejecuta local:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

7. Abre `http://127.0.0.1:8000/docs`; identifica rutas con prefijo `/inventario-proveedores`.
8. Usa un JWT **de administrador clínico**, no JWT de administrador SaaS, y prueba primero crear categoría.
9. Revisar diferencias `git diff -- app/main.py app/modules/gestion_inventario_proveedores/api/router.py` y `git status --short`.
10. Para agregar SOLO la entrega CU26 al commit (no ZIP ni .bak):

```powershell
git add app/main.py app/modules/gestion_inventario_proveedores/api/router.py app/modules/gestion_inventario_proveedores/models/catalogo.py app/modules/gestion_inventario_proveedores/schemas/catalogo.py app/modules/gestion_inventario_proveedores/services/catalogo.py tests/test_cu26_backend.py
git diff --cached --stat
git commit -m "feat(cu26): implementar catalogo de categorias productos y lotes"
```

**Precauciones:** el instalador reemplaza únicamente el `router.py` placeholder y guarda una copia como `router.py.backup-cu26`; edita `main.py` solo para registrar el router. No ejecutar `git add .`, `git clean`, `git reset --hard` o scripts de migración SQL. La documentación completa está en `docs/CU26_DOCUMENTACION_BACKEND.md` dentro del ZIP.
