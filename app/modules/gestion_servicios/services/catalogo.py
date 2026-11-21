# CU38: reglas de lectura sobre la sesion tenant, sin conexiones alternativas.
from fastapi import HTTPException
from app.modules.gestion_servicios.repositories import catalogo as repo

def listar(db, *, busqueda="", offset=0, limite=20):
    return repo.listar_activos(db, busqueda=busqueda.strip().lower(), offset=offset, limite=limite)

def detalle(db, servicio_id):
    resultado = repo.obtener_activo(db, servicio_id)
    if resultado is None:
        raise HTTPException(404, "Servicio no disponible en el catálogo de esta empresa")
    return resultado
