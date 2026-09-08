import ast
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import jwt
import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.core.config import JWT_ALGORITHM, JWT_SECRET_KEY
from app.database.base import Base
from app.main import app
from app.modules.gestion_agenda_citas.api.router import permiso_consultar_historial_citas
from app.modules.gestion_historial_clinico.api.router import (
    permiso_gestionar_antecedentes,
    permiso_historial_clinico,
)


RUTAS = [
    ("GET", "/agenda-citas/historial?paciente_id=1", 13),
    ("GET", "/historial-clinico/1", 15),
    ("POST", "/historial-clinico/antecedentes", 16),
    ("PUT", "/historial-clinico/antecedentes/100", 16),
]


@pytest.mark.parametrize("metodo,url,funcion_id", RUTAS)
@pytest.mark.parametrize("motivo", ["sin_token", "invalido", "expirado", "usuario_inactivo"])
def test_jwt_obligatorio(cliente_historial, db_historial, metodo, url, funcion_id, motivo):
    if motivo == "sin_token":
        del cliente_historial.headers["Authorization"]
    elif motivo == "invalido":
        cliente_historial.headers["Authorization"] = "Bearer invalido"
    elif motivo == "expirado":
        token = jwt.encode(
            {"sub": "7", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
            JWT_SECRET_KEY, algorithm=JWT_ALGORITHM,
        )
        cliente_historial.headers["Authorization"] = f"Bearer {token}"
    else:
        db_historial.execute(text("UPDATE usuario SET estado = 0 WHERE id = 7"))
        db_historial.commit()
    assert cliente_historial.request(metodo, url, json={}).status_code == 401


@pytest.mark.parametrize("metodo,url,funcion_id", RUTAS)
@pytest.mark.parametrize("motivo", ["sin_funcion", "funcion_inactiva", "accion_inactiva", "accion_insuficiente"])
def test_permiso_real_obligatorio(cliente_historial, db_historial, metodo, url, funcion_id, motivo):
    if motivo == "sin_funcion":
        sql = "DELETE FROM rol_funcion WHERE funcion_id = :id"
    elif motivo == "funcion_inactiva":
        sql = "UPDATE funcion SET estado = 0 WHERE id = :id"
    elif motivo == "accion_inactiva":
        sql = "UPDATE accion SET estado = 0 WHERE id = 3"
    else:
        accion = 2 if metodo == "GET" else 1
        sql = f"UPDATE rol_funcion SET accion_id = {accion} WHERE funcion_id = :id"
    db_historial.execute(text(sql), {"id": funcion_id})
    db_historial.commit()
    assert cliente_historial.request(metodo, url, json={}).status_code == 403


@pytest.mark.parametrize("permiso,nombre,aceptadas,rechazada", [
    (permiso_consultar_historial_citas, "Consultar historial de citas", ["LECTURA", "AMBAS"], "ESCRITURA"),
    (permiso_historial_clinico, "Consultar historial clínico", ["LECTURA", "AMBAS"], "ESCRITURA"),
    (permiso_gestionar_antecedentes, "Gestionar antecedentes clínicos", ["ESCRITURA", "AMBAS"], "LECTURA"),
])
def test_nombre_exacto_y_accion(permiso, nombre, aceptadas, rechazada):
    db = MagicMock()
    usuario = MagicMock(rol_id=1)
    for accion in aceptadas:
        db.execute.return_value.first.return_value.accion_nombre = accion
        assert permiso(usuario=usuario, db=db) is usuario
        assert nombre in db.execute.call_args.args[0].compile().params.values()
    db.execute.return_value.first.return_value.accion_nombre = rechazada
    with pytest.raises(HTTPException) as error:
        permiso(usuario=usuario, db=db)
    assert error.value.status_code == 403


def rutas_registradas(routes):
    for route in routes:
        # FastAPI 0.141 conserva los routers incluidos; versiones anteriores
        # exponen los APIRoute directamente.
        if hasattr(route, "original_router"):
            yield from rutas_registradas(route.original_router.routes)
        elif hasattr(route, "methods"):
            for method in route.methods:
                yield method, route.path


def test_rutas_sin_duplicados_y_regresion_cu09_cu11():
    rutas = Counter(rutas_registradas(app.routes))
    assert all(cantidad == 1 for cantidad in rutas.values())
    esperadas = [
        ("GET", "/agenda-citas/historial"),
        ("GET", "/historial-clinico/{paciente_id}"),
        ("POST", "/historial-clinico/antecedentes"),
        ("PUT", "/historial-clinico/antecedentes/{antecedente_id}"),
        ("GET", "/agenda-citas/oftalmologos"),
        ("GET", "/agenda-citas/disponibilidad"),
        ("GET", "/agenda-citas/oftalmologos/{oftalmologo_id}/agenda"),
        ("POST", "/agenda-citas/citas"), ("GET", "/agenda-citas/citas"),
        ("GET", "/agenda-citas/citas/{cita_id}"),
        ("PUT", "/agenda-citas/citas/{cita_id}"),
        ("PATCH", "/agenda-citas/citas/{cita_id}/estado"),
        ("GET", "/agenda-citas/configuracion/oftalmologos"),
        ("GET", "/agenda-citas/configuracion/oftalmologos/{oftalmologo_id}"),
    ]
    for coleccion, identificador in [("horarios", "horario_id"), ("bloqueos", "bloqueo_id")]:
        base = f"/agenda-citas/configuracion/oftalmologos/{{oftalmologo_id}}/{coleccion}"
        esperadas.extend([
            ("POST", base), ("PUT", base + "/{" + identificador + "}"),
            ("PATCH", base + "/{" + identificador + "}/estado"),
        ])
    for method, path in esperadas:
        assert rutas[method, path] == 1
        assert method.lower() in app.openapi()["paths"][path]
    assert not any("antecedentes" in path for _, path in rutas if path.startswith("/pacientes"))


def test_modelos_clinicos_unicos():
    objetivo = {"historial_clinico", "antecedente_clinico"}
    definiciones = Counter()
    for archivo in (Path(__file__).resolve().parents[1] / "app").rglob("*.py"):
        for nodo in ast.walk(ast.parse(archivo.read_text(encoding="utf-8-sig"))):
            if isinstance(nodo, ast.Assign) and isinstance(nodo.value, ast.Constant):
                if any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in nodo.targets):
                    if nodo.value.value in objetivo:
                        definiciones[nodo.value.value] += 1
    assert definiciones == {tabla: 1 for tabla in objetivo}
    for tabla in objetivo:
        mapeos = [m for m in Base.registry.mappers if m.local_table.name == tabla]
        assert len(mapeos) == 1
        assert ".gestion_historial_clinico.models." in mapeos[0].class_.__module__
