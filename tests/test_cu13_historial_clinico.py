import pytest

from app.modules.gestion_historial_clinico.models.models import HistorialClinico
from app.modules.gestion_historial_clinico.services.service import consultar_historial_clinico


def test_historial_con_antecedentes_activos_y_contrato(cliente_historial):
    respuesta = cliente_historial.get("/historial-clinico/1")
    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["paciente"]["id"] == 1
    assert datos["paciente"]["telefono"] is None
    assert set(datos["paciente"]) == {
        "id", "usuario_id", "nombres", "apellidos", "ci", "fecha_nacimiento",
        "sexo", "telefono", "contacto_emergencia", "fecha_registro", "direccion", "estado",
    }
    assert set(datos["historial"]) == {
        "id", "fecha_apertura", "observaciones_generales", "antecedentes",
    }
    assert datos["historial"]["id"] == 10
    assert datos["historial"]["observaciones_generales"] is None
    assert [a["id"] for a in datos["historial"]["antecedentes"]] == [102, 100]
    assert set(datos["historial"]["antecedentes"][0]) == {
        "id", "tipo", "descripcion", "fecha_registro",
    }


@pytest.mark.parametrize("paciente_id", [3, 4])
def test_historial_ausente_o_inactivo_devuelve_null(cliente_historial, paciente_id):
    respuesta = cliente_historial.get(f"/historial-clinico/{paciente_id}")
    assert respuesta.status_code == 200
    assert respuesta.json()["historial"] is None
    assert respuesta.json()["paciente"]["id"] == paciente_id


def test_historial_sin_antecedentes(cliente_historial):
    respuesta = cliente_historial.get("/historial-clinico/2")
    assert respuesta.status_code == 200
    assert respuesta.json()["historial"]["antecedentes"] == []


def test_paciente_inexistente(cliente_historial):
    assert cliente_historial.get("/historial-clinico/999").status_code == 404


@pytest.mark.parametrize("paciente_id", [0, -1, 2**63, "abc"])
def test_id_invalido(cliente_historial, paciente_id):
    assert cliente_historial.get(f"/historial-clinico/{paciente_id}").status_code == 422


def test_recarga_coleccion_previamente_cargada_sin_inactivos(db_historial):
    historial = db_historial.get(HistorialClinico, 10)
    assert len(historial.antecedentes) == 3
    respuesta = consultar_historial_clinico(db_historial, 1)
    assert [a.id for a in respuesta.historial.antecedentes] == [102, 100]
    assert historial.paciente.id == 1
