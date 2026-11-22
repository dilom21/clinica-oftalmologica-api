"""Fase 3C.6: recordatorios automaticos, sin red ni bases reales."""

from datetime import date, datetime, time, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.modules.gestion_agenda_citas.models.models import Cita
from app.modules.notificaciones_push.models.models import Notificacion, NotificacionEntrega
from app.modules.notificaciones_push.orquestacion import (
    ESTADO_POLICY_NOT_CONFIGURED,
    ConfiguracionCiclo,
    ejecutar_ciclo_tenant,
)
from app.modules.notificaciones_push.services.recordatorios import (
    ESTADO_POLITICA_INVALIDA,
    ESTADO_POLICY_NOT_CONFIGURED as POLICY_NOT_CONFIGURED,
    PoliticaRecordatorios,
    generar_recordatorios,
)
from app.modules.notificaciones_push.services.worker_fcm import procesar_entregas

AHORA = datetime(2026, 12, 31, 15, 0, tzinfo=timezone.utc)  # 11:00 La Paz

DDL = (
    "CREATE TABLE rol (id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, descripcion TEXT, estado BOOLEAN NOT NULL, protegido BOOLEAN NOT NULL, fecha_creacion TIMESTAMP NOT NULL)",
    "CREATE TABLE usuario (id INTEGER PRIMARY KEY, correo TEXT NOT NULL, password_hash TEXT NOT NULL, estado BOOLEAN NOT NULL, fecha_creacion TIMESTAMP NOT NULL, rol_id INTEGER NOT NULL REFERENCES rol(id))",
    "CREATE TABLE paciente (id INTEGER PRIMARY KEY, usuario_id INTEGER REFERENCES usuario(id), nombres TEXT NOT NULL, apellidos TEXT NOT NULL, ci TEXT, fecha_nacimiento DATE, sexo TEXT, telefono TEXT, contacto_emergencia TEXT, fecha_registro TIMESTAMP NOT NULL, direccion TEXT, estado BOOLEAN NOT NULL)",
    "CREATE TABLE oftalmologo (id INTEGER PRIMARY KEY)",
    """CREATE TABLE cita (id INTEGER PRIMARY KEY, paciente_id INTEGER NOT NULL REFERENCES paciente(id), oftalmologo_id INTEGER NOT NULL REFERENCES oftalmologo(id), fecha DATE NOT NULL, hora_inicio TIME NOT NULL, hora_fin TIME NOT NULL, motivo TEXT, observaciones TEXT, estado VARCHAR(20) NOT NULL, canal TEXT, creado_por_usuario_id INTEGER REFERENCES usuario(id), fecha_registro TIMESTAMP NOT NULL, fecha_actualizacion TIMESTAMP NOT NULL)""",
    """CREATE TABLE dispositivo_push (id INTEGER PRIMARY KEY, usuario_id INTEGER NOT NULL REFERENCES usuario(id), token_fcm VARCHAR(1024) NOT NULL, plataforma VARCHAR(10) NOT NULL, instalacion_id TEXT, app_version TEXT, nombre_dispositivo TEXT, activo BOOLEAN NOT NULL DEFAULT 1, fecha_registro TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, fecha_actualizacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, fecha_ultimo_uso TIMESTAMP, revocado_en TIMESTAMP)""",
    """CREATE TABLE notificacion (id INTEGER PRIMARY KEY, usuario_id INTEGER NOT NULL REFERENCES usuario(id), tipo VARCHAR(30) NOT NULL, evento_clave VARCHAR(150) NOT NULL UNIQUE, titulo VARCHAR(150) NOT NULL, cuerpo TEXT NOT NULL, datos JSON, cita_id INTEGER REFERENCES cita(id), leida BOOLEAN NOT NULL DEFAULT 0, fecha_lectura TIMESTAMP, cancelada_en TIMESTAMP, fecha_creacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, fecha_actualizacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE notificacion_entrega (id INTEGER PRIMARY KEY, notificacion_id INTEGER NOT NULL REFERENCES notificacion(id), dispositivo_id INTEGER NOT NULL REFERENCES dispositivo_push(id), estado VARCHAR(25) NOT NULL DEFAULT 'PENDIENTE', proveedor VARCHAR(20) NOT NULL DEFAULT 'FCM', proveedor_mensaje_id TEXT, intentos SMALLINT NOT NULL DEFAULT 0, proximo_intento TIMESTAMP, bloqueado_hasta TIMESTAMP, lease_token VARCHAR(64), codigo_ultimo_error VARCHAR(100), fecha_creacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, fecha_actualizacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, fecha_aceptado TIMESTAMP, UNIQUE(notificacion_id, dispositivo_id))""",
    """CREATE TABLE preferencia_notificacion (id INTEGER PRIMARY KEY, usuario_id INTEGER NOT NULL UNIQUE REFERENCES usuario(id), push_habilitado BOOLEAN NOT NULL DEFAULT 1, citas_creadas BOOLEAN NOT NULL DEFAULT 1, citas_confirmadas BOOLEAN NOT NULL DEFAULT 1, citas_reprogramadas BOOLEAN NOT NULL DEFAULT 1, citas_canceladas BOOLEAN NOT NULL DEFAULT 1, recordatorios BOOLEAN NOT NULL DEFAULT 1, fecha_actualizacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
)


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool)
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        for ddl in DDL:
            conn.exec_driver_sql(ddl)
        conn.exec_driver_sql("INSERT INTO rol VALUES (1,'Paciente',NULL,1,0,'2026-01-01')")
        conn.exec_driver_sql("INSERT INTO usuario VALUES (1,'paciente@example.test','x',1,'2026-01-01',1)")
        conn.exec_driver_sql("INSERT INTO paciente VALUES (1,1,'Nombre','Prueba',NULL,NULL,NULL,NULL,NULL,'2026-01-01',NULL,1)")
        conn.exec_driver_sql("INSERT INTO oftalmologo VALUES (1)")
        conn.exec_driver_sql("INSERT INTO dispositivo_push (id,usuario_id,token_fcm,plataforma) VALUES (1,1,'token-ficticio-aaaaaaaaaaaa','ANDROID')")
    with Session(engine, autoflush=False) as session:
        yield session


def cita(db, *, cita_id=1, fecha="2027-01-01", hora="11:00:00", estado="PROGRAMADA"):
    fecha = date.fromisoformat(fecha) if isinstance(fecha, str) else fecha
    hora = time.fromisoformat(hora) if isinstance(hora, str) else hora
    db.execute(Cita.__table__.insert().values(
        id=cita_id, paciente_id=1, oftalmologo_id=1, fecha=fecha,
        hora_inicio=hora, hora_fin=time(12, 0), estado=estado,
        fecha_registro=AHORA, fecha_actualizacion=AHORA,
    ))
    db.commit()


def politica(*offsets, gracia=10, zona="America/La_Paz"):
    return PoliticaRecordatorios(
        anticipaciones_minutos=tuple(offsets), gracia_minutos=gracia,
        zona_horaria=zona,
    )


def contar(db, modelo):
    return db.scalar(select(func.count()).select_from(modelo))


def test_cita_en_ventana_crea_bandeja_y_entrega(db):
    cita(db)
    resultado = generar_recordatorios(db, politica=politica(1440), reloj=lambda: AHORA)
    assert resultado.creadas == 1
    assert contar(db, Notificacion) == 1
    assert contar(db, NotificacionEntrega) == 1


@pytest.mark.parametrize("fecha,hora,estado", [
    ("2027-01-02", "11:00:00", "PROGRAMADA"),
    ("2026-12-31", "10:59:00", "PROGRAMADA"),
    ("2027-01-01", "11:00:00", "CANCELADA"),
    ("2027-01-01", "11:00:00", "ATENDIDA"),
    ("2027-01-01", "11:00:00", "NO_ASISTIO"),
    ("2027-01-01", "11:00:00", "EN_ESPERA"),
])
def test_fuera_de_ventana_pasada_o_estado_no_elegible(db, fecha, hora, estado):
    cita(db, fecha=fecha, hora=hora, estado=estado)
    generar_recordatorios(db, politica=politica(1440), reloj=lambda: AHORA)
    assert contar(db, Notificacion) == 0


@pytest.mark.parametrize("tabla", ["paciente", "usuario"])
def test_destinatario_inactivo_se_omite(db, tabla):
    cita(db)
    db.connection().exec_driver_sql(f"UPDATE {tabla} SET estado=0 WHERE id=1")
    db.commit()
    resultado = generar_recordatorios(db, politica=politica(1440), reloj=lambda: AHORA)
    assert resultado.omitidas_sin_destinatario == 1
    assert contar(db, Notificacion) == 0


@pytest.mark.parametrize("campo", ["recordatorios", "push_habilitado"])
def test_preferencia_solo_impide_push_y_conserva_bandeja(db, campo):
    cita(db)
    db.connection().exec_driver_sql(
        f"INSERT INTO preferencia_notificacion (id,usuario_id,{campo}) VALUES (1,1,0)"
    )
    db.commit()
    generar_recordatorios(db, politica=politica(1440), reloj=lambda: AHORA)
    assert contar(db, Notificacion) == 1
    assert contar(db, NotificacionEntrega) == 0


def test_dos_ciclos_no_duplican(db):
    cita(db)
    primera = generar_recordatorios(db, politica=politica(1440), reloj=lambda: AHORA)
    segunda = generar_recordatorios(db, politica=politica(1440), reloj=lambda: AHORA)
    assert (primera.creadas, segunda.existentes) == (1, 1)
    assert contar(db, Notificacion) == contar(db, NotificacionEntrega) == 1


def test_reprogramacion_cancela_anterior_y_crea_nuevo(db):
    cita(db)
    generar_recordatorios(db, politica=politica(1440), reloj=lambda: AHORA)
    db.connection().exec_driver_sql("UPDATE cita SET fecha='2027-01-02', fecha_actualizacion='2026-12-31 15:01:00' WHERE id=1")
    db.commit()
    manana = datetime(2027, 1, 1, 15, 0, tzinfo=timezone.utc)
    resultado = generar_recordatorios(db, politica=politica(1440), reloj=lambda: manana)
    notas = list(db.scalars(select(Notificacion).order_by(Notificacion.id)))
    assert resultado.obsoletas_canceladas == 1
    assert len(notas) == 2 and notas[0].cancelada_en is not None and notas[1].cancelada_en is None


class ProveedorNoDebeLlamarse:
    configurado = True
    def enviar(self, **_kwargs):
        raise AssertionError("un recordatorio obsoleto nunca debe llegar al proveedor")


@pytest.mark.parametrize("mutacion", [
    "UPDATE cita SET fecha='2027-01-02' WHERE id=1",
    "UPDATE cita SET estado='CANCELADA' WHERE id=1",
    "UPDATE cita SET estado='ATENDIDA' WHERE id=1",
    "UPDATE cita SET estado='NO_ASISTIO' WHERE id=1",
])
def test_worker_revalida_antes_de_enviar(db, mutacion):
    cita(db)
    generar_recordatorios(db, politica=politica(1440), reloj=lambda: AHORA)
    db.connection().exec_driver_sql(mutacion)
    db.commit()
    resultado = procesar_entregas(db, proveedor=ProveedorNoDebeLlamarse(), reloj=lambda: AHORA)
    entrega = db.scalar(select(NotificacionEntrega))
    assert resultado.omitidas == 1
    assert entrega.estado == "OMITIDO" and entrega.codigo_ultimo_error == "RECORDATORIO_OBSOLETO"


@pytest.mark.parametrize("policy,estado", [
    (PoliticaRecordatorios(), POLICY_NOT_CONFIGURED),
    (politica(0), ESTADO_POLITICA_INVALIDA),
    (politica(60, zona="Zona/Inexistente"), ESTADO_POLITICA_INVALIDA),
])
def test_politica_ausente_o_invalida_falla_cerrado(db, policy, estado):
    cita(db)
    assert generar_recordatorios(db, politica=policy, reloj=lambda: AHORA).estado == estado
    assert contar(db, Notificacion) == 0


def test_dst_ambiguo_falla_cerrado(db):
    cita(db, fecha="2026-11-01", hora="01:30:00")
    ahora = datetime(2026, 10, 31, 5, 30, tzinfo=timezone.utc)
    resultado = generar_recordatorios(
        db, politica=politica(1440, zona="America/New_York"), reloj=lambda: ahora,
    )
    assert resultado.omitidas_tiempo_invalido == 1
    assert contar(db, Notificacion) == 0


def test_mensaje_payload_y_resultado_no_contienen_pii(db):
    cita(db)
    resultado = generar_recordatorios(db, politica=politica(1440), reloj=lambda: AHORA)
    nota = db.scalar(select(Notificacion))
    serializado = repr((nota.titulo, nota.cuerpo, nota.datos, resultado))
    for prohibido in ("Nombre", "Prueba", "paciente@example", "token-ficticio", "diagnostico", "motivo"):
        assert prohibido not in serializado


def test_aislamiento_por_sesion_con_ids_locales_iguales(db):
    cita(db)
    generar_recordatorios(db, politica=politica(1440), reloj=lambda: AHORA)
    engine_b = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool)
    with engine_b.begin() as conn:
        for ddl in DDL:
            conn.exec_driver_sql(ddl)
    with Session(engine_b) as tenant_b:
        assert contar(tenant_b, Notificacion) == 0


def test_orquestador_declara_policy_not_configured_sin_abrir_sesion():
    fuente = SimpleNamespace(abrir=lambda: pytest.fail("no debe abrir sesiones"))
    config = ConfiguracionCiclo(recordatorios=PoliticaRecordatorios())
    resultado = ejecutar_ciclo_tenant(fuente, proveedor=SimpleNamespace(configurado=False), configuracion=config)
    assert resultado.estado == ESTADO_POLICY_NOT_CONFIGURED
    assert resultado.codigo_salida == 5
