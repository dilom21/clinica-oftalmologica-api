from fastapi import FastAPI

from app.modules.gestion_usuarios_seguridad.api.router import router as usuarios_seguridad_router
from app.modules.gestion_pacientes.api.router import router as pacientes_router
from app.modules.gestion_agenda_citas.api.router import router as agenda_citas_router
from app.modules.gestion_historial_clinico.api.router import router as historial_clinico_router
from app.modules.gestion_inventario_proveedores.api.router import router as inventario_proveedores_router
from app.modules.gestion_pagos.api.router import router as pagos_router
from app.modules.notificaciones_interaccion_chatbot.api.router import router as notificaciones_chatbot_router
from app.modules.reportes_panel_administrativo.api.router import router as reportes_panel_router


app = FastAPI(
    title="API Clínica Oftalmológica",
    version="1.0.0"
)


app.include_router(usuarios_seguridad_router)
app.include_router(pacientes_router)
app.include_router(agenda_citas_router)
app.include_router(historial_clinico_router)
app.include_router(inventario_proveedores_router)
app.include_router(pagos_router)
app.include_router(notificaciones_chatbot_router)
app.include_router(reportes_panel_router)


@app.get("/")
def inicio():
    return {
        "mensaje": "API Clínica Oftalmológica funcionando correctamente"
    }