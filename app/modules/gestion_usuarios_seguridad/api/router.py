from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

# Importamos la conexión a la base de datos (Asumiendo que get_db está en app/database/__init__.py o similar)
# Si te marca error aquí, revisa el nombre del archivo dentro de tu carpeta app/database
from app.database.session import get_db


# Importamos las clases de tus esquemas (nota que usamos el nombre exacto de tu archivo: usuario_schema)
from ..schemas.usuario_schema import UsuarioCreate, UsuarioResponse
# Importamos tu caso de uso
from ..casos_uso import cu04_gestionar_usuarios

# Mantenemos la configuración original de tu equipo
router = APIRouter(
    prefix="/usuarios-seguridad",
    tags=["Usuarios y Seguridad"]
)

# 1. Ruta para listar todos los usuarios
@router.get("/usuarios", response_model=List[UsuarioResponse])
def listar_usuarios(db: Session = Depends(get_db)):
    return cu04_gestionar_usuarios.obtener_usuarios(db)

# 2. Ruta para buscar un usuario específico por ID
@router.get("/usuarios/{id_usuario}", response_model=UsuarioResponse)
def obtener_usuario(id_usuario: int, db: Session = Depends(get_db)):
    usuario = cu04_gestionar_usuarios.obtener_usuario_por_id(db, id_usuario)
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return usuario

# 3. Ruta para registrar un usuario nuevo
@router.post("/usuarios", response_model=UsuarioResponse, status_code=status.HTTP_201_CREATED)
def registrar_usuario(usuario: UsuarioCreate, db: Session = Depends(get_db)):
    return cu04_gestionar_usuarios.crear_usuario(db, usuario)

# 4. Ruta para cambiar el estado (habilitar/deshabilitar)
@router.patch("/usuarios/{id_usuario}/estado", response_model=UsuarioResponse)
def cambiar_estado(id_usuario: int, estado: bool, db: Session = Depends(get_db)):
    usuario_actualizado = cu04_gestionar_usuarios.cambiar_estado_usuario(db, id_usuario, estado)
    if not usuario_actualizado:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return usuario_actualizado