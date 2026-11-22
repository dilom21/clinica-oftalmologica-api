"""Productor idempotente de recordatorios para una unica base tenant."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import APP_TIMEZONE
from app.modules.notificaciones_push.repositories import notificaciones as repo_notificaciones
from app.modules.notificaciones_push.repositories import recordatorios as repo_recordatorios
from app.modules.notificaciones_push.schemas.schemas import NotificacionPersistencia
from app.modules.notificaciones_push.services import entregas as entregas_push
from app.modules.notificaciones_push.services.eventos_cita import resolver_usuario_destinatario

ESTADO_COMPLETADO = "COMPLETADO"
ESTADO_SIN_TRABAJO = "SIN_TRABAJO"
ESTADO_POLICY_NOT_CONFIGURED = "POLICY_NOT_CONFIGURED"
ESTADO_POLITICA_INVALIDA = "POLITICA_INVALIDA"

TITULO_RECORDATORIO = "Recordatorio de cita"
CUERPO_RECORDATORIO = "Tienes una cita próxima."


@dataclass(frozen=True)
class PoliticaRecordatorios:
    """Anticipaciones explicitas; no existen valores de negocio por defecto."""

    anticipaciones_minutos: tuple[int, ...] = ()
    gracia_minutos: int | None = None
    zona_horaria: str = APP_TIMEZONE
    limite_citas: int = 100

    def validar(self) -> str | None:
        if not self.anticipaciones_minutos or self.gracia_minutos is None:
            return ESTADO_POLICY_NOT_CONFIGURED
        if (
            isinstance(self.gracia_minutos, bool)
            or not isinstance(self.gracia_minutos, int)
            or not 0 <= self.gracia_minutos <= 1440
        ):
            return ESTADO_POLITICA_INVALIDA
        if not 1 <= self.limite_citas <= 100:
            return ESTADO_POLITICA_INVALIDA
        if any(
            isinstance(valor, bool) or not isinstance(valor, int) or valor <= 0
            or valor > 525600
            for valor in self.anticipaciones_minutos
        ):
            return ESTADO_POLITICA_INVALIDA
        if len(set(self.anticipaciones_minutos)) != len(self.anticipaciones_minutos):
            return ESTADO_POLITICA_INVALIDA
        try:
            ZoneInfo(self.zona_horaria)
        except (ZoneInfoNotFoundError, TypeError, ValueError):
            return ESTADO_POLITICA_INVALIDA
        return None


@dataclass
class ResultadoRecordatorios:
    estado: str
    examinadas: int = 0
    creadas: int = 0
    existentes: int = 0
    omitidas_sin_destinatario: int = 0
    omitidas_tiempo_invalido: int = 0
    obsoletas_canceladas: int = 0


def huella_horario(cita) -> str:
    canonica = f"{cita.fecha.isoformat()}|{cita.hora_inicio.replace(microsecond=0).isoformat()}"
    return hashlib.sha256(canonica.encode("ascii")).hexdigest()[:24]


def clave_recordatorio(cita, anticipacion_minutos: int) -> str:
    return f"recordatorio:{int(cita.id)}:{anticipacion_minutos}:{huella_horario(cita)}"


def interpretar_clave_recordatorio(clave: object) -> tuple[int, int, str] | None:
    if not isinstance(clave, str):
        return None
    partes = clave.split(":")
    if len(partes) != 4 or partes[0] != "recordatorio":
        return None
    try:
        cita_id, anticipacion = int(partes[1]), int(partes[2])
    except ValueError:
        return None
    if cita_id <= 0 or anticipacion <= 0 or len(partes[3]) != 24:
        return None
    return cita_id, anticipacion, partes[3]


def _instante_cita(cita, zona: ZoneInfo) -> datetime | None:
    local_naive = datetime.combine(cita.fecha, cita.hora_inicio)
    candidatos: list[datetime] = []
    for fold in (0, 1):
        local = local_naive.replace(tzinfo=zona, fold=fold)
        vuelta = local.astimezone(timezone.utc).astimezone(zona)
        if vuelta.replace(tzinfo=None) == local_naive and vuelta.fold == fold:
            candidatos.append(local)
    if len(candidatos) != 1:  # hora inexistente o ambigua por DST
        return None
    return candidatos[0]


def _crear_idempotente(db: Session, cita, usuario_id: int, clave: str):
    existente = repo_notificaciones.obtener_por_evento_clave(db, clave)
    if existente is not None:
        return existente, False
    datos = NotificacionPersistencia(
        usuario_id=usuario_id,
        tipo="RECORDATORIO_CITA",
        evento_clave=clave,
        titulo=TITULO_RECORDATORIO,
        cuerpo=CUERPO_RECORDATORIO,
        datos={"ruta": "/citas", "pantalla": "detalle_cita", "seccion": "citas", "accion": "ver", "cita_id": cita.id},
        cita_id=cita.id,
    )
    try:
        with db.begin_nested():
            return repo_notificaciones.crear(db, datos), True
    except IntegrityError:
        existente = repo_notificaciones.obtener_por_evento_clave(db, clave)
        if existente is None:
            raise
        return existente, False


def generar_recordatorios(
    db: Session, *, politica: PoliticaRecordatorios | None,
    reloj: Callable[[], datetime] | None = None,
) -> ResultadoRecordatorios:
    politica = politica or PoliticaRecordatorios()
    error = politica.validar()
    if error:
        return ResultadoRecordatorios(estado=error)
    ahora = (reloj or (lambda: datetime.now(timezone.utc)))()
    if ahora.tzinfo is None:
        return ResultadoRecordatorios(estado=ESTADO_POLITICA_INVALIDA)
    zona = ZoneInfo(politica.zona_horaria)
    ahora_local = ahora.astimezone(zona)
    anticipaciones = tuple(sorted(politica.anticipaciones_minutos))
    gracia = timedelta(minutes=politica.gracia_minutos)
    minimo = ahora_local + timedelta(minutes=min(anticipaciones)) - gracia
    maximo = ahora_local + timedelta(minutes=max(anticipaciones))
    citas = repo_recordatorios.listar_citas_elegibles(
        db, fecha_desde=minimo.date(), fecha_hasta=maximo.date(),
        limite=politica.limite_citas,
    )
    resultado = ResultadoRecordatorios(estado=ESTADO_SIN_TRABAJO, examinadas=len(citas))
    try:
        for cita in citas:
            instante = _instante_cita(cita, zona)
            if instante is None or instante <= ahora_local:
                resultado.omitidas_tiempo_invalido += 1
                continue
            usuario_id = resolver_usuario_destinatario(db, cita)
            if usuario_id is None:
                resultado.omitidas_sin_destinatario += 1
                continue
            claves_vigentes = {clave_recordatorio(cita, offset) for offset in anticipaciones}
            for anterior in repo_recordatorios.listar_recordatorios_de_cita(db, cita.id):
                if anterior.evento_clave not in claves_vigentes and anterior.cancelada_en is None:
                    repo_notificaciones.cancelar(db, anterior, ahora)
                    resultado.obsoletas_canceladas += 1
            for offset in anticipaciones:
                vencimiento = instante - timedelta(minutes=offset)
                if not vencimiento <= ahora_local <= vencimiento + gracia:
                    continue
                clave = clave_recordatorio(cita, offset)
                notificacion, creada = _crear_idempotente(db, cita, usuario_id, clave)
                entregas_push.encolar_entregas(db, notificacion)
                if creada:
                    resultado.creadas += 1
                else:
                    resultado.existentes += 1
        db.commit()  # frontera productor -> worker: lote atomico y visible
    except Exception:
        db.rollback()
        raise
    if resultado.creadas or resultado.existentes or resultado.obsoletas_canceladas:
        resultado.estado = ESTADO_COMPLETADO
    return resultado
