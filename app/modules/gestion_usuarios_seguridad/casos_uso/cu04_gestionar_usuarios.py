from sqlalchemy.orm import Session
from ..models.usuario import Usuario
from ..schemas.usuario_schema import UsuarioCreate

def obtener_usuarios(db: Session):
    return db.query(Usuario).all()

def obtener_usuario_por_id(db: Session, id_usuario: int):
    return db.query(Usuario).filter(Usuario.ID == id_usuario).first()

def crear_usuario(db: Session, usuario: UsuarioCreate):
    nuevo_usuario = Usuario(
        Correo=usuario.Correo,
        Password_hash=usuario.Password + "_hash_simulado",  # Reemplazar con hashing real
        Id_Rol=usuario.Id_Rol,
        Estado=usuario.Estado
    )
    db.add(nuevo_usuario)
    db.commit()
    db.refresh(nuevo_usuario)
    return nuevo_usuario

def cambiar_estado_usuario(db: Session, id_usuario: int, estado: bool):
    usuario = db.query(Usuario).filter(Usuario.ID == id_usuario).first()
    if usuario:
        usuario.Estado = estado
        db.commit()
        db.refresh(usuario)
    return usuario