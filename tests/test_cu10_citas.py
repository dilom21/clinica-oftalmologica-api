import unittest
from contextlib import ExitStack
from datetime import date, time, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.modules.gestion_agenda_citas.repositories import repository as repo
from app.modules.gestion_agenda_citas.schemas.schemas import (
    CitaEstadoUpdate,
    CitaResponse,
    CitaUpdate,
)
from app.modules.gestion_agenda_citas.services import citas

FUNCION_CU10 = "Gestionar citas médicas"


def fecha_futura():
    """Fecha estrictamente futura para pruebas deterministas."""
    return date.today() + timedelta(days=7)


def usuario_falso(usuario_id=5, rol="Administrador", rol_id=1):
    return SimpleNamespace(
        id=usuario_id,
        rol_id=rol_id,
        rol=SimpleNamespace(nombre=rol),
        estado=True,
    )


def paciente_falso(paciente_id=1, estado=True):
    return SimpleNamespace(
        id=paciente_id,
        nombres="Ana",
        apellidos="Perez",
        ci="12345",
        estado=estado,
    )


def oftalmologo_falso(usuario_id=2, estado=True):
    return SimpleNamespace(
        id=1,
        usuario_id=usuario_id,
        matricula="MAT-001",
        nombres="Salet",
        apellidos="Ejemplo",
        especialidad="Oftalmología General",
        estado=estado,
    )


def intervalo(inicio, fin):
    return SimpleNamespace(hora_inicio=inicio, hora_fin=fin)


def disponibilidad_falsa(tiene_horario=True, intervalos=None):
    return SimpleNamespace(
        tiene_horario=tiene_horario,
        intervalos_disponibles=intervalos or [],
    )


def cita_falsa(
    cita_id=1,
    fecha=None,
    inicio=time(8, 0),
    fin=time(9, 0),
    estado="PROGRAMADA",
    motivo="Consulta",
    observaciones=None,
    paciente_id=1,
    oftalmologo_id=1,
):
    return SimpleNamespace(
        id=cita_id,
        paciente_id=paciente_id,
        oftalmologo_id=oftalmologo_id,
        fecha=fecha or fecha_futura(),
        hora_inicio=inicio,
        hora_fin=fin,
        motivo=motivo,
        observaciones=observaciones,
        estado=estado,
    )


def datos_cita(
    fecha=None,
    inicio=time(8, 0),
    motivo="Consulta",
    observaciones=None,
):
    return SimpleNamespace(
        paciente_id=1,
        oftalmologo_id=1,
        fecha=fecha or fecha_futura(),
        hora_inicio=inicio,
        motivo=motivo,
        observaciones=observaciones,
    )


# =========================================================
# Registrar cita (CU10)
# =========================================================

class TestRegistrarCitaCU10(unittest.TestCase):

    def setUp(self):
        self.db = MagicMock()
        self.usuario = usuario_falso()

    def test_registrar_cita_valida_y_crea_con_hora_fin_automatica(self):
        creada = cita_falsa(cita_id=50)
        with patch.object(citas, "obtener_paciente_por_id", return_value=paciente_falso()), \
             patch.object(citas.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso()), \
             patch.object(
                 citas.service_cu09,
                 "consultar_disponibilidad",
                 return_value=disponibilidad_falsa(
                     intervalos=[intervalo(time(8, 0), time(12, 0))]
                 ),
             ), \
             patch.object(citas.repo, "crear_cita", return_value=creada) as mock_crear:
            resultado = citas.registrar_cita(
                self.db,
                datos_cita(inicio=time(8, 0), motivo="Dolor ocular"),
                self.usuario,
            )

        self.assertIs(resultado, creada)
        kwargs = mock_crear.call_args.kwargs
        self.assertEqual(kwargs["paciente_id"], 1)
        self.assertEqual(kwargs["oftalmologo_id"], 1)
        self.assertEqual(kwargs["hora_inicio"], time(8, 0))
        self.assertEqual(kwargs["hora_fin"], time(9, 0))
        self.assertEqual(kwargs["estado"], "PROGRAMADA")
        self.assertEqual(kwargs["creado_por_usuario_id"], self.usuario.id)
        self.assertEqual(kwargs["motivo"], "Dolor ocular")

    def test_paciente_inexistente_devuelve_404(self):
        with patch.object(citas, "obtener_paciente_por_id", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                citas.registrar_cita(self.db, datos_cita(), self.usuario)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_paciente_inactivo_devuelve_404(self):
        with patch.object(citas, "obtener_paciente_por_id", return_value=paciente_falso(estado=False)):
            with self.assertRaises(HTTPException) as ctx:
                citas.registrar_cita(self.db, datos_cita(), self.usuario)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_oftalmologo_inexistente_devuelve_404(self):
        with patch.object(citas, "obtener_paciente_por_id", return_value=paciente_falso()), \
             patch.object(citas.repo, "obtener_oftalmologo_activo_por_id", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                citas.registrar_cita(self.db, datos_cita(), self.usuario)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_fecha_pasada_devuelve_400(self):
        with patch.object(citas, "obtener_paciente_por_id", return_value=paciente_falso()), \
             patch.object(citas.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso()):
            with self.assertRaises(HTTPException) as ctx:
                citas.registrar_cita(
                    self.db,
                    datos_cita(fecha=date(2020, 1, 1)),
                    self.usuario,
                )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_dia_sin_horario_devuelve_409(self):
        with patch.object(citas, "obtener_paciente_por_id", return_value=paciente_falso()), \
             patch.object(citas.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso()), \
             patch.object(
                 citas.service_cu09,
                 "consultar_disponibilidad",
                 return_value=disponibilidad_falsa(tiene_horario=False),
             ):
            with self.assertRaises(HTTPException) as ctx:
                citas.registrar_cita(self.db, datos_cita(), self.usuario)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_horario_fuera_de_intervalo_disponible_devuelve_409(self):
        # Solo quedó disponible 09:00-12:00; se pide 08:00-09:00.
        with patch.object(citas, "obtener_paciente_por_id", return_value=paciente_falso()), \
             patch.object(citas.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso()), \
             patch.object(
                 citas.service_cu09,
                 "consultar_disponibilidad",
                 return_value=disponibilidad_falsa(
                     intervalos=[intervalo(time(9, 0), time(12, 0))]
                 ),
             ):
            with self.assertRaises(HTTPException) as ctx:
                citas.registrar_cita(self.db, datos_cita(inicio=time(8, 0)), self.usuario)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_horario_bloqueado_o_ocupado_devuelve_409(self):
        # Disponible solo 11:00-12:00; 10:00-11:00 está ocupado/bloqueado.
        with patch.object(citas, "obtener_paciente_por_id", return_value=paciente_falso()), \
             patch.object(citas.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso()), \
             patch.object(
                 citas.service_cu09,
                 "consultar_disponibilidad",
                 return_value=disponibilidad_falsa(
                     intervalos=[intervalo(time(11, 0), time(12, 0))]
                 ),
             ):
            with self.assertRaises(HTTPException) as ctx:
                citas.registrar_cita(self.db, datos_cita(inicio=time(10, 0)), self.usuario)
        self.assertEqual(ctx.exception.status_code, 409)


# =========================================================
# Consultar / listar citas (CU10)
# =========================================================

class TestListarCitasCU10(unittest.TestCase):

    def setUp(self):
        self.db = MagicMock()

    def test_listar_con_filtros_pasa_estado_normalizado(self):
        with patch.object(citas.repo, "listar_citas", return_value=[cita_falsa()]) as mock_listar:
            resultado = citas.listar_citas(
                self.db,
                fecha=fecha_futura(),
                paciente_id=1,
                oftalmologo_id=2,
                estado="confirmada",
            )

        kwargs = mock_listar.call_args.kwargs
        self.assertEqual(kwargs["fecha"], fecha_futura())
        self.assertEqual(kwargs["paciente_id"], 1)
        self.assertEqual(kwargs["oftalmologo_id"], 2)
        self.assertEqual(kwargs["estado"], "CONFIRMADA")
        self.assertEqual(len(resultado), 1)

    def test_listar_sin_filtros_envia_estado_none(self):
        with patch.object(citas.repo, "listar_citas", return_value=[]) as mock_listar:
            citas.listar_citas(self.db)

        self.assertIsNone(mock_listar.call_args.kwargs["estado"])

    def test_listar_estado_invalido_devuelve_400(self):
        with patch.object(citas.repo, "listar_citas", return_value=[]) as mock_listar:
            with self.assertRaises(HTTPException) as ctx:
                citas.listar_citas(self.db, estado="INEXISTENTE")
        self.assertEqual(ctx.exception.status_code, 400)
        mock_listar.assert_not_called()


# =========================================================
# Detalle de cita (CU10)
# =========================================================

class TestDetalleCitaCU10(unittest.TestCase):

    def test_obtener_cita_existente(self):
        db = MagicMock()
        cita = cita_falsa(cita_id=7)
        with patch.object(citas.repo, "obtener_cita_por_id", return_value=cita):
            resultado = citas.obtener_cita(db, 7)
        self.assertIs(resultado, cita)

    def test_obtener_cita_inexistente_devuelve_404(self):
        db = MagicMock()
        with patch.object(citas.repo, "obtener_cita_por_id", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                citas.obtener_cita(db, 999)
        self.assertEqual(ctx.exception.status_code, 404)


# =========================================================
# Reprogramar cita (CU10)
# =========================================================

class TestReprogramarCitaCU10(unittest.TestCase):

    def setUp(self):
        self.db = MagicMock()
        self.usuario = usuario_falso()
        self.fecha_a = fecha_futura()
        self.fecha_b = self.fecha_a + timedelta(days=1)
        self.cita = cita_falsa(
            cita_id=10,
            fecha=self.fecha_a,
            inicio=time(8, 0),
            fin=time(9, 0),
            estado="PROGRAMADA",
        )

    def _patches_base(self, disponibilidad=None):
        stack = ExitStack()
        stack.enter_context(
            patch.object(citas.repo, "obtener_cita_por_id", return_value=self.cita)
        )
        mock_dispo = stack.enter_context(
            patch.object(
                citas.service_cu09,
                "consultar_disponibilidad",
                return_value=(
                    disponibilidad
                    if disponibilidad is not None
                    else disponibilidad_falsa(
                        intervalos=[intervalo(time(9, 0), time(12, 0))]
                    )
                ),
            )
        )
        mock_actualizar = stack.enter_context(
            patch.object(citas.repo, "actualizar_cita", return_value=self.cita)
        )
        return stack, mock_dispo, mock_actualizar

    def test_reprogramar_fecha_y_hora_valida_con_cu09(self):
        datos = SimpleNamespace(
            fecha=self.fecha_b,
            hora_inicio=time(10, 0),
            motivo=None,
            observaciones=None,
        )
        stack, mock_dispo, mock_actualizar = self._patches_base()
        with stack:
            resultado = citas.reprogramar_cita(self.db, 10, datos, self.usuario)

        self.assertIs(resultado, self.cita)
        mock_dispo.assert_called_once_with(self.db, 1, self.fecha_b)
        kwargs = mock_actualizar.call_args.kwargs
        self.assertEqual(kwargs["fecha"], self.fecha_b)
        self.assertEqual(kwargs["hora_inicio"], time(10, 0))
        self.assertEqual(kwargs["hora_fin"], time(11, 0))

    def test_reprogramar_solo_motivo_no_revalida_disponibilidad(self):
        datos = SimpleNamespace(
            fecha=None,
            hora_inicio=None,
            motivo="Control postoperatorio",
            observaciones=None,
        )
        stack, mock_dispo, mock_actualizar = self._patches_base()
        with stack:
            citas.reprogramar_cita(self.db, 10, datos, self.usuario)

        mock_dispo.assert_not_called()
        kwargs = mock_actualizar.call_args.kwargs
        self.assertEqual(kwargs["fecha"], self.fecha_a)
        self.assertEqual(kwargs["hora_inicio"], time(8, 0))
        self.assertEqual(kwargs["hora_fin"], time(9, 0))
        self.assertEqual(kwargs["motivo"], "Control postoperatorio")

    def test_reprogramar_sin_cambios_devuelve_cita_actual(self):
        datos = SimpleNamespace(
            fecha=self.fecha_a,
            hora_inicio=time(8, 0),
            motivo=self.cita.motivo,
            observaciones=self.cita.observaciones,
        )
        stack, mock_dispo, mock_actualizar = self._patches_base()
        with stack:
            resultado = citas.reprogramar_cita(self.db, 10, datos, self.usuario)

        self.assertIs(resultado, self.cita)
        mock_dispo.assert_not_called()
        mock_actualizar.assert_not_called()

    def test_reprogramar_slot_no_disponible_devuelve_409(self):
        datos = SimpleNamespace(
            fecha=self.fecha_b,
            hora_inicio=time(10, 0),
            motivo=None,
            observaciones=None,
        )
        disponibilidad = disponibilidad_falsa(
            intervalos=[intervalo(time(11, 0), time(12, 0))]
        )
        stack, _, _ = self._patches_base(disponibilidad=disponibilidad)
        with stack:
            with self.assertRaises(HTTPException) as ctx:
                citas.reprogramar_cita(self.db, 10, datos, self.usuario)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_reprogramar_fecha_pasada_devuelve_400(self):
        datos = SimpleNamespace(
            fecha=date(2020, 1, 1),
            hora_inicio=time(10, 0),
            motivo=None,
            observaciones=None,
        )
        stack, _, _ = self._patches_base()
        with stack:
            with self.assertRaises(HTTPException) as ctx:
                citas.reprogramar_cita(self.db, 10, datos, self.usuario)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_reprogramar_cita_inexistente_devuelve_404(self):
        datos = SimpleNamespace(
            fecha=self.fecha_b,
            hora_inicio=time(10, 0),
            motivo=None,
            observaciones=None,
        )
        with patch.object(citas.repo, "obtener_cita_por_id", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                citas.reprogramar_cita(self.db, 999, datos, self.usuario)
        self.assertEqual(ctx.exception.status_code, 404)


# =========================================================
# Cambiar estado y cancelar cita (CU10)
# =========================================================

class TestEstadoCitaCU10(unittest.TestCase):

    def setUp(self):
        self.db = MagicMock()
        self.usuario = usuario_falso()

    def test_cambiar_estado_valido_actualiza(self):
        cita = cita_falsa(cita_id=3, estado="PROGRAMADA")
        actualizada = cita_falsa(cita_id=3, estado="CONFIRMADA")
        with patch.object(citas.repo, "obtener_cita_por_id", return_value=cita), \
             patch.object(citas.repo, "actualizar_estado_cita", return_value=actualizada) as mock_estado:
            resultado = citas.cambiar_estado_cita(
                self.db,
                3,
                "confirmada",
                self.usuario,
            )

        self.assertEqual(resultado.estado, "CONFIRMADA")
        self.assertEqual(
            mock_estado.call_args.args[2],
            "CONFIRMADA",
        )

    def test_cambiar_mismo_estado_no_actualiza(self):
        cita = cita_falsa(cita_id=3, estado="ATENDIDA")
        with patch.object(citas.repo, "obtener_cita_por_id", return_value=cita), \
             patch.object(citas.repo, "actualizar_estado_cita", return_value=cita) as mock_estado:
            resultado = citas.cambiar_estado_cita(self.db, 3, "ATENDIDA", self.usuario)

        self.assertIs(resultado, cita)
        mock_estado.assert_not_called()

    def test_estado_invalido_devuelve_400(self):
        cita = cita_falsa(cita_id=3, estado="PROGRAMADA")
        with patch.object(citas.repo, "obtener_cita_por_id", return_value=cita), \
             patch.object(citas.repo, "actualizar_estado_cita", return_value=cita) as mock_estado:
            with self.assertRaises(HTTPException) as ctx:
                citas.cambiar_estado_cita(self.db, 3, "NO_EXISTE", self.usuario)
        self.assertEqual(ctx.exception.status_code, 400)
        mock_estado.assert_not_called()

    def test_cambiar_estado_cita_inexistente_devuelve_404(self):
        with patch.object(citas.repo, "obtener_cita_por_id", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                citas.cambiar_estado_cita(self.db, 999, "CONFIRMADA", self.usuario)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_cancelar_cita_cambia_estado_a_cancelada(self):
        cita = cita_falsa(cita_id=5, estado="PROGRAMADA")
        cancelada = cita_falsa(cita_id=5, estado="CANCELADA")
        with patch.object(citas.repo, "obtener_cita_por_id", return_value=cita), \
             patch.object(citas.repo, "actualizar_estado_cita", return_value=cancelada) as mock_estado:
            resultado = citas.cancelar_cita(self.db, 5, self.usuario)

        self.assertEqual(resultado.estado, "CANCELADA")
        self.assertEqual(
            mock_estado.call_args.args[2],
            "CANCELADA",
        )

    def test_cancelar_cita_ya_cancelada_no_actualiza(self):
        cita = cita_falsa(cita_id=5, estado="CANCELADA")
        with patch.object(citas.repo, "obtener_cita_por_id", return_value=cita), \
             patch.object(citas.repo, "actualizar_estado_cita", return_value=cita) as mock_estado:
            resultado = citas.cancelar_cita(self.db, 5, self.usuario)

        self.assertIs(resultado, cita)
        mock_estado.assert_not_called()


# =========================================================
# Schemas CU10
# =========================================================

class TestSchemasCU10(unittest.TestCase):

    def test_estado_update_normaliza_minusculas(self):
        datos = CitaEstadoUpdate(estado="confirmada")
        self.assertEqual(datos.estado, "CONFIRMADA")

    def test_estado_update_estado_invalido_rechazado(self):
        with self.assertRaises(ValidationError):
            CitaEstadoUpdate(estado="INEXISTENTE")

    def test_estados_validos_aceptados(self):
        for estado in ("PROGRAMADA", "CONFIRMADA", "EN_ESPERA",
                       "ATENDIDA", "CANCELADA", "NO_ASISTIO"):
            self.assertEqual(
                CitaEstadoUpdate(estado=estado).estado,
                estado,
            )

    def test_update_vacio_rechazado(self):
        with self.assertRaises(ValidationError):
            CitaUpdate()

    def test_update_con_solo_motivo_aceptado(self):
        datos = CitaUpdate(motivo="Control")
        self.assertEqual(datos.motivo, "Control")
        self.assertIsNone(datos.fecha)

    def test_update_con_fecha_y_hora_aceptado(self):
        datos = CitaUpdate(fecha=fecha_futura(), hora_inicio=time(10, 0))
        self.assertEqual(datos.hora_inicio, time(10, 0))

    def test_response_valida_objeto_cita(self):
        cita = cita_falsa(
            cita_id=1,
            inicio=time(8, 0),
            fin=time(9, 0),
            estado="PROGRAMADA",
            motivo="Consulta",
        )
        respuesta = CitaResponse.model_validate(cita)
        self.assertEqual(respuesta.id, 1)
        self.assertEqual(respuesta.hora_fin, time(9, 0))
        self.assertEqual(respuesta.estado, "PROGRAMADA")


# =========================================================
# Repositorio: verificar_disponibilidad_horario (CU10)
# =========================================================

class TestVerificarDisponibilidadHorario(unittest.TestCase):

    def _horario(self, inicio, fin):
        return SimpleNamespace(
            id=1,
            oftalmologo_id=1,
            dia_semana=3,
            hora_inicio=inicio,
            hora_fin=fin,
            estado=True,
        )

    def _bloqueo(self, inicio, fin):
        return SimpleNamespace(
            id=1,
            oftalmologo_id=1,
            fecha=fecha_futura(),
            hora_inicio=inicio,
            hora_fin=fin,
            estado=True,
        )

    def _cita(self, cita_id, inicio, fin):
        return SimpleNamespace(
            id=cita_id,
            oftalmologo_id=1,
            fecha=fecha_futura(),
            hora_inicio=inicio,
            hora_fin=fin,
            estado="CONFIRMADA",
        )

    def test_intervalo_dentro_del_horario_sin_conflictos(self):
        db = MagicMock()
        with patch.object(repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[self._horario(time(8, 0), time(12, 0))]), \
             patch.object(repo, "obtener_bloqueos_por_oftalmologo_y_fecha", return_value=[]), \
             patch.object(repo, "obtener_citas_por_oftalmologo_y_fecha", return_value=[]):
            disponible = repo.verificar_disponibilidad_horario(
                db,
                oftalmologo_id=1,
                fecha=fecha_futura(),
                hora_inicio=time(8, 0),
                hora_fin=time(9, 0),
            )
        self.assertTrue(disponible)

    def test_intervalo_fuera_del_horario_no_disponible(self):
        db = MagicMock()
        with patch.object(repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[self._horario(time(9, 0), time(12, 0))]):
            disponible = repo.verificar_disponibilidad_horario(
                db,
                oftalmologo_id=1,
                fecha=fecha_futura(),
                hora_inicio=time(8, 0),
                hora_fin=time(9, 0),
            )
        self.assertFalse(disponible)

    def test_intervalo_con_bloqueo_no_disponible(self):
        db = MagicMock()
        with patch.object(repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[self._horario(time(8, 0), time(12, 0))]), \
             patch.object(repo, "obtener_bloqueos_por_oftalmologo_y_fecha", return_value=[self._bloqueo(time(9, 0), time(11, 0))]):
            disponible = repo.verificar_disponibilidad_horario(
                db,
                oftalmologo_id=1,
                fecha=fecha_futura(),
                hora_inicio=time(9, 0),
                hora_fin=time(10, 0),
            )
        self.assertFalse(disponible)

    def test_intervalo_con_otra_cita_no_disponible(self):
        db = MagicMock()
        with patch.object(repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[self._horario(time(8, 0), time(12, 0))]), \
             patch.object(repo, "obtener_bloqueos_por_oftalmologo_y_fecha", return_value=[]), \
             patch.object(repo, "obtener_citas_por_oftalmologo_y_fecha", return_value=[self._cita(9, time(8, 0), time(9, 0))]):
            disponible = repo.verificar_disponibilidad_horario(
                db,
                oftalmologo_id=1,
                fecha=fecha_futura(),
                hora_inicio=time(8, 0),
                hora_fin=time(9, 0),
            )
        self.assertFalse(disponible)

    def test_intervalo_ignora_la_propia_cita_al_reprogramar(self):
        db = MagicMock()
        cita = self._cita(10, time(8, 0), time(9, 0))
        with patch.object(repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[self._horario(time(8, 0), time(12, 0))]), \
             patch.object(repo, "obtener_bloqueos_por_oftalmologo_y_fecha", return_value=[]), \
             patch.object(repo, "obtener_citas_por_oftalmologo_y_fecha", return_value=[cita]):
            disponible = repo.verificar_disponibilidad_horario(
                db,
                oftalmologo_id=1,
                fecha=fecha_futura(),
                hora_inicio=time(8, 0),
                hora_fin=time(9, 0),
                excluir_cita_id=10,
            )
        self.assertTrue(disponible)


if __name__ == "__main__":
    unittest.main()
