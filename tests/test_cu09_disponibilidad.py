import unittest
from datetime import date, time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.modules.gestion_agenda_citas.repositories import repository as repo
from app.modules.gestion_agenda_citas.services.disponibilidad import (
    calcular_intervalos_disponibles,
    fusionar_intervalos,
    obtener_dia_semana,
)
from app.modules.gestion_agenda_citas.services import service as service_mod


def intervalo(h1, m1, h2, m2):
    return (time(h1, m1), time(h2, m2))


def oftalmologo_falso():
    return SimpleNamespace(
        id=1,
        matricula="MAT-001",
        nombres="Salet",
        apellidos="Ejemplo",
        especialidad="Oftalmología General",
        estado=True,
    )


def horario_falso(inicio, fin):
    return SimpleNamespace(
        id=1,
        oftalmologo_id=1,
        dia_semana=3,
        hora_inicio=inicio,
        hora_fin=fin,
        estado=True,
    )


def bloqueo_falso(inicio, fin):
    return SimpleNamespace(
        id=1,
        oftalmologo_id=1,
        fecha=date(2026, 9, 10),
        hora_inicio=inicio,
        hora_fin=fin,
        estado=True,
    )


def cita_falsa(inicio, fin, estado="CONFIRMADA"):
    return SimpleNamespace(
        id=1,
        paciente_id=1,
        oftalmologo_id=1,
        fecha=date(2026, 9, 10),
        hora_inicio=inicio,
        hora_fin=fin,
        estado=estado,
        motivo="Consulta",
    )


class TestDisponibilidad(unittest.TestCase):

    def test_horario_sin_ocupaciones_queda_completo(self):
        resultado = calcular_intervalos_disponibles(
            horarios_base=[intervalo(8, 0, 12, 0)],
            ocupaciones=[],
        )
        self.assertEqual(resultado, [intervalo(8, 0, 12, 0)])

    def test_dia_sin_horario_devuelve_vacio(self):
        resultado = calcular_intervalos_disponibles(
            horarios_base=[],
            ocupaciones=[],
        )
        self.assertEqual(resultado, [])

    def test_horario_con_cita_en_medio(self):
        resultado = calcular_intervalos_disponibles(
            horarios_base=[intervalo(8, 0, 12, 0)],
            ocupaciones=[intervalo(8, 30, 9, 0)],
        )
        self.assertEqual(
            resultado,
            [intervalo(8, 0, 8, 30), intervalo(9, 0, 12, 0)],
        )

    def test_horario_con_bloqueo_en_medio(self):
        resultado = calcular_intervalos_disponibles(
            horarios_base=[intervalo(8, 0, 12, 0)],
            ocupaciones=[intervalo(10, 0, 11, 0)],
        )
        self.assertEqual(
            resultado,
            [intervalo(8, 0, 10, 0), intervalo(11, 0, 12, 0)],
        )

    def test_cita_y_bloqueo_solapados(self):
        resultado = calcular_intervalos_disponibles(
            horarios_base=[intervalo(8, 0, 12, 0)],
            ocupaciones=[
                intervalo(9, 30, 10, 30),
                intervalo(10, 0, 11, 0),
            ],
        )
        self.assertEqual(
            resultado,
            [intervalo(8, 0, 9, 30), intervalo(11, 0, 12, 0)],
        )

    def test_varias_citas_y_bloqueos(self):
        resultado = calcular_intervalos_disponibles(
            horarios_base=[intervalo(8, 0, 12, 0)],
            ocupaciones=[
                intervalo(8, 30, 9, 0),
                intervalo(9, 0, 9, 30),
                intervalo(10, 0, 11, 0),
            ],
        )
        self.assertEqual(
            resultado,
            [intervalo(8, 0, 8, 30), intervalo(9, 30, 10, 0), intervalo(11, 0, 12, 0)],
        )

    def test_ocupacion_que_toca_inicio_del_horario(self):
        resultado = calcular_intervalos_disponibles(
            horarios_base=[intervalo(8, 0, 12, 0)],
            ocupaciones=[intervalo(8, 0, 9, 0)],
        )
        self.assertEqual(resultado, [intervalo(9, 0, 12, 0)])

    def test_ocupacion_que_toca_fin_del_horario(self):
        resultado = calcular_intervalos_disponibles(
            horarios_base=[intervalo(8, 0, 12, 0)],
            ocupaciones=[intervalo(11, 0, 12, 0)],
        )
        self.assertEqual(resultado, [intervalo(8, 0, 11, 0)])

    def test_todo_ocupado_devuelve_vacio(self):
        resultado = calcular_intervalos_disponibles(
            horarios_base=[intervalo(8, 0, 12, 0)],
            ocupaciones=[intervalo(8, 0, 12, 0)],
        )
        self.assertEqual(resultado, [])

    def test_ocupacion_fuera_del_horario_no_afecta(self):
        resultado = calcular_intervalos_disponibles(
            horarios_base=[intervalo(8, 0, 12, 0)],
            ocupaciones=[intervalo(6, 0, 7, 0), intervalo(13, 0, 14, 0)],
        )
        self.assertEqual(resultado, [intervalo(8, 0, 12, 0)])

    def test_multiples_horarios_base_ordenados(self):
        resultado = calcular_intervalos_disponibles(
            horarios_base=[
                intervalo(14, 0, 18, 0),
                intervalo(8, 0, 12, 0),
            ],
            ocupaciones=[intervalo(9, 0, 10, 0), intervalo(15, 0, 16, 0)],
        )
        self.assertEqual(
            resultado,
            [
                intervalo(8, 0, 9, 0),
                intervalo(10, 0, 12, 0),
                intervalo(14, 0, 15, 0),
                intervalo(16, 0, 18, 0),
            ],
        )

    def test_ocupacion_en_hueco_entre_horarios_no_genera_disponibilidad(self):
        resultado = calcular_intervalos_disponibles(
            horarios_base=[
                intervalo(8, 0, 12, 0),
                intervalo(13, 0, 17, 0),
            ],
            ocupaciones=[intervalo(12, 30, 12, 45)],
        )
        self.assertEqual(
            resultado,
            [intervalo(8, 0, 12, 0), intervalo(13, 0, 17, 0)],
        )

    def test_horario_invertido_se_ignora(self):
        resultado = calcular_intervalos_disponibles(
            horarios_base=[intervalo(12, 0, 8, 0)],
            ocupaciones=[],
        )
        self.assertEqual(resultado, [])

    def test_fusionar_intervalos_solapados(self):
        self.assertEqual(
            fusionar_intervalos([intervalo(9, 0, 11, 0), intervalo(8, 0, 10, 0)]),
            [intervalo(8, 0, 11, 0)],
        )

    def test_fusionar_intervalos_contiguos(self):
        self.assertEqual(
            fusionar_intervalos([intervalo(8, 0, 9, 0), intervalo(9, 0, 10, 0)]),
            [intervalo(8, 0, 10, 0)],
        )

    def test_dia_semana_iso(self):
        self.assertEqual(obtener_dia_semana(date(2026, 9, 7)), 1)
        self.assertEqual(obtener_dia_semana(date(2026, 9, 10)), 4)
        self.assertEqual(obtener_dia_semana(date(2026, 9, 13)), 7)


class TestRepositorio(unittest.TestCase):

    def _capturar_statement(self, funcion, *args, **kwargs):
        db = MagicMock()
        capturado = {}

        def _scalars(stmt, *a, **k):
            capturado["stmt"] = stmt
            resultado = MagicMock()
            resultado.all.return_value = []
            return resultado

        db.scalars.side_effect = _scalars
        funcion(db, *args, **kwargs)
        return capturado["stmt"]

    def test_consulta_citas_excluye_canceladas(self):
        stmt_sql = self._capturar_statement(
            repo.obtener_citas_por_oftalmologo_y_fecha,
            oftalmologo_id=1,
            fecha=date(2026, 9, 10),
        )

        sql = str(
            stmt_sql.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        self.assertIn("cita", sql)
        self.assertIn("CANCELADA", sql)
        self.assertNotIn("CONFIRMADA", sql)
        self.assertIn("NOT IN", sql)

    def test_consulta_horarios_solo_activos_y_dia(self):
        stmt_sql = self._capturar_statement(
            repo.obtener_horarios_por_oftalmologo_y_dia,
            oftalmologo_id=1,
            dia_semana=4,
        )
        sql = str(
            stmt_sql.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        self.assertIn("dia_semana", sql)
        self.assertIn("oftalmologo_id", sql)
        self.assertIn("true", sql.lower())


class TestServiceCU09(unittest.TestCase):

    def setUp(self):
        self.db = object()

    def test_consultar_disponibilidad_sin_horario(self):
        with patch.object(service_mod.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso()), \
             patch.object(service_mod.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[]):
            resultado = service_mod.consultar_disponibilidad(
                self.db,
                oftalmologo_id=1,
                fecha=date(2026, 9, 10),
            )

        self.assertFalse(resultado.tiene_horario)
        self.assertEqual(resultado.horarios_base, [])
        self.assertEqual(resultado.intervalos_disponibles, [])
        self.assertEqual(resultado.oftalmologo.id, 1)

    def test_consultar_disponibilidad_con_cita_en_medio(self):
        with patch.object(service_mod.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso()), \
             patch.object(service_mod.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[horario_falso(time(8, 0), time(12, 0))]), \
             patch.object(service_mod.repo, "obtener_bloqueos_por_oftalmologo_y_fecha", return_value=[]), \
             patch.object(service_mod.repo, "obtener_citas_por_oftalmologo_y_fecha", return_value=[cita_falsa(time(8, 30), time(9, 0))]):
            resultado = service_mod.consultar_disponibilidad(
                self.db,
                oftalmologo_id=1,
                fecha=date(2026, 9, 10),
            )

        self.assertTrue(resultado.tiene_horario)
        self.assertEqual(
            [(i.hora_inicio, i.hora_fin) for i in resultado.intervalos_disponibles],
            [intervalo(8, 0, 8, 30), intervalo(9, 0, 12, 0)],
        )

    def test_consultar_disponibilidad_oftalmologo_inexistente(self):
        with patch.object(service_mod.repo, "obtener_oftalmologo_activo_por_id", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                service_mod.consultar_disponibilidad(
                    self.db,
                    oftalmologo_id=999,
                    fecha=date(2026, 9, 10),
                )

        self.assertEqual(ctx.exception.status_code, 404)

    def test_consultar_agenda_sin_horario(self):
        with patch.object(service_mod.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso()), \
             patch.object(service_mod.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[]):
            resultado = service_mod.consultar_agenda(
                self.db,
                oftalmologo_id=1,
                fecha=date(2026, 9, 10),
            )

        self.assertFalse(resultado.tiene_horario)
        self.assertEqual(resultado.citas, [])

    def test_consultar_agenda_ignora_canceladas(self):
        with patch.object(service_mod.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso()), \
             patch.object(service_mod.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[horario_falso(time(8, 0), time(12, 0))]), \
             patch.object(service_mod.repo, "obtener_citas_por_oftalmologo_y_fecha", return_value=[cita_falsa(time(9, 0), time(10, 0))]):
            resultado = service_mod.consultar_agenda(
                self.db,
                oftalmologo_id=1,
                fecha=date(2026, 9, 10),
            )

        self.assertTrue(resultado.tiene_horario)
        self.assertEqual(len(resultado.citas), 1)
        self.assertEqual(resultado.citas[0].estado, "CONFIRMADA")
        self.assertFalse(hasattr(resultado.citas[0], "paciente_id"))

    def test_listar_oftalmologos_activos(self):
        with patch.object(service_mod.repo, "listar_oftalmologos_activos", return_value=[oftalmologo_falso()]):
            resultado = service_mod.listar_oftalmologos_activos(self.db)

        self.assertEqual(len(resultado), 1)


if __name__ == "__main__":
    unittest.main()
