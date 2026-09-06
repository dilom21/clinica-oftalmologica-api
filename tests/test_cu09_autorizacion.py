import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.core.dependencies import (
    ACCION_AMBAS,
    ACCION_ESCRITURA,
    ACCION_LECTURA,
    requerir_permiso,
)
from app.modules.gestion_agenda_citas.services import service as service_mod
from app.modules.gestion_usuarios_seguridad.repositories import (
    repository as repo_seguridad,
)
from app.modules.gestion_usuarios_seguridad.services import menu_service


def usuario_falso(usuario_id=5, rol="Administrador", rol_id=1):
    return SimpleNamespace(
        id=usuario_id,
        rol_id=rol_id,
        rol=SimpleNamespace(nombre=rol),
        estado=True,
    )


def oftalmologo_falso(usuario_id=2):
    return SimpleNamespace(
        id=1,
        usuario_id=usuario_id,
        matricula="MAT-001",
        nombres="Salet",
        apellidos="Ejemplo",
        especialidad="Oftalmología General",
        estado=True,
    )


def db_con_accion(accion_nombre):
    db = MagicMock()
    db.execute.return_value.first.return_value = SimpleNamespace(
        accion_nombre=accion_nombre
    )
    return db


def db_sin_permiso():
    db = MagicMock()
    db.execute.return_value.first.return_value = None
    return db


FUNCION_CU09 = "Consultar agenda y disponibilidad médica"


class TestRequerirPermisoAccion(unittest.TestCase):
    """CORRECCIÓN 1: la acción otorgada debe satisfacer la requerida."""

    def test_lectura_permite_rol_funcion_con_lectura(self):
        permiso = requerir_permiso(FUNCION_CU09, ACCION_LECTURA)
        usuario = usuario_falso()
        self.assertIs(
            permiso(usuario=usuario, db=db_con_accion("LECTURA")),
            usuario,
        )

    def test_lectura_permite_rol_funcion_con_ambas(self):
        permiso = requerir_permiso(FUNCION_CU09, ACCION_LECTURA)
        usuario = usuario_falso()
        self.assertIs(
            permiso(usuario=usuario, db=db_con_accion(ACCION_AMBAS)),
            usuario,
        )

    def test_lectura_no_permite_solo_escritura(self):
        permiso = requerir_permiso(FUNCION_CU09, ACCION_LECTURA)
        with self.assertRaises(HTTPException) as ctx:
            permiso(usuario=usuario_falso(), db=db_con_accion("ESCRITURA"))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_escritura_permite_rol_funcion_con_escritura(self):
        permiso = requerir_permiso(FUNCION_CU09, ACCION_ESCRITURA)
        usuario = usuario_falso()
        self.assertIs(
            permiso(usuario=usuario, db=db_con_accion("ESCRITURA")),
            usuario,
        )

    def test_escritura_permite_rol_funcion_con_ambas(self):
        permiso = requerir_permiso(FUNCION_CU09, ACCION_ESCRITURA)
        usuario = usuario_falso()
        self.assertIs(
            permiso(usuario=usuario, db=db_con_accion(ACCION_AMBAS)),
            usuario,
        )

    def test_usuario_sin_funcion_devuelve_403(self):
        permiso = requerir_permiso(FUNCION_CU09, ACCION_LECTURA)
        with self.assertRaises(HTTPException) as ctx:
            permiso(usuario=usuario_falso(), db=db_sin_permiso())
        self.assertEqual(ctx.exception.status_code, 403)

    def test_llamada_sin_accion_conserva_compatibilidad(self):
        permiso = requerir_permiso("Gestionar roles y permisos")
        usuario = usuario_falso()
        self.assertIs(
            permiso(usuario=usuario, db=db_con_accion(ACCION_AMBAS)),
            usuario,
        )


class TestAutorizacionContextualAgendaCU09(unittest.TestCase):
    """CORRECCIÓN 3: reglas por rol sobre agenda/disponibilidad."""

    def setUp(self):
        self.db = object()
        self.fecha = date(2026, 9, 10)

    def test_paciente_puede_consultar_disponibilidad(self):
        paciente = usuario_falso(usuario_id=9, rol="Paciente", rol_id=4)
        with patch.object(service_mod.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(service_mod.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[]):
            resultado = service_mod.consultar_disponibilidad(
                self.db,
                oftalmologo_id=1,
                fecha=self.fecha,
            )
        self.assertIsNotNone(resultado)
        self.assertFalse(resultado.tiene_horario)

    def test_paciente_no_puede_consultar_agenda(self):
        paciente = usuario_falso(usuario_id=9, rol="Paciente", rol_id=4)
        with patch.object(service_mod.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)):
            with self.assertRaises(HTTPException) as ctx:
                service_mod.consultar_agenda(
                    self.db,
                    oftalmologo_id=1,
                    fecha=self.fecha,
                    usuario=paciente,
                )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_oftalmologo_puede_consultar_su_propia_agenda(self):
        oftalmologo = usuario_falso(usuario_id=7, rol="Oftalmólogo", rol_id=2)
        with patch.object(service_mod.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=7)), \
             patch.object(service_mod.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[]):
            resultado = service_mod.consultar_agenda(
                self.db,
                oftalmologo_id=1,
                fecha=self.fecha,
                usuario=oftalmologo,
            )
        self.assertIsNotNone(resultado)
        self.assertFalse(resultado.tiene_horario)

    def test_oftalmologo_no_puede_consultar_agenda_ajena(self):
        oftalmologo = usuario_falso(usuario_id=7, rol="Oftalmólogo", rol_id=2)
        with patch.object(service_mod.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)):
            with self.assertRaises(HTTPException) as ctx:
                service_mod.consultar_agenda(
                    self.db,
                    oftalmologo_id=1,
                    fecha=self.fecha,
                    usuario=oftalmologo,
                )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_recepcionista_puede_consultar_agenda_de_un_oftalmologo(self):
        recepcionista = usuario_falso(usuario_id=8, rol="Recepcionista", rol_id=3)
        with patch.object(service_mod.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(service_mod.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[]):
            resultado = service_mod.consultar_agenda(
                self.db,
                oftalmologo_id=1,
                fecha=self.fecha,
                usuario=recepcionista,
            )
        self.assertIsNotNone(resultado)
        self.assertFalse(resultado.tiene_horario)

    def test_administrador_mantiene_acceso_a_agenda(self):
        administrador = usuario_falso(usuario_id=1, rol="Administrador", rol_id=1)
        with patch.object(service_mod.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(service_mod.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[]):
            resultado = service_mod.consultar_agenda(
                self.db,
                oftalmologo_id=1,
                fecha=self.fecha,
                usuario=administrador,
            )
        self.assertIsNotNone(resultado)
        self.assertFalse(resultado.tiene_horario)

    def test_oftalmologo_al_listar_solo_ve_su_registro(self):
        oftalmologo = usuario_falso(usuario_id=7, rol="Oftalmólogo", rol_id=2)
        con_lista = [
            oftalmologo_falso(usuario_id=7),
            oftalmologo_falso(usuario_id=2),
        ]
        with patch.object(service_mod.repo, "listar_oftalmologos_activos", return_value=con_lista):
            resultado = service_mod.listar_oftalmologos_activos(
                self.db,
                usuario=oftalmologo,
            )
        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0].usuario_id, 7)


class TestMenuDinamicoCU09(unittest.TestCase):
    """CORRECCIÓN 4: menú construido desde rol_funcion + accion."""

    def _capturar_statement(self, funcion, *args, **kwargs):
        db = MagicMock()
        capturado = {}

        def _execute(stmt, *a, **k):
            capturado["stmt"] = stmt
            resultado = MagicMock()
            resultado.mappings.return_value.all.return_value = []
            return resultado

        db.execute.side_effect = _execute
        funcion(db, *args, **kwargs)
        return capturado["stmt"]

    def test_menu_devuelve_unicamente_funciones_de_rol_funcion(self):
        filas = [
            {
                "modulo_id": 2,
                "modulo_nombre": "Agenda y Citas",
                "funcion_id": 10,
                "funcion_nombre": "Consultar agenda y disponibilidad médica",
                "funcion_descripcion": None,
                "accion_id": 3,
                "accion_nombre": ACCION_AMBAS,
            },
            {
                "modulo_id": 1,
                "modulo_nombre": "Seguridad",
                "funcion_id": 5,
                "funcion_nombre": "Gestionar roles y permisos",
                "funcion_descripcion": None,
                "accion_id": 3,
                "accion_nombre": ACCION_AMBAS,
            },
        ]
        with patch.object(menu_service.repo, "listar_menu_por_rol", return_value=filas):
            menu = menu_service.obtener_menu(object(), rol_id=2)

        nombres_modulos = {modulo.nombre for modulo in menu}
        self.assertEqual(nombres_modulos, {"Agenda y Citas", "Seguridad"})
        total_funciones = sum(
            len(modulo.funciones) for modulo in menu
        )
        self.assertEqual(total_funciones, 2)

        sql = str(
            self._capturar_statement(
                repo_seguridad.listar_menu_por_rol,
                rol_id=2,
            ).compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        self.assertIn("rol_funcion", sql)
        self.assertIn("accion", sql)
        self.assertIn("funcion", sql)
        self.assertIn("modulo", sql)
        self.assertIn("rol_id", sql)

    def test_menu_incluye_accion_correspondiente(self):
        filas = [
            {
                "modulo_id": 2,
                "modulo_nombre": "Agenda y Citas",
                "funcion_id": 10,
                "funcion_nombre": "Consultar agenda y disponibilidad médica",
                "funcion_descripcion": None,
                "accion_id": 1,
                "accion_nombre": ACCION_LECTURA,
            }
        ]
        with patch.object(menu_service.repo, "listar_menu_por_rol", return_value=filas):
            menu = menu_service.obtener_menu(object(), rol_id=3)

        funcion = menu[0].funciones[0]
        self.assertEqual(funcion.accion_id, 1)
        self.assertEqual(funcion.accion_nombre, ACCION_LECTURA)


if __name__ == "__main__":
    unittest.main()
