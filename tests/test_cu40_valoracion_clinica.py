"""Pruebas integrales de CU40 (Opción A: única tabla nueva `valoracion_clinica`
reutilizando `public.notificacion` / `dispositivo_push` / `notificacion_entrega`
sin alterar sus columnas ni sus CHECK constraints).
"""

from datetime import date, datetime, time, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import dependencies
from app.core.security import crear_access_token, crear_tenant_access_token
from app.core.tenancy.context import TenantContext
from app.database import session as db_session
from app.modules.gestion_agenda_citas.models.models import Cita, Oftalmologo
from app.modules.gestion_historial_clinico.api.router import (
    router as historial_router,
)
from app.modules.gestion_historial_clinico.models.models import (
    ConsultaClinica,
    HistorialClinico,
)
from app.modules.gestion_pacientes.models.models import Paciente
from app.modules.gestion_usuarios_seguridad.api.router import (
    router as seguridad_router,
)
from app.modules.gestion_usuarios_seguridad.models.models import Bitacora
from app.modules.gestion_usuarios_seguridad.models.valoracion_clinica import (
    ValoracionClinica,
)


def _crear_engine_sqlite_cu40():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    ddls = [
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
        """CREATE TABLE modulo (
            id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, descripcion TEXT,
            estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE funcion (
            id INTEGER PRIMARY KEY, modulo_id INTEGER NOT NULL REFERENCES modulo(id),
            nombre TEXT NOT NULL, descripcion TEXT, estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE accion (
            id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, descripcion TEXT,
            estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE rol_funcion (
            id INTEGER PRIMARY KEY, rol_id INTEGER NOT NULL REFERENCES rol(id),
            funcion_id INTEGER NOT NULL REFERENCES funcion(id),
            accion_id INTEGER NOT NULL REFERENCES accion(id),
            descripcion TEXT, UNIQUE (rol_id, funcion_id)
        )""",
        """CREATE TABLE paciente (
            id INTEGER PRIMARY KEY, usuario_id INTEGER UNIQUE REFERENCES usuario(id),
            nombres TEXT NOT NULL, apellidos TEXT NOT NULL, ci TEXT,
            fecha_nacimiento DATE, sexo TEXT, telefono TEXT, contacto_emergencia TEXT,
            fecha_registro TIMESTAMP NOT NULL, direccion TEXT, estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE oftalmologo (
            id INTEGER PRIMARY KEY, usuario_id INTEGER NOT NULL UNIQUE REFERENCES usuario(id),
            matricula TEXT NOT NULL UNIQUE, nombres TEXT NOT NULL, apellidos TEXT NOT NULL,
            especialidad TEXT, estado BOOLEAN NOT NULL, fecha_registro TIMESTAMP NOT NULL
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
            paciente_id INTEGER NOT NULL UNIQUE REFERENCES paciente(id),
            fecha_apertura TIMESTAMP NOT NULL, observaciones_generales TEXT,
            estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE consulta_clinica (
            id INTEGER PRIMARY KEY,
            historial_clinico_id INTEGER NOT NULL REFERENCES historial_clinico(id),
            cita_id INTEGER UNIQUE REFERENCES cita(id),
            oftalmologo_id INTEGER NOT NULL REFERENCES oftalmologo(id),
            fecha_consulta TIMESTAMP NOT NULL,
            motivo_consulta TEXT, anamnesis TEXT, observaciones TEXT,
            estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE receta (
            id INTEGER PRIMARY KEY,
            consulta_clinica_id INTEGER NOT NULL REFERENCES consulta_clinica(id),
            fecha_emision TIMESTAMP NOT NULL, observaciones TEXT, estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE detalle_receta (
            id INTEGER PRIMARY KEY,
            receta_id INTEGER NOT NULL REFERENCES receta(id),
            medicamento TEXT NOT NULL, presentacion TEXT, dosis TEXT,
            frecuencia TEXT, duracion TEXT, indicaciones TEXT
        )""",
        """CREATE TABLE bitacora (
            id INTEGER PRIMARY KEY, usuario_id INTEGER REFERENCES usuario(id),
            fecha_hora TIMESTAMP NOT NULL, ip TEXT, accion TEXT NOT NULL,
            entidad_afectada TEXT, id_registro_afectado INTEGER, descripcion TEXT
        )""",
        # Tablas de notificaciones EXACTAS como existen en los tenants (sin cambios DDL)
        """CREATE TABLE notificacion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL REFERENCES usuario(id),
            tipo TEXT NOT NULL CHECK (tipo IN (
                'CITA_CREADA','CITA_CONFIRMADA','CITA_REPROGRAMADA',
                'CITA_CANCELADA','RECORDATORIO_CITA','ACTUALIZACION_OPERATIVA'
            )),
            evento_clave TEXT NOT NULL UNIQUE,
            titulo TEXT NOT NULL,
            cuerpo TEXT NOT NULL,
            datos TEXT,
            cita_id INTEGER REFERENCES cita(id),
            leida BOOLEAN NOT NULL DEFAULT 0,
            fecha_lectura TIMESTAMP,
            cancelada_en TIMESTAMP,
            fecha_creacion TIMESTAMP NOT NULL,
            fecha_actualizacion TIMESTAMP NOT NULL,
            CHECK (
                (leida = 1 AND fecha_lectura IS NOT NULL)
                OR (leida = 0 AND fecha_lectura IS NULL)
            )
        )""",
        """CREATE TABLE dispositivo_push (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL REFERENCES usuario(id),
            token_fcm TEXT NOT NULL,
            plataforma TEXT NOT NULL,
            activo BOOLEAN NOT NULL DEFAULT 1,
            revocado_en TIMESTAMP
        )""",
        """CREATE TABLE notificacion_entrega (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notificacion_id INTEGER NOT NULL REFERENCES notificacion(id),
            dispositivo_id INTEGER NOT NULL REFERENCES dispositivo_push(id),
            estado TEXT NOT NULL,
            proveedor TEXT NOT NULL,
            intentos INTEGER NOT NULL DEFAULT 0,
            fecha_creacion TIMESTAMP NOT NULL,
            fecha_actualizacion TIMESTAMP NOT NULL,
            UNIQUE (notificacion_id, dispositivo_id)
        )""",
        # Única tabla nueva de CU40
        """CREATE TABLE valoracion_clinica (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paciente_id INTEGER NOT NULL REFERENCES paciente(id),
            consulta_clinica_id INTEGER NOT NULL UNIQUE REFERENCES consulta_clinica(id),
            oftalmologo_id INTEGER NOT NULL REFERENCES oftalmologo(id),
            puntuacion SMALLINT NOT NULL CHECK (puntuacion BETWEEN 1 AND 5),
            comentario TEXT,
            fecha_creacion TIMESTAMP NOT NULL,
            fecha_actualizacion TIMESTAMP NOT NULL
        )""",
    ]

    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        for ddl in ddls:
            conn.exec_driver_sql(ddl)

        conn.exec_driver_sql(
            """INSERT INTO rol VALUES
                (1, 'Administrador', NULL, 1, 1, '2026-01-01'),
                (2, 'Oftalmólogo', NULL, 1, 0, '2026-01-01'),
                (4, 'Paciente', NULL, 1, 0, '2026-01-01')"""
        )
        conn.exec_driver_sql(
            """INSERT INTO usuario VALUES
                (1, 'admin@clinica.test', 'hash', 1, '2026-01-01', 1),
                (2, 'medico@clinica.test', 'hash', 1, '2026-01-01', 2),
                (10, 'paciente1@clinica.test', 'hash', 1, '2026-01-01', 4),
                (11, 'paciente2@clinica.test', 'hash', 1, '2026-01-01', 4)"""
        )
        conn.exec_driver_sql(
            "INSERT INTO modulo VALUES (3, 'Pacientes e Historial Clínico', NULL, 1)"
        )
        conn.exec_driver_sql(
            """INSERT INTO funcion VALUES
                (17, 3, 'Registrar consulta clínica', NULL, 1),
                (22, 3, 'Registrar tratamientos, indicaciones y recetas', NULL, 1)"""
        )
        conn.exec_driver_sql(
            "INSERT INTO accion VALUES (1, 'LECTURA', NULL, 1), (2, 'ESCRITURA', NULL, 1), (3, 'AMBAS', NULL, 1)"
        )
        conn.exec_driver_sql(
            "INSERT INTO rol_funcion VALUES (1, 2, 17, 3, NULL), (2, 2, 22, 3, NULL)"
        )
        conn.exec_driver_sql(
            "INSERT INTO dispositivo_push (usuario_id, token_fcm, plataforma, activo) VALUES (10, 'fcm-token-demo', 'ANDROID', 1)"
        )

    ahora = datetime(2026, 11, 22, 14, 0, tzinfo=timezone.utc)
    with Session(engine, autoflush=False) as db:
        db.add(
            Oftalmologo(
                id=1,
                usuario_id=2,
                matricula="MAT-100",
                nombres="Carlos",
                apellidos="Mendoza",
                especialidad="Retina y Vítreo",
                estado=True,
                fecha_registro=ahora,
            )
        )
        db.add_all(
            [
                Paciente(
                    id=100,
                    usuario_id=10,
                    nombres="Austin",
                    apellidos="Salas",
                    ci="CI-100",
                    fecha_registro=ahora,
                    estado=True,
                ),
                Paciente(
                    id=101,
                    usuario_id=11,
                    nombres="Lucía",
                    apellidos="Rojas",
                    ci="CI-101",
                    fecha_registro=ahora,
                    estado=True,
                ),
            ]
        )
        db.flush()
        db.add_all(
            [
                HistorialClinico(
                    id=200, paciente_id=100, fecha_apertura=ahora, estado=True
                ),
                HistorialClinico(
                    id=201, paciente_id=101, fecha_apertura=ahora, estado=True
                ),
            ]
        )
        db.flush()
        db.add(
            Cita(
                id=300,
                paciente_id=100,
                oftalmologo_id=1,
                fecha=date(2026, 11, 22),
                hora_inicio=time(9, 0),
                hora_fin=time(9, 30),
                motivo="Molestia en el ojo",
                estado="CONFIRMADA",
                canal="MOVIL",
                fecha_registro=ahora,
                fecha_actualizacion=ahora,
            )
        )
        db.commit()
    return engine


def _crear_app_cu40():
    app_cu40 = FastAPI()
    app_cu40.include_router(seguridad_router)
    app_cu40.include_router(historial_router)
    return app_cu40


@pytest.fixture
def entorno_cu40():
    engine = _crear_engine_sqlite_cu40()
    app_cu40 = _crear_app_cu40()
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        db = session_local()
        try:
            yield db
        finally:
            db.close()

    app_cu40.dependency_overrides[dependencies.get_db] = override_get_db
    app_cu40.dependency_overrides[db_session.get_db] = override_get_db

    with TestClient(app_cu40) as client:
        yield client, session_local

    engine.dispose()


def _headers(usuario_id: int, rol_id: int) -> dict[str, str]:
    token = crear_access_token(usuario_id, rol_id)
    return {"Authorization": f"Bearer {token}"}


def test_acceso_no_autenticado_y_roles_internos_rechazados(entorno_cu40):
    client, _ = entorno_cu40

    assert client.get("/seguridad/valoraciones/resumen").status_code in (401, 403)
    assert client.get("/seguridad/valoraciones/pendientes").status_code in (401, 403)
    assert client.post(
        "/seguridad/valoraciones",
        json={"consulta_clinica_id": 1, "puntuacion": 5},
    ).status_code in (401, 403)

    bad = {"Authorization": "Bearer token-invalido"}
    assert client.get("/seguridad/valoraciones/resumen", headers=bad).status_code == 401

    assert (
        client.get("/seguridad/valoraciones/resumen", headers=_headers(1, 1)).status_code
        == 403
    )
    assert (
        client.post(
            "/seguridad/valoraciones",
            headers=_headers(2, 2),
            json={"consulta_clinica_id": 1, "puntuacion": 5},
        ).status_code
        == 403
    )


def test_flujo_completo_post_atencion_receta_registro_y_actualizacion(entorno_cu40):
    client, session_local = entorno_cu40

    # 1. El médico registra la consulta clínica (CU15)
    resp_consulta = client.post(
        "/historial-clinico/consultas",
        headers=_headers(2, 2),
        json={
            "historial_clinico_id": 200,
            "cita_id": 300,
            "motivo_consulta": "Molestia en el ojo derecho",
            "observaciones": "Irritación leve",
        },
    )
    assert resp_consulta.status_code == 201, resp_consulta.text
    consulta_id = resp_consulta.json()["id"]

    # Verifica inserción en `notificacion` existente + entrega FCM
    with session_local() as db:
        fila_notif = db.execute(
            text("SELECT tipo, evento_clave, leida, fecha_lectura FROM notificacion")
        ).mappings().all()
        assert len(fila_notif) == 1
        assert fila_notif[0]["tipo"] == "ACTUALIZACION_OPERATIVA"
        assert fila_notif[0]["evento_clave"] == f"VALORACION_CONSULTA:{consulta_id}"
        assert bool(fila_notif[0]["leida"]) is False
        assert fila_notif[0]["fecha_lectura"] is None
        entregas = db.scalar(text("SELECT count(*) FROM notificacion_entrega"))
        assert entregas == 1

    # 2. Al finalizar la consulta llega el aviso móvil para valorar al médico
    pendientes = client.get(
        "/seguridad/valoraciones/pendientes",
        headers=_headers(10, 4),
    )
    assert pendientes.status_code == 200
    items = pendientes.json()
    assert len(items) == 1
    assert items[0]["consulta_clinica_id"] == consulta_id
    assert items[0]["motivo_disparo"] == "CONSULTA_FINALIZADA"
    assert "Carlos Mendoza" in items[0]["mensaje"]
    assert items[0]["leida"] is False

    # 3. El médico emite la receta (CU17) -> actualiza la MISMA fila de `notificacion`
    resp_receta = client.post(
        f"/historial-clinico/consultas/{consulta_id}/recetas",
        headers=_headers(2, 2),
        json={
            "observaciones": "Aplicar gotas 3 veces al día",
            "detalles": [{"medicamento": "Lágrimas artificiales", "dosis": "1 gota"}],
        },
    )
    assert resp_receta.status_code == 201, resp_receta.text

    pendientes_post_receta = client.get(
        "/seguridad/valoraciones/pendientes",
        headers=_headers(10, 4),
    ).json()
    assert len(pendientes_post_receta) == 1
    assert pendientes_post_receta[0]["motivo_disparo"] == "RECETA_EMITIDA"
    assert pendientes_post_receta[0]["tiene_receta"] is True
    notif_id = pendientes_post_receta[0]["id"]

    with session_local() as db:
        assert db.scalar(text("SELECT count(*) FROM notificacion")) == 1

    # 4. Marcar aviso como leído (respeta ck_notificacion_lectura_coherente)
    resp_leer = client.patch(
        f"/seguridad/valoraciones/notificaciones/{notif_id}/leer",
        headers=_headers(10, 4),
    )
    assert resp_leer.status_code == 200
    assert resp_leer.json()["leida"] is True

    # 5. Validaciones de escala y campos prohibidos
    assert (
        client.post(
            "/seguridad/valoraciones",
            headers=_headers(10, 4),
            json={"consulta_clinica_id": consulta_id, "puntuacion": 0},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/seguridad/valoraciones",
            headers=_headers(10, 4),
            json={"consulta_clinica_id": consulta_id, "puntuacion": 6},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/seguridad/valoraciones",
            headers=_headers(10, 4),
            json={
                "consulta_clinica_id": consulta_id,
                "puntuacion": 5,
                "paciente_id": 101,
            },
        ).status_code
        == 422
    )

    # 6. Registrar valoración (comentario opcional -> None)
    resp_crear = client.post(
        "/seguridad/valoraciones",
        headers=_headers(10, 4),
        json={
            "consulta_clinica_id": consulta_id,
            "puntuacion": 4,
            "comentario": "   ",
        },
    )
    assert resp_crear.status_code == 201, resp_crear.text
    creada = resp_crear.json()
    assert creada["paciente_id"] == 100
    assert creada["oftalmologo_id"] == 1
    assert creada["puntuacion"] == 4
    assert creada["etiqueta_puntuacion"] == "Buena"
    assert creada["comentario"] is None
    assert "registrada" in creada["mensaje"].lower()
    valoracion_id = creada["id"]

    # 7. Ya no aparece en pendientes
    assert (
        client.get(
            "/seguridad/valoraciones/pendientes",
            headers=_headers(10, 4),
        ).json()
        == []
    )

    # 8. Segundo POST para la misma atención -> 409 Conflict (cero duplicados)
    assert (
        client.post(
            "/seguridad/valoraciones",
            headers=_headers(10, 4),
            json={
                "consulta_clinica_id": consulta_id,
                "puntuacion": 5,
                "comentario": "Intento duplicado",
            },
        ).status_code
        == 409
    )

    # 9. Consultar valoración existente y actualizarla (PUT) sin duplicar
    resp_get = client.get(
        f"/seguridad/valoraciones/consulta/{consulta_id}",
        headers=_headers(10, 4),
    )
    assert resp_get.status_code == 200
    assert resp_get.json()["id"] == valoracion_id

    resp_upd = client.put(
        f"/seguridad/valoraciones/{valoracion_id}",
        headers=_headers(10, 4),
        json={
            "puntuacion": 5,
            "comentario": "Excelente trato del Dr. Carlos Mendoza.",
        },
    )
    assert resp_upd.status_code == 200
    actualizada = resp_upd.json()
    assert actualizada["id"] == valoracion_id
    assert actualizada["puntuacion"] == 5
    assert actualizada["etiqueta_puntuacion"] == "Excelente"
    assert actualizada["comentario"] == "Excelente trato del Dr. Carlos Mendoza."
    assert "actualizada" in actualizada["mensaje"].lower()

    # 10. Verificar en BD: 1 registro en valoracion_clinica, notificacion leída, bitácora OK
    with session_local() as db:
        assert db.scalar(select(func.count(ValoracionClinica.id))) == 1
        notif_db = db.execute(
            text("SELECT leida, fecha_lectura FROM notificacion WHERE id = :id"),
            {"id": notif_id},
        ).mappings().first()
        assert bool(notif_db["leida"]) is True
        assert notif_db["fecha_lectura"] is not None
        acciones = list(
            db.scalars(
                select(Bitacora.accion).where(
                    Bitacora.entidad_afectada == "valoracion_clinica"
                )
            )
        )
        assert "REGISTRAR_VALORACION_CLINICA" in acciones
        assert "ACTUALIZAR_VALORACION_CLINICA" in acciones


def test_aislamiento_entre_pacientes_mismo_tenant(entorno_cu40):
    client, _ = entorno_cu40

    consulta_id = client.post(
        "/historial-clinico/consultas",
        headers=_headers(2, 2),
        json={"historial_clinico_id": 200, "motivo_consulta": "Control anual"},
    ).json()["id"]

    assert (
        client.post(
            "/seguridad/valoraciones",
            headers=_headers(11, 4),
            json={"consulta_clinica_id": consulta_id, "puntuacion": 1},
        ).status_code
        == 404
    )

    val_id = client.post(
        "/seguridad/valoraciones",
        headers=_headers(10, 4),
        json={"consulta_clinica_id": consulta_id, "puntuacion": 5},
    ).json()["id"]

    assert (
        client.put(
            f"/seguridad/valoraciones/{val_id}",
            headers=_headers(11, 4),
            json={"puntuacion": 1, "comentario": "Ajena"},
        ).status_code
        == 404
    )


def test_aislamiento_multitenant_estricto_entre_empresas(monkeypatch):
    """Las valoraciones de VISION-CLARA nunca se ven ni mezclan con OFTALMO-NORTE."""
    engine_vision = _crear_engine_sqlite_cu40()
    engine_norte = _crear_engine_sqlite_cu40()

    ahora = datetime(2026, 11, 22, 16, 0, tzinfo=timezone.utc)
    for eng, motivo in (
        (engine_vision, "Molestia en ojo - Visión Clara"),
        (engine_norte, "Revisión lentes - Oftalmo Norte"),
    ):
        with Session(eng, autoflush=False) as db:
            db.add(
                ConsultaClinica(
                    id=50,
                    historial_clinico_id=200,
                    cita_id=None,
                    oftalmologo_id=1,
                    fecha_consulta=ahora,
                    motivo_consulta=motivo,
                    estado=True,
                )
            )
            db.commit()

    contexts = {
        "VISION-CLARA": TenantContext(
            empresa_id=1,
            empresa_codigo="VISION-CLARA",
            empresa_slug="vision-clara",
            empresa_estado="ACTIVA",
            suscripcion_id=1,
            plan_codigo="PRO",
            suscripcion_estado="ACTIVA",
            suscripcion_fecha_inicio=date(2026, 1, 1),
            suscripcion_fecha_fin=date(2027, 1, 1),
            tenant_database_id=1,
            database_name="tenant_vision_clara",
            database_estado="ACTIVA",
            version_schema="v9",
            empresa_nombre="Centro Oftalmológico Visión Clara",
        ),
        "OFTALMO-NORTE": TenantContext(
            empresa_id=2,
            empresa_codigo="OFTALMO-NORTE",
            empresa_slug="oftalmo-norte",
            empresa_estado="ACTIVA",
            suscripcion_id=2,
            plan_codigo="PRO",
            suscripcion_estado="ACTIVA",
            suscripcion_fecha_inicio=date(2026, 1, 1),
            suscripcion_fecha_fin=date(2027, 1, 1),
            tenant_database_id=2,
            database_name="tenant_oftalmo_norte",
            database_estado="ACTIVA",
            version_schema="v9",
            empresa_nombre="Clínica Oftalmológica Norte",
        ),
    }

    sessions_by_db = {
        "tenant_vision_clara": sessionmaker(
            bind=engine_vision, autoflush=False, autocommit=False
        ),
        "tenant_oftalmo_norte": sessionmaker(
            bind=engine_norte, autoflush=False, autocommit=False
        ),
    }

    class FakeRegistry:
        def get_session(self, ctx: TenantContext):
            return sessions_by_db[ctx.database_name]()

    class FakeResolver:
        def __init__(self, _repo):
            pass

        def resolver_por_codigo_empresa(self, codigo: str) -> TenantContext:
            return contexts[codigo]

        def require_active_database(self, _ctx: TenantContext) -> None:
            return None

    class FakeControlSession:
        def close(self):
            return None

    monkeypatch.setattr(db_session, "open_control_session", lambda: FakeControlSession())
    monkeypatch.setattr(db_session, "TenantResolver", FakeResolver)

    app_mt = _crear_app_cu40()
    fake_registry = FakeRegistry()
    app_mt.dependency_overrides[dependencies.get_tenant_engine_registry] = (
        lambda: fake_registry
    )

    token_vision = crear_tenant_access_token(
        usuario_id=10,
        rol_id=4,
        tenant_id=1,
        empresa_id=1,
        empresa_codigo="VISION-CLARA",
        rol_nombre="Paciente",
    )
    token_norte = crear_tenant_access_token(
        usuario_id=10,
        rol_id=4,
        tenant_id=2,
        empresa_id=2,
        empresa_codigo="OFTALMO-NORTE",
        rol_nombre="Paciente",
    )

    try:
        with TestClient(app_mt) as client:
            r_vision = client.post(
                "/seguridad/valoraciones",
                headers={"Authorization": f"Bearer {token_vision}"},
                json={
                    "consulta_clinica_id": 50,
                    "puntuacion": 5,
                    "comentario": "Excelente atención en Visión Clara",
                },
            )
            assert r_vision.status_code == 201, r_vision.text
            assert r_vision.json()["empresa_codigo"] == "VISION-CLARA"
            assert (
                r_vision.json()["empresa_nombre"]
                == "Centro Oftalmológico Visión Clara"
            )

            resumen_norte = client.get(
                "/seguridad/valoraciones/resumen",
                headers={"Authorization": f"Bearer {token_norte}"},
            )
            assert resumen_norte.status_code == 200
            data_norte = resumen_norte.json()
            assert data_norte["empresa_codigo"] == "OFTALMO-NORTE"
            assert data_norte["valoraciones"] == []
            assert len(data_norte["pendientes"]) == 1

            r_norte = client.post(
                "/seguridad/valoraciones",
                headers={"Authorization": f"Bearer {token_norte}"},
                json={
                    "consulta_clinica_id": 50,
                    "puntuacion": 3,
                    "comentario": "Regular en Oftalmo Norte",
                },
            )
            assert r_norte.status_code == 201
            assert r_norte.json()["puntuacion"] == 3
            assert r_norte.json()["empresa_codigo"] == "OFTALMO-NORTE"

            with Session(engine_vision) as db_v, Session(engine_norte) as db_n:
                vals_v = list(db_v.scalars(select(ValoracionClinica)))
                vals_n = list(db_n.scalars(select(ValoracionClinica)))
                assert len(vals_v) == 1 and vals_v[0].puntuacion == 5
                assert len(vals_n) == 1 and vals_n[0].puntuacion == 3
    finally:
        engine_vision.dispose()
        engine_norte.dispose()
