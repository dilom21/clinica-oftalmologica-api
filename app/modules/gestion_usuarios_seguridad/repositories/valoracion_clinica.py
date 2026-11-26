from datetime import datetime, timezone
import json

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session, selectinload

from app.modules.gestion_agenda_citas.models.models import Oftalmologo
from app.modules.gestion_historial_clinico.models.models import (
    ConsultaClinica,
    HistorialClinico,
)
from app.modules.gestion_pacientes.models.models import Paciente
from app.modules.gestion_usuarios_seguridad.models.valoracion_clinica import (
    ValoracionClinica,
)


PREFIJO_EVENTO_VALORACION = "VALORACION_CONSULTA:"
TIPO_NOTIFICACION_OPERATIVA = "ACTUALIZACION_OPERATIVA"
ACCION_NOTIFICACION_VALORAR = "VALORAR_ATENCION"


def evento_clave_valoracion(consulta_clinica_id: int) -> str:
    return f"{PREFIJO_EVENTO_VALORACION}{consulta_clinica_id}"


def tabla_existe(db: Session, nombre_tabla: str) -> bool:
    """Comprueba si la tabla física existe usando la conexión activa de la sesión."""
    conn = db.connection()
    return inspect(conn).has_table(nombre_tabla)


def _parsear_datos_json(valor) -> dict:
    if isinstance(valor, dict):
        return valor
    if isinstance(valor, str) and valor.strip():
        try:
            cargado = json.loads(valor)
            if isinstance(cargado, dict):
                return cargado
        except ValueError:
            return {}
    return {}


def _extraer_consulta_id_de_notificacion(evento_clave: str, datos: dict) -> int | None:
    raw = datos.get("consulta_clinica_id")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    if isinstance(evento_clave, str) and evento_clave.startswith(PREFIJO_EVENTO_VALORACION):
        sufijo = evento_clave[len(PREFIJO_EVENTO_VALORACION):].strip()
        if sufijo.isdigit():
            return int(sufijo)
    return None


def _parsear_datetime(valor) -> datetime:
    if isinstance(valor, datetime):
        return valor
    if isinstance(valor, str):
        texto = valor.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(texto)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _normalizar_fila_notificacion(mapping) -> dict:
    datos = _parsear_datos_json(mapping["datos"])
    evento_clave = mapping["evento_clave"]
    return {
        "id": int(mapping["id"]),
        "usuario_id": int(mapping["usuario_id"]),
        "evento_clave": evento_clave,
        "consulta_clinica_id": _extraer_consulta_id_de_notificacion(
            evento_clave, datos,
        ),
        "motivo_disparo": datos.get("motivo_disparo") or "CONSULTA_FINALIZADA",
        "titulo": mapping["titulo"],
        "mensaje": mapping["cuerpo"],
        "cita_id": mapping["cita_id"],
        "leida": bool(mapping["leida"]),
        "fecha_lectura": (
            _parsear_datetime(mapping["fecha_lectura"])
            if mapping["fecha_lectura"] is not None
            else None
        ),
        "fecha_creacion": _parsear_datetime(mapping["fecha_creacion"]),
        "fecha_actualizacion": _parsear_datetime(mapping["fecha_actualizacion"]),
    }


# =========================================================
# CONSULTAS CLÍNICAS DEL PACIENTE
# =========================================================

def obtener_consulta_activa_del_paciente(
    db: Session,
    consulta_clinica_id: int,
    paciente_id: int,
) -> ConsultaClinica | None:
    """Devuelve la consulta activa solo si pertenece al paciente indicado."""
    stmt = (
        select(ConsultaClinica)
        .join(
            HistorialClinico,
            HistorialClinico.id == ConsultaClinica.historial_clinico_id,
        )
        .options(
            selectinload(ConsultaClinica.oftalmologo),
            selectinload(ConsultaClinica.recetas),
        )
        .where(
            ConsultaClinica.id == consulta_clinica_id,
            ConsultaClinica.estado.is_(True),
            HistorialClinico.paciente_id == paciente_id,
            HistorialClinico.estado.is_(True),
        )
        .limit(1)
    )
    return db.scalar(stmt)


def obtener_contexto_consulta_para_aviso(
    db: Session,
    consulta_clinica_id: int,
) -> tuple[ConsultaClinica, Paciente, Oftalmologo] | None:
    """Resuelve consulta activa + paciente activo + oftalmólogo para avisos."""
    stmt = (
        select(ConsultaClinica, Paciente, Oftalmologo)
        .join(
            HistorialClinico,
            HistorialClinico.id == ConsultaClinica.historial_clinico_id,
        )
        .join(Paciente, Paciente.id == HistorialClinico.paciente_id)
        .join(Oftalmologo, Oftalmologo.id == ConsultaClinica.oftalmologo_id)
        .where(
            ConsultaClinica.id == consulta_clinica_id,
            ConsultaClinica.estado.is_(True),
            HistorialClinico.estado.is_(True),
            Paciente.estado.is_(True),
        )
        .limit(1)
    )
    row = db.execute(stmt).first()
    if row is None:
        return None
    return row[0], row[1], row[2]


def listar_consultas_atendidas_por_paciente(
    db: Session,
    paciente_id: int,
) -> list[ConsultaClinica]:
    """Lista todas las atenciones activas del paciente (más recientes primero)."""
    stmt = (
        select(ConsultaClinica)
        .join(
            HistorialClinico,
            HistorialClinico.id == ConsultaClinica.historial_clinico_id,
        )
        .options(
            selectinload(ConsultaClinica.oftalmologo),
            selectinload(ConsultaClinica.recetas),
        )
        .where(
            HistorialClinico.paciente_id == paciente_id,
            HistorialClinico.estado.is_(True),
            ConsultaClinica.estado.is_(True),
        )
        .order_by(
            ConsultaClinica.fecha_consulta.desc(),
            ConsultaClinica.id.desc(),
        )
    )
    return list(db.scalars(stmt).all())


# =========================================================
# VALORACIÓN CLÍNICA (ÚNICA TABLA NUEVA)
# =========================================================

def obtener_valoracion_por_id(
    db: Session,
    valoracion_id: int,
) -> ValoracionClinica | None:
    stmt = (
        select(ValoracionClinica)
        .options(
            selectinload(ValoracionClinica.oftalmologo),
            selectinload(ValoracionClinica.consulta_clinica),
        )
        .where(ValoracionClinica.id == valoracion_id)
        .limit(1)
    )
    return db.scalar(stmt)


def obtener_valoracion_por_consulta(
    db: Session,
    consulta_clinica_id: int,
) -> ValoracionClinica | None:
    stmt = (
        select(ValoracionClinica)
        .options(
            selectinload(ValoracionClinica.oftalmologo),
            selectinload(ValoracionClinica.consulta_clinica),
        )
        .where(ValoracionClinica.consulta_clinica_id == consulta_clinica_id)
        .limit(1)
    )
    return db.scalar(stmt)


def listar_valoraciones_por_paciente(
    db: Session,
    paciente_id: int,
) -> list[ValoracionClinica]:
    stmt = (
        select(ValoracionClinica)
        .options(
            selectinload(ValoracionClinica.oftalmologo),
            selectinload(ValoracionClinica.consulta_clinica),
        )
        .where(ValoracionClinica.paciente_id == paciente_id)
        .order_by(
            ValoracionClinica.fecha_actualizacion.desc(),
            ValoracionClinica.id.desc(),
        )
    )
    return list(db.scalars(stmt).all())


def crear_valoracion(
    db: Session,
    *,
    paciente_id: int,
    consulta_clinica_id: int,
    oftalmologo_id: int,
    puntuacion: int,
    comentario: str | None,
) -> ValoracionClinica:
    ahora = datetime.now(timezone.utc)
    valoracion = ValoracionClinica(
        paciente_id=paciente_id,
        consulta_clinica_id=consulta_clinica_id,
        oftalmologo_id=oftalmologo_id,
        puntuacion=puntuacion,
        comentario=comentario,
        fecha_creacion=ahora,
        fecha_actualizacion=ahora,
    )
    db.add(valoracion)
    db.flush()
    db.refresh(valoracion)
    return valoracion


def actualizar_valoracion(
    db: Session,
    valoracion: ValoracionClinica,
    *,
    puntuacion: int,
    comentario: str | None,
) -> ValoracionClinica:
    valoracion.puntuacion = puntuacion
    valoracion.comentario = comentario
    valoracion.fecha_actualizacion = datetime.now(timezone.utc)
    db.flush()
    db.refresh(valoracion)
    return valoracion


# =========================================================
# REUTILIZACIÓN DE `public.notificacion` EXISTENTE (OPCIÓN A)
# Sin modificar columnas ni constraints del módulo push.
# =========================================================

def obtener_notificacion_valoracion_por_id(
    db: Session,
    notificacion_id: int,
    usuario_id: int,
) -> dict | None:
    if not tabla_existe(db, "notificacion"):
        return None
    row = db.execute(
        text(
            """
            SELECT id, usuario_id, evento_clave, titulo, cuerpo, datos,
                   cita_id, leida, fecha_lectura, fecha_creacion,
                   fecha_actualizacion
            FROM notificacion
            WHERE id = :notificacion_id
              AND usuario_id = :usuario_id
              AND evento_clave LIKE :prefijo
              AND cancelada_en IS NULL
            LIMIT 1
            """
        ),
        {
            "notificacion_id": notificacion_id,
            "usuario_id": usuario_id,
            "prefijo": f"{PREFIJO_EVENTO_VALORACION}%",
        },
    ).mappings().first()
    if row is None:
        return None
    return _normalizar_fila_notificacion(row)


def listar_notificaciones_valoracion_por_usuario(
    db: Session,
    usuario_id: int,
) -> dict[int, dict]:
    """Devuelve `{consulta_clinica_id: notificacion}` desde `notificacion`."""
    if not tabla_existe(db, "notificacion"):
        return {}
    rows = db.execute(
        text(
            """
            SELECT id, usuario_id, evento_clave, titulo, cuerpo, datos,
                   cita_id, leida, fecha_lectura, fecha_creacion,
                   fecha_actualizacion
            FROM notificacion
            WHERE usuario_id = :usuario_id
              AND evento_clave LIKE :prefijo
              AND cancelada_en IS NULL
            ORDER BY fecha_creacion DESC, id DESC
            """
        ),
        {
            "usuario_id": usuario_id,
            "prefijo": f"{PREFIJO_EVENTO_VALORACION}%",
        },
    ).mappings().all()

    resultado: dict[int, dict] = {}
    for row in rows:
        item = _normalizar_fila_notificacion(row)
        consulta_id = item["consulta_clinica_id"]
        if consulta_id is not None and consulta_id not in resultado:
            resultado[consulta_id] = item
    return resultado


def crear_o_actualizar_notificacion_valoracion(
    db: Session,
    *,
    usuario_id: int,
    cita_id: int | None,
    consulta_clinica_id: int,
    oftalmologo_id: int,
    motivo_disparo: str,
    titulo: str,
    cuerpo: str,
) -> dict | None:
    """Inserta o actualiza la fila en `notificacion` (y entrega FCM si aplica).

    Respeta íntegramente `ck_notificacion_tipo` (`ACTUALIZACION_OPERATIVA`),
    `uq_notificacion_evento_clave` (`VALORACION_CONSULTA:<consulta_id>`) y
    `ck_notificacion_lectura_coherente` (`leida=FALSE` <-> `fecha_lectura=NULL`).
    """
    if not tabla_existe(db, "notificacion"):
        return None

    ahora = datetime.now(timezone.utc)
    evento_clave = evento_clave_valoracion(consulta_clinica_id)
    datos_json = json.dumps(
        {
            "accion": ACCION_NOTIFICACION_VALORAR,
            "consulta_clinica_id": consulta_clinica_id,
            "oftalmologo_id": oftalmologo_id,
            "motivo_disparo": motivo_disparo,
        }
    )

    existente = db.execute(
        text(
            """
            SELECT id
            FROM notificacion
            WHERE evento_clave = :evento_clave
            LIMIT 1
            """
        ),
        {"evento_clave": evento_clave},
    ).first()

    if existente is not None:
        notificacion_id = int(existente[0])
        db.execute(
            text(
                """
                UPDATE notificacion
                SET titulo = :titulo,
                    cuerpo = :cuerpo,
                    datos = :datos,
                    leida = FALSE,
                    fecha_lectura = NULL,
                    cancelada_en = NULL,
                    fecha_actualizacion = :ahora
                WHERE id = :notificacion_id
                """
            ),
            {
                "notificacion_id": notificacion_id,
                "titulo": titulo[:150],
                "cuerpo": cuerpo,
                "datos": datos_json,
                "ahora": ahora,
            },
        )
    else:
        es_postgres = db.connection().dialect.name == "postgresql"
        sql_insert = (
            """
            INSERT INTO notificacion (
                usuario_id, tipo, evento_clave, titulo, cuerpo, datos,
                cita_id, leida, fecha_lectura, cancelada_en,
                fecha_creacion, fecha_actualizacion
            )
            VALUES (
                :usuario_id, :tipo, :evento_clave, :titulo, :cuerpo,
                CAST(:datos AS json), :cita_id, FALSE, NULL, NULL,
                :ahora, :ahora
            )
            RETURNING id
            """
            if es_postgres
            else """
            INSERT INTO notificacion (
                usuario_id, tipo, evento_clave, titulo, cuerpo, datos,
                cita_id, leida, fecha_lectura, cancelada_en,
                fecha_creacion, fecha_actualizacion
            )
            VALUES (
                :usuario_id, :tipo, :evento_clave, :titulo, :cuerpo,
                :datos, :cita_id, FALSE, NULL, NULL,
                :ahora, :ahora
            )
            """
        )
        res = db.execute(
            text(sql_insert),
            {
                "usuario_id": usuario_id,
                "tipo": TIPO_NOTIFICACION_OPERATIVA,
                "evento_clave": evento_clave,
                "titulo": titulo[:150],
                "cuerpo": cuerpo,
                "datos": datos_json,
                "cita_id": cita_id,
                "ahora": ahora,
            },
        )
        if es_postgres:
            notificacion_id = int(res.scalar_one())
        else:
            notificacion_id = int(
                db.scalar(
                    text(
                        "SELECT id FROM notificacion WHERE evento_clave = :k LIMIT 1"
                    ),
                    {"k": evento_clave},
                )
            )

        _encolar_entrega_fcm_si_existe(
            db,
            notificacion_id=notificacion_id,
            usuario_id=usuario_id,
            ahora=ahora,
        )

    return obtener_notificacion_valoracion_por_id(
        db, notificacion_id, usuario_id,
    )


def _encolar_entrega_fcm_si_existe(
    db: Session,
    *,
    notificacion_id: int,
    usuario_id: int,
    ahora: datetime,
) -> None:
    """Encola entrega FCM en `notificacion_entrega` si el tenant tiene push."""
    if not (
        tabla_existe(db, "dispositivo_push")
        and tabla_existe(db, "notificacion_entrega")
    ):
        return

    try:
        with db.begin_nested():
            if tabla_existe(db, "preferencia_notificacion"):
                pref = db.scalar(
                    text(
                        """
                        SELECT push_habilitado
                        FROM preferencia_notificacion
                        WHERE usuario_id = :usuario_id
                        LIMIT 1
                        """
                    ),
                    {"usuario_id": usuario_id},
                )
                if pref is False:
                    return

            dispositivos = db.scalars(
                text(
                    """
                    SELECT id
                    FROM dispositivo_push
                    WHERE usuario_id = :usuario_id
                      AND activo = TRUE
                      AND revocado_en IS NULL
                    """
                ),
                {"usuario_id": usuario_id},
            ).all()

            for disp_id in dispositivos:
                ya_existe = db.scalar(
                    text(
                        """
                        SELECT 1
                        FROM notificacion_entrega
                        WHERE notificacion_id = :nid
                          AND dispositivo_id = :did
                        LIMIT 1
                        """
                    ),
                    {"nid": notificacion_id, "did": disp_id},
                )
                if ya_existe is not None:
                    continue
                db.execute(
                    text(
                        """
                        INSERT INTO notificacion_entrega (
                            notificacion_id, dispositivo_id, estado, proveedor,
                            intentos, fecha_creacion, fecha_actualizacion
                        )
                        VALUES (
                            :nid, :did, 'PENDIENTE', 'FCM',
                            0, :ahora, :ahora
                        )
                        """
                    ),
                    {"nid": notificacion_id, "did": disp_id, "ahora": ahora},
                )
    except Exception:
        return


def marcar_notificacion_leida(
    db: Session,
    notificacion_id: int,
) -> None:
    if not tabla_existe(db, "notificacion"):
        return
    ahora = datetime.now(timezone.utc)
    db.execute(
        text(
            """
            UPDATE notificacion
            SET leida = TRUE,
                fecha_lectura = COALESCE(fecha_lectura, :ahora),
                fecha_actualizacion = :ahora
            WHERE id = :notificacion_id
            """
        ),
        {"notificacion_id": notificacion_id, "ahora": ahora},
    )


def marcar_notificacion_valoracion_leida_por_consulta(
    db: Session,
    consulta_clinica_id: int,
    usuario_id: int,
) -> None:
    if not tabla_existe(db, "notificacion"):
        return
    ahora = datetime.now(timezone.utc)
    db.execute(
        text(
            """
            UPDATE notificacion
            SET leida = TRUE,
                fecha_lectura = COALESCE(fecha_lectura, :ahora),
                fecha_actualizacion = :ahora
            WHERE usuario_id = :usuario_id
              AND evento_clave = :evento_clave
              AND cancelada_en IS NULL
            """
        ),
        {
            "usuario_id": usuario_id,
            "evento_clave": evento_clave_valoracion(consulta_clinica_id),
            "ahora": ahora,
        },
    )
