"""Ensayo opcional CU32/CU33 contra PostgreSQL descartable.

Solo se ejecuta cuando ``CU32_CU33_AUDIT_URL`` apunta explícitamente a una
base cuyo nombre termina en ``_test`` o ``_testing``. Nunca usa DATABASE_URL.
"""

from __future__ import annotations

import os
import threading
import uuid

import psycopg
import pytest
from psycopg import errors
from sqlalchemy.engine import make_url


AUDIT_URL = os.getenv("CU32_CU33_AUDIT_URL")


def _audit_dsn() -> str:
    if not AUDIT_URL:
        pytest.skip("CU32_CU33_AUDIT_URL no configurada")
    url = make_url(AUDIT_URL)
    database = url.database or ""
    if not database.endswith(("_test", "_testing")):
        pytest.fail("La integracion PostgreSQL exige una base descartable *_test")
    if url.host not in {"127.0.0.1", "localhost", "::1"}:
        pytest.fail("La integracion PostgreSQL solo admite un host local")
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def test_postgres_schema_rls_constraints_and_ledger():
    with psycopg.connect(_audit_dsn()) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            assert cursor.fetchone()[0].endswith(("_test", "_testing"))
            cursor.execute("""
                SELECT relname, relrowsecurity,
                       has_table_privilege('public', oid, 'SELECT')
                  FROM pg_class
                 WHERE oid IN (
                    'public.plan_pago'::regclass,
                    'public.plan_pago_servicio'::regclass,
                    'public.cuota'::regclass,
                    'public.intento_pago_cuota'::regclass
                 )
                 ORDER BY relname
            """)
            rows = cursor.fetchall()
            assert len(rows) == 4
            assert all(rls and not public_select for _, rls, public_select in rows)
            cursor.execute("""
                SELECT c.relname
                  FROM pg_index i
                  JOIN pg_class c ON c.oid=i.indexrelid
                 WHERE c.relname IN (
                    'uq_pps_servicio_comprometido',
                    'uq_intento_cuota_aplicada'
                   )
                   AND i.indisunique IS TRUE
                   AND i.indpred IS NOT NULL
            """)
            assert {row[0] for row in cursor.fetchall()} == {
                "uq_pps_servicio_comprometido", "uq_intento_cuota_aplicada",
            }
            cursor.execute("""
                SELECT version FROM app_migrations.schema_migration
                 WHERE version IN (
                    '20261007211200', '20261007211247',
                    '20261019000000', '20261031000000'
                 ) ORDER BY version
            """)
            assert [row[0] for row in cursor.fetchall()] == [
                "20261007211200", "20261007211247",
                "20261019000000", "20261031000000",
            ]


def test_postgres_partial_unique_indexes_prevent_concurrent_double_charge():
    dsn = _audit_dsn()
    marker = f"cu32-audit-{uuid.uuid4().hex}"
    ids: dict[str, int] = {}
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO usuario(correo,password_hash,estado,fecha_creacion,rol_id) "
                "VALUES (%s,'synthetic-not-a-login',true,now(),1) RETURNING id",
                (f"{marker}-admin@example.invalid",),
            )
            ids["admin_user"] = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO usuario(correo,password_hash,estado,fecha_creacion,rol_id) "
                "VALUES (%s,'synthetic-not-a-login',true,now(),4) RETURNING id",
                (f"{marker}-doctor@example.invalid",),
            )
            ids["doctor_user"] = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO oftalmologo(usuario_id,matricula,nombres,apellidos,estado,fecha_registro) "
                "VALUES (%s,%s,'Audit','Doctor',true,now()) RETURNING id",
                (ids["doctor_user"], marker),
            )
            ids["doctor"] = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO paciente(nombres,apellidos,fecha_registro,estado) "
                "VALUES ('Audit','Patient',now(),true) RETURNING id"
            )
            ids["patient"] = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO historial_clinico(paciente_id,fecha_apertura,estado) "
                "VALUES (%s,now(),true) RETURNING id", (ids["patient"],),
            )
            ids["history"] = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO consulta_clinica(historial_clinico_id,oftalmologo_id,fecha_consulta,estado) "
                "VALUES (%s,%s,now(),true) RETURNING id",
                (ids["history"], ids["doctor"]),
            )
            ids["consultation"] = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO servicio_oftalmologico(nombre,precio,estado) "
                "VALUES (%s,60.00,true) RETURNING id", (marker,),
            )
            ids["service"] = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO servicio_realizado(servicio_id,paciente_id,consulta_clinica_id,oftalmologo_id,fecha_realizacion,estado,precio_aplicado) "
                "VALUES (%s,%s,%s,%s,now(),true,60.00) RETURNING id",
                (ids["service"], ids["patient"], ids["consultation"], ids["doctor"]),
            )
            ids["performed"] = cursor.fetchone()[0]
            for suffix in ("a", "b"):
                cursor.execute(
                    "INSERT INTO plan_pago(paciente_id,consulta_clinica_id,monto_total,cantidad_cuotas,creado_por,clave_idempotencia) "
                    "VALUES (%s,%s,60.00,1,%s,%s) RETURNING id",
                    (ids["patient"], ids["consultation"], ids["admin_user"], f"{marker}-{suffix}"),
                )
                ids[f"plan_{suffix}"] = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO cuota(plan_pago_id,numero_cuota,monto_programado,fecha_vencimiento) "
                "VALUES (%s,1,60.00,current_date) RETURNING id", (ids["plan_a"],),
            )
            ids["installment"] = cursor.fetchone()[0]
            for suffix in ("a", "b"):
                cursor.execute(
                    "INSERT INTO pago(monto,metodo_pago,estado,clave_idempotencia) "
                    "VALUES (60.00,'TARJETA','APROBADO',%s) RETURNING id",
                    (f"{marker}-payment-{suffix}",),
                )
                ids[f"payment_{suffix}"] = cursor.fetchone()[0]
        connection.commit()

    def race(sql: str, arguments: list[tuple[object, ...]]) -> list[str]:
        barrier = threading.Barrier(len(arguments))
        outcomes: list[str] = []

        def run(params: tuple[object, ...]) -> None:
            try:
                with psycopg.connect(dsn) as connection:
                    barrier.wait(timeout=5)
                    with connection.cursor() as cursor:
                        cursor.execute(sql, params)
                    connection.commit()
                outcomes.append("ok")
            except errors.UniqueViolation:
                outcomes.append("unique")

        threads = [threading.Thread(target=run, args=(params,)) for params in arguments]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert not any(thread.is_alive() for thread in threads)
        return outcomes

    try:
        assert sorted(race(
            "INSERT INTO plan_pago_servicio(plan_pago_id,servicio_realizado_id,monto_financiado) VALUES (%s,%s,60.00)",
            [(ids["plan_a"], ids["performed"]), (ids["plan_b"], ids["performed"])],
        )) == ["ok", "unique"]
        assert sorted(race(
            "INSERT INTO intento_pago_cuota(cuota_id,pago_id,aplicado_a_cuota) VALUES (%s,%s,true)",
            [(ids["installment"], ids["payment_a"]), (ids["installment"], ids["payment_b"])],
        )) == ["ok", "unique"]
    finally:
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM intento_pago_cuota WHERE pago_id IN (%s,%s)", (ids["payment_a"], ids["payment_b"]))
                cursor.execute("DELETE FROM plan_pago_servicio WHERE plan_pago_id IN (%s,%s)", (ids["plan_a"], ids["plan_b"]))
                cursor.execute("DELETE FROM cuota WHERE plan_pago_id IN (%s,%s)", (ids["plan_a"], ids["plan_b"]))
                cursor.execute("DELETE FROM plan_pago WHERE id IN (%s,%s)", (ids["plan_a"], ids["plan_b"]))
                cursor.execute("DELETE FROM pago WHERE id IN (%s,%s)", (ids["payment_a"], ids["payment_b"]))
                cursor.execute("DELETE FROM servicio_realizado WHERE id=%s", (ids["performed"],))
                cursor.execute("DELETE FROM servicio_oftalmologico WHERE id=%s", (ids["service"],))
                cursor.execute("DELETE FROM consulta_clinica WHERE id=%s", (ids["consultation"],))
                cursor.execute("DELETE FROM historial_clinico WHERE id=%s", (ids["history"],))
                cursor.execute("DELETE FROM paciente WHERE id=%s", (ids["patient"],))
                cursor.execute("DELETE FROM oftalmologo WHERE id=%s", (ids["doctor"],))
                cursor.execute("DELETE FROM usuario WHERE id IN (%s,%s)", (ids["admin_user"], ids["doctor_user"]))
            connection.commit()
