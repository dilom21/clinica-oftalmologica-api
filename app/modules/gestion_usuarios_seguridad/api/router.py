from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

# Importamos la conexión a la base de datos y el repositorio
from app.database.session import get_db
from app.modules.gestion_usuarios_seguridad.repositories import repository

router = APIRouter(prefix="/usuarios-seguridad", tags=["Usuarios y Seguridad"])

# El "molde" de los datos que envía Angular
class UsuarioRegistro(BaseModel):
    correo: str
    password_hash: str
    rol_id: int
    estado: bool = True

@router.get("/")
def obtener_modulo():
    return {"mensaje": "Módulo de Usuarios y Seguridad funcionando"}

# --- RUTA POST ACTUALIZADA CON BASE DE DATOS ---
@router.post("/usuarios")
def registrar_usuario(usuario: UsuarioRegistro, db: Session = Depends(get_db)):
     # 1. Verificamos si el correo ya existe
    usuario_existente = repository.obtener_usuario_por_correo(db, usuario.correo)
    if usuario_existente:
        # Si existe, detenemos todo y lanzamos un error 400
        raise HTTPException(status_code=400, detail="Este correo ya está registrado.")
    
    # 2. Si no existe, procedemos a guardarlo normalmente
    nuevo_usuario = repository.crear_usuario(db, usuario)
    return {
         "mensaje": "¡Usuario guardado permanentemente en la base de datos!",
        "id_generado": nuevo_usuario.ID,
        "correo": nuevo_usuario.Correo
    }

@router.get("/usuarios")
def listar_usuarios(db: Session = Depends(get_db)):
    usuarios = repository.obtener_usuarios(db)
    return usuarios

@router.delete("/usuarios/{usuario_id}")
def eliminar_usuario(usuario_id: int, db: Session = Depends(get_db)):
    usuario = repository.dar_de_baja_usuario(db, usuario_id)
    
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
        
    return {"mensaje": f"El usuario {usuario_id} fue dado de baja exitosamente."}

@router.put("/usuarios/{usuario_id}")
def actualizar_usuario_endpoint(usuario_id: int, usuario: UsuarioRegistro, db: Session = Depends(get_db)):
    
    # Mandamos al repositorio a actualizar los datos
    usuario_actualizado = repository.actualizar_usuario(db, usuario_id, usuario)
    
    if not usuario_actualizado:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
        
    return {
        "mensaje": "¡Usuario actualizado exitosamente!",
        "id_generado": usuario_actualizado.ID
    }