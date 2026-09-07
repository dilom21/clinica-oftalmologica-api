import unittest
from datetime import date, time, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app.core.dependencies import (
    ACCION_AMBAS,
    ACCION_ESCRITURA,
    ACCION_LECTURA,
    requerir_permiso,
)
from app.modules.gestion_agenda_citas.repositories import repository as repo
from app.modules.gestion_agenda_citas.services import configuracion as cfg
from app.modules.gestion_agenda_citas.services import service as service_mod
from app.modules.gestion_agenda_citas.schemas.schemas import (
    BloqueoHorarioCrear,
    HorarioOftalmologoCrear,
)

FUNCION_CU11 = "Configurar disponibilidad del oftalmólogo"


def usuario_falso(usuario_id=5, rol="Administrador", rol_id=1):
    return SimpleNamespace(
        id=usuario_id,
        rol_id=rol_id,
        rol=SimpleNamespace(nombre=rol),
        estado=True,
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


def horario_falso(
    horario_id=1,
    oftalmologo_id=1,
    dia_semana=1,
    inicio=time(8, 0),
    fin=time(12, 0),
    estado=True,
):
    return SimpleNamespace(
        id=horario_id,
        oftalmologo_id=oftalmologo_id,
        dia_semana=dia_semana,
        hora_inicio=inicio,
        hora_fin=fin,
        estado=estado,
    )


def bloqueo_falso(
    bloqueo_id=1,
    oftalmologo_id=1,
    fecha=None,
    inicio=time(10, 0),
    fin=time(11, 0),
    motivo="Mantenimiento",
    estado=True,
):
    return SimpleNamespace(
        id=bloqueo_id,
        oftalmologo_id=oftalmologo_id,
        fecha=fecha or proxima_fecha_con_dia(1),
        hora_inicio=inicio,
        hora_fin=fin,
        motivo=motivo,
        estado=estado,
    )


def cita_falsa(
    cita_id=1,
    fecha=None,
    inicio=time(10, 0),
    fin=time(10, 30),
    estado="CONFIRMADA",
):
    return SimpleNamespace(
        id=cita_id,
        paciente_id=1,
        oftalmologo_id=1,
        fecha=fecha or proximo_lunes(),
        hora_inicio=inicio,
        hora_fin=fin,
        estado=estado,
        motivo="Consulta",
    )


def datos_horario(dia=1, inicio=time(8, 0), fin=time(12, 0)):
    return SimpleNamespace(
        dia_semana=dia,
        hora_inicio=inicio,
        hora_fin=fin,
    )


def datos_bloqueo(fecha=None, inicio=time(10, 0), fin=time(11, 0), motivo="Motivo"):
    return SimpleNamespace(
        fecha=fecha or proxima_fecha_con_dia(1),
        hora_inicio=inicio,
        hora_fin=fin,
        motivo=motivo,
    )


def proximo_lunes():
    """Próximo lunes estrictamente futuro (determinista)."""
    hoy = date.today()
    delta = (0 - hoy.weekday()) % 7
    if delta == 0:
        delta = 7
    return hoy + timedelta(days=delta)


def proxima_fecha_con_dia(dia_semana: int):
    """Próxima fecha futura cuyo isoweekday sea `dia_semana`."""
    hoy = date.today()
    for dias in range(1, 15):
        fecha = hoy + timedelta(days=dias)
        if fecha.isoweekday() == dia_semana:
            return fecha
    return hoy + timedelta(days=1)


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


# =========================================================
# Permisos de función/acción (puntos 6, 7, 8)
# =========================================================

class TestPermisosFuncionAccionCU11(unittest.TestCase):

    def test_get_acepta_lectura(self):
        permiso = requerir_permiso(FUNCION_CU11, ACCION_LECTURA)
        usuario = usuario_falso()
        self.assertIs(
            permiso(usuario=usuario, db=db_con_accion(ACCION_LECTURA)),
            usuario,
        )

    def test_get_no_acepta_solo_escritura(self):
        permiso = requerir_permiso(FUNCION_CU11, ACCION_LECTURA)
        with self.assertRaises(HTTPException) as ctx:
            permiso(usuario=usuario_falso(), db=db_con_accion(ACCION_ESCRITURA))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_escritura_no_acepta_solo_lectura(self):
        permiso = requerir_permiso(FUNCION_CU11, ACCION_ESCRITURA)
        with self.assertRaises(HTTPException) as ctx:
            permiso(usuario=usuario_falso(), db=db_con_accion(ACCION_LECTURA))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_escritura_acepta_escritura(self):
        permiso = requerir_permiso(FUNCION_CU11, ACCION_ESCRITURA)
        usuario = usuario_falso()
        self.assertIs(
            permiso(usuario=usuario, db=db_con_accion(ACCION_ESCRITURA)),
            usuario,
        )

    def test_ambas_satisface_get_y_escritura(self):
        for accion_minima in (ACCION_LECTURA, ACCION_ESCRITURA):
            permiso = requerir_permiso(FUNCION_CU11, accion_minima)
            usuario = usuario_falso()
            self.assertIs(
                permiso(usuario=usuario, db=db_con_accion(ACCION_AMBAS)),
                usuario,
            )

    def test_sin_funcion_devuelve_403(self):
        permiso = requerir_permiso(FUNCION_CU11, ACCION_LECTURA)
        with self.assertRaises(HTTPException) as ctx:
            permiso(usuario=usuario_falso(), db=db_sin_permiso())
        self.assertEqual(ctx.exception.status_code, 403)


# =========================================================
# Autorización contextual por rol (puntos 1 a 5)
# =========================================================

class TestAutorizacionContextualCU11(unittest.TestCase):

    def _db(self):
        return MagicMock()

    def test_administrador_configura_cualquier_oftalmologo(self):
        db = self._db()
        administrador = usuario_falso(usuario_id=1, rol="Administrador")
        creado = horario_falso(horario_id=10)
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(cfg.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[]), \
             patch.object(cfg.repo, "crear_horario_oftalmologo", return_value=creado):
            resultado = cfg.crear_horario(
                db,
                oftalmologo_id=1,
                datos=datos_horario(),
                usuario=administrador,
            )
        self.assertEqual(resultado.id, 10)

    def test_oftalmologo_configura_su_propia_disponibilidad(self):
        db = self._db()
        oftalmologo = usuario_falso(usuario_id=7, rol="Oftalmólogo", rol_id=2)
        creado = horario_falso(horario_id=11)
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=7)), \
             patch.object(cfg.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[]), \
             patch.object(cfg.repo, "crear_horario_oftalmologo", return_value=creado):
            resultado = cfg.crear_horario(
                db,
                oftalmologo_id=1,
                datos=datos_horario(),
                usuario=oftalmologo,
            )
        self.assertEqual(resultado.id, 11)

    def test_oftalmologo_no_configura_otro_oftalmologo(self):
        db = self._db()
        oftalmologo = usuario_falso(usuario_id=7, rol="Oftalmólogo", rol_id=2)
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)):
            with self.assertRaises(HTTPException) as ctx:
                cfg.crear_horario(
                    db,
                    oftalmologo_id=1,
                    datos=datos_horario(),
                    usuario=oftalmologo,
                )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_recepcionista_sin_acceso(self):
        db = self._db()
        recepcionista = usuario_falso(usuario_id=8, rol="Recepcionista", rol_id=3)
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)):
            with self.assertRaises(HTTPException) as ctx:
                cfg.crear_horario(
                    db,
                    oftalmologo_id=1,
                    datos=datos_horario(),
                    usuario=recepcionista,
                )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_paciente_sin_acceso(self):
        db = self._db()
        paciente = usuario_falso(usuario_id=9, rol="Paciente", rol_id=4)
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)):
            with self.assertRaises(HTTPException) as ctx:
                cfg.crear_horario(
                    db,
                    oftalmologo_id=1,
                    datos=datos_horario(),
                    usuario=paciente,
                )
        self.assertEqual(ctx.exception.status_code, 403)


# =========================================================
# Validaciones de esquema (puntos 10 y 11)
# =========================================================

class TestValidacionesSchemaCU11(unittest.TestCase):

    def test_crear_horario_valido(self):
        modelo = HorarioOftalmologoCrear(
            dia_semana=1,
            hora_inicio=time(8, 0),
            hora_fin=time(12, 0),
        )
        self.assertEqual(modelo.dia_semana, 1)

    def test_hora_fin_igual_a_hora_inicio_invalida(self):
        with self.assertRaises(ValidationError):
            HorarioOftalmologoCrear(
                dia_semana=1,
                hora_inicio=time(8, 0),
                hora_fin=time(8, 0),
            )

    def test_hora_fin_menor_a_hora_inicio_invalida(self):
        with self.assertRaises(ValidationError):
            HorarioOftalmologoCrear(
                dia_semana=1,
                hora_inicio=time(12, 0),
                hora_fin=time(8, 0),
            )

    def test_dia_semana_invalido(self):
        for dia in (0, 8, -1):
            with self.assertRaises(ValidationError):
                HorarioOftalmologoCrear(
                    dia_semana=dia,
                    hora_inicio=time(8, 0),
                    hora_fin=time(12, 0),
                )

    def test_dia_semana_extremos_validos(self):
        for dia in (1, 7):
            modelo = HorarioOftalmologoCrear(
                dia_semana=dia,
                hora_inicio=time(8, 0),
                hora_fin=time(12, 0),
            )
            self.assertEqual(modelo.dia_semana, dia)

    def test_bloqueo_hora_invertida_invalida(self):
        with self.assertRaises(ValidationError):
            BloqueoHorarioCrear(
                fecha=proxima_fecha_con_dia(1),
                hora_inicio=time(12, 0),
                hora_fin=time(8, 0),
            )


# =========================================================
# Horarios semanales (puntos 9, 12 a 17)
# =========================================================

class TestHorariosCU11(unittest.TestCase):

    def _db(self):
        return MagicMock()

    def test_crear_horario_valido(self):
        db = self._db()
        administrador = usuario_falso(usuario_id=1, rol="Administrador")
        creado = horario_falso(horario_id=20)
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(cfg.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[]), \
             patch.object(cfg.repo, "crear_horario_oftalmologo", return_value=creado) as m_crear:
            resultado = cfg.crear_horario(
                db,
                oftalmologo_id=1,
                datos=datos_horario(),
                usuario=administrador,
            )
        self.assertEqual(resultado.id, 20)
        m_crear.assert_called_once()
        db.commit.assert_called()

    def test_crear_horario_solapado_rechazado(self):
        db = self._db()
        administrador = usuario_falso(usuario_id=1, rol="Administrador")
        existente = horario_falso(horario_id=1, dia_semana=1, inicio=time(8, 0), fin=time(10, 0))
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(cfg.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[existente]):
            with self.assertRaises(HTTPException) as ctx:
                cfg.crear_horario(
                    db,
                    oftalmologo_id=1,
                    datos=datos_horario(dia=1, inicio=time(9, 0), fin=time(11, 0)),
                    usuario=administrador,
                )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_crear_horario_duplicado_rechazado(self):
        db = self._db()
        administrador = usuario_falso(usuario_id=1, rol="Administrador")
        existente = horario_falso(horario_id=1, dia_semana=1, inicio=time(8, 0), fin=time(12, 0))
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(cfg.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[existente]):
            with self.assertRaises(HTTPException) as ctx:
                cfg.crear_horario(
                    db,
                    oftalmologo_id=1,
                    datos=datos_horario(dia=1, inicio=time(8, 0), fin=time(12, 0)),
                    usuario=administrador,
                )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_crear_horario_contiguo_permitido(self):
        db = self._db()
        administrador = usuario_falso(usuario_id=1, rol="Administrador")
        existente = horario_falso(horario_id=1, dia_semana=1, inicio=time(8, 0), fin=time(9, 0))
        creado = horario_falso(horario_id=21, dia_semana=1, inicio=time(9, 0), fin=time(10, 0))
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(cfg.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[existente]), \
             patch.object(cfg.repo, "crear_horario_oftalmologo", return_value=creado):
            resultado = cfg.crear_horario(
                db,
                oftalmologo_id=1,
                datos=datos_horario(dia=1, inicio=time(9, 0), fin=time(10, 0)),
                usuario=administrador,
            )
        self.assertEqual(resultado.id, 21)

    def test_actualizar_horario_valido(self):
        db = self._db()
        administrador = usuario_falso(usuario_id=1, rol="Administrador")
        actual = horario_falso(horario_id=1, dia_semana=1, inicio=time(8, 0), fin=time(12, 0), estado=True)
        actualizado = horario_falso(horario_id=1, dia_semana=1, inicio=time(7, 0), fin=time(13, 0), estado=True)
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(cfg.repo, "obtener_horario_por_id", return_value=actual), \
             patch.object(cfg.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[actual]), \
             patch.object(cfg.repo, "obtener_horarios_activos_por_oftalmologo", return_value=[actual]), \
             patch.object(cfg.repo, "obtener_citas_futuras_por_oftalmologo", return_value=[]), \
             patch.object(cfg.repo, "actualizar_horario_oftalmologo", return_value=actualizado) as m_upd:
            resultado = cfg.actualizar_horario(
                db,
                oftalmologo_id=1,
                horario_id=1,
                datos=datos_horario(dia=1, inicio=time(7, 0), fin=time(13, 0)),
                usuario=administrador,
            )
        self.assertEqual(resultado.id, 1)
        m_upd.assert_called_once()
        db.commit.assert_called()

    def test_cambio_horario_que_deja_cita_futura_fuera_rechazado(self):
        db = self._db()
        administrador = usuario_falso(usuario_id=1, rol="Administrador")
        fecha_lunes = proximo_lunes()
        actual = horario_falso(horario_id=1, dia_semana=1, inicio=time(8, 0), fin=time(12, 0), estado=True)
        cita = cita_falsa(cita_id=1, fecha=fecha_lunes, inicio=time(10, 0), fin=time(10, 30), estado="CONFIRMADA")
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(cfg.repo, "obtener_horario_por_id", return_value=actual), \
             patch.object(cfg.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[actual]), \
             patch.object(cfg.repo, "obtener_horarios_activos_por_oftalmologo", return_value=[actual]), \
             patch.object(cfg.repo, "obtener_citas_futuras_por_oftalmologo", return_value=[cita]):
            with self.assertRaises(HTTPException) as ctx:
                cfg.actualizar_horario(
                    db,
                    oftalmologo_id=1,
                    horario_id=1,
                    datos=datos_horario(dia=1, inicio=time(8, 0), fin=time(9, 0)),
                    usuario=administrador,
                )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_cambio_de_dia_que_deja_cita_futura_fuera_rechazado(self):
        db = self._db()
        administrador = usuario_falso(usuario_id=1, rol="Administrador")
        actual = horario_falso(horario_id=1, dia_semana=1, inicio=time(8, 0), fin=time(12, 0), estado=True)
        cita_lunes = cita_falsa(cita_id=1, fecha=proximo_lunes(), inicio=time(10, 0), fin=time(10, 30))
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(cfg.repo, "obtener_horario_por_id", return_value=actual), \
             patch.object(cfg.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[]), \
             patch.object(cfg.repo, "obtener_horarios_activos_por_oftalmologo", return_value=[actual]), \
             patch.object(cfg.repo, "obtener_citas_futuras_por_oftalmologo", return_value=[cita_lunes]):
            with self.assertRaises(HTTPException) as ctx:
                cfg.actualizar_horario(
                    db,
                    oftalmologo_id=1,
                    horario_id=1,
                    datos=datos_horario(dia=2, inicio=time(8, 0), fin=time(12, 0)),
                    usuario=administrador,
                )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_desactivar_horario_sin_citas_futuras(self):
        db = self._db()
        administrador = usuario_falso(usuario_id=1, rol="Administrador")
        actual = horario_falso(horario_id=1, dia_semana=1, inicio=time(8, 0), fin=time(12, 0), estado=True)
        desactivado = horario_falso(horario_id=1, dia_semana=1, inicio=time(8, 0), fin=time(12, 0), estado=False)
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(cfg.repo, "obtener_horario_por_id", return_value=actual), \
             patch.object(cfg.repo, "obtener_horarios_activos_por_oftalmologo", return_value=[actual]), \
             patch.object(cfg.repo, "obtener_citas_futuras_por_oftalmologo", return_value=[]), \
             patch.object(cfg.repo, "cambiar_estado_horario_oftalmologo", return_value=desactivado) as m_estado:
            resultado = cfg.cambiar_estado_horario(
                db,
                oftalmologo_id=1,
                horario_id=1,
                estado=False,
                usuario=administrador,
            )
        self.assertFalse(resultado.estado)
        m_estado.assert_called_once()
        db.commit.assert_called()

    def test_desactivar_horario_con_cita_futura_rechazado(self):
        db = self._db()
        administrador = usuario_falso(usuario_id=1, rol="Administrador")
        actual = horario_falso(horario_id=1, dia_semana=1, inicio=time(8, 0), fin=time(12, 0), estado=True)
        cita_lunes = cita_falsa(cita_id=1, fecha=proximo_lunes(), inicio=time(10, 0), fin=time(10, 30))
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(cfg.repo, "obtener_horario_por_id", return_value=actual), \
             patch.object(cfg.repo, "obtener_horarios_activos_por_oftalmologo", return_value=[actual]), \
             patch.object(cfg.repo, "obtener_citas_futuras_por_oftalmologo", return_value=[cita_lunes]):
            with self.assertRaises(HTTPException) as ctx:
                cfg.cambiar_estado_horario(
                    db,
                    oftalmologo_id=1,
                    horario_id=1,
                    estado=False,
                    usuario=administrador,
                )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_reactivar_horario_duplicado_rechazado(self):
        db = self._db()
        administrador = usuario_falso(usuario_id=1, rol="Administrador")
        inactivo = horario_falso(horario_id=1, dia_semana=1, inicio=time(8, 0), fin=time(12, 0), estado=False)
        activo_duplicado = horario_falso(horario_id=2, dia_semana=1, inicio=time(8, 0), fin=time(12, 0), estado=True)
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(cfg.repo, "obtener_horario_por_id", return_value=inactivo), \
             patch.object(cfg.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[activo_duplicado]):
            with self.assertRaises(HTTPException) as ctx:
                cfg.cambiar_estado_horario(
                    db,
                    oftalmologo_id=1,
                    horario_id=1,
                    estado=True,
                    usuario=administrador,
                )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_reactivar_horario_valido(self):
        db = self._db()
        administrador = usuario_falso(usuario_id=1, rol="Administrador")
        inactivo = horario_falso(horario_id=1, dia_semana=1, inicio=time(8, 0), fin=time(12, 0), estado=False)
        activado = horario_falso(horario_id=1, dia_semana=1, inicio=time(8, 0), fin=time(12, 0), estado=True)
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(cfg.repo, "obtener_horario_por_id", return_value=inactivo), \
             patch.object(cfg.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[]), \
             patch.object(cfg.repo, "cambiar_estado_horario_oftalmologo", return_value=activado):
            resultado = cfg.cambiar_estado_horario(
                db,
                oftalmologo_id=1,
                horario_id=1,
                estado=True,
                usuario=administrador,
            )
        self.assertTrue(resultado.estado)


# =========================================================
# Bloqueos por fecha (puntos 18 a 26)
# =========================================================

class TestBloqueosCU11(unittest.TestCase):

    def _db(self):
        return MagicMock()

    def test_crear_bloqueo_valido(self):
        db = self._db()
        administrador = usuario_falso(usuario_id=1, rol="Administrador")
        fecha = proxima_fecha_con_dia(1)
        horario_activo = horario_falso(dia_semana=1, inicio=time(8, 0), fin=time(12, 0))
        creado = bloqueo_falso(bloqueo_id=30, fecha=fecha, inicio=time(10, 0), fin=time(11, 0))
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(cfg.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[horario_activo]), \
             patch.object(cfg.repo, "obtener_bloqueos_por_oftalmologo_y_fecha", return_value=[]), \
             patch.object(cfg.repo, "obtener_citas_por_oftalmologo_y_fecha", return_value=[]), \
             patch.object(cfg.repo, "crear_bloqueo_horario", return_value=creado) as m_crear:
            resultado = cfg.crear_bloqueo(
                db,
                oftalmologo_id=1,
                datos=datos_bloqueo(fecha=fecha),
                usuario=administrador,
            )
        self.assertEqual(resultado.id, 30)
        m_crear.assert_called_once()
        db.commit.assert_called()

    def test_crear_bloqueo_en_fecha_pasada_rechazado(self):
        db = self._db()
        administrador = usuario_falso(usuario_id=1, rol="Administrador")
        fecha_pasada = date.today() - timedelta(days=1)
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)):
            with self.assertRaises(HTTPException) as ctx:
                cfg.crear_bloqueo(
                    db,
                    oftalmologo_id=1,
                    datos=datos_bloqueo(fecha=fecha_pasada),
                    usuario=administrador,
                )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_crear_bloqueo_sin_horario_activo_rechazado(self):
        db = self._db()
        administrador = usuario_falso(usuario_id=1, rol="Administrador")
        fecha = proxima_fecha_con_dia(1)
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(cfg.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[]):
            with self.assertRaises(HTTPException) as ctx:
                cfg.crear_bloqueo(
                    db,
                    oftalmologo_id=1,
                    datos=datos_bloqueo(fecha=fecha),
                    usuario=administrador,
                )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_crear_bloqueo_solapado_con_bloqueo_rechazado(self):
        db = self._db()
        administrador = usuario_falso(usuario_id=1, rol="Administrador")
        fecha = proxima_fecha_con_dia(1)
        horario_activo = horario_falso(dia_semana=1, inicio=time(8, 0), fin=time(12, 0))
        bloqueo_existente = bloqueo_falso(bloqueo_id=1, fecha=fecha, inicio=time(10, 0), fin=time(11, 0))
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(cfg.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[horario_activo]), \
             patch.object(cfg.repo, "obtener_bloqueos_por_oftalmologo_y_fecha", return_value=[bloqueo_existente]):
            with self.assertRaises(HTTPException) as ctx:
                cfg.crear_bloqueo(
                    db,
                    oftalmologo_id=1,
                    datos=datos_bloqueo(fecha=fecha, inicio=time(10, 30), fin=time(11, 30)),
                    usuario=administrador,
                )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_crear_bloqueo_duplicado_rechazado(self):
        db = self._db()
        administrador = usuario_falso(usuario_id=1, rol="Administrador")
        fecha = proxima_fecha_con_dia(1)
        horario_activo = horario_falso(dia_semana=1, inicio=time(8, 0), fin=time(12, 0))
        bloqueo_existente = bloqueo_falso(bloqueo_id=1, fecha=fecha, inicio=time(10, 0), fin=time(11, 0))
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(cfg.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[horario_activo]), \
             patch.object(cfg.repo, "obtener_bloqueos_por_oftalmologo_y_fecha", return_value=[bloqueo_existente]):
            with self.assertRaises(HTTPException) as ctx:
                cfg.crear_bloqueo(
                    db,
                    oftalmologo_id=1,
                    datos=datos_bloqueo(fecha=fecha, inicio=time(10, 0), fin=time(11, 0)),
                    usuario=administrador,
                )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_crear_bloqueo_solapado_con_cita_rechazado(self):
        db = self._db()
        administrador = usuario_falso(usuario_id=1, rol="Administrador")
        fecha = proxima_fecha_con_dia(1)
        horario_activo = horario_falso(dia_semana=1, inicio=time(8, 0), fin=time(12, 0))
        cita = cita_falsa(cita_id=1, fecha=fecha, inicio=time(9, 30), fin=time(10, 0), estado="CONFIRMADA")
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(cfg.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[horario_activo]), \
             patch.object(cfg.repo, "obtener_bloqueos_por_oftalmologo_y_fecha", return_value=[]), \
             patch.object(cfg.repo, "obtener_citas_por_oftalmologo_y_fecha", return_value=[cita]):
            with self.assertRaises(HTTPException) as ctx:
                cfg.crear_bloqueo(
                    db,
                    oftalmologo_id=1,
                    datos=datos_bloqueo(fecha=fecha, inicio=time(9, 45), fin=time(11, 0)),
                    usuario=administrador,
                )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_cita_cancelada_no_impide_bloqueo(self):
        db = self._db()
        administrador = usuario_falso(usuario_id=1, rol="Administrador")
        fecha = proxima_fecha_con_dia(1)
        horario_activo = horario_falso(dia_semana=1, inicio=time(8, 0), fin=time(12, 0))
        creado = bloqueo_falso(bloqueo_id=31, fecha=fecha, inicio=time(9, 45), fin=time(11, 0))
        cita_cancelada = cita_falsa(cita_id=1, fecha=fecha, inicio=time(9, 30), fin=time(10, 0), estado="CANCELADA")
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(cfg.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[horario_activo]), \
             patch.object(cfg.repo, "obtener_bloqueos_por_oftalmologo_y_fecha", return_value=[]), \
             patch.object(cfg.repo, "obtener_citas_por_oftalmologo_y_fecha", return_value=[cita_cancelada]) as m_citas, \
             patch.object(cfg.repo, "crear_bloqueo_horario", return_value=creado):
            resultado = cfg.crear_bloqueo(
                db,
                oftalmologo_id=1,
                datos=datos_bloqueo(fecha=fecha, inicio=time(9, 45), fin=time(11, 0)),
                usuario=administrador,
            )
        self.assertEqual(resultado.id, 31)
        m_citas.assert_called_once()

    def test_actualizar_bloqueo_valido(self):
        db = self._db()
        administrador = usuario_falso(usuario_id=1, rol="Administrador")
        fecha = proxima_fecha_con_dia(1)
        actual = bloqueo_falso(bloqueo_id=1, fecha=fecha, inicio=time(9, 0), fin=time(10, 0), estado=True)
        actualizado = bloqueo_falso(bloqueo_id=1, fecha=fecha, inicio=time(10, 0), fin=time(11, 0), estado=True)
        horario_activo = horario_falso(dia_semana=1, inicio=time(8, 0), fin=time(12, 0))
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(cfg.repo, "obtener_bloqueo_por_id", return_value=actual), \
             patch.object(cfg.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[horario_activo]), \
             patch.object(cfg.repo, "obtener_bloqueos_por_oftalmologo_y_fecha", return_value=[actual]), \
             patch.object(cfg.repo, "obtener_citas_por_oftalmologo_y_fecha", return_value=[]), \
             patch.object(cfg.repo, "actualizar_bloqueo_horario", return_value=actualizado) as m_upd:
            resultado = cfg.actualizar_bloqueo(
                db,
                oftalmologo_id=1,
                bloqueo_id=1,
                datos=datos_bloqueo(fecha=fecha, inicio=time(10, 0), fin=time(11, 0)),
                usuario=administrador,
            )
        self.assertEqual(resultado.id, 1)
        m_upd.assert_called_once()
        db.commit.assert_called()

    def test_actualizar_bloqueo_a_fecha_pasada_rechazado(self):
        db = self._db()
        administrador = usuario_falso(usuario_id=1, rol="Administrador")
        fecha = proxima_fecha_con_dia(1)
        actual = bloqueo_falso(bloqueo_id=1, fecha=fecha, inicio=time(9, 0), fin=time(10, 0), estado=True)
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(cfg.repo, "obtener_bloqueo_por_id", return_value=actual):
            with self.assertRaises(HTTPException) as ctx:
                cfg.actualizar_bloqueo(
                    db,
                    oftalmologo_id=1,
                    bloqueo_id=1,
                    datos=datos_bloqueo(fecha=date.today() - timedelta(days=1)),
                    usuario=administrador,
                )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_desactivar_bloqueo_valido(self):
        db = self._db()
        administrador = usuario_falso(usuario_id=1, rol="Administrador")
        actual = bloqueo_falso(bloqueo_id=1, estado=True)
        desactivado = bloqueo_falso(bloqueo_id=1, estado=False)
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(cfg.repo, "obtener_bloqueo_por_id", return_value=actual), \
             patch.object(cfg.repo, "cambiar_estado_bloqueo_horario", return_value=desactivado) as m_estado:
            resultado = cfg.cambiar_estado_bloqueo(
                db,
                oftalmologo_id=1,
                bloqueo_id=1,
                estado=False,
                usuario=administrador,
            )
        self.assertFalse(resultado.estado)
        m_estado.assert_called_once()

    def test_reactivar_bloqueo_con_conflicto_rechazado(self):
        db = self._db()
        administrador = usuario_falso(usuario_id=1, rol="Administrador")
        fecha = proxima_fecha_con_dia(1)
        inactivo = bloqueo_falso(bloqueo_id=1, fecha=fecha, inicio=time(9, 45), fin=time(11, 0), estado=False)
        horario_activo = horario_falso(dia_semana=1, inicio=time(8, 0), fin=time(12, 0))
        cita = cita_falsa(cita_id=1, fecha=fecha, inicio=time(10, 0), fin=time(10, 30), estado="CONFIRMADA")
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(cfg.repo, "obtener_bloqueo_por_id", return_value=inactivo), \
             patch.object(cfg.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[horario_activo]), \
             patch.object(cfg.repo, "obtener_bloqueos_por_oftalmologo_y_fecha", return_value=[]), \
             patch.object(cfg.repo, "obtener_citas_por_oftalmologo_y_fecha", return_value=[cita]):
            with self.assertRaises(HTTPException) as ctx:
                cfg.cambiar_estado_bloqueo(
                    db,
                    oftalmologo_id=1,
                    bloqueo_id=1,
                    estado=True,
                    usuario=administrador,
                )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_reactivar_bloqueo_valido(self):
        db = self._db()
        administrador = usuario_falso(usuario_id=1, rol="Administrador")
        fecha = proxima_fecha_con_dia(1)
        inactivo = bloqueo_falso(bloqueo_id=1, fecha=fecha, inicio=time(10, 0), fin=time(11, 0), estado=False)
        activado = bloqueo_falso(bloqueo_id=1, fecha=fecha, inicio=time(10, 0), fin=time(11, 0), estado=True)
        horario_activo = horario_falso(dia_semana=1, inicio=time(8, 0), fin=time(12, 0))
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(cfg.repo, "obtener_bloqueo_por_id", return_value=inactivo), \
             patch.object(cfg.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[horario_activo]), \
             patch.object(cfg.repo, "obtener_bloqueos_por_oftalmologo_y_fecha", return_value=[]), \
             patch.object(cfg.repo, "obtener_citas_por_oftalmologo_y_fecha", return_value=[]), \
             patch.object(cfg.repo, "cambiar_estado_bloqueo_horario", return_value=activado):
            resultado = cfg.cambiar_estado_bloqueo(
                db,
                oftalmologo_id=1,
                bloqueo_id=1,
                estado=True,
                usuario=administrador,
            )
        self.assertTrue(resultado.estado)


# =========================================================
# Consultas de configuración
# =========================================================

class TestConsultasConfiguracionCU11(unittest.TestCase):

    def test_consultar_configuracion_incluye_horarios_y_bloqueos(self):
        db = MagicMock()
        administrador = usuario_falso(usuario_id=1, rol="Administrador")
        horarios = [
            horario_falso(horario_id=1, estado=True),
            horario_falso(horario_id=2, estado=False),
        ]
        bloqueos = [bloqueo_falso(bloqueo_id=1)]
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(cfg.repo, "obtener_horarios_por_oftalmologo", return_value=horarios), \
             patch.object(cfg.repo, "obtener_bloqueos_por_oftalmologo", return_value=bloqueos):
            resultado = cfg.consultar_configuracion(
                db,
                oftalmologo_id=1,
                usuario=administrador,
            )
        self.assertEqual(resultado.oftalmologo.id, 1)
        self.assertEqual(len(resultado.horarios), 2)
        self.assertEqual({h.estado for h in resultado.horarios}, {True, False})
        self.assertEqual(len(resultado.bloqueos), 1)

    def test_consultar_configuracion_oftalmologo_ajeno_403(self):
        db = MagicMock()
        oftalmologo = usuario_falso(usuario_id=7, rol="Oftalmólogo", rol_id=2)
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)):
            with self.assertRaises(HTTPException) as ctx:
                cfg.consultar_configuracion(
                    db,
                    oftalmologo_id=1,
                    usuario=oftalmologo,
                )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_consultar_configuracion_oftalmologo_inexistente_404(self):
        db = MagicMock()
        administrador = usuario_falso(usuario_id=1, rol="Administrador")
        with patch.object(cfg.repo, "obtener_oftalmologo_activo_por_id", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                cfg.consultar_configuracion(
                    db,
                    oftalmologo_id=999,
                    usuario=administrador,
                )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_listar_oftalmologos_configuracion_pasa_usuario(self):
        with patch.object(cfg, "listar_oftalmologos_activos", return_value=[oftalmologo_falso()]) as m_lista:
            resultado = cfg.listar_oftalmologos_configuracion(
                MagicMock(),
                usuario=usuario_falso(usuario_id=1, rol="Administrador"),
            )
        m_lista.assert_called_once()
        self.assertEqual(len(resultado), 1)


# =========================================================
# Regresión CU09 (puntos 27 a 29)
# =========================================================

class TestRegresionCU09(unittest.TestCase):

    def test_cu09_consulta_disponibilidad_sigue_funcionando(self):
        db = MagicMock()
        horario = SimpleNamespace(
            id=1, oftalmologo_id=1, dia_semana=4,
            hora_inicio=time(8, 0), hora_fin=time(12, 0), estado=True,
        )
        with patch.object(service_mod.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(service_mod.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[horario]), \
             patch.object(service_mod.repo, "obtener_bloqueos_por_oftalmologo_y_fecha", return_value=[]), \
             patch.object(service_mod.repo, "obtener_citas_por_oftalmologo_y_fecha", return_value=[]):
            resultado = service_mod.consultar_disponibilidad(
                db,
                oftalmologo_id=1,
                fecha=proxima_fecha_con_dia(4),
            )
        self.assertTrue(resultado.tiene_horario)
        self.assertEqual(len(resultado.intervalos_disponibles), 1)

    def test_cu09_descuenta_bloqueo_de_cu11(self):
        db = MagicMock()
        horario = SimpleNamespace(
            id=1, oftalmologo_id=1, dia_semana=4,
            hora_inicio=time(8, 0), hora_fin=time(12, 0), estado=True,
        )
        bloqueo = SimpleNamespace(
            id=1, oftalmologo_id=1,
            fecha=proxima_fecha_con_dia(4),
            hora_inicio=time(9, 0), hora_fin=time(10, 0),
            estado=True,
        )
        with patch.object(service_mod.repo, "obtener_oftalmologo_activo_por_id", return_value=oftalmologo_falso(usuario_id=2)), \
             patch.object(service_mod.repo, "obtener_horarios_por_oftalmologo_y_dia", return_value=[horario]), \
             patch.object(service_mod.repo, "obtener_bloqueos_por_oftalmologo_y_fecha", return_value=[bloqueo]), \
             patch.object(service_mod.repo, "obtener_citas_por_oftalmologo_y_fecha", return_value=[]):
            resultado = service_mod.consultar_disponibilidad(
                db,
                oftalmologo_id=1,
                fecha=proxima_fecha_con_dia(4),
            )
        intervalos = [
            (i.hora_inicio, i.hora_fin)
            for i in resultado.intervalos_disponibles
        ]
        self.assertEqual(
            intervalos,
            [
                (time(8, 0), time(9, 0)),
                (time(10, 0), time(12, 0)),
            ],
        )

    def test_repo_citas_futuras_excluye_canceladas(self):
        db = MagicMock()
        capturado = {}

        def _scalars(stmt, *a, **k):
            capturado["stmt"] = stmt
            resultado = MagicMock()
            resultado.all.return_value = []
            return resultado

        db.scalars.side_effect = _scalars
        repo.obtener_citas_futuras_por_oftalmologo(
            db,
            oftalmologo_id=1,
            desde=date.today(),
        )
        sql = str(
            capturado["stmt"].compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        self.assertIn("CANCELADA", sql)
        self.assertIn("NOT IN", sql)


if __name__ == "__main__":
    unittest.main()
