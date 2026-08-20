from fastapi import FastAPI

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


app.include_router(seguridad_router)
app.include_router(pacientes_router)


@app.get("/")
def root():
    return {
        "mensaje": "API Clínica Oftalmológica funcionando correctamente"
    }