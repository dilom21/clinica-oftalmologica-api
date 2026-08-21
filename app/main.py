from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.modules.gestion_usuarios_seguridad.api.router import (
    router as seguridad_router,
)

from app.modules.gestion_pacientes.api.router import (
    router as pacientes_router,
)


app = FastAPI(
    title="API Clínica Oftalmológica",
    version="1.0.0",
)

allowed_origins = [
    "http://localhost:4201",
    "http://127.0.0.1:4201",
    "http://localhost:4200",
    "http://127.0.0.1:4200",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


app.include_router(seguridad_router)
app.include_router(pacientes_router)


@app.get("/")
def root():
    return {
        "mensaje": "API Clínica Oftalmológica funcionando correctamente"
    }
