"""Pruebas unitarias CU17 - Registrar tratamientos, indicaciones y recetas.

Fixture SQLite en memoria propio: no se conecta a la BD compartida ni genera
DDL desde los modelos SQLAlchemy.
"""

from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core import dependencies
from app.core.security import crear_access_token
from app.database import session
from app.main import app
from app.modules.gestion_historial_clinico.models.models import (
    ConsultaClinica,
    DetalleReceta,
    Indicacion,
    Receta,
    Tratamiento,
)
from app.modules.gestion_historial_clinico.repositories import repository as repo
from app.modules.gestion_historial_clinico.schemas.schemas import RecetaCrear
from app.modules.gestion_historial_clinico.services import service
from app.modules.gestion_usuarios_seguridad.models.models import (
    Bitacora,
    Usuario,
)

URL = "/historial-clinico/consultas"
URL_RECETAS = "/historial-clinico/recetas"

CONSULTA_PROPIA = 100
CONSULTA_AJENA = 200
CONSULTA_INACTIVA = 300

USUARIO_OFTALMOLOGO = (7, 1)
USUARIO_ADMIN = (8, 2)
USUARIO_RECEPCIONISTA = (9, 3)
USUARIO_PACIENTE = (10, 4)
USUARIO_OFTALMOLOGO_SIN_PERFIL = (11, 1)
USUARIO_INVITADO = (13, 5)

DATOS_TRATAMIENTO = {
    "descripcion": "Corrección óptica permanente",
    "observaciones": "Control en 7 días",
}
DATOS_INDICACION = {"descripcion": "Evitar esfuerzo visual prolongado"}
DATOS_RECETA = {
    "observaciones": "Aplicar después del desayuno",
    "detalles": [
        {
            "medicamento": "Ketorolaco",
            "presentacion": "Gotas 0.5%",
            "dosis": "1 gota",
            "frecuencia": "cada 8 horas",
            "duracion": "5 días",
            "indicaciones": "En el ojo afectado",
        }
    ],
}


def _ddl_tablas_cu17():
    """DDL exclusivo de SQLite (nunca se ejecuta sobre PostgreSQL)."""
    return [
        """CREATE TABLE rol (
            id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, descripcion TEXT,
            estado BOOLEAN NOT NULL, protegido BOOLEAN NOT NULL,
            fecha_creacion TIMESTAMP NOT NULL
        )""",
        """CREATE TABLE usuario (
            id INTEGER PRIMARY KEY, correo TEXT NOT NULL, password_hash TEXT NOT NULL,
            estado BOOLEAN NOT NULL, fecha_creacion TIMESTAMP NOT NULL,
            rol_id INTEGER NOT NULL REFERENCES rol(id)
        )""",
        """CREATE TABLE funcion (
            id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE accion (
            id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE rol_funcion (
            id INTEGER PRIMARY KEY, rol_id INTEGER NOT NULL REFERENCES rol(id),
            funcion_id INTEGER NOT NULL REFERENCES funcion(id),
            accion_id INTEGER NOT NULL REFERENCES accion(id),
            UNIQUE (rol_id, funcion_id)
        )""",
        """CREATE TABLE paciente (
            id INTEGER PRIMARY KEY, usuario_id INTEGER REFERENCES usuario(id),
            nombres TEXT NOT NULL, apellidos TEXT NOT NULL, ci TEXT,
            fecha_nacimiento DATE, sexo TEXT, telefono TEXT, contacto_emergencia TEXT,
            fecha_registro TIMESTAMP NOT NULL, direccion TEXT, estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE oftalmologo (
            id INTEGER PRIMARY KEY,
            usuario_id INTEGER NOT NULL UNIQUE REFERENCES usuario(id),
            matricula TEXT NOT NULL UNIQUE, nombres TEXT NOT NULL,
            apellidos TEXT NOT NULL, especialidad TEXT, estado BOOLEAN NOT NULL,
            fecha_registro TIMESTAMP NOT NULL
        )""",
        """CREATE TABLE cita (
            id INTEGER PRIMARY KEY, paciente_id INTEGER NOT NULL REFERENCES paciente(id),
            oftalmologo_id INTEGER NOT NULL REFERENCES oftalmologo(id), fecha DATE NOT NULL,
            hora_inicio TIME NOT NULL, hora_fin TIME NOT NULL, motivo TEXT,
            observaciones TEXT, estado TEXT NOT NULL, canal TEXT,
            creado_por_usuario_id INTEGER REFERENCES usuario(id),
            fecha_registro TIMESTAMP NOT NULL, fecha_actualizacion TIMESTAMP NOT NULL
        )""",
        """CREATE TABLE historial_clinico (
            id INTEGER PRIMARY KEY,
            paciente_id INTEGER NOT NULL UNIQUE REFERENCES paciente(id) ON DELETE RESTRICT,
            fecha_apertura TIMESTAMP NOT NULL, observaciones_generales TEXT,
            estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE antecedente_clinico (
            id INTEGER PRIMARY KEY,
            historial_clinico_id INTEGER NOT NULL REFERENCES historial_clinico(id)
                ON DELETE CASCADE,
            tipo VARCHAR(30) NOT NULL CHECK (tipo IN (
                'ALERGIA', 'ENFERMEDAD', 'CIRUGIA', 'MEDICAMENTO',
                'ANTECEDENTE_FAMILIAR', 'OTRO'
            )),
            descripcion TEXT NOT NULL, fecha_registro TIMESTAMP NOT NULL,
            estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE bitacora (
            id INTEGER PRIMARY KEY, usuario_id INTEGER REFERENCES usuario(id),
            fecha_hora TIMESTAMP NOT NULL, ip TEXT, accion TEXT NOT NULL,
            entidad_afectada TEXT, id_registro_afectado INTEGER, descripcion TEXT
        )""",
        """CREATE TABLE consulta_clinica (
            id INTEGER PRIMARY KEY,
            historial_clinico_id INTEGER NOT NULL REFERENCES historial_clinico(id),
            cita_id INTEGER UNIQUE REFERENCES cita(id),
            oftalmologo_id INTEGER NOT NULL REFERENCES oftalmologo(id),
            fecha_consulta TIMESTAMP NOT NULL, motivo_consulta VARCHAR(255),
            anamnesis TEXT, observaciones TEXT, estado BOOLEAN NOT NULL
        )""",
        # --- CU17: esquema FINAL (alineado con el diagrama de clases) ---
        """CREATE TABLE tratamiento (
            id INTEGER PRIMARY KEY,
            consulta_clinica_id INTEGER NOT NULL REFERENCES consulta_clinica(id)
                ON DELETE CASCADE,
            descripcion TEXT NOT NULL, observaciones TEXT,
            fecha_inicio DATE, fecha_fin DATE, estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE indicacion (
            id INTEGER PRIMARY KEY,
            consulta_clinica_id INTEGER NOT NULL REFERENCES consulta_clinica(id)
                ON DELETE CASCADE,
            descripcion TEXT NOT NULL, fecha_registro TIMESTAMP NOT NULL
        )""",
        """CREATE TABLE receta (
            id INTEGER PRIMARY KEY,
            consulta_clinica_id INTEGER NOT NULL REFERENCES consulta_clinica(id)
                ON DELETE CASCADE,
            fecha_emision TIMESTAMP NOT NULL, observaciones TEXT,
            estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE detalle_receta (
            id INTEGER PRIMARY KEY,
            receta_id INTEGER NOT NULL REFERENCES receta(id) ON DELETE CASCADE,
            medicamento VARCHAR(150) NOT NULL, presentacion VARCHAR(100),
            dosis VARCHAR(100), frecuencia VARCHAR(100), duracion VARCHAR(100),
            indicaciones TEXT
        )""",
        """CREATE TABLE diagnostico (
            id INTEGER PRIMARY KEY,
            consulta_clinica_id INTEGER NOT NULL REFERENCES consulta_clinica(id)
                ON DELETE CASCADE,
            nombre VARCHAR(150) NOT NULL,
            descripcion TEXT,
            fecha_diagnostico TIMESTAMP NOT NULL,
            estado BOOLEAN NOT NULL
        )""",
    ]


def _crear_esquema_cu17(engine):
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        for ddl in _ddl_tablas_cu17():
            conn.exec_driver_sql(ddl)

        conn.exec_driver_sql(
            """INSERT INTO rol VALUES
                (1, 'Oftalmólogo', NULL, 1, 0, '2026-01-01'),
                (2, 'Administrador', NULL, 1, 1, '2026-01-01'),
                (3, 'Recepcionista', NULL, 1, 0, '2026-01-01'),
                (4, 'Paciente', NULL, 1, 0, '2026-01-01'),
                (5, 'Invitado', NULL, 1, 0, '2026-01-01')"""
        )
        conn.exec_driver_sql(
            """INSERT INTO usuario VALUES
                (7, 'oftalmo@test', 'x', 1, '2026-01-01', 1),
                (8, 'admin@test', 'x', 1, '2026-01-01', 2),
                (9, 'recepcion@test', 'x', 1, '2026-01-01', 3),
                (10, 'paciente@test', 'x', 1, '2026-01-01', 4),
                (11, 'sinperfil@test', 'x', 1, '2026-01-01', 1),
                (12, 'oftalmo2@test', 'x', 1, '2026-01-01', 1),
                (13, 'invitado@test', 'x', 1, '2026-01-01', 5)"""
        )
        conn.exec_driver_sql(
            "INSERT INTO accion VALUES (1, 'LECTURA', 1), (2, 'ESCRITURA', 1), (3, 'AMBAS', 1)"
        )
        conn.exec_driver_sql(
            """INSERT INTO funcion VALUES
                (15, 'Consultar historial clínico', 1),
                (17, 'Registrar consulta clínica', 1),
                (18, 'Registrar diagnóstico', 1),
                (19, 'Registrar tratamientos, indicaciones y recetas', 1)"""
        )
        # rol 1: Oftalmólogo (CU17 en ESCRITURA)
        # rol 2: Administrador (CU17 en AMBAS, igual que CU15/CU16)
        # rol 3: Recepcionista -> CU17 solo LECTURA (el POST debe dar 403)
        # rol 4: Paciente -> sin CU17
        # rol 5: Invitado -> sin permisos clínicos
        conn.exec_driver_sql(
            """INSERT INTO rol_funcion VALUES
                (1, 1, 15, 1), (2, 1, 17, 2), (3, 1, 18, 2), (4, 1, 19, 2),
                (5, 2, 15, 3), (6, 2, 17, 3), (7, 2, 18, 3), (8, 2, 19, 3),
                (9, 3, 15, 1), (10, 3, 17, 2), (11, 3, 19, 1),
                (12, 4, 15, 1),
                (13, 5, 15, 1)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO paciente
                (id, usuario_id, nombres, apellidos, ci, fecha_registro, estado) VALUES
                (1, NULL, 'Ana', 'Pérez', 'CI-001', '2026-09-01', 1),
                (2, NULL, 'Luis', 'Gómez', 'CI-002', '2026-09-01', 1)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO oftalmologo
                (id, usuario_id, matricula, nombres, apellidos, especialidad, estado, fecha_registro) VALUES
                (1, 7, 'MAT-001', 'Salet', 'Ejemplo', 'Oftalmología General', 1, '2026-01-01'),
                (2, 12, 'MAT-002', 'Otro', 'Doctor', 'Retina', 1, '2026-01-01')"""
        )
        conn.exec_driver_sql(
            """INSERT INTO cita
                (id, paciente_id, oftalmologo_id, fecha, hora_inicio, hora_fin, estado,
                 fecha_registro, fecha_actualizacion) VALUES
                (4, 1, 1, '2026-09-03', '08:00:00', '09:00:00', 'ATENDIDA',
                 '2026-09-03', '2026-09-03')"""
        )
        conn.exec_driver_sql(
            """INSERT INTO historial_clinico
                (id, paciente_id, fecha_apertura, observaciones_generales, estado) VALUES
                (10, 1, '2026-09-01', NULL, 1),
                (20, 2, '2026-09-01', NULL, 1)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO antecedente_clinico
                (id, historial_clinico_id, tipo, descripcion, fecha_registro, estado) VALUES
                (100, 10, 'ALERGIA', 'Alergia a la penicilina', '2026-09-01', 1),
                (101, 10, 'OTRO', 'Antecedente inactivo', '2026-09-01', 0)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO consulta_clinica
                (id, historial_clinico_id, cita_id, oftalmologo_id, fecha_consulta,
                 motivo_consulta, anamnesis, observaciones, estado) VALUES
                (100, 10, 4, 1, '2026-09-03 08:00:00', 'Consulta previa', NULL, NULL, 1),
                (200, 20, NULL, 2, '2026-09-04 08:00:00', 'Otra consulta', NULL, NULL, 1),
                (300, 10, NULL, 1, '2026-09-05 08:00:00', 'Consulta inactiva', NULL, NULL, 0)"""
        )


@pytest.fixture
def db_cu17():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    _crear_esquema_cu17(engine)

    with Session(engine, autoflush=False) as db:
        yield db

    engine.dispose()


@pytest.fixture
def cliente_cu17(db_cu17):
    def obtener_db_local():
        yield db_cu17

    anteriores = app.dependency_overrides.copy()
    app.dependency_overrides[session.get_db] = obtener_db_local
    app.dependency_overrides[dependencies.get_db] = obtener_db_local
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(anteriores)


def autenticar(client, usuario):
    usuario_id, rol_id = usuario
    client.headers["Authorization"] = (
        f"Bearer {crear_access_token(usuario_id, rol_id)}"
    )


def contar(db, modelo):
    return db.scalar(select(func.count()).select_from(modelo))


# =========================================================
# TRATAMIENTOS
# =========================================================


def test_oftalmologo_registra_tratamiento_y_bitacora(cliente_cu17, db_cu17):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/tratamientos", json=DATOS_TRATAMIENTO,
    )
    assert respuesta.status_code == 201
    datos = respuesta.json()
    assert set(datos) == {
        "id", "consulta_clinica_id", "descripcion", "observaciones",
        "fecha_inicio", "fecha_fin", "estado",
    }
    assert datos["consulta_clinica_id"] == CONSULTA_PROPIA
    assert datos["descripcion"] == "Corrección óptica permanente"
    assert datos["observaciones"] == "Control en 7 días"
    assert datos["fecha_inicio"] is None
    assert datos["fecha_fin"] is None
    assert datos["estado"] is True

    tratamiento = db_cu17.get(Tratamiento, datos["id"])
    assert tratamiento.consulta_clinica_id == CONSULTA_PROPIA
    assert tratamiento.estado is True

    bitacora = db_cu17.scalar(select(Bitacora))
    assert bitacora.usuario_id == 7
    assert bitacora.accion == "REGISTRAR_TRATAMIENTO"
    assert bitacora.entidad_afectada == "tratamiento"
    assert bitacora.id_registro_afectado == tratamiento.id
    assert bitacora.descripcion == "Tratamiento registrado"


def test_tratamiento_con_fechas_sin_observaciones(cliente_cu17):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/tratamientos",
        json={
            "descripcion": "  Tratamiento farmacológico durante 7 días  ",
            "fecha_inicio": "2026-09-03",
            "fecha_fin": "2026-09-10",
        },
    )
    assert respuesta.status_code == 201
    datos = respuesta.json()
    assert datos["descripcion"] == "Tratamiento farmacológico durante 7 días"
    assert datos["observaciones"] is None
    assert datos["fecha_inicio"] == "2026-09-03"
    assert datos["fecha_fin"] == "2026-09-10"


def test_tratamiento_fechas_iguales_aceptadas(cliente_cu17):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/tratamientos",
        json={
            "descripcion": "Tratamiento de un día",
            "fecha_inicio": "2026-09-03",
            "fecha_fin": "2026-09-03",
        },
    )
    assert respuesta.status_code == 201
    assert respuesta.json()["fecha_fin"] == "2026-09-03"


@pytest.mark.parametrize("cambios", [
    {},
    {"descripcion": ""},
    {"descripcion": "   "},
    {"descripcion": "\t\n"},
    {"descripcion": None},
    {"fecha_inicio": "2026-09-10", "fecha_fin": "2026-09-03"},
    {"id": 99},
    {"consulta_clinica_id": 200},
    {"estado": False},
    {"usuario_id": 7},
    {"oftalmologo_id": 1},
    {"campo_desconocido": "x"},
])
def test_tratamiento_validaciones_422(cliente_cu17, db_cu17, cambios):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    if cambios == {}:
        cuerpo = {"observaciones": "sin descripción"}
    else:
        cuerpo = {**DATOS_TRATAMIENTO, **cambios}

    respuesta = cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/tratamientos", json=cuerpo,
    )
    assert respuesta.status_code == 422
    assert contar(db_cu17, Tratamiento) == 0
    assert contar(db_cu17, Bitacora) == 0


@pytest.mark.parametrize("consulta_id", [999, CONSULTA_INACTIVA])
def test_tratamiento_consulta_inexistente_o_inactiva_404(
    cliente_cu17, db_cu17, consulta_id,
):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu17.post(
        f"{URL}/{consulta_id}/tratamientos", json=DATOS_TRATAMIENTO,
    )
    assert respuesta.status_code == 404
    assert "consulta" in respuesta.json()["detail"].lower()
    assert contar(db_cu17, Tratamiento) == 0
    assert contar(db_cu17, Bitacora) == 0


def test_tratamiento_consulta_de_otro_oftalmologo_403(cliente_cu17, db_cu17):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu17.post(
        f"{URL}/{CONSULTA_AJENA}/tratamientos", json=DATOS_TRATAMIENTO,
    )
    assert respuesta.status_code == 403
    assert "pertenece" in respuesta.json()["detail"].lower()
    assert contar(db_cu17, Tratamiento) == 0
    assert contar(db_cu17, Bitacora) == 0


@pytest.mark.parametrize("usuario", [
    USUARIO_RECEPCIONISTA, USUARIO_PACIENTE,
])
def test_tratamiento_roles_sin_permiso_no_registran(cliente_cu17, db_cu17, usuario):
    """Recepcionista (CU17 en LECTURA) y Paciente (sin CU17) nunca registran.

    El Administrador ya no forma parte de este grupo: tiene CU17 en AMBAS y
    acceso total (ver la sección ADMINISTRADOR más abajo).
    """
    autenticar(cliente_cu17, usuario)
    respuesta = cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/tratamientos", json=DATOS_TRATAMIENTO,
    )
    assert respuesta.status_code == 403
    assert contar(db_cu17, Tratamiento) == 0
    assert contar(db_cu17, Bitacora) == 0


def test_tratamiento_rol_sin_permiso_cu17(cliente_cu17, db_cu17):
    autenticar(cliente_cu17, USUARIO_INVITADO)
    respuesta = cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/tratamientos", json=DATOS_TRATAMIENTO,
    )
    assert respuesta.status_code == 403
    assert "No tiene permiso" in respuesta.json()["detail"]
    assert contar(db_cu17, Tratamiento) == 0


def test_tratamiento_permiso_lectura_no_alcanza_para_escribir(
    cliente_cu17, db_cu17,
):
    """La Recepcionista tiene CU17 en LECTURA: el POST exige ESCRITURA."""
    autenticar(cliente_cu17, USUARIO_RECEPCIONISTA)
    respuesta = cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/tratamientos", json=DATOS_TRATAMIENTO,
    )
    assert respuesta.status_code == 403
    assert "ESCRITURA" in respuesta.json()["detail"]
    assert contar(db_cu17, Tratamiento) == 0


def test_tratamiento_oftalmologo_sin_perfil_activo(cliente_cu17, db_cu17):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO_SIN_PERFIL)
    respuesta = cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/tratamientos", json=DATOS_TRATAMIENTO,
    )
    assert respuesta.status_code == 403
    assert "perfil de oftalmólogo" in respuesta.json()["detail"]
    assert contar(db_cu17, Tratamiento) == 0
    assert contar(db_cu17, Bitacora) == 0


def test_tratamiento_requiere_autenticacion(cliente_cu17):
    respuesta = cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/tratamientos", json=DATOS_TRATAMIENTO,
    )
    # Sin token nunca puede crear: HTTPBearer responde 401/403 según versión.
    assert respuesta.status_code in (401, 403)


@pytest.mark.parametrize("fallo", ["bitacora", "commit", "flush", "refresh"])
def test_tratamiento_rollback_atomico(cliente_cu17, db_cu17, monkeypatch, fallo):
    bitacora_real = service.registrar_bitacora

    def fallar(*args, **kwargs):
        raise SQLAlchemyError("Fallo de persistencia simulado")

    def bitacora_y_fallo(*args, **kwargs):
        bitacora_real(*args, **kwargs)
        fallar()

    with monkeypatch.context() as patch:
        if fallo == "bitacora":
            patch.setattr(service, "registrar_bitacora", bitacora_y_fallo)
        else:
            patch.setattr(db_cu17, fallo, fallar)

        autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
        with pytest.raises(SQLAlchemyError):
            cliente_cu17.post(
                f"{URL}/{CONSULTA_PROPIA}/tratamientos",
                json=DATOS_TRATAMIENTO,
            )

    assert contar(db_cu17, Tratamiento) == 0
    assert contar(db_cu17, Bitacora) == 0


def test_repositorio_tratamiento_sin_commit():
    """El Repository hace add/flush/refresh pero NUNCA commit."""
    db = MagicMock()
    tratamiento = repo.crear_tratamiento(
        db,
        consulta_clinica_id=CONSULTA_PROPIA,
        descripcion="Tratamiento de prueba",
        observaciones=None,
        fecha_inicio=date(2026, 9, 3),
        fecha_fin=date(2026, 9, 10),
    )
    assert tratamiento.consulta_clinica_id == CONSULTA_PROPIA
    assert tratamiento.descripcion == "Tratamiento de prueba"
    assert tratamiento.fecha_inicio == date(2026, 9, 3)
    assert tratamiento.fecha_fin == date(2026, 9, 10)
    assert tratamiento.estado is True
    db.add.assert_called_once_with(tratamiento)
    db.flush.assert_called_once()
    db.refresh.assert_called_once_with(tratamiento)
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_listar_tratamientos_solo_activos_de_esa_consulta(cliente_cu17, db_cu17):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    primero = cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/tratamientos",
        json={"descripcion": "Primero"},
    ).json()
    segundo = cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/tratamientos",
        json={"descripcion": "Segundo"},
    ).json()
    # Tratamiento inactivo de la misma consulta: no debe listarse.
    db_cu17.add(Tratamiento(
        consulta_clinica_id=CONSULTA_PROPIA,
        descripcion="Inactivo",
        estado=False,
    ))
    # Tratamiento activo de otra consulta: no debe listarse aquí.
    db_cu17.add(Tratamiento(
        consulta_clinica_id=CONSULTA_AJENA,
        descripcion="Otra consulta",
        estado=True,
    ))
    db_cu17.commit()

    respuesta = cliente_cu17.get(f"{URL}/{CONSULTA_PROPIA}/tratamientos")
    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert [d["id"] for d in datos] == [segundo["id"], primero["id"]]
    assert all(d["estado"] is True for d in datos)


def test_listar_tratamientos_consulta_inexistente_o_inactiva(cliente_cu17):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    for consulta_id in (999, CONSULTA_INACTIVA):
        respuesta = cliente_cu17.get(f"{URL}/{consulta_id}/tratamientos")
        assert respuesta.status_code == 404


def test_listar_tratamientos_es_reutilizable_por_lectura(cliente_cu17):
    """El GET exige "Consultar historial clínico" (LECTURA), no CU17."""
    autenticar(cliente_cu17, USUARIO_PACIENTE)
    respuesta = cliente_cu17.get(f"{URL}/{CONSULTA_PROPIA}/tratamientos")
    assert respuesta.status_code == 200
    assert respuesta.json() == []


# =========================================================
# INDICACIONES
# =========================================================


def test_registrar_indicacion_y_bitacora(cliente_cu17, db_cu17):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/indicaciones", json=DATOS_INDICACION,
    )
    assert respuesta.status_code == 201
    datos = respuesta.json()
    assert set(datos) == {
        "id", "consulta_clinica_id", "descripcion", "fecha_registro",
    }
    assert datos["consulta_clinica_id"] == CONSULTA_PROPIA
    assert datos["descripcion"] == "Evitar esfuerzo visual prolongado"
    assert "estado" not in datos  # Indicacion no posee estado

    indicacion = db_cu17.get(Indicacion, datos["id"])
    assert indicacion.consulta_clinica_id == CONSULTA_PROPIA
    assert indicacion.fecha_registro.date() == datetime.now(timezone.utc).date()

    bitacora = db_cu17.scalar(select(Bitacora))
    assert bitacora.accion == "REGISTRAR_INDICACION"
    assert bitacora.entidad_afectada == "indicacion"
    assert bitacora.id_registro_afectado == indicacion.id
    assert bitacora.descripcion == "Indicación registrada"


@pytest.mark.parametrize("cuerpo", [
    {},
    {"descripcion": ""},
    {"descripcion": "   "},
    {"descripcion": None},
    {"descripcion": "x", "estado": True},
    {"descripcion": "x", "consulta_clinica_id": 200},
    {"descripcion": "x", "fecha_registro": "2026-01-01T00:00:00Z"},
    {"campo_desconocido": "x"},
])
def test_indicacion_validaciones_422(cliente_cu17, db_cu17, cuerpo):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/indicaciones", json=cuerpo,
    )
    assert respuesta.status_code == 422
    assert contar(db_cu17, Indicacion) == 0
    assert contar(db_cu17, Bitacora) == 0


def test_multiples_indicaciones_en_la_misma_consulta(cliente_cu17):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    for texto in (
        "Evitar esfuerzo visual prolongado",
        "Realizar pausas visuales periódicas",
        "Acudir a control si presenta pérdida súbita de visión",
    ):
        respuesta = cliente_cu17.post(
            f"{URL}/{CONSULTA_PROPIA}/indicaciones",
            json={"descripcion": texto},
        )
        assert respuesta.status_code == 201

    listado = cliente_cu17.get(f"{URL}/{CONSULTA_PROPIA}/indicaciones").json()
    assert len(listado) == 3


def test_indicacion_consulta_de_otro_oftalmologo_403(cliente_cu17, db_cu17):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu17.post(
        f"{URL}/{CONSULTA_AJENA}/indicaciones", json=DATOS_INDICACION,
    )
    assert respuesta.status_code == 403
    assert contar(db_cu17, Indicacion) == 0
    assert contar(db_cu17, Bitacora) == 0


def test_indicacion_consulta_inexistente_o_inactiva_404(cliente_cu17):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    for consulta_id in (999, CONSULTA_INACTIVA):
        respuesta = cliente_cu17.post(
            f"{URL}/{consulta_id}/indicaciones", json=DATOS_INDICACION,
        )
        assert respuesta.status_code == 404


@pytest.mark.parametrize("usuario", [
    USUARIO_RECEPCIONISTA, USUARIO_PACIENTE,
])
def test_indicacion_roles_sin_permiso_no_registran(cliente_cu17, db_cu17, usuario):
    """Recepcionista (CU17 en LECTURA) y Paciente (sin CU17) nunca registran."""
    autenticar(cliente_cu17, usuario)
    respuesta = cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/indicaciones", json=DATOS_INDICACION,
    )
    assert respuesta.status_code == 403
    assert contar(db_cu17, Indicacion) == 0


@pytest.mark.parametrize("fallo", ["bitacora", "commit", "flush", "refresh"])
def test_indicacion_rollback_atomico(cliente_cu17, db_cu17, monkeypatch, fallo):
    bitacora_real = service.registrar_bitacora

    def fallar(*args, **kwargs):
        raise SQLAlchemyError("Fallo de persistencia simulado")

    def bitacora_y_fallo(*args, **kwargs):
        bitacora_real(*args, **kwargs)
        fallar()

    with monkeypatch.context() as patch:
        if fallo == "bitacora":
            patch.setattr(service, "registrar_bitacora", bitacora_y_fallo)
        else:
            patch.setattr(db_cu17, fallo, fallar)

        autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
        with pytest.raises(SQLAlchemyError):
            cliente_cu17.post(
                f"{URL}/{CONSULTA_PROPIA}/indicaciones",
                json=DATOS_INDICACION,
            )

    assert contar(db_cu17, Indicacion) == 0
    assert contar(db_cu17, Bitacora) == 0


def test_repositorio_indicacion_sin_commit():
    db = MagicMock()
    antes = datetime.now(timezone.utc)
    indicacion = repo.crear_indicacion(
        db, consulta_clinica_id=CONSULTA_PROPIA, descripcion="Indicación",
    )
    assert antes <= indicacion.fecha_registro <= datetime.now(timezone.utc)
    assert indicacion.fecha_registro.tzinfo == timezone.utc
    db.flush.assert_called_once()
    db.refresh.assert_called_once_with(indicacion)
    db.commit.assert_not_called()


def test_listar_indicaciones_orden_estable(cliente_cu17, db_cu17):
    """Orden: fecha_registro DESC, id DESC."""
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    ids = []
    for texto in ("Uno", "Dos", "Tres"):
        ids.append(cliente_cu17.post(
            f"{URL}/{CONSULTA_PROPIA}/indicaciones",
            json={"descripcion": texto},
        ).json()["id"])

    # Indicación de otra consulta: no debe aparecer.
    db_cu17.add(Indicacion(
        consulta_clinica_id=CONSULTA_AJENA,
        descripcion="Ajena",
        fecha_registro=datetime.now(timezone.utc),
    ))
    db_cu17.commit()

    datos = cliente_cu17.get(f"{URL}/{CONSULTA_PROPIA}/indicaciones").json()
    assert [d["id"] for d in datos] == sorted(ids, reverse=True)
    fechas = [d["fecha_registro"] for d in datos]
    assert fechas == sorted(fechas, reverse=True)


def test_listar_indicaciones_consulta_inexistente_o_inactiva(cliente_cu17):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    for consulta_id in (999, CONSULTA_INACTIVA):
        respuesta = cliente_cu17.get(f"{URL}/{consulta_id}/indicaciones")
        assert respuesta.status_code == 404


# =========================================================
# RECETAS
# =========================================================


def test_registrar_receta_con_un_medicamento(cliente_cu17, db_cu17):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/recetas", json=DATOS_RECETA,
    )
    assert respuesta.status_code == 201
    datos = respuesta.json()
    assert set(datos) == {
        "id", "consulta_clinica_id", "fecha_emision", "observaciones",
        "estado", "detalles",
    }
    assert datos["consulta_clinica_id"] == CONSULTA_PROPIA
    assert datos["observaciones"] == "Aplicar después del desayuno"
    assert datos["estado"] is True
    assert datos["fecha_emision"][:10] == datetime.now(timezone.utc).date().isoformat()

    assert len(datos["detalles"]) == 1
    detalle = datos["detalles"][0]
    assert set(detalle) == {
        "id", "receta_id", "medicamento", "presentacion", "dosis",
        "frecuencia", "duracion", "indicaciones",
    }
    assert detalle["receta_id"] == datos["id"]
    assert detalle["medicamento"] == "Ketorolaco"
    assert detalle["presentacion"] == "Gotas 0.5%"
    assert detalle["dosis"] == "1 gota"
    assert detalle["frecuencia"] == "cada 8 horas"
    assert detalle["duracion"] == "5 días"
    assert detalle["indicaciones"] == "En el ojo afectado"

    assert contar(db_cu17, Receta) == 1
    assert contar(db_cu17, DetalleReceta) == 1


def test_registrar_receta_con_multiples_medicamentos(cliente_cu17, db_cu17):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/recetas",
        json={
            "observaciones": "Tratamiento combinado",
            "detalles": [
                {"medicamento": "Medicamento 1"},
                {"medicamento": "Medicamento 2", "dosis": "2 gotas"},
                {"medicamento": "Medicamento 3", "frecuencia": "diaria"},
            ],
        },
    )
    assert respuesta.status_code == 201
    datos = respuesta.json()
    assert [d["medicamento"] for d in datos["detalles"]] == [
        "Medicamento 1", "Medicamento 2", "Medicamento 3",
    ]
    # Detalles en orden de creación (id ASC).
    ids = [d["id"] for d in datos["detalles"]]
    assert ids == sorted(ids)
    assert contar(db_cu17, DetalleReceta) == 3


def test_receta_presentacion_y_opcionales_normalizados(cliente_cu17, db_cu17):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/recetas",
        json={
            "observaciones": "   ",
            "detalles": [
                {
                    "medicamento": "  Tobramicina  ",
                    "presentacion": "   ",
                    "dosis": "",
                    "frecuencia": "  cada 12 horas  ",
                    "duracion": "   ",
                    "indicaciones": " ",
                }
            ],
        },
    )
    assert respuesta.status_code == 201
    datos = respuesta.json()
    assert datos["observaciones"] is None
    detalle = datos["detalles"][0]
    assert detalle["medicamento"] == "Tobramicina"
    assert detalle["presentacion"] is None
    assert detalle["dosis"] is None
    assert detalle["frecuencia"] == "cada 12 horas"
    assert detalle["duracion"] is None
    assert detalle["indicaciones"] is None


@pytest.mark.parametrize("cuerpo", [
    {},
    {"detalles": []},
    {"detalles": [{}]},
    {"detalles": [{"medicamento": ""}]},
    {"detalles": [{"medicamento": "   "}]},
    {"detalles": [{"medicamento": None}]},
    {"detalles": [{"medicamento": "X" * 151}]},
    {"detalles": [{"medicamento": "X", "presentacion": "P" * 101}]},
    {"detalles": [{"medicamento": "X", "campo_desconocido": "x"}]},
    {"detalles": [{"medicamento": "X"}], "id": 99},
    {"detalles": [{"medicamento": "X"}], "consulta_clinica_id": 200},
    {"detalles": [{"medicamento": "X"}], "estado": False},
    {"detalles": [{"medicamento": "X"}], "fecha_emision": "2026-01-01T00:00:00Z"},
    {"detalles": [{"medicamento": "X"}], "oftalmologo_id": 1},
    {"detalles": [{"medicamento": "X"}], "usuario_id": 7},
    {"detalles": [{"medicamento": "X"}], "campo_desconocido": "x"},
])
def test_receta_validaciones_422(cliente_cu17, db_cu17, cuerpo):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/recetas", json=cuerpo,
    )
    assert respuesta.status_code == 422
    assert contar(db_cu17, Receta) == 0
    assert contar(db_cu17, DetalleReceta) == 0
    assert contar(db_cu17, Bitacora) == 0


def test_receta_consulta_de_otro_oftalmologo_403(cliente_cu17, db_cu17):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu17.post(
        f"{URL}/{CONSULTA_AJENA}/recetas", json=DATOS_RECETA,
    )
    assert respuesta.status_code == 403
    assert contar(db_cu17, Receta) == 0
    assert contar(db_cu17, DetalleReceta) == 0
    assert contar(db_cu17, Bitacora) == 0


def test_receta_consulta_inexistente_o_inactiva_404(cliente_cu17, db_cu17):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    for consulta_id in (999, CONSULTA_INACTIVA):
        respuesta = cliente_cu17.post(
            f"{URL}/{consulta_id}/recetas", json=DATOS_RECETA,
        )
        assert respuesta.status_code == 404
    assert contar(db_cu17, Receta) == 0


@pytest.mark.parametrize("usuario", [
    USUARIO_RECEPCIONISTA, USUARIO_PACIENTE,
])
def test_receta_roles_sin_permiso_no_registran(cliente_cu17, db_cu17, usuario):
    """Recepcionista (CU17 en LECTURA) y Paciente (sin CU17) nunca registran."""
    autenticar(cliente_cu17, usuario)
    respuesta = cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/recetas", json=DATOS_RECETA,
    )
    assert respuesta.status_code == 403
    assert contar(db_cu17, Receta) == 0
    assert contar(db_cu17, Bitacora) == 0


def test_bitacora_unica_para_la_receta(cliente_cu17, db_cu17):
    """Una receta con N medicamentos genera UNA sola entrada de bitácora."""
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/recetas",
        json={
            "detalles": [
                {"medicamento": "Medicamento 1"},
                {"medicamento": "Medicamento 2"},
                {"medicamento": "Medicamento 3"},
            ],
        },
    )
    assert respuesta.status_code == 201
    receta_id = respuesta.json()["id"]

    assert contar(db_cu17, DetalleReceta) == 3
    assert contar(db_cu17, Bitacora) == 1
    bitacora = db_cu17.scalar(select(Bitacora))
    assert bitacora.accion == "REGISTRAR_RECETA"
    assert bitacora.entidad_afectada == "receta"
    assert bitacora.id_registro_afectado == receta_id
    assert bitacora.descripcion == "Receta registrada"


def test_un_solo_commit_cubre_receta_detalles_y_bitacora(db_cu17, monkeypatch):
    """Receta + N detalles + bitácora se confirman con un único commit."""
    commit = MagicMock(wraps=db_cu17.commit)
    monkeypatch.setattr(db_cu17, "commit", commit)

    usuario = db_cu17.get(Usuario, 7)
    receta = service.registrar_receta(
        db_cu17,
        CONSULTA_PROPIA,
        RecetaCrear(
            observaciones=None,
            detalles=[
                {"medicamento": "Medicamento 1"},
                {"medicamento": "Medicamento 2"},
            ],
        ),
        usuario,
    )

    commit.assert_called_once()
    assert contar(db_cu17, Bitacora) == 1
    assert [d.medicamento for d in receta.detalles] == [
        "Medicamento 1", "Medicamento 2",
    ]


def test_receta_rollback_si_falla_el_segundo_detalle(cliente_cu17, db_cu17, monkeypatch):
    """Si falla el segundo DetalleReceta no queda nada en la base."""
    original = repo.crear_detalle_receta
    llamadas = {"total": 0}

    def crear_con_fallo(db, **kwargs):
        llamadas["total"] += 1
        if llamadas["total"] == 2:
            raise SQLAlchemyError("Fallo simulado en el segundo medicamento")
        return original(db, **kwargs)

    monkeypatch.setattr(repo, "crear_detalle_receta", crear_con_fallo)

    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    with pytest.raises(SQLAlchemyError):
        cliente_cu17.post(
            f"{URL}/{CONSULTA_PROPIA}/recetas",
            json={
                "detalles": [
                    {"medicamento": "Medicamento 1"},
                    {"medicamento": "Medicamento 2"},
                ],
            },
        )

    assert llamadas["total"] == 2
    assert contar(db_cu17, Receta) == 0
    assert contar(db_cu17, DetalleReceta) == 0
    assert contar(db_cu17, Bitacora) == 0


@pytest.mark.parametrize("fallo", ["bitacora", "commit", "flush", "refresh"])
def test_receta_rollback_atomico(cliente_cu17, db_cu17, monkeypatch, fallo):
    bitacora_real = service.registrar_bitacora

    def fallar(*args, **kwargs):
        raise SQLAlchemyError("Fallo de persistencia simulado")

    def bitacora_y_fallo(*args, **kwargs):
        bitacora_real(*args, **kwargs)
        fallar()

    with monkeypatch.context() as patch:
        if fallo == "bitacora":
            patch.setattr(service, "registrar_bitacora", bitacora_y_fallo)
        else:
            patch.setattr(db_cu17, fallo, fallar)

        autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
        with pytest.raises(SQLAlchemyError):
            cliente_cu17.post(
                f"{URL}/{CONSULTA_PROPIA}/recetas",
                json={
                    "detalles": [
                        {"medicamento": "Medicamento 1"},
                        {"medicamento": "Medicamento 2"},
                    ],
                },
            )

    assert contar(db_cu17, Receta) == 0
    assert contar(db_cu17, DetalleReceta) == 0
    assert contar(db_cu17, Bitacora) == 0


def test_repositorio_receta_y_detalle_sin_commit():
    """crear_receta / crear_detalle_receta nunca confirman la transacción."""
    db = MagicMock()
    antes = datetime.now(timezone.utc)
    receta = repo.crear_receta(
        db, consulta_clinica_id=CONSULTA_PROPIA, observaciones="Obs",
    )
    assert antes <= receta.fecha_emision <= datetime.now(timezone.utc)
    assert receta.fecha_emision.tzinfo == timezone.utc
    assert receta.estado is True
    db.flush.assert_called_once()
    db.refresh.assert_called_once_with(receta)
    db.commit.assert_not_called()

    db2 = MagicMock()
    detalle = repo.crear_detalle_receta(
        db2,
        receta_id=1,
        medicamento="Ketorolaco",
        presentacion="Gotas",
        dosis="1 gota",
        frecuencia="cada 8 horas",
        duracion="5 días",
        indicaciones="Ojo afectado",
    )
    assert detalle.receta_id == 1
    assert detalle.presentacion == "Gotas"
    db2.flush.assert_called_once()
    db2.refresh.assert_called_once_with(detalle)
    db2.commit.assert_not_called()
    db2.rollback.assert_not_called()


def test_listar_recetas_con_detalles_anidados(cliente_cu17, db_cu17):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    primera = cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/recetas",
        json={"detalles": [{"medicamento": "Medicamento 1"}]},
    ).json()
    segunda = cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/recetas",
        json={
            "detalles": [
                {"medicamento": "Medicamento 2"},
                {"medicamento": "Medicamento 3"},
            ],
        },
    ).json()

    # Receta inactiva y receta de otra consulta: no deben listarse.
    db_cu17.add(Receta(
        consulta_clinica_id=CONSULTA_PROPIA,
        fecha_emision=datetime.now(timezone.utc),
        estado=False,
    ))
    db_cu17.add(Receta(
        consulta_clinica_id=CONSULTA_AJENA,
        fecha_emision=datetime.now(timezone.utc),
        estado=True,
    ))
    db_cu17.commit()

    respuesta = cliente_cu17.get(f"{URL}/{CONSULTA_PROPIA}/recetas")
    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert [d["id"] for d in datos] == [segunda["id"], primera["id"]]
    assert [len(d["detalles"]) for d in datos] == [2, 1]
    assert [d["estado"] for d in datos] == [True, True]
    assert [d["detalles"][0]["medicamento"] for d in datos] == [
        "Medicamento 2", "Medicamento 1",
    ]


def test_listar_recetas_consulta_inexistente_o_inactiva(cliente_cu17):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    for consulta_id in (999, CONSULTA_INACTIVA):
        respuesta = cliente_cu17.get(f"{URL}/{consulta_id}/recetas")
        assert respuesta.status_code == 404


def test_consultar_receta_por_id(cliente_cu17):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    creada = cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/recetas", json=DATOS_RECETA,
    ).json()

    respuesta = cliente_cu17.get(f"{URL_RECETAS}/{creada['id']}")
    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["id"] == creada["id"]
    assert datos["consulta_clinica_id"] == CONSULTA_PROPIA
    assert datos["detalles"][0]["medicamento"] == "Ketorolaco"


def test_consultar_receta_inexistente_404(cliente_cu17):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu17.get(f"{URL_RECETAS}/999")
    assert respuesta.status_code == 404


# =========================================================
# ADMINISTRADOR: ACCESO TOTAL EN CU17 (PERMISO AMBAS)
# El sistema define que el Administrador tiene acceso total: su permiso de
# CU17 es AMBAS, así que debe poder registrar sobre cualquier consulta activa.
# La validación de propiedad NO se debilita para el Oftalmólogo (se sigue
# comprobando en test_*_consulta_de_otro_oftalmologo_403).
# =========================================================


def test_admin_registra_tratamiento_en_consulta_de_otro_oftalmologo(
    cliente_cu17, db_cu17,
):
    """El Administrador registra sobre la consulta de otro oftalmólogo.

    El usuario Administrador (id 8) no tiene fila en `oftalmologo`, por lo que
    tampoco se le puede exigir el perfil que sí se exige al rol Oftalmólogo.
    """
    assert db_cu17.execute(
        text("SELECT count(*) FROM oftalmologo WHERE usuario_id = 8")
    ).scalar() == 0

    autenticar(cliente_cu17, USUARIO_ADMIN)
    respuesta = cliente_cu17.post(
        f"{URL}/{CONSULTA_AJENA}/tratamientos", json=DATOS_TRATAMIENTO,
    )
    assert respuesta.status_code == 201
    datos = respuesta.json()
    assert datos["consulta_clinica_id"] == CONSULTA_AJENA

    bitacora = db_cu17.scalar(select(Bitacora))
    assert bitacora.usuario_id == 8
    assert bitacora.accion == "REGISTRAR_TRATAMIENTO"
    assert bitacora.entidad_afectada == "tratamiento"
    assert bitacora.id_registro_afectado == datos["id"]

    listado = cliente_cu17.get(f"{URL}/{CONSULTA_AJENA}/tratamientos")
    assert listado.status_code == 200
    assert [t["id"] for t in listado.json()] == [datos["id"]]


def test_admin_registra_indicacion_en_consulta_de_otro_oftalmologo(
    cliente_cu17, db_cu17,
):
    autenticar(cliente_cu17, USUARIO_ADMIN)
    respuesta = cliente_cu17.post(
        f"{URL}/{CONSULTA_AJENA}/indicaciones", json=DATOS_INDICACION,
    )
    assert respuesta.status_code == 201
    assert respuesta.json()["consulta_clinica_id"] == CONSULTA_AJENA
    assert contar(db_cu17, Indicacion) == 1

    bitacora = db_cu17.scalar(select(Bitacora))
    assert bitacora.usuario_id == 8
    assert bitacora.accion == "REGISTRAR_INDICACION"
    assert bitacora.entidad_afectada == "indicacion"


def test_admin_registra_receta_en_consulta_de_otro_oftalmologo(
    cliente_cu17, db_cu17,
):
    autenticar(cliente_cu17, USUARIO_ADMIN)
    respuesta = cliente_cu17.post(
        f"{URL}/{CONSULTA_AJENA}/recetas", json=DATOS_RECETA,
    )
    assert respuesta.status_code == 201
    datos = respuesta.json()
    assert datos["consulta_clinica_id"] == CONSULTA_AJENA
    assert [d["medicamento"] for d in datos["detalles"]] == ["Ketorolaco"]

    assert contar(db_cu17, DetalleReceta) == 1
    # Sigue habiendo una sola bitácora por receta, también para el Admin.
    assert contar(db_cu17, Bitacora) == 1
    bitacora = db_cu17.scalar(select(Bitacora))
    assert bitacora.usuario_id == 8
    assert bitacora.accion == "REGISTRAR_RECETA"
    assert bitacora.entidad_afectada == "receta"


@pytest.mark.parametrize("consulta_id", [999, CONSULTA_INACTIVA])
def test_admin_consulta_inexistente_o_inactiva_404(
    cliente_cu17, db_cu17, consulta_id,
):
    """El acceso total del Administrador no salta existencia ni estado."""
    autenticar(cliente_cu17, USUARIO_ADMIN)
    for recurso, cuerpo in (
        ("tratamientos", DATOS_TRATAMIENTO),
        ("indicaciones", DATOS_INDICACION),
        ("recetas", DATOS_RECETA),
    ):
        respuesta = cliente_cu17.post(
            f"{URL}/{consulta_id}/{recurso}", json=cuerpo,
        )
        assert respuesta.status_code == 404

    assert contar(db_cu17, Tratamiento) == 0
    assert contar(db_cu17, Indicacion) == 0
    assert contar(db_cu17, Receta) == 0
    assert contar(db_cu17, Bitacora) == 0


def test_admin_es_el_unico_rol_extra_con_acceso_total(cliente_cu17, db_cu17):
    """Recepcionista (CU17 en LECTURA) no escribe ni en una consulta ajena."""
    autenticar(cliente_cu17, USUARIO_RECEPCIONISTA)
    for recurso, cuerpo in (
        ("tratamientos", DATOS_TRATAMIENTO),
        ("indicaciones", DATOS_INDICACION),
        ("recetas", DATOS_RECETA),
    ):
        respuesta = cliente_cu17.post(
            f"{URL}/{CONSULTA_AJENA}/{recurso}", json=cuerpo,
        )
        assert respuesta.status_code == 403

    assert contar(db_cu17, Tratamiento) == 0
    assert contar(db_cu17, Indicacion) == 0
    assert contar(db_cu17, Receta) == 0


# =========================================================
# SEGURIDAD GENERAL: IDs Y CUERPO
# =========================================================


@pytest.mark.parametrize("consulta_id", ["0", "-1", "abc", "1.5"])
def test_identificadores_invalidos_422(cliente_cu17, consulta_id):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    for recurso in ("tratamientos", "indicaciones", "recetas"):
        respuesta = cliente_cu17.post(
            f"{URL}/{consulta_id}/{recurso}", json={},
        )
        assert respuesta.status_code == 422


@pytest.mark.parametrize("receta_id", ["0", "-3", "xyz"])
def test_receta_id_invalido_422(cliente_cu17, receta_id):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    assert cliente_cu17.get(f"{URL_RECETAS}/{receta_id}").status_code == 422


# =========================================================
# CU17 NO TOCA OTROS MODELOS CLÍNICOS
# =========================================================


def test_cu17_solo_escribe_las_tres_entidades_su_tabla(cliente_cu17, db_cu17):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    consultas_antes = contar(db_cu17, ConsultaClinica)

    cliente_cu17.post(f"{URL}/{CONSULTA_PROPIA}/tratamientos", json=DATOS_TRATAMIENTO)
    cliente_cu17.post(f"{URL}/{CONSULTA_PROPIA}/indicaciones", json=DATOS_INDICACION)
    cliente_cu17.post(f"{URL}/{CONSULTA_PROPIA}/recetas", json=DATOS_RECETA)

    assert contar(db_cu17, ConsultaClinica) == consultas_antes
    assert contar(db_cu17, Tratamiento) == 1
    assert contar(db_cu17, Indicacion) == 1
    assert contar(db_cu17, Receta) == 1
    assert contar(db_cu17, DetalleReceta) == 1
    # Una bitácora por operación de CU17 (3 en total).
    assert contar(db_cu17, Bitacora) == 3


# =========================================================
# REGRESIÓN CU13 / CU15 / CU16
# =========================================================


def test_cu13_consultar_historial_clinico_sigue_funcionando(cliente_cu17):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu17.get("/historial-clinico/1")
    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["paciente"]["id"] == 1
    assert datos["historial"]["id"] == 10
    # Solo el antecedente activo del historial (CU13/CU14 intactos).
    assert [a["id"] for a in datos["historial"]["antecedentes"]] == [100]


def test_cu15_registrar_consulta_sigue_funcionando(cliente_cu17, db_cu17):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu17.post(URL, json={"historial_clinico_id": 10})
    assert respuesta.status_code == 201
    datos = respuesta.json()
    assert datos["historial_clinico_id"] == 10
    assert datos["estado"] is True
    assert contar(db_cu17, Bitacora) == 1


def test_cu16_registrar_diagnostico_sigue_funcionando(cliente_cu17, db_cu17):
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/diagnosticos", json={"nombre": "Miopía"},
    )
    assert respuesta.status_code == 201
    assert respuesta.json()["consulta_clinica_id"] == CONSULTA_PROPIA
    assert contar(db_cu17, Bitacora) == 1

    listado = cliente_cu17.get(f"{URL}/{CONSULTA_PROPIA}/diagnosticos")
    assert listado.status_code == 200
    assert len(listado.json()) == 1


def test_cu15_cu16_cu17_conviven_en_la_misma_consulta(cliente_cu17):
    """Una consulta puede tener diagnóstico + tratamiento + indicación + receta."""
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    assert cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/diagnosticos", json={"nombre": "Miopía"},
    ).status_code == 201
    assert cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/tratamientos", json=DATOS_TRATAMIENTO,
    ).status_code == 201
    assert cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/indicaciones", json=DATOS_INDICACION,
    ).status_code == 201
    assert cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/recetas", json=DATOS_RECETA,
    ).status_code == 201

    assert len(cliente_cu17.get(f"{URL}/{CONSULTA_PROPIA}/diagnosticos").json()) == 1
    assert len(cliente_cu17.get(f"{URL}/{CONSULTA_PROPIA}/tratamientos").json()) == 1
    assert len(cliente_cu17.get(f"{URL}/{CONSULTA_PROPIA}/indicaciones").json()) == 1
    assert len(cliente_cu17.get(f"{URL}/{CONSULTA_PROPIA}/recetas").json()) == 1


def test_cu17_no_exige_diagnostico_previo(cliente_cu17):
    """CU17 es independiente: sin ningún diagnóstico debe funcionar igual."""
    autenticar(cliente_cu17, USUARIO_OFTALMOLOGO)
    assert cliente_cu17.get(
        f"{URL}/{CONSULTA_PROPIA}/diagnosticos",
    ).json() == []

    assert cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/tratamientos", json=DATOS_TRATAMIENTO,
    ).status_code == 201
    assert cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/indicaciones", json=DATOS_INDICACION,
    ).status_code == 201
    assert cliente_cu17.post(
        f"{URL}/{CONSULTA_PROPIA}/recetas", json=DATOS_RECETA,
    ).status_code == 201
