from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware


# --- IMPORTACIÓN DE MODELOS ---
from app.modules.gestion_usuarios_seguridad.models.models import Usuario
from app.modules.gestion_usuarios_seguridad.models.models import Rol
from app.modules.gestion_servicios.models.models import ServicioOftalmologico # <--- AGREGA ESTA LÍNEA

# --- IMPORTACIÓN DE RUTAS ---
from app.modules.gestion_usuarios_seguridad.api.router import router as usuarios_seguridad_router
from app.modules.gestion_pacientes.api.router import router as pacientes_router
from app.modules.gestion_agenda_citas.api.router import router as agenda_citas_router
from app.modules.gestion_historial_clinico.api.router import router as historial_clinico_router
from app.modules.gestion_inventario_proveedores.api.router import router as inventario_proveedores_router
from app.modules.gestion_pagos.api.router import router as pagos_router
from app.modules.notificaciones_interaccion_chatbot.api.router import router as notificaciones_chatbot_router
from app.modules.reportes_panel_administrativo.api.router import router as reportes_panel_router
from app.modules.gestion_servicios.api.router import router as servicios_router # <--- AGREGA ESTA LÍNEA

app = FastAPI(
    title="API Clínica Oftalmológica",
    version="1.0.0",
)

# --- CONFIGURACIÓN DE CORS PARA ANGULAR ---
allowed_origins = [
    "http://localhost:4201",
    "http://127.0.0.1:4201",
    "http://localhost:4200",
    "http://127.0.0.1:4200",
    "https://clinica-oftalmologica-web.vercel.app",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,  
    allow_methods=["*"],     
    allow_headers=["*"],     
)

# El esquema se administra mediante SQL en Supabase.

app.include_router(usuarios_seguridad_router)
app.include_router(pacientes_router)
app.include_router(agenda_citas_router)
app.include_router(historial_clinico_router)
app.include_router(inventario_proveedores_router)
app.include_router(pagos_router)
app.include_router(notificaciones_chatbot_router)
app.include_router(reportes_panel_router)
app.include_router(servicios_router)

# --- RUTA PRINCIPAL ---
@app.get("/")
def root():
    return {
        "mensaje": "API Clínica Oftalmológica funcionando correctamente"
    }