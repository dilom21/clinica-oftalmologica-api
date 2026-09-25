import pytest


URL = "/agenda-citas/historial"


@pytest.mark.parametrize("filtros", [
    {"paciente_id": 1}, {"codigo": "1"}, {"identificacion": "CI-001"},
    {"nombre": "pérez ANA"}, {"nombre": "  María  "},
    {"paciente_id": 1, "codigo": "1", "nombre": "Ana", "identificacion": "CI-001"},
])
def test_busqueda_y_contrato_frontend(cliente_historial, filtros):
    respuesta = cliente_historial.get(URL, params=filtros)
    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["paciente"] == {
        "id": 1, "nombres": "Ana María", "apellidos": "Pérez", "ci": "CI-001",
    }
    assert [cita["id"] for cita in datos["citas"]] == [1, 2, 3]
    assert datos["mensaje"] is None
    assert datos["citas"][0] == {
        "id": 1, "paciente_id": 1, "oftalmologo_id": 1, "fecha": "2026-09-01",
        "hora_inicio": "08:00:00", "hora_fin": "09:00:00", "motivo": None,
        "observaciones": None, "estado": "ATENDIDA", "canal": None,
    }


@pytest.mark.parametrize("filtros,ids", [
    ({"fecha_desde": "2026-09-02", "fecha_hasta": "2026-09-02"}, [2]),
    ({"fecha_desde": "2026-09-02"}, [2, 3]),
    ({"fecha_hasta": "2026-09-02"}, [1, 2]),
    ({"estado": "  cancelada  "}, [2]),
    ({"fecha_desde": "2026-09-02", "estado": "ATENDIDA"}, []),
])
def test_filtros_citas_inclusivos(cliente_historial, filtros, ids):
    respuesta = cliente_historial.get(URL, params={"paciente_id": 1, **filtros})
    assert respuesta.status_code == 200
    assert [cita["id"] for cita in respuesta.json()["citas"]] == ids
    if not ids:
        assert respuesta.json()["mensaje"]


def test_paciente_sin_citas(cliente_historial):
    respuesta = cliente_historial.get(URL, params={"paciente_id": 3})
    assert respuesta.status_code == 200
    assert respuesta.json()["citas"] == []
    assert respuesta.json()["mensaje"]
    assert respuesta.json()["paciente"]["ci"] is None


@pytest.mark.parametrize("filtros,status", [
    ({"paciente_id": 999}, 404),
    ({"identificacion": "CI-00"}, 404),
    ({"paciente_id": 1, "codigo": 2}, 404),
    ({"paciente_id": 1, "identificacion": "CI-002"}, 404),
    ({"nombre": "Ana"}, 409),
    ({}, 400), ({"nombre": "   ", "identificacion": "   "}, 400),
    ({"paciente_id": 1, "fecha_desde": "2026-09-03", "fecha_hasta": "2026-09-01"}, 400),
    ({"paciente_id": 1, "estado": "INVENTADO"}, 400),
    ({"codigo": "ABC"}, 422), ({"paciente_id": 0}, 422),
    ({"codigo": 2**63}, 422), ({"paciente_id": -1}, 422),
    ({"paciente_id": 1, "fecha_desde": "no-fecha"}, 422),
])
def test_errores_busqueda(cliente_historial, filtros, status):
    assert cliente_historial.get(URL, params=filtros).status_code == status


def test_nombre_escapa_comodines_sql(cliente_historial):
    respuesta = cliente_historial.get(URL, params={"nombre": "%_"})
    assert respuesta.status_code == 200
    assert respuesta.json()["paciente"]["id"] == 5
