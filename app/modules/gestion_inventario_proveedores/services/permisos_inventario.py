"""Preparación y aplicación administrativa explícita del permiso CU30."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import hmac
import json
from typing import Callable

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.dependencies import ACCION_AMBAS, ACCION_LECTURA, _normalizar_accion
from app.core.tenancy.context import TenantContext
from app.core.tenancy.models import TenantDatabase
from app.core.tenancy.repository import TenantControlPlaneRepository
from app.core.tenancy.resolver import TenantResolver
from app.core.time import fecha_local_aplicacion
from app.modules.gestion_usuarios_seguridad.models.models import Accion, Bitacora, Funcion, Modulo, Rol, RolFuncion
from app.modules.gestion_usuarios_seguridad.repositories import repository as repo


NOMBRE_FUNCION = "Consultar inventario y alertas"
NOMBRE_MODULO = "Inventario y Proveedores"
NOMBRE_ROL = "Administrador"
VERSION_PLAN = "cu30-rbac-v1"


class HabilitacionCU30Error(RuntimeError):
    def __init__(self, codigo: str, mensaje: str, errores: tuple[dict, ...] = ()):
        super().__init__(mensaje)
        self.codigo = codigo
        self.errores = errores

    def to_dict(self) -> dict:
        return {"estado": "BLOQUEADO", "codigo": self.codigo, "mensaje": str(self), "errores": list(self.errores)}


def _huella(valor: dict) -> str:
    contenido = json.dumps(valor, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(contenido.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PlanEmpresa:
    contexto: TenantContext
    modulo_id: int
    rol_id: int
    accion_lectura_id: int
    funcion_id: int | None
    permiso_id: int | None
    accion_existente: str | None
    crear_funcion: bool
    asignar_lectura: bool
    huella_estado: str

    def to_dict(self) -> dict:
        return {
            "empresa": self.contexto.empresa_codigo,
            "empresa_id": self.contexto.empresa_id,
            "tenant_id": self.contexto.tenant_database_id,
            "modulo": NOMBRE_MODULO, "modulo_id": self.modulo_id,
            "rol": NOMBRE_ROL, "rol_id": self.rol_id,
            "accion_a_asignar": ACCION_LECTURA if self.asignar_lectura else None,
            "accion_lectura_id": self.accion_lectura_id,
            "accion_existente": self.accion_existente,
            "funcion": NOMBRE_FUNCION, "funcion_id": self.funcion_id,
            "permiso_id": self.permiso_id,
            "crear_funcion": self.crear_funcion,
            "asignar_lectura": self.asignar_lectura,
            "registrar_bitacora": self.crear_funcion or self.asignar_lectura,
        }


@dataclass(frozen=True)
class PlanHabilitacion:
    fecha: date
    empresas: tuple[PlanEmpresa, ...]
    huella: str

    def to_dict(self) -> dict:
        return {
            "modo": "PLAN_SOLO_LECTURA", "version": VERSION_PLAN,
            "fecha": self.fecha.isoformat(), "plan_hash": self.huella,
            "empresas": [empresa.to_dict() for empresa in self.empresas],
            "totales": {
                "empresas": len(self.empresas),
                "funciones_nuevas": sum(empresa.crear_funcion for empresa in self.empresas),
                "permisos_lectura_nuevos": sum(empresa.asignar_lectura for empresa in self.empresas),
                "auditorias_nuevas": sum(empresa.crear_funcion or empresa.asignar_lectura for empresa in self.empresas),
            },
            "requiere_aprobacion": True,
            "aplicacion": "--apply --confirm-plan " + self.huella,
        }


def _uno(elementos: list, codigo: str, descripcion: str):
    if len(elementos) != 1:
        raise HabilitacionCU30Error(codigo, f"{descripcion}: debe existir exactamente un registro.")
    return elementos[0]


def preparar_empresa(db: Session, contexto: TenantContext) -> PlanEmpresa:
    """Solo SELECT. Las ausencias o ambigüedades nunca se corrigen implícitamente."""
    with db.no_autoflush:
        if db.get_bind().dialect.name == "postgresql":
            if db.scalar(text("SELECT current_database()")) != contexto.database_name:
                raise HabilitacionCU30Error("BASE_NO_COINCIDE", "La conexión no corresponde a la base tenant registrada.")
        # También deben existir los destinos de escritura aunque CU30 aún no tenga función.
        db.execute(select(RolFuncion).limit(0)).all()
        db.execute(select(Bitacora).limit(0)).all()
        modulo = _uno(list(db.scalars(select(Modulo).where(
            func.lower(Modulo.nombre) == NOMBRE_MODULO.lower(),
        )).all()), "MODULO_INVALIDO", "Módulo de inventario")
        if modulo.nombre != NOMBRE_MODULO or not modulo.estado:
            raise HabilitacionCU30Error("MODULO_INVALIDO", "El módulo de inventario debe estar activo y tener el nombre exacto.")
        rol = _uno(list(db.scalars(select(Rol).where(
            func.lower(Rol.nombre) == NOMBRE_ROL.lower(),
        )).all()), "ROL_INVALIDO", "Rol Administrador")
        if rol.nombre != NOMBRE_ROL or not rol.estado or not rol.protegido:
            raise HabilitacionCU30Error("ROL_INVALIDO", "Se requiere el rol Administrador activo y protegido.")
        acciones = list(db.scalars(select(Accion)).all())
        lectura = _uno([accion for accion in acciones if _normalizar_accion(accion.nombre) == ACCION_LECTURA],
                       "ACCION_INVALIDA", "Acción LECTURA")
        if not lectura.estado:
            raise HabilitacionCU30Error("ACCION_INVALIDA", "La acción LECTURA debe estar activa.")
        funciones = list(db.scalars(select(Funcion).where(
            func.lower(Funcion.nombre) == NOMBRE_FUNCION.lower(),
        )).all())
        if len(funciones) > 1:
            raise HabilitacionCU30Error("FUNCION_AMBIGUA", "Hay más de una función para consultar inventario y alertas.")
        funcion = funciones[0] if funciones else None
        if funcion is not None and (
            funcion.nombre != NOMBRE_FUNCION or funcion.modulo_id != modulo.id or not funcion.estado
        ):
            raise HabilitacionCU30Error("FUNCION_INVALIDA", "La función existente tiene nombre, módulo o estado incompatible; requiere revisión explícita.")
        permisos = list(db.scalars(select(RolFuncion).where(
            RolFuncion.funcion_id == funcion.id,
        )).all()) if funcion is not None else []
        asignados = [permiso for permiso in permisos if permiso.rol_id == rol.id]
        if len(asignados) > 1:
            raise HabilitacionCU30Error("PERMISO_AMBIGUO", "Administrador tiene más de un permiso CU30.")
        permiso = asignados[0] if asignados else None
        if permiso is not None:
            accion_actual = next((accion for accion in acciones if accion.id == permiso.accion_id), None)
            if accion_actual is None or not accion_actual.estado or _normalizar_accion(accion_actual.nombre) not in {ACCION_LECTURA, ACCION_AMBAS}:
                raise HabilitacionCU30Error("PERMISO_INCOMPATIBLE", "El permiso existente no otorga lectura activa; requiere revisión explícita.")
        estado = {
            "contexto": asdict(contexto),
            "modulo": [modulo.id, modulo.nombre, modulo.estado],
            "rol": [rol.id, rol.nombre, rol.estado, rol.protegido],
            "acciones": sorted((accion.id, accion.nombre, accion.estado) for accion in acciones),
            "funcion": [funcion.id, funcion.nombre, funcion.modulo_id, funcion.estado] if funcion else None,
            "permisos_cu30": sorted((permiso.id, permiso.rol_id, permiso.accion_id) for permiso in permisos),
        }
        return PlanEmpresa(
            contexto=contexto, modulo_id=modulo.id, rol_id=rol.id, accion_lectura_id=lectura.id,
            funcion_id=funcion.id if funcion else None,
            permiso_id=permiso.id if permiso else None,
            accion_existente=accion_actual.nombre if permiso is not None else None,
            crear_funcion=funcion is None, asignar_lectura=permiso is None,
            huella_estado=_huella(estado),
        )


def _solo_lectura(db: Session) -> None:
    dialecto = db.get_bind().dialect.name
    if dialecto == "postgresql":
        db.execute(text("SET TRANSACTION READ ONLY"))
    elif dialecto != "sqlite":
        raise HabilitacionCU30Error("DIALECTO_NO_SOPORTADO", "Se requiere PostgreSQL o una prueba SQLite aislada.")


def _resolver_contextos(control_factory: Callable[[], Session], codigos: tuple[str, ...] | None,
                       expected_companies: int) -> tuple[TenantContext, ...]:
    if expected_companies < 1:
        raise HabilitacionCU30Error("SELECCION_INVALIDA", "La cantidad esperada de empresas debe ser positiva.")
    control = control_factory()
    try:
        _solo_lectura(control)
        repository = TenantControlPlaneRepository(control)
        empresas = repository.list_active_empresas()
        activos = [empresa.codigo for empresa in empresas]
        seleccionados = list(codigos) if codigos is not None else activos
        if (not seleccionados or len(seleccionados) != len(set(seleccionados))
                or len(activos) != len(set(activos))
                or any(codigo not in activos for codigo in seleccionados)
                or len(seleccionados) != expected_companies):
            raise HabilitacionCU30Error("SELECCION_INVALIDA", "La selección debe contener la cantidad esperada de empresas activas distintas.")
        resolver = TenantResolver(repository)
        contextos = []
        for codigo in sorted(seleccionados):
            contexto = resolver.resolver_por_codigo_empresa(codigo)
            resolver.require_active_database(contexto)
            if contexto.database_name == control.get_bind().engine.url.database:
                raise HabilitacionCU30Error("MAPEO_CONTROL_PLANE", "La base del Control Plane no puede ser un destino de esta operación.")
            mapeos = list(control.scalars(select(TenantDatabase).where(TenantDatabase.empresa_id == contexto.empresa_id)).all())
            if len(mapeos) != 1:
                raise HabilitacionCU30Error("MAPEO_AMBIGUO", "Cada empresa debe tener exactamente una base tenant.")
            compartidos = list(control.scalars(select(TenantDatabase).where(TenantDatabase.database_name == contexto.database_name)).all())
            if len(compartidos) != 1:
                raise HabilitacionCU30Error("MAPEO_COMPARTIDO", "La base tenant no puede estar asignada a otra empresa.")
            contextos.append(contexto)
        if len({contexto.database_name for contexto in contextos}) != len(contextos):
            raise HabilitacionCU30Error("MAPEO_COMPARTIDO", "Las empresas seleccionadas no pueden compartir una base tenant.")
        return tuple(contextos)
    except HabilitacionCU30Error:
        raise
    except Exception as error:
        raise HabilitacionCU30Error("CONTROL_PLANE_INVALIDO", "No fue posible validar todas las empresas, suscripciones y bases tenant.") from error
    finally:
        control.rollback()
        control.close()


def preparar_plan(control_factory, registry, *, codigos: tuple[str, ...] | None = None,
                  expected_companies: int = 7) -> PlanHabilitacion:
    contextos = _resolver_contextos(control_factory, codigos, expected_companies)
    planes, errores = [], []
    for contexto in contextos:
        db = None
        try:
            db = registry.get_session(contexto)
            _solo_lectura(db)
            planes.append(preparar_empresa(db, contexto))
        except Exception as error:
            errores.append({"empresa": contexto.empresa_codigo,
                            "codigo": error.codigo if isinstance(error, HabilitacionCU30Error) else "TENANT_NO_DISPONIBLE",
                            "mensaje": str(error) if isinstance(error, HabilitacionCU30Error) else "No se pudo inspeccionar el catálogo de permisos de esta empresa."})
        finally:
            if db is not None:
                db.rollback()
                db.close()
    if errores:
        raise HabilitacionCU30Error("PREFLIGHT_BLOQUEADO", "No se aplicará ningún cambio: falló la preparación de una o más empresas.", tuple(errores))
    fecha = fecha_local_aplicacion()
    huella = _huella({"version": VERSION_PLAN, "fecha": fecha.isoformat(),
                     "empresas": [{"contexto": asdict(plan.contexto), "estado": plan.huella_estado} for plan in planes]})
    return PlanHabilitacion(fecha=fecha, empresas=tuple(planes), huella=huella)


def aplicar_empresa(db: Session, plan: PlanEmpresa, *, plan_hash: str | None = None) -> bool:
    """El caller controla commit/rollback. No cambia permisos existentes ni otros roles."""
    actual = preparar_empresa(db, plan.contexto)
    if actual != plan:
        raise HabilitacionCU30Error("PLAN_CAMBIO", "El catálogo de permisos cambió después de preparar el plan.")
    if not plan.crear_funcion and not plan.asignar_lectura:
        return False
    funcion_id = plan.funcion_id
    if plan.crear_funcion:
        funcion_id = repo.crear_funcion(db, plan.modulo_id, NOMBRE_FUNCION,
                                       "CU30 - Consulta Web de inventario y alertas").id
    permiso_id = plan.permiso_id
    if plan.asignar_lectura:
        permiso_id = repo.asignar_permiso(db, plan.rol_id, funcion_id, plan.accion_lectura_id,
                                         "CU30 - Solo lectura de inventario y alertas").id
    repo.registrar_bitacora(
        db, usuario_id=None, accion="HABILITAR_CU30", entidad_afectada="rol_funcion",
        id_registro_afectado=permiso_id,
        descripcion=json.dumps({"empresa": plan.contexto.empresa_codigo, "funcion": NOMBRE_FUNCION,
                                "rol": NOMBRE_ROL, "accion": ACCION_LECTURA,
                                "crear_funcion": plan.crear_funcion, "asignar_lectura": plan.asignar_lectura,
                                "plan_hash": plan_hash}, ensure_ascii=False, sort_keys=True),
    )
    return True


def aplicar_plan(control_factory, registry, *, plan_hash: str,
                 codigos: tuple[str, ...] | None = None, expected_companies: int = 7) -> dict:
    plan = preparar_plan(control_factory, registry, codigos=codigos, expected_companies=expected_companies)
    if not hmac.compare_digest(plan.huella, plan_hash):
        raise HabilitacionCU30Error("PLAN_CAMBIO", "La huella confirmada no coincide con el plan actual; requiere nueva revisión.")
    aplicadas, sin_cambios = [], []
    for indice, empresa in enumerate(plan.empresas):
        db = None
        intento_commit = False
        try:
            actual = _resolver_contextos(control_factory, (empresa.contexto.empresa_codigo,), 1)[0]
            if actual != empresa.contexto:
                raise HabilitacionCU30Error("PLAN_CAMBIO", "El contexto de la empresa cambió después de preparar el plan.")
            db = registry.get_session(actual)
            with db.begin():
                if db.get_bind().dialect.name == "postgresql":
                    db.execute(text("SET LOCAL lock_timeout = '5s'"))
                    db.execute(text("SET LOCAL statement_timeout = '30s'"))
                    db.execute(text("LOCK TABLE public.modulo, public.rol, public.accion, public.funcion, public.rol_funcion IN SHARE ROW EXCLUSIVE MODE"))
                cambio = aplicar_empresa(db, empresa, plan_hash=plan.huella)
                intento_commit = True
            (aplicadas if cambio else sin_cambios).append(empresa.contexto.empresa_codigo)
        except Exception as error:
            if db is not None:
                try:
                    db.rollback()
                except Exception:
                    intento_commit = True
            return {"estado": "PARCIAL" if aplicadas else "FALLIDO", "plan_hash": plan.huella,
                    "aplicadas": aplicadas, "sin_cambios": sin_cambios,
                    "fallida": empresa.contexto.empresa_codigo,
                    "codigo": error.codigo if isinstance(error, HabilitacionCU30Error) else "APLICACION_FALLIDA",
                    "mensaje": str(error) if isinstance(error, HabilitacionCU30Error) else "No se pudo completar esta empresa; se detuvo el lote. Verifica su estado antes de reintentar.",
                    "estado_fallida": "REQUIERE_VERIFICACION" if intento_commit else "REVERTIDA",
                    "pendientes": [pendiente.contexto.empresa_codigo for pendiente in plan.empresas[indice + 1:]],
                    "atomicidad": "POR_EMPRESA"}
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass
    return {"estado": "COMPLETADO", "plan_hash": plan.huella, "aplicadas": aplicadas,
            "sin_cambios": sin_cambios, "fallida": None, "pendientes": [], "atomicidad": "POR_EMPRESA"}
