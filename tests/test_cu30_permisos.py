"""Administración CU30 exclusivamente sobre bases SQLite en memoria."""

from datetime import date
import importlib.util
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core import time
from app.core.tenancy.models import Empresa, PlanSaas, Suscripcion, TenantDatabase
from app.modules.gestion_inventario_proveedores.services import permisos_inventario as service


HOY = date(2026, 10, 26)
CODIGOS = tuple(f"CLINICA-{numero}" for numero in range(1, 8))


def _engine():
    return create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool)


def _tenant(engine):
    with engine.begin() as db:
        for ddl in (
            "CREATE TABLE modulo(id INTEGER PRIMARY KEY, nombre TEXT, descripcion TEXT, estado BOOLEAN)",
            "CREATE TABLE rol(id INTEGER PRIMARY KEY, nombre TEXT, descripcion TEXT, estado BOOLEAN, protegido BOOLEAN, fecha_creacion TIMESTAMP)",
            "CREATE TABLE accion(id INTEGER PRIMARY KEY, nombre TEXT, descripcion TEXT, estado BOOLEAN)",
            "CREATE TABLE funcion(id INTEGER PRIMARY KEY, modulo_id INTEGER, nombre TEXT, descripcion TEXT, estado BOOLEAN)",
            "CREATE TABLE rol_funcion(id INTEGER PRIMARY KEY, rol_id INTEGER, funcion_id INTEGER, accion_id INTEGER, descripcion TEXT)",
            "CREATE TABLE bitacora(id INTEGER PRIMARY KEY, usuario_id INTEGER, fecha_hora TIMESTAMP, ip TEXT, accion TEXT, entidad_afectada TEXT, id_registro_afectado INTEGER, descripcion TEXT)",
            "CREATE TABLE usuario(id INTEGER PRIMARY KEY, correo TEXT)",
        ):
            db.exec_driver_sql(ddl)
        db.exec_driver_sql("INSERT INTO modulo VALUES(17, 'Inventario y Proveedores', NULL, 1), (18,'Clinico',NULL,1)")
        db.exec_driver_sql("INSERT INTO rol VALUES(22,'Administrador',NULL,1,1,'2026-01-01'), (23,'Oftalmologo',NULL,1,0,'2026-01-01'), (24,'Paciente',NULL,1,0,'2026-01-01')")
        db.exec_driver_sql("INSERT INTO accion VALUES(31,'LECTURA',NULL,1),(32,'ESCRITURA',NULL,1),(33,'AMBAS',NULL,1)")
        db.exec_driver_sql("INSERT INTO funcion VALUES(40,18,'Otra funcion','No cambiar',1)")
        db.exec_driver_sql("INSERT INTO rol_funcion VALUES(50,23,40,33,'Conservar'),(51,22,40,32,'Conservar tambien')")
        db.exec_driver_sql("INSERT INTO usuario VALUES(60,'clinico@example.test')")


@pytest.fixture
def entorno(monkeypatch):
    monkeypatch.setattr(time, "fecha_local_aplicacion", lambda: HOY)
    monkeypatch.setattr(service, "fecha_local_aplicacion", lambda: HOY)
    control = _engine()

    @event.listens_for(control, "connect")
    def _attach(connection, _):
        connection.execute("ATTACH DATABASE ':memory:' AS saas_control")

    with control.begin() as db:
        for model in (Empresa, PlanSaas, Suscripcion, TenantDatabase):
            model.__table__.create(db)
    with Session(control) as db:
        db.add(PlanSaas(id=1, codigo="PRO", estado=True))
        for numero, codigo in enumerate(CODIGOS, 1):
            db.add_all([
                Empresa(id=numero, codigo=codigo, slug=codigo.lower(), estado="ACTIVA"),
                Suscripcion(id=numero, empresa_id=numero, plan_id=1, estado="ACTIVA", fecha_inicio=date(2026,1,1), fecha_fin=date(2026,12,31)),
                TenantDatabase(id=numero, empresa_id=numero, database_name=f"tenant_clinica_{numero}", estado="ACTIVA"),
            ])
        db.commit()
    engines = {codigo: _engine() for codigo in CODIGOS}
    sentencias = []
    for engine in engines.values():
        _tenant(engine)
    for engine in (*engines.values(), control):
        @event.listens_for(engine, "before_cursor_execute")
        def _capture(_conn, _cursor, statement, _params, _context, _many):
            sentencias.append(statement)

    class Registry:
        calls = []

        def get_session(self, contexto):
            self.calls.append(contexto.empresa_codigo)
            return Session(engines[contexto.empresa_codigo], autoflush=False)

    try:
        yield lambda: Session(control, autoflush=False), Registry(), engines, control, sentencias
    finally:
        for engine in (*engines.values(), control):
            engine.dispose()


def _plan(entorno, **kwargs):
    return service.preparar_plan(entorno[0], entorno[1], **kwargs)


def _aplicar(entorno, plan):
    return service.aplicar_plan(entorno[0], entorno[1], plan_hash=plan.huella)


def _counts(engine):
    with engine.connect() as db:
        return tuple(db.scalar(text(f"SELECT count(*) FROM {tabla}")) for tabla in ("funcion", "rol_funcion", "bitacora", "usuario"))


def test_plan_siete_empresas_solo_select_huella_estable_sin_escrituras(entorno):
    plan = _plan(entorno)
    assert plan == _plan(entorno)
    assert plan.to_dict()["totales"] == {"empresas":7,"funciones_nuevas":7,"permisos_lectura_nuevos":7,"auditorias_nuevas":7}
    assert [empresa.contexto.empresa_codigo for empresa in plan.empresas] == list(CODIGOS)
    assert all(empresa.modulo_id == 17 and empresa.rol_id == 22 and empresa.accion_lectura_id == 31 for empresa in plan.empresas)
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in entorno[4])
    assert all(_counts(engine) == (1,2,0,1) for engine in entorno[2].values())


@pytest.mark.parametrize("sql,codigo", [
    ("DELETE FROM modulo WHERE id=17", "MODULO_INVALIDO"),
    ("UPDATE modulo SET estado=0 WHERE id=17", "MODULO_INVALIDO"),
    ("INSERT INTO modulo VALUES(99,'Inventario y Proveedores',NULL,1)", "MODULO_INVALIDO"),
    ("UPDATE rol SET estado=0 WHERE id=22", "ROL_INVALIDO"),
    ("UPDATE rol SET protegido=0 WHERE id=22", "ROL_INVALIDO"),
    ("UPDATE rol SET nombre='administrador' WHERE id=22", "ROL_INVALIDO"),
    ("INSERT INTO rol VALUES(99,'Administrador',NULL,1,1,'2026-01-01')", "ROL_INVALIDO"),
    ("UPDATE accion SET estado=0 WHERE id=31", "ACCION_INVALIDA"),
    ("INSERT INTO accion VALUES(99,' lectura ',NULL,1)", "ACCION_INVALIDA"),
    ("INSERT INTO funcion VALUES(99,17,'Consultar inventario y alertas',NULL,0)", "FUNCION_INVALIDA"),
    ("INSERT INTO funcion VALUES(99,18,'Consultar inventario y alertas',NULL,1)", "FUNCION_INVALIDA"),
])
def test_preflight_bloquea_todo_sin_corregir_catalogos(entorno, sql, codigo):
    with entorno[2][CODIGOS[-1]].begin() as db:
        db.exec_driver_sql(sql)
    entorno[4].clear()
    with pytest.raises(service.HabilitacionCU30Error) as captured:
        _plan(entorno)
    assert captured.value.codigo == "PREFLIGHT_BLOQUEADO"
    assert captured.value.errores[0]["codigo"] == codigo
    assert set(entorno[1].calls) == set(CODIGOS)
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in entorno[4])


def test_aplicar_reutiliza_repos_y_preserva_permisos_ajenos_e_idempotencia(entorno, monkeypatch):
    llamadas = []
    for nombre in ("crear_funcion", "asignar_permiso", "registrar_bitacora"):
        original = getattr(service.repo, nombre)
        def _spy(*args, _nombre=nombre, _original=original, **kwargs):
            llamadas.append(_nombre)
            return _original(*args, **kwargs)
        monkeypatch.setattr(service.repo, nombre, _spy)
    plan = _plan(entorno)
    resultado = _aplicar(entorno, plan)
    assert resultado["estado"] == "COMPLETADO"
    assert resultado["aplicadas"] == list(CODIGOS)
    assert all(llamadas.count(nombre) == 7 for nombre in ("crear_funcion","asignar_permiso","registrar_bitacora"))
    for engine in entorno[2].values():
        assert _counts(engine) == (2,3,1,1)
        with engine.connect() as db:
            assert db.execute(text("SELECT rol_id,funcion_id,accion_id,descripcion FROM rol_funcion WHERE id IN(50,51) ORDER BY id")).all() == [(23,40,33,"Conservar"),(22,40,32,"Conservar tambien")]
            assert db.execute(text("SELECT usuario_id,accion,entidad_afectada FROM bitacora")).one() == (None,"HABILITAR_CU30","rol_funcion")
            assert db.scalar(text("SELECT count(*) FROM rol_funcion rf JOIN funcion f ON f.id=rf.funcion_id WHERE f.nombre='Consultar inventario y alertas' AND (rf.rol_id !=22 OR rf.accion_id !=31)")) == 0
    nuevo_plan = _plan(entorno)
    assert nuevo_plan.to_dict()["totales"]["funciones_nuevas"] == 0
    assert _aplicar(entorno, nuevo_plan)["sin_cambios"] == list(CODIGOS)
    assert all(_counts(engine) == (2,3,1,1) for engine in entorno[2].values())
    with pytest.raises(service.HabilitacionCU30Error, match="huella"):
        _aplicar(entorno, plan)


@pytest.mark.parametrize("accion", [31,33])
def test_permiso_lectura_o_ambas_existente_no_se_sobrescribe(entorno, accion):
    for engine in entorno[2].values():
        with engine.begin() as db:
            db.exec_driver_sql("INSERT INTO funcion VALUES(99,17,'Consultar inventario y alertas',NULL,1)")
            db.exec_driver_sql("INSERT INTO rol_funcion VALUES(100,22,99,?,'Conservar CU30')", (accion,))
    plan = _plan(entorno)
    assert all(not empresa.asignar_lectura for empresa in plan.empresas)
    assert all(empresa.accion_existente == ("LECTURA" if accion == 31 else "AMBAS") for empresa in plan.empresas)
    assert _aplicar(entorno, plan)["sin_cambios"] == list(CODIGOS)
    assert all(_counts(engine) == (2,3,0,1) for engine in entorno[2].values())


def test_permiso_escritura_existente_bloquea_sin_reasignar(entorno):
    with entorno[2][CODIGOS[0]].begin() as db:
        db.exec_driver_sql("INSERT INTO funcion VALUES(99,17,'Consultar inventario y alertas',NULL,1)")
        db.exec_driver_sql("INSERT INTO rol_funcion VALUES(100,22,99,32,'No tocar')")
    with pytest.raises(service.HabilitacionCU30Error) as captured:
        _plan(entorno)
    assert captured.value.errores[0]["codigo"] == "PERMISO_INCOMPATIBLE"
    assert _counts(entorno[2][CODIGOS[0]]) == (2,3,0,1)


def test_falla_auditoria_revierte_empresa_y_reporta_parcial_sin_tocar_restantes(entorno, monkeypatch):
    original = service.repo.registrar_bitacora
    def _audit(db, **kwargs):
        if json.loads(kwargs["descripcion"])["empresa"] == CODIGOS[1]:
            raise RuntimeError("postgresql://password-secret.invalid")
        return original(db, **kwargs)
    monkeypatch.setattr(service.repo, "registrar_bitacora", _audit)
    resultado = _aplicar(entorno, _plan(entorno))
    assert resultado["estado"] == "PARCIAL"
    assert resultado["aplicadas"] == [CODIGOS[0]]
    assert resultado["fallida"] == CODIGOS[1]
    assert resultado["pendientes"] == list(CODIGOS[2:])
    assert "secret" not in json.dumps(resultado)
    assert _counts(entorno[2][CODIGOS[0]]) == (2,3,1,1)
    assert all(_counts(entorno[2][codigo]) == (1,2,0,1) for codigo in CODIGOS[1:])


def test_huella_cambiada_o_invalida_no_escribe(entorno):
    plan = _plan(entorno)
    with entorno[2][CODIGOS[0]].begin() as db:
        db.exec_driver_sql("UPDATE accion SET descripcion='Cambio externo' WHERE id=31")
        db.exec_driver_sql("UPDATE accion SET nombre=' lectura ' WHERE id=31")
    with pytest.raises(service.HabilitacionCU30Error, match="huella"):
        _aplicar(entorno, plan)
    assert all(_counts(engine) == (1,2,0,1) for engine in entorno[2].values())


@pytest.mark.parametrize("tabla", ["rol_funcion", "bitacora"])
def test_tabla_destino_ausente_bloquea_preflight_antes_de_cualquier_escritura(entorno, tabla):
    with entorno[2][CODIGOS[-1]].begin() as db:
        db.exec_driver_sql(f"DROP TABLE {tabla}")
    entorno[4].clear()
    with pytest.raises(service.HabilitacionCU30Error) as captured:
        _plan(entorno)
    assert captured.value.errores[0]["codigo"] == "TENANT_NO_DISPONIBLE"
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in entorno[4])
    assert all(_counts(entorno[2][codigo]) == (1,2,0,1) for codigo in CODIGOS[:-1])


@pytest.mark.parametrize("caso", ["inactiva", "vencida", "sin_base", "compartida", "seleccion", "cantidad"])
def test_control_plane_inseguro_bloquea_antes_de_tenants(entorno, caso):
    opciones = {}
    with Session(entorno[3]) as db:
        if caso == "inactiva":
            db.get(TenantDatabase,1).estado = "SUSPENDIDA"
        elif caso == "vencida":
            db.get(Suscripcion,1).fecha_fin = date(2026,1,1)
        elif caso == "sin_base":
            db.delete(db.get(TenantDatabase,1))
        elif caso == "compartida":
            db.get(TenantDatabase,2).database_name = "tenant_clinica_1"
            opciones = {"codigos":(CODIGOS[0],),"expected_companies":1}
        elif caso == "seleccion":
            opciones = {"codigos":("OTRA",),"expected_companies":1}
        else:
            opciones = {"expected_companies":6}
        db.commit()
    with pytest.raises(service.HabilitacionCU30Error):
        _plan(entorno, **opciones)
    assert not entorno[1].calls


def _script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "saas" / "habilitar_cu30.py"
    spec = importlib.util.spec_from_file_location("habilitar_cu30_test", path)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_script_default_plan_no_persiste_y_apply_requiere_huella(entorno, capsys):
    script = _script()
    assert script.main([], control_factory=entorno[0], registry=entorno[1]) == 0
    resultado = json.loads(capsys.readouterr().out)
    assert resultado["modo"] == "PLAN_SOLO_LECTURA"
    assert resultado["totales"]["funciones_nuevas"] == 7
    assert all(_counts(engine) == (1,2,0,1) for engine in entorno[2].values())
    with pytest.raises(SystemExit) as captured:
        script.main(["--apply"], control_factory=lambda: pytest.fail("No debe conectar"))
    assert captured.value.code == 2
    assert script.main(["--apply", "--confirm-plan", resultado["plan_hash"]],
                       control_factory=entorno[0], registry=entorno[1]) == 0
    assert json.loads(capsys.readouterr().out)["estado"] == "COMPLETADO"
