from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.modules.gestion_historial_clinico.models.models import AntecedenteClinico
from app.modules.gestion_historial_clinico.repositories import repository as repo
from app.modules.gestion_historial_clinico.schemas.schemas import AntecedenteClinicoCrear
from app.modules.gestion_historial_clinico.services import service
from app.modules.gestion_usuarios_seguridad.models.models import Bitacora, Usuario


URL = "/historial-clinico/antecedentes"
DATOS = {"tipo": "ALERGIA", "descripcion": "Detalle de prueba"}


def contar(db, modelo):
    return db.scalar(select(func.count()).select_from(modelo))


def test_post_persiste_antecedente_y_bitacora(cliente_historial, db_historial):
    respuesta = cliente_historial.post(URL, json={
        "historial_clinico_id": 20, "tipo": " alergia ", "descripcion": "  Detalle de prueba  ",
    })
    assert respuesta.status_code == 201
    datos = respuesta.json()
    assert set(datos) == {"id", "tipo", "descripcion", "fecha_registro"}
    assert datos["tipo"] == "ALERGIA"
    assert datos["descripcion"] == "Detalle de prueba"
    assert datetime.fromisoformat(datos["fecha_registro"]).date() == datetime.now(timezone.utc).date()
    antecedente = db_historial.get(AntecedenteClinico, datos["id"])
    assert antecedente.historial_clinico_id == 20
    assert antecedente.estado is True
    bitacora = db_historial.scalar(select(Bitacora))
    assert bitacora.usuario_id == 7
    assert bitacora.accion == "CREAR_ANTECEDENTE"
    assert bitacora.entidad_afectada == "antecedente_clinico"
    assert bitacora.id_registro_afectado == antecedente.id
    consulta = cliente_historial.get("/historial-clinico/2").json()
    assert consulta["historial"]["antecedentes"] == [datos]


def test_put_conserva_historial_fecha_y_estado(cliente_historial, db_historial):
    original = db_historial.get(AntecedenteClinico, 100)
    fecha_original = original.fecha_registro
    respuesta = cliente_historial.put(f"{URL}/100", json={
        "tipo": " medicamento ", "descripcion": "  Descripción actualizada  ",
    })
    assert respuesta.status_code == 200
    assert respuesta.json() == {
        "id": 100, "tipo": "MEDICAMENTO", "descripcion": "Descripción actualizada",
        "fecha_registro": fecha_original.isoformat(),
    }
    db_historial.refresh(original)
    assert original.historial_clinico_id == 10
    assert original.estado is True
    assert original.fecha_registro == fecha_original
    bitacora = db_historial.scalar(select(Bitacora))
    assert bitacora.usuario_id == 7
    assert bitacora.accion == "ACTUALIZAR_ANTECEDENTE"
    assert bitacora.id_registro_afectado == 100


@pytest.mark.parametrize("historial_id", [999, 40])
def test_post_historial_inexistente_o_inactivo(cliente_historial, db_historial, historial_id):
    respuesta = cliente_historial.post(URL, json={"historial_clinico_id": historial_id, **DATOS})
    assert respuesta.status_code == 404
    assert contar(db_historial, AntecedenteClinico) == 4
    assert contar(db_historial, Bitacora) == 0


@pytest.mark.parametrize("antecedente_id", [999, 101, 400])
def test_put_antecedente_no_disponible(cliente_historial, db_historial, antecedente_id):
    assert cliente_historial.put(f"{URL}/{antecedente_id}", json=DATOS).status_code == 404
    assert contar(db_historial, Bitacora) == 0


@pytest.mark.parametrize("metodo", ["post", "put"])
@pytest.mark.parametrize("cambios", [
    {"tipo": "NO_VALIDO"}, {"tipo": ""}, {"tipo": None}, {"tipo": "X" * 31},
    {"descripcion": "  \t\n"}, {"descripcion": None},
    {"estado": False}, {"fecha_registro": "2020-01-01"}, {"usuario_id": 8},
])
def test_validacion_campos(cliente_historial, db_historial, metodo, cambios):
    datos = {**DATOS, **cambios}
    if metodo == "post":
        datos["historial_clinico_id"] = 10
    respuesta = cliente_historial.request(
        metodo, URL if metodo == "post" else f"{URL}/100", json=datos,
    )
    assert respuesta.status_code == 422
    assert contar(db_historial, AntecedenteClinico) == 4
    assert contar(db_historial, Bitacora) == 0


@pytest.mark.parametrize("tipo", [
    "ALERGIA", "ENFERMEDAD", "CIRUGIA", "MEDICAMENTO", "ANTECEDENTE_FAMILIAR", "OTRO",
])
def test_tipos_compatibles_con_constraint_supabase(cliente_historial, tipo):
    respuesta = cliente_historial.post(URL, json={
        "historial_clinico_id": 10, "tipo": tipo, "descripcion": "Detalle",
    })
    assert respuesta.status_code == 201


@pytest.mark.parametrize("metodo,datos", [
    ("post", DATOS),
    ("post", {"historial_clinico_id": 0, **DATOS}),
    ("post", {"historial_clinico_id": 2**63, **DATOS}),
    ("put", {"tipo": "ALERGIA"}),
    ("put", {"descripcion": "Detalle"}),
    ("put", {"historial_clinico_id": 20, **DATOS}),
])
def test_campos_requeridos_y_historial_inmutable(cliente_historial, db_historial, metodo, datos):
    respuesta = cliente_historial.request(
        metodo, URL if metodo == "post" else f"{URL}/100", json=datos,
    )
    assert respuesta.status_code == 422
    assert db_historial.get(AntecedenteClinico, 100).historial_clinico_id == 10
    assert contar(db_historial, Bitacora) == 0


@pytest.mark.parametrize("metodo", ["post", "put"])
@pytest.mark.parametrize("fallo", ["bitacora", "commit", "flush", "refresh"])
def test_rollback_atomico(cliente_historial, db_historial, monkeypatch, metodo, fallo):
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
            patch.setattr(db_historial, fallo, fallar)
        datos = {"historial_clinico_id": 10, **DATOS} if metodo == "post" else DATOS
        with pytest.raises(SQLAlchemyError):
            cliente_historial.request(
                metodo, URL if metodo == "post" else f"{URL}/100", json=datos,
            )

    assert contar(db_historial, AntecedenteClinico) == 4
    assert contar(db_historial, Bitacora) == 0
    assert db_historial.get(AntecedenteClinico, 100).descripcion == "Antecedente inicial"


def test_fecha_utc_y_repositorio_sin_commit():
    db = MagicMock()
    antes = datetime.now(timezone.utc)
    antecedente = repo.crear_antecedente(
        db, AntecedenteClinicoCrear(historial_clinico_id=10, **DATOS),
    )
    assert antes <= antecedente.fecha_registro <= datetime.now(timezone.utc)
    assert antecedente.fecha_registro.tzinfo == timezone.utc
    assert antecedente.estado is True
    db.flush.assert_called_once()
    db.refresh.assert_called_once_with(antecedente)
    db.commit.assert_not_called()


def test_service_confirma_una_sola_transaccion(db_historial, monkeypatch):
    commit_real = db_historial.commit
    commit = MagicMock(wraps=commit_real)
    monkeypatch.setattr(db_historial, "commit", commit)
    service.crear_antecedente(
        db_historial, AntecedenteClinicoCrear(historial_clinico_id=10, **DATOS),
        db_historial.get(Usuario, 7),
    )
    commit.assert_called_once()
    assert contar(db_historial, Bitacora) == 1
