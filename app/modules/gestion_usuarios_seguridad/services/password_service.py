from datetime import datetime, timedelta, timezone
import hashlib
import secrets

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import (
	PASSWORD_RESET_TOKEN_EXPIRE_MINUTES,
	PASSWORD_RESET_URL_BASE,
)
from app.core.security import hash_password
from app.modules.gestion_usuarios_seguridad.repositories import repository as repo
from app.modules.gestion_usuarios_seguridad.schemas.schemas import (
	RecuperarPasswordRequest,
	RestablecerPasswordRequest,
)
from app.shared.services.email_service import (
	construir_enlace_recuperacion_password,
	enviar_correo_recuperacion_password,
)


def _hash_token(token_plano: str) -> str:
	return hashlib.sha256(token_plano.encode("utf-8")).hexdigest()


def solicitar_recuperacion_password(
	db: Session,
	datos: RecuperarPasswordRequest,
) -> dict:
	usuario = repo.obtener_usuario_por_correo(db, datos.correo)

	if not usuario or not usuario.estado:
		return {
			"mensaje": "El correo no existe en el sistema y no se han enviado instrucciones para cambiar la contraseña"
		}

	respuesta_base = {
		"mensaje": "Se enviaron instrucciones para recuperar la contraseña"
	}

	token_plano = secrets.token_urlsafe(48)
	token_hash = _hash_token(token_plano)
	fecha_expiracion = datetime.now(timezone.utc) + timedelta(
		minutes=PASSWORD_RESET_TOKEN_EXPIRE_MINUTES
	)

	try:
		repo.invalidar_tokens_activos_usuario(db, usuario.id)
		repo.crear_token_recuperacion(
			db=db,
			usuario_id=usuario.id,
			token_hash=token_hash,
			fecha_expiracion=fecha_expiracion,
		)
		repo.registrar_bitacora(
			db=db,
			usuario_id=usuario.id,
			accion="SOLICITAR_RECUPERACION_PASSWORD",
			entidad_afectada="token_recuperacion",
			descripcion="Se generó token de recuperación de contraseña",
		)
		db.commit()
	except Exception:
		db.rollback()
		raise

	enlace = construir_enlace_recuperacion_password(
		PASSWORD_RESET_URL_BASE,
		token_plano,
	)

	try:
		enviar_correo_recuperacion_password(
			destinatario=usuario.correo,
			enlace=enlace,
			minutos_expira=PASSWORD_RESET_TOKEN_EXPIRE_MINUTES,
		)
	except Exception as exc:
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail="No fue posible enviar el correo de recuperación",
		) from exc

	return respuesta_base


def restablecer_password(
	db: Session,
	datos: RestablecerPasswordRequest,
) -> dict:
	token_hash = _hash_token(datos.token)
	registro_token = repo.obtener_token_por_hash(db, token_hash)
	ahora = datetime.now(timezone.utc)

	if (
		not registro_token
		or registro_token.usado
		or registro_token.fecha_expiracion <= ahora
	):
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="Token inválido o expirado",
		)

	usuario = repo.obtener_usuario_por_id(db, registro_token.usuario_id)
	if not usuario or not usuario.estado:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="No fue posible restablecer la contraseña",
		)

	try:
		repo.actualizar_password_usuario(
			db,
			usuario.id,
			hash_password(datos.nueva_password),
		)
		repo.marcar_token_como_usado(db, registro_token.id)
		repo.invalidar_tokens_activos_usuario(db, usuario.id)
		repo.registrar_bitacora(
			db=db,
			usuario_id=usuario.id,
			accion="RESTABLECER_PASSWORD",
			entidad_afectada="usuario",
			id_registro_afectado=usuario.id,
			descripcion="El usuario restableció su contraseña",
		)
		db.commit()
		return {
			"mensaje": "Contraseña actualizada correctamente"
		}
	except Exception:
		db.rollback()
		raise
