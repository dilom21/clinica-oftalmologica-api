# -*- coding: utf-8 -*-
"""Fase 3C.5.1 - validacion PostgreSQL aislada del worker FCM.

Estas pruebas solo se ejecutan cuando el entorno entrega la URL de una base
PostgreSQL **sintetica** mediante ``CU3C51_SYNTHETIC_DATABASE_URL``. Sin esa
variable se marcan como ``skipped``; nunca se leen ``.env``, DSN ni secretos del
repositorio.

Guardas de aislamiento (fail-closed):

* host loopback (``127.0.0.1``/``localhost``/``::1``);
* nombre de base con prefijo inequivocamente sintetico ``synth_cu3c51``;
* se crean bases derivadas ``synth_cu3c51_*`` en la misma instancia local.

Cubren concurrencia real con dos conexiones independientes:

* ``FOR UPDATE SKIP LOCKED``: dos workers no reclaman la misma fila;
* lease vigente no se reclama y lease vencido si es recuperable;
* fencing por ``lease_token`` a nivel SQL;
* rollback que no deja claims parciales y confirmacion explicita del claim;
* migracion v7 -> v8 con el ``tenant_migrator`` y el adaptador PostgreSQL
  reales (la actualizacion de version del Control Plane usa un doble en
  memoria porque esta prohibido abrir el Control Plane real).

No se llama Firebase/FCM: el proveedor es un doble local. No se conecta ningun
tenant real, legacy ni Supabase.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.modules.administracion_saas.tenant_migrator import (
    ChecksumMismatchError,
    MigrationError,
    TenantTarget,
    build_plan,
    execute_plan,
    load_bundle,
)
from app.modules.notificaciones_push.models.models import NotificacionEntrega
from app.modules.notificaciones_push.providers import fcm
from app.modules.notificaciones_push.repositories import (
    entregas as repo_entregas,
)
from app.modules.notificaciones_push.repositories import (
    revocaciones as repo_revocaciones,
)
from app.modules.notificaciones_push.services.worker_fcm import (
    ConfiguracionWorker,
    procesar_entregas,
)
from scripts.saas.migrate_active_tenants import (
    PsycopgTenantDatabase,
    PsycopgTenantFactory,
)

ENV_URL = "CU3C51_SYNTHETIC_DATABASE_URL"
PREFIJO_SINTETICO = "synth_cu3c51"
ROOT = Path(__file__).resolve().parents[1]
MIGRACIONES = ROOT / "database" / "tenant_migrations"

VERSION_V7 = "20261121000000"
VERSION_V8 = "20261122000000"
VERSION_V9 = "20261123000000"
VERSION_SCHEMA_V7 = "v7-sprint5-notificaciones-push"
VERSION_SCHEMA_V8 = "v8-sprint5-worker-fcm-push"
VERSION_SCHEMA_V9 = "v9-sprint5-revocacion-global-push"

T0 = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
LEASE = timedelta(seconds=120)

BASE_DDL = """
CREATE TABLE public.usuario (
    id BIGINT PRIMARY KEY,
    correo VARCHAR(255),
    estado BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE TABLE public.rol (id BIGINT PRIMARY KEY);
CREATE TABLE public.modulo (id BIGINT PRIMARY KEY);
CREATE TABLE public.funcion (id BIGINT PRIMARY KEY);
CREATE TABLE public.accion (id BIGINT PRIMARY KEY);
CREATE TABLE public.rol_funcion (id BIGINT PRIMARY KEY);
CREATE TABLE public.paciente (
    id BIGINT PRIMARY KEY,
    usuario_id BIGINT REFERENCES public.usuario(id)
);
CREATE TABLE public.cita (
    id BIGINT PRIMARY KEY,
    paciente_id BIGINT REFERENCES public.paciente(id),
    estado VARCHAR(20)
);
CREATE TABLE public.bitacora (id BIGINT PRIMARY KEY);
-- Tabla legacy requerida por el fingerprint del tenant_migrator real.
CREATE TABLE public.servicios_oftalmologicos (
    id BIGINT PRIMARY KEY,
    nombre VARCHAR(150)
);
"""


# --------------------------------------------------------------------------- #
# Utilidades de entorno sintetico
# --------------------------------------------------------------------------- #
def _url_sintetica() -> str:
    raw = os.environ.get(ENV_URL)
    if not raw:
        pytest.skip(
            f"{ENV_URL} no esta definida: validacion PostgreSQL omitida"
        )
    url = make_url(raw)
    if (url.host or "") not in {"127.0.0.1", "localhost", "::1"}:
        pytest.fail("La URL sintetica debe apuntar a loopback")
    if not (url.database or "").startswith(PREFIJO_SINTETICO):
        pytest.fail("La base sintetica debe llamarse synth_cu3c51*")
    if url.get_backend_name() != "postgresql":
        pytest.fail("La validacion exige PostgreSQL")
    return raw


def _conectar(url, database: str | None = None):
    """Conexion psycopg directa; nunca imprime la URL ni la clave."""
    parsed = make_url(url)
    return psycopg.connect(
        dbname=database or parsed.database,
        user=parsed.username,
        password=parsed.password,
        host=parsed.host,
        port=parsed.port,
        connect_timeout=10,
        autocommit=True,
        application_name="cu3c51-validacion",
    )


def _recrear_base(url: str, nombre: str) -> None:
    assert nombre.startswith(PREFIJO_SINTETICO)
    with _conectar(url, "postgres") as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{nombre}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{nombre}"')


def _crear_esquema_base(conn) -> None:
    with conn.cursor() as cursor:
        cursor.execute(BASE_DDL)
        cursor.execute(
            "INSERT INTO public.usuario (id, correo) VALUES (9001, 'sintetico')"
        )
        cursor.execute("INSERT INTO public.cita (id) VALUES (9001)")


def _sembrar_ledger_v7(conn, runner_version: str) -> None:
    """Completa el ledger de una base v7 ya establecida.

    Se aplica la migracion 3C.2 con el mecanismo real y se registran las seis
    versiones anteriores que un tenant v7 ya tendria aplicadas. Asi el plan
    oficial solo puede aplicar la version v8.
    """
    bundle = load_bundle(MIGRACIONES)
    assert bundle.runner_version == runner_version
    pendientes = {
        item.version: item
        for item in bundle.migrations
        if item.version not in {VERSION_V7, VERSION_V8, VERSION_V9}
    }
    filas = [(item.version, item.name, item.checksum) for item in pendientes.values()]
    filas.extend(
        (item.version, item.name, item.checksum)
        for item in bundle.external_migrations
    )
    with conn.transaction():
        with conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO app_migrations.schema_migration
                    (version, nombre, checksum_sha256, duracion_ms, runner_version)
                VALUES (%s, %s, %s, 0, %s)
                """,
                [(*fila, runner_version) for fila in filas],
            )


def _preparar_v7(url: str, nombre: str) -> None:
    _recrear_base(url, nombre)
    bundle = load_bundle(MIGRACIONES)
    with _conectar(url, nombre) as conn:
        _crear_esquema_base(conn)
        database = PsycopgTenantDatabase(conn, nombre, bundle.runner_version)
        database.bootstrap_ledger(bundle.bootstrap_sql)
        v7 = next(item for item in bundle.migrations if item.version == VERSION_V7)
        database.apply_transactional(v7, bundle.runner_version)
        _sembrar_ledger_v7(conn, bundle.runner_version)
        database.run_preflight(bundle.preflight_sql)


class _ControlPlaneFalso:
    """Doble en memoria: nunca abre el Control Plane real."""

    def __init__(self) -> None:
        self.actualizaciones: list[tuple[str, str]] = []

    def assert_target_current(self, target: TenantTarget) -> None:
        assert target.tenant_database_id is not None

    def update_schema_version(self, target: TenantTarget, schema_version: str) -> None:
        self.actualizaciones.append((target.database_name, schema_version))


def _target(nombre: str) -> TenantTarget:
    return TenantTarget(
        tenant_database_id=1,
        empresa_codigo="SYNTH-CU3C51",
        database_name=nombre,
        version_schema=VERSION_SCHEMA_V7,
        managed=True,
    )


def _plan_y_ejecutar(url: str, nombre: str):
    bundle = load_bundle(MIGRACIONES)
    factory = PsycopgTenantFactory(url, bundle.runner_version)
    control = _ControlPlaneFalso()
    plan = build_plan((_target(nombre),), bundle, factory)
    ejecucion = execute_plan(plan, plan.plan_hash, bundle, factory, control)
    return bundle, plan, ejecucion, control


def _preparar_v8(url: str, nombre: str) -> None:
    _preparar_v7(url, nombre)
    bundle, plan, ejecucion, control = _plan_y_ejecutar(url, nombre)
    assert plan.schema_version == VERSION_SCHEMA_V9
    assert ejecucion[0].applied == (VERSION_V8, VERSION_V9)
    assert control.actualizaciones == [(nombre, VERSION_SCHEMA_V9)]
    assert bundle.schema_version == VERSION_SCHEMA_V9


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def url_sintetica() -> str:
    return _url_sintetica()


@pytest.fixture(scope="session")
def base_v8(url_sintetica: str) -> str:
    nombre = f"{PREFIJO_SINTETICO}_worker"
    _preparar_v8(url_sintetica, nombre)
    return nombre


@pytest.fixture(scope="session")
def base_migracion_v8(url_sintetica: str) -> str:
    nombre = f"{PREFIJO_SINTETICO}_migr"
    _preparar_v8(url_sintetica, nombre)
    return nombre


@pytest.fixture()
def worker_db(url_sintetica: str, base_v8: str):
    """Limpia las tablas de notificaciones antes de cada escenario."""
    with _conectar(url_sintetica, base_v8) as conn:
        conn.execute(
            """
            TRUNCATE public.notificacion_entrega, public.notificacion,
                     public.dispositivo_push, public.preferencia_notificacion,
                     public.cita_evento, public.dispositivo_push_revocacion,
                     public.usuario, public.cita,
                     public.paciente RESTART IDENTITY CASCADE
            """
        )
        conn.execute(
            "INSERT INTO public.usuario (id, correo) VALUES (9001, 'sintetico')"
        )
        conn.execute("INSERT INTO public.paciente (id, usuario_id) VALUES (9001, 9001)")
        conn.execute(
            "INSERT INTO public.cita (id, paciente_id) VALUES (9001, 9001)"
        )
    return url_sintetica, base_v8


def _url_para(url: str, base: str) -> str:
    return make_url(url).set(database=base).render_as_string(hide_password=False)


def _engine(url: str):
    return create_engine(url, poolclass=NullPool, future=True)


def _sembrar(
    conn,
    *,
    usuario: int = 9001,
    estado: str = "PENDIENTE",
    token_lease: str | None = None,
    bloqueado_hasta=None,
    proximo_intento=None,
    intentos: int = 0,
    notificacion_id: int | None = None,
    dispositivo_id: int | None = None,
) -> int:
    if notificacion_id is None:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO public.notificacion
                    (usuario_id, tipo, evento_clave, titulo, cuerpo)
                VALUES (%s, 'CITA_CREADA', %s, 'Tu cita fue registrada.',
                        'Tu cita fue registrada.')
                RETURNING id
                """,
                (usuario, f"sintetico-{os.urandom(8).hex()}"),
            )
            notificacion_id = cursor.fetchone()[0]
    if dispositivo_id is None:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO public.dispositivo_push
                    (usuario_id, token_fcm, plataforma, activo)
                VALUES (%s, %s, 'ANDROID', TRUE)
                RETURNING id
                """,
                (usuario, f"sintetico-token-{os.urandom(8).hex()}"),
            )
            dispositivo_id = cursor.fetchone()[0]
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO public.notificacion_entrega
                (notificacion_id, dispositivo_id, estado, lease_token,
                 bloqueado_hasta, proximo_intento, intentos, fecha_creacion)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                notificacion_id,
                dispositivo_id,
                estado,
                token_lease,
                bloqueado_hasta,
                proximo_intento,
                intentos,
                T0,
            ),
        )
        return cursor.fetchone()[0]


def _estado_entrega(conn, entrega_id: int) -> dict:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT estado, lease_token, intentos, proximo_intento,
                   bloqueado_hasta, proveedor_mensaje_id, fecha_aceptado
              FROM public.notificacion_entrega WHERE id = %s
            """,
            (entrega_id,),
        )
        fila = cursor.fetchone()
    return {
        "estado": fila[0],
        "lease_token": fila[1],
        "intentos": fila[2],
        "proximo_intento": fila[3],
        "bloqueado_hasta": fila[4],
        "proveedor_mensaje_id": fila[5],
        "fecha_aceptado": fila[6],
    }


# --------------------------------------------------------------------------- #
# 1. Aislamiento / version
# --------------------------------------------------------------------------- #
def test_instancia_sintetica_identificada(url_sintetica: str) -> None:
    with _conectar(url_sintetica) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT current_database(), version()")
            base, version = cursor.fetchone()
    assert base.startswith(PREFIJO_SINTETICO)
    assert "PostgreSQL" in version


# --------------------------------------------------------------------------- #
# 2. FOR UPDATE SKIP LOCKED
# --------------------------------------------------------------------------- #
def test_skip_locked_workers_reclaman_filas_distintas(worker_db) -> None:
    url, base = worker_db
    engine_a = _engine(_url_para(url, base))
    engine_b = _engine(_url_para(url, base))
    with _conectar(url, base) as seed:
        e1 = _sembrar(seed)
        e2 = _sembrar(seed)

    sesion_a = Session(engine_a)
    sesion_b = Session(engine_b)
    try:
        lote_a = repo_entregas.reclamar_lote(
            sesion_a,
            ahora=T0 + timedelta(seconds=10),
            lease_token="lease-a",
            lease_hasta=T0 + timedelta(seconds=10) + LEASE,
            limite=1,
        )
        assert [item.id for item in lote_a] == [e1]

        # A mantiene el bloqueo sin confirmar; B debe saltar esa fila.
        lote_b = repo_entregas.reclamar_lote(
            sesion_b,
            ahora=T0 + timedelta(seconds=10),
            lease_token="lease-b",
            lease_hasta=T0 + timedelta(seconds=10) + LEASE,
            limite=10,
        )
        assert [item.id for item in lote_b] == [e2]
        assert {item.id for item in lote_a}.isdisjoint(
            {item.id for item in lote_b}
        )

        sesion_b.commit()
        sesion_a.commit()
    finally:
        sesion_a.close()
        sesion_b.close()
        engine_a.dispose()
        engine_b.dispose()

    with _conectar(url, base) as conn:
        fila_a = _estado_entrega(conn, e1)
        fila_b = _estado_entrega(conn, e2)
    assert fila_a["estado"] == "PROCESANDO"
    assert fila_a["lease_token"] == "lease-a"
    assert fila_b["estado"] == "PROCESANDO"
    assert fila_b["lease_token"] == "lease-b"
    assert fila_a["lease_token"] != fila_b["lease_token"]


def test_skip_locked_no_duplica_claim_de_una_sola_fila(worker_db) -> None:
    url, base = worker_db
    with _conectar(url, base) as seed:
        entrega = _sembrar(seed)

    engine_a = _engine(_url_para(url, base))
    engine_b = _engine(_url_para(url, base))
    sesion_a = Session(engine_a)
    sesion_b = Session(engine_b)
    try:
        lote_a = repo_entregas.reclamar_lote(
            sesion_a, ahora=T0, lease_token="lease-a",
            lease_hasta=T0 + LEASE, limite=5,
        )
        assert [item.id for item in lote_a] == [entrega]

        lote_b = repo_entregas.reclamar_lote(
            sesion_b, ahora=T0, lease_token="lease-b",
            lease_hasta=T0 + LEASE, limite=5,
        )
        assert lote_b == []
        sesion_a.commit()
    finally:
        sesion_a.close()
        sesion_b.close()
        engine_a.dispose()
        engine_b.dispose()

    with _conectar(url, base) as conn:
        assert _estado_entrega(conn, entrega)["lease_token"] == "lease-a"


def test_skip_locked_compila_en_postgresql() -> None:
    sentencia = repo_entregas.construir_consulta_reclamo(
        T0, 10, dialecto="postgresql",
    )
    sql = str(sentencia.compile(
        dialect=__import__("sqlalchemy").dialects.postgresql.dialect()
    ))
    assert "FOR UPDATE SKIP LOCKED" in sql


# --------------------------------------------------------------------------- #
# 3. Lease vigente y vencido
# --------------------------------------------------------------------------- #
def test_lease_vigente_no_se_reclama_y_vencido_si(worker_db) -> None:
    url, base = worker_db
    with _conectar(url, base) as seed:
        vigente = _sembrar(
            seed,
            estado="PROCESANDO",
            token_lease="lease-vigente",
            bloqueado_hasta=T0 + timedelta(hours=1),
        )
        vencido = _sembrar(
            seed,
            estado="PROCESANDO",
            token_lease="lease-vencido",
            bloqueado_hasta=T0 - timedelta(minutes=5),
            intentos=3,
        )
        futuro = _sembrar(
            seed,
            estado="PENDIENTE",
            proximo_intento=T0 + timedelta(minutes=30),
        )

    engine = _engine(_url_para(url, base))
    with Session(engine) as sesion:
        reclamadas = repo_entregas.reclamar_lote(
            sesion, ahora=T0, lease_token="lease-nuevo",
            lease_hasta=T0 + LEASE, limite=10,
        )
        ids = [item.id for item in reclamadas]
        sesion.commit()

    assert vigente not in ids
    assert futuro not in ids
    assert vencido in ids

    with _conectar(url, base) as conn:
        fila_vigente = _estado_entrega(conn, vigente)
        fila_vencida = _estado_entrega(conn, vencido)
        fila_futuro = _estado_entrega(conn, futuro)
    assert fila_vigente["lease_token"] == "lease-vigente"
    assert fila_vencida["lease_token"] == "lease-nuevo"
    assert fila_vencida["lease_token"] != "lease-vencido"
    assert fila_vencida["intentos"] == 3
    assert fila_futuro["estado"] == "PENDIENTE"
    engine.dispose()


# --------------------------------------------------------------------------- #
# 4. Fencing
# --------------------------------------------------------------------------- #
def test_fencing_worker_antiguo_no_sobrescribe_lease_nuevo(worker_db) -> None:
    url, base = worker_db
    with _conectar(url, base) as seed:
        entrega = _sembrar(seed)

    engine_a = _engine(_url_para(url, base))
    engine_b = _engine(_url_para(url, base))

    sesion_a = Session(engine_a)
    lote = repo_entregas.reclamar_lote(
        sesion_a, ahora=T0, lease_token="lease-a",
        lease_hasta=T0 + LEASE, limite=1,
    )
    assert [item.id for item in lote] == [entrega]
    sesion_a.commit()
    sesion_a.close()

    # El lease de A vence y B recupera la entrega con un token nuevo.
    sesion_b = Session(engine_b)
    lote_b = repo_entregas.reclamar_lote(
        sesion_b, ahora=T0 + LEASE + timedelta(seconds=1),
        lease_token="lease-b",
        lease_hasta=T0 + LEASE + timedelta(seconds=1) + LEASE,
        limite=1,
    )
    assert [item.id for item in lote_b] == [entrega]
    sesion_b.commit()

    # A intenta persistir con su lease viejo: cero filas.
    sesion_a = Session(engine_a)
    aplicado_a = repo_entregas.actualizar_resultado(
        sesion_a,
        entrega,
        lease_token="lease-a",
        valores={
            "estado": "ACEPTADO_POR_FCM",
            "proveedor_mensaje_id": "projects/sintetico/messages/a",
            "lease_token": None,
            "bloqueado_hasta": None,
            "intentos": 1,
            "fecha_actualizacion": T0,
        },
    )
    sesion_a.commit()
    sesion_a.close()
    assert aplicado_a is False

    with _conectar(url, base) as conn:
        fila = _estado_entrega(conn, entrega)
    assert fila["estado"] == "PROCESANDO"
    assert fila["lease_token"] == "lease-b"

    aplicado_b = repo_entregas.actualizar_resultado(
        sesion_b,
        entrega,
        lease_token="lease-b",
        valores={
            "estado": "ACEPTADO_POR_FCM",
            "proveedor_mensaje_id": "projects/sintetico/messages/b",
            "lease_token": None,
            "bloqueado_hasta": None,
            "intentos": 1,
            "fecha_actualizacion": T0,
            "fecha_aceptado": T0,
        },
    )
    sesion_b.commit()
    sesion_b.close()
    assert aplicado_b is True

    with _conectar(url, base) as conn:
        fila = _estado_entrega(conn, entrega)
    assert fila["estado"] == "ACEPTADO_POR_FCM"
    assert fila["proveedor_mensaje_id"] == "projects/sintetico/messages/b"
    assert fila["lease_token"] is None
    engine_a.dispose()
    engine_b.dispose()


# --------------------------------------------------------------------------- #
# 5. Transacciones
# --------------------------------------------------------------------------- #
def test_rollback_del_claim_no_deja_estado_parcial(worker_db) -> None:
    url, base = worker_db
    with _conectar(url, base) as seed:
        entrega = _sembrar(seed)

    engine = _engine(_url_para(url, base))
    with Session(engine) as sesion:
        lote = repo_entregas.reclamar_lote(
            sesion, ahora=T0, lease_token="lease-descartado",
            lease_hasta=T0 + LEASE, limite=1,
        )
        assert [item.id for item in lote] == [entrega]
        sesion.rollback()

    with _conectar(url, base) as conn:
        fila = _estado_entrega(conn, entrega)
    assert fila["estado"] == "PENDIENTE"
    assert fila["lease_token"] is None
    assert fila["bloqueado_hasta"] is None
    engine.dispose()


def test_worker_confirma_claim_antes_de_red_y_propaga_fallo(worker_db) -> None:
    url, base = worker_db

    class _ProveedorQueFalla:
        configurado = True

        def enviar(self, **_kwargs):
            raise RuntimeError("fallo simulado de red")

    with _conectar(url, base) as seed:
        entrega = _sembrar(seed)

    engine = _engine(_url_para(url, base))
    with Session(engine) as sesion:
        with pytest.raises(RuntimeError):
            procesar_entregas(
                sesion,
                proveedor=_ProveedorQueFalla(),
                configuracion=ConfiguracionWorker(
                    tamano_lote=1, lease_segundos=60,
                ),
                reloj=lambda: T0,
            )

    # El claim se confirmo antes de la llamada de red: queda PROCESANDO con
    # lease, recuperable tras su vencimiento. No hay resultado inventado.
    with _conectar(url, base) as conn:
        fila = _estado_entrega(conn, entrega)
    assert fila["estado"] == "PROCESANDO"
    assert fila["lease_token"] is not None
    assert fila["proveedor_mensaje_id"] is None
    assert fila["intentos"] == 0
    engine.dispose()


class _ProveedorAcepta:
    configurado = True

    def __init__(self) -> None:
        self.llamadas = 0

    def enviar(self, *, token, titulo, cuerpo, datos):
        self.llamadas += 1
        assert token.startswith("sintetico-token-")
        assert titulo and cuerpo
        assert set(datos) <= {"ruta", "pantalla", "seccion", "accion", "cita_id"}
        return fcm.ResultadoFcm(
            fcm.ACEPTADO, "ACEPTADO",
            mensaje_id="projects/sintetico/messages/ok",
        )


def test_worker_acepta_y_persiste_resultado_en_postgresql(worker_db) -> None:
    url, base = worker_db
    with _conectar(url, base) as seed:
        entrega = _sembrar(seed)

    engine = _engine(_url_para(url, base))
    proveedor = _ProveedorAcepta()
    with Session(engine) as sesion:
        resultado = procesar_entregas(
            sesion,
            proveedor=proveedor,
            configuracion=ConfiguracionWorker(tamano_lote=5, lease_segundos=60),
            reloj=lambda: T0,
        )
    assert proveedor.llamadas == 1
    assert resultado.reclamadas == 1
    assert resultado.aceptadas == 1
    assert resultado.conflicto_lease == 0

    with _conectar(url, base) as conn:
        fila = _estado_entrega(conn, entrega)
    assert fila["estado"] == "ACEPTADO_POR_FCM"
    assert fila["proveedor_mensaje_id"] == "projects/sintetico/messages/ok"
    assert fila["intentos"] == 1
    assert fila["lease_token"] is None
    assert fila["fecha_aceptado"] is not None
    engine.dispose()


# --------------------------------------------------------------------------- #
# 6. Migracion v7 -> v8 en base sintetica
# --------------------------------------------------------------------------- #
def test_migracion_v7_a_v8_aplica_solo_v8(
    url_sintetica: str, base_migracion_v8: str,
) -> None:
    nombre = f"{PREFIJO_SINTETICO}_migr_ciclo"
    _preparar_v7(url_sintetica, nombre)
    bundle = load_bundle(MIGRACIONES)

    with _conectar(url_sintetica, nombre) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT pg_get_constraintdef(oid)
                  FROM pg_constraint
                 WHERE conname = 'ck_notificacion_entrega_estado'
                """
            )
            antes = cursor.fetchone()[0]
            cursor.execute(
                "SELECT to_regclass('public.notificacion_entrega') IS NOT NULL"
            )
            assert cursor.fetchone()[0] is True
            cursor.execute(
                """
                SELECT count(*) FROM information_schema.columns
                 WHERE table_schema='public' AND table_name='notificacion_entrega'
                   AND column_name='lease_token'
                """
            )
            assert cursor.fetchone()[0] == 0
    assert "OMITIDO" not in antes
    assert "ACEPTADO_POR_FCM" in antes

    bundle2, plan, ejecucion, control = _plan_y_ejecutar(url_sintetica, nombre)
    assert bundle2.schema_version == VERSION_SCHEMA_V9
    estados = dict(plan.tenants[0].migration_states)
    assert estados[VERSION_V8] == "pending"
    assert estados[VERSION_V9] == "pending"
    assert ejecucion[0].applied == (VERSION_V8, VERSION_V9)
    assert VERSION_V7 in ejecucion[0].skipped
    assert ejecucion[0].version_updated is True

    with _conectar(url_sintetica, nombre) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT data_type, character_maximum_length
                  FROM information_schema.columns
                 WHERE table_schema='public'
                   AND table_name='notificacion_entrega'
                   AND column_name='lease_token'
                """
            )
            assert cursor.fetchone() == ("character varying", 64)

            cursor.execute(
                """
                SELECT pg_get_constraintdef(oid)
                  FROM pg_constraint
                 WHERE conname='ck_notificacion_entrega_estado'
                """
            )
            definicion = cursor.fetchone()[0]
            for estado in (
                "PENDIENTE", "PROCESANDO", "ACEPTADO_POR_FCM",
                "FALLIDO", "INVALIDO", "OMITIDO",
            ):
                assert estado in definicion

            cursor.execute(
                """
                SELECT indexdef FROM pg_indexes
                 WHERE schemaname='public'
                   AND indexname='ix_notificacion_entrega_lease'
                """
            )
            indice = cursor.fetchone()[0]
            assert "UNIQUE" not in indice
            assert "WHERE" in indice and "PROCESANDO" in indice

            cursor.execute(
                """
                SELECT version, checksum_sha256
                  FROM app_migrations.schema_migration
                 ORDER BY version
                """
            )
            ledger = dict(cursor.fetchall())
    assert ledger[VERSION_V8] == next(
        item.checksum for item in bundle.migrations
        if item.version == VERSION_V8
    )
    assert control.actualizaciones == [(nombre, VERSION_SCHEMA_V9)]


def test_verify_es_repetible(
    url_sintetica: str, base_migracion_v8: str,
) -> None:
    bundle = load_bundle(MIGRACIONES)
    with _conectar(url_sintetica, base_migracion_v8) as conn:
        database = PsycopgTenantDatabase(
            conn, base_migracion_v8, bundle.runner_version,
        )
        database.verify(bundle.verification_sql)
        database.verify(bundle.verification_sql)
        database.run_preflight(bundle.preflight_sql)
        database.run_preflight(bundle.preflight_sql)


def test_reinstalar_v8_falla_cerrado(
    url_sintetica: str, base_migracion_v8: str,
) -> None:
    bundle = load_bundle(MIGRACIONES)
    v8 = next(item for item in bundle.migrations if item.version == VERSION_V8)
    with _conectar(url_sintetica, base_migracion_v8) as conn:
        database = PsycopgTenantDatabase(
            conn, base_migracion_v8, bundle.runner_version,
        )
        with pytest.raises(psycopg.errors.RaiseException):
            database.apply_transactional(v8, bundle.runner_version)


def test_preflight_detecta_instalacion_parcial(url_sintetica: str) -> None:
    nombre = f"{PREFIJO_SINTETICO}_parcial"
    _preparar_v7(url_sintetica, nombre)
    bundle = load_bundle(MIGRACIONES)

    with _conectar(url_sintetica, nombre) as conn:
        conn.execute(
            "ALTER TABLE public.notificacion_entrega "
            "ADD COLUMN lease_token VARCHAR(64)"
        )
        database = PsycopgTenantDatabase(conn, nombre, bundle.runner_version)
        with pytest.raises(psycopg.errors.RaiseException):
            database.run_preflight(bundle.preflight_sql)


def test_plan_rechaza_version_origen_desconocida(
    url_sintetica: str, base_migracion_v8: str,
) -> None:
    bundle = load_bundle(MIGRACIONES)
    factory = PsycopgTenantFactory(url_sintetica, bundle.runner_version)
    target = TenantTarget(
        tenant_database_id=1,
        empresa_codigo="SYNTH-CU3C51",
        database_name=base_migracion_v8,
        version_schema="v2-desconocida",
        managed=True,
    )
    with pytest.raises(MigrationError):
        build_plan((target,), bundle, factory)


def test_plan_rechaza_checksum_alterado(
    url_sintetica: str,
) -> None:
    nombre = f"{PREFIJO_SINTETICO}_checksum"
    _preparar_v7(url_sintetica, nombre)
    with _conectar(url_sintetica, nombre) as conn:
        conn.execute(
            "UPDATE app_migrations.schema_migration "
            "SET checksum_sha256 = repeat('0', 64) WHERE version = %s",
            (VERSION_V7,),
        )
    bundle = load_bundle(MIGRACIONES)
    factory = PsycopgTenantFactory(url_sintetica, bundle.runner_version)
    with pytest.raises(ChecksumMismatchError):
        build_plan((_target(nombre),), bundle, factory)


# --------------------------------------------------------------------------- #
# 7. Restricciones del esquema v8
# --------------------------------------------------------------------------- #
def test_check_rechaza_estado_desconocido_y_admite_omitido(
    worker_db,
) -> None:
    url, base = worker_db
    with _conectar(url, base) as conn:
        entrega = _sembrar(conn)
        conn.execute(
            "UPDATE public.notificacion_entrega SET estado='OMITIDO' WHERE id=%s",
            (entrega,),
        )
        assert _estado_entrega(conn, entrega)["estado"] == "OMITIDO"
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE public.notificacion_entrega SET estado='DESCONOCIDO' "
                "WHERE id=%s",
                (entrega,),
            )



# --------------------------------------------------------------------------- #
# 8. Outbox de revocaciones globales v9 (Fase 3C.5.2)
# --------------------------------------------------------------------------- #
def _sembrar_revocacion(conn, *, huella: str = "a" * 64) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            "INSERT INTO public.dispositivo_push "
            "(usuario_id, token_fcm, plataforma, activo) "
            "VALUES (9001, %s, 'ANDROID', TRUE) RETURNING id",
            (f"sintetico-token-{huella[:8]}",),
        )
        dispositivo_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO public.dispositivo_push_revocacion
                (dispositivo_id, usuario_id, token_fingerprint, estado,
                 fecha_creacion, fecha_actualizacion)
            VALUES (%s, 9001, %s, 'PENDIENTE', %s, %s)
            RETURNING id
            """,
            (dispositivo_id, huella, T0, T0),
        )
        return cursor.fetchone()[0]


def test_outbox_revocacion_impone_unicidad_y_estados(worker_db) -> None:
    url, base = worker_db
    with _conectar(url, base) as conn:
        solicitud = _sembrar_revocacion(conn)

        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT dispositivo_id FROM public.dispositivo_push_revocacion "
                "WHERE id = %s",
                (solicitud,),
            )
            dispositivo_id = cursor.fetchone()[0]

        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO public.dispositivo_push_revocacion "
                "(dispositivo_id, usuario_id, token_fingerprint) "
                "VALUES (%s, 9001, %s)",
                (dispositivo_id, "a" * 64),
            )
        conn.rollback()

        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE public.dispositivo_push_revocacion SET estado='OTRO' "
                "WHERE id = %s",
                (solicitud,),
            )
        conn.rollback()

        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE public.dispositivo_push_revocacion "
                "SET token_fingerprint='XYZ' WHERE id = %s",
                (solicitud,),
            )
        conn.rollback()

        conn.execute(
            "UPDATE public.dispositivo_push_revocacion SET estado='COMPLETADA' "
            "WHERE id = %s",
            (solicitud,),
        )
        conn.rollback()

        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema='public' "
                "AND table_name='dispositivo_push_revocacion' "
                "AND column_name IN ('token_fcm','token','fcm_token')"
            )
            assert cursor.fetchone()[0] == 0


def test_claim_revocaciones_no_duplica_bajo_skip_locked(worker_db) -> None:
    url, base = worker_db
    with _conectar(url, base) as seed:
        primera = _sembrar_revocacion(seed, huella="a" * 64)
        segunda = _sembrar_revocacion(seed, huella="b" * 64)

    engine_a = _engine(_url_para(url, base))
    engine_b = _engine(_url_para(url, base))
    sesion_a = Session(engine_a)
    sesion_b = Session(engine_b)
    try:
        lote_a = repo_revocaciones.reclamar_lote(
            sesion_a, ahora=T0, lease_token="rev-a",
            lease_hasta=T0 + LEASE, limite=1,
        )
        assert [item.id for item in lote_a] == [primera]

        lote_b = repo_revocaciones.reclamar_lote(
            sesion_b, ahora=T0, lease_token="rev-b",
            lease_hasta=T0 + LEASE, limite=10,
        )
        assert [item.id for item in lote_b] == [segunda]
        assert {item.id for item in lote_a}.isdisjoint(
            {item.id for item in lote_b}
        )
        sesion_b.commit()
        sesion_a.commit()
    finally:
        sesion_a.close()
        sesion_b.close()
        engine_a.dispose()
        engine_b.dispose()

    with _conectar(url, base) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, estado, lease_token "
                "FROM public.dispositivo_push_revocacion ORDER BY id"
            )
            filas = cursor.fetchall()
    assert filas == [
        (primera, "PROCESANDO", "rev-a"),
        (segunda, "PROCESANDO", "rev-b"),
    ]
