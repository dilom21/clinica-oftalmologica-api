"""Disposable CU30 HTTP harness backed exclusively by temporary SQLite files."""

import os
import sys
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, URL
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool


PASSWORD_PRUEBA = "Cu30-Prueba-2026!"
CORREO_ADMIN = "admin@cu30.test"
CORREO_LECTURA = "lectura@cu30.test"
CORREO_ESCRITURA = "escritura@cu30.test"
CORREO_SIN_PERMISO = "sin-permiso@cu30.test"
CORREO_ROL_INACTIVO = "rol-inactivo@cu30.test"
CORREO_USUARIO_INACTIVO = "usuario-inactivo@cu30.test"


@dataclass
class EntornoAislado:
    control: Engine
    tenants: dict[str, Engine]
    registry: object
    empresas: tuple[str, ...]
    fecha: object
    sentencias: list[tuple[str, str]]


def _configurar_proceso_aislado():
    if "app.core.config" in sys.modules:
        raise RuntimeError("Inicia la factory en un proceso nuevo para aislar su configuracion")
    # Override before importing the application; load_dotenv cannot replace these.
    os.environ.update({
        "DATABASE_URL": "sqlite+pysqlite:///:memory:",
        "JWT_SECRET_KEY": "cu30-isolated-local-integration-secret-2026",
        "JWT_ALGORITHM": "HS256",
        "PASSWORD_RESET_URL_BASE": "http://127.0.0.1/password/restablecer",
        "GMAIL_CLIENT_ID": "cu30-isolated-client",
        "GMAIL_CLIENT_SECRET": "cu30-isolated-secret",
        "GMAIL_REFRESH_TOKEN": "cu30-isolated-token",
        "GMAIL_SENDER_EMAIL": "cu30@example.test",
        "EMAIL_TEST": "",
        "APP_ENV": "test",
        "ENVIRONMENT": "test",
        "RENDER": "false",
    })


def _completar_seguridad(engine, password_hash):
    with engine.begin() as db:
        db.exec_driver_sql("CREATE TABLE IF NOT EXISTS modulo (id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, descripcion TEXT, estado BOOLEAN NOT NULL)")
        for tabla, columnas in {
            "funcion": {"modulo_id": "INTEGER DEFAULT 4", "descripcion": "TEXT"},
            "accion": {"descripcion": "TEXT"},
            "rol_funcion": {"descripcion": "TEXT"},
            "modulo": {"descripcion": "TEXT"},
        }.items():
            existentes = {fila[1] for fila in db.exec_driver_sql(f"PRAGMA table_info({tabla})")}
            for columna, tipo in columnas.items():
                if columna not in existentes:
                    db.exec_driver_sql(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo}")
        db.exec_driver_sql("CREATE TABLE IF NOT EXISTS bitacora (id INTEGER PRIMARY KEY, usuario_id INTEGER, fecha_hora TIMESTAMP NOT NULL, "
                           "ip TEXT, accion TEXT NOT NULL, entidad_afectada TEXT, id_registro_afectado INTEGER, descripcion TEXT)")
        db.exec_driver_sql("INSERT OR IGNORE INTO modulo (id,nombre,descripcion,estado) VALUES (4,'Inventario y Proveedores',NULL,1)")
        db.exec_driver_sql("UPDATE funcion SET modulo_id=4 WHERE id=30")
        db.exec_driver_sql("UPDATE rol SET protegido=1 WHERE id=1")
        db.exec_driver_sql("UPDATE usuario SET correo=?, password_hash=? WHERE id=7", (CORREO_ADMIN, password_hash))
        db.exec_driver_sql("UPDATE rol_funcion SET accion_id=3 WHERE rol_id=1")
        db.exec_driver_sql("INSERT INTO rol VALUES (2,'Sin permiso',NULL,1,0,'2026-01-01'), "
                           "(3,'Solo escritura',NULL,1,0,'2026-01-01'), (4,'Lector',NULL,1,0,'2026-01-01'), "
                           "(5,'Rol inactivo',NULL,0,0,'2026-01-01')")
        usuarios = [
            (8, CORREO_SIN_PERMISO, password_hash, 1, "2026-01-01", 2),
            (9, CORREO_ESCRITURA, password_hash, 1, "2026-01-01", 3),
            (10, CORREO_LECTURA, password_hash, 1, "2026-01-01", 4),
            (11, CORREO_ROL_INACTIVO, password_hash, 1, "2026-01-01", 5),
            (12, CORREO_USUARIO_INACTIVO, password_hash, 0, "2026-01-01", 4),
        ]
        db.exec_driver_sql("INSERT INTO usuario VALUES (?,?,?,?,?,?)", usuarios)
        db.exec_driver_sql("INSERT INTO rol_funcion VALUES (2,3,30,2,NULL), (3,4,30,1,NULL)")


def _engine_aislado(directorio: Path, nombre: str, esquema: str) -> Engine:
    # A request owns its DBAPI connection. StaticPool would share one live
    # SQLite connection across FastAPI worker threads, including rollbacks.
    if esquema not in {"public", "saas_control"}:
        raise ValueError("Esquema de pruebas no admitido")
    engine = create_engine(
        URL.create("sqlite+pysqlite", database=str(directorio / f"{nombre}.sqlite3")),
        poolclass=NullPool,
        connect_args={"check_same_thread": False},
    )
    archivo_esquema = str(directorio / f"{nombre}_{esquema}.sqlite3")

    @event.listens_for(engine, "connect")
    def adjuntar_esquema(connection, _):
        # Every new connection sees the same private fixture database; an
        # ATTACH ':memory:' would instead create an empty schema per connection.
        cursor = connection.cursor()
        try:
            cursor.execute(f"ATTACH DATABASE ? AS {esquema}", (archivo_esquema,))
        finally:
            cursor.close()

    return engine


@contextmanager
def entorno_aislado(api):
    """Replace connection factories only; authentication and business code remain real."""
    from app.core import time
    from app.core.security import hash_password
    from app.core.tenancy.dependencies import get_tenant_engine_registry
    from app.core.tenancy.models import Empresa, PlanSaas, Suscripcion, TenantDatabase
    from app.database import session as clinical_session
    from app.modules.gestion_inventario_proveedores.services import inventario as service
    from tests.test_cu30_inventario_alertas import EMPRESAS, HOY, _crear_tenant

    original_connect = Engine.connect

    def conectar_solo_sqlite(engine, *args, **kwargs):
        if engine.dialect.name != "sqlite":
            raise RuntimeError("El entorno CU30 aislado bloquea bases de datos externas")
        return original_connect(engine, *args, **kwargs)

    with ExitStack() as stack:
        stack.enter_context(patch.object(Engine, "connect", conectar_solo_sqlite))
        stack.enter_context(patch.object(time, "fecha_local_aplicacion", lambda: HOY))
        stack.enter_context(patch.object(service, "fecha_local_aplicacion", lambda: HOY))
        # ExitStack disposes engines before removing their temporary files.
        directorio = Path(stack.enter_context(TemporaryDirectory(prefix="cu30-")))
        control = _engine_aislado(directorio, "control", "saas_control")
        stack.callback(control.dispose)

        with control.begin() as db:
            for model in (Empresa, PlanSaas, Suscripcion, TenantDatabase):
                model.__table__.create(db)
        with Session(control) as db:
            db.add(PlanSaas(id=1, codigo="PRO", estado=True))
            for numero, codigo in enumerate(EMPRESAS, 1):
                db.add_all([
                    Empresa(id=numero, codigo=codigo, slug=codigo.lower(), estado="ACTIVA",
                            nombre_comercial=f"Clinica aislada {codigo}"),
                    Suscripcion(id=numero, empresa_id=numero, plan_id=1, estado="ACTIVA",
                                 fecha_inicio=HOY.replace(month=1, day=1), fecha_fin=HOY.replace(month=12, day=31)),
                    TenantDatabase(id=numero, empresa_id=numero,
                                   database_name="tenant_" + codigo.lower().replace("-", "_"), estado="ACTIVA"),
                ])
            db.commit()

        tenants = {}
        sentencias = []
        password_hash = hash_password(PASSWORD_PRUEBA)
        for numero, codigo in enumerate(EMPRESAS, 1):
            engine = _engine_aislado(directorio, f"tenant_{numero}", "public")
            stack.callback(engine.dispose)
            _crear_tenant(engine, codigo, numero)
            _completar_seguridad(engine, password_hash)
            tenants[codigo] = engine

            def registrar(_db, _cursor, sql, _params, _context, _many, empresa=codigo):
                sentencias.append((empresa, sql))

            event.listen(engine, "before_cursor_execute", registrar)

        class Registry:
            def __init__(self):
                self.calls = []

            def get_session(self, context):
                self.calls.append(context.empresa_codigo)
                return Session(tenants[context.empresa_codigo], autoflush=False)

        registry = Registry()
        stack.enter_context(patch.object(clinical_session, "SessionLocal", lambda: Session(control)))
        stack.enter_context(patch.object(clinical_session, "open_control_session", lambda: Session(control)))
        overrides = api.dependency_overrides.copy()
        stack.callback(lambda: (api.dependency_overrides.clear(), api.dependency_overrides.update(overrides)))
        api.dependency_overrides[get_tenant_engine_registry] = lambda: registry
        yield EntornoAislado(control, tenants, registry, EMPRESAS, HOY, sentencias)


def api_aislada_para_pruebas():
    from fastapi import APIRouter, FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from app.modules.gestion_usuarios_seguridad.api.router import router as seguridad
    from app.modules.gestion_inventario_proveedores.api.router import router as inventario

    @asynccontextmanager
    async def lifespan(api):
        with entorno_aislado(api) as entorno:
            api.state.cu30_entorno = entorno
            yield

    api = FastAPI(title="CU30 isolated integration", lifespan=lifespan)
    api.add_middleware(CORSMiddleware,
                       allow_origins=[f"http://{host}:{puerto}" for host in ("localhost", "127.0.0.1")
                                      for puerto in (4200, 4201, 4202)],
                       allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["Authorization", "Content-Type"])
    # Exact production routes and dependencies, with no mail/payment/CRUD surface.
    rutas_seguras = {"/seguridad/tenant/empresas", "/seguridad/tenant/login", "/seguridad/menu"}
    api.include_router(APIRouter(routes=[r for r in seguridad.routes if r.path in rutas_seguras]))
    api.include_router(inventario)

    @api.get("/__cu30_test/status", include_in_schema=False)
    def estado_aislado():
        return {"entorno": "cu30-aislado", "empresas": len(api.state.cu30_entorno.empresas)}

    return api


def crear_app():
    """Uvicorn factory; call in a new process so every configured secret is disposable."""
    _configurar_proceso_aislado()
    return api_aislada_para_pruebas()
