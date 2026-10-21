from dataclasses import dataclass
from typing import Any

from sqlalchemy import asc, desc

from app.modules.gestion_pacientes.models.models import Paciente
from app.modules.gestion_agenda_citas.models.models import Cita, Oftalmologo
from app.modules.gestion_historial_clinico.models.models import ConsultaClinica, Diagnostico, HistorialClinico
from app.modules.gestion_usuarios_seguridad.models.models import Usuario, Rol


@dataclass(frozen=True)
class Campo:
    expr: Any
    label: str
    tipo: str
    operadores: tuple[str, ...]


def campos(model, names, tipo_map=None, labels=None):
    tipo_map, labels = tipo_map or {}, labels or {}
    result = {}
    for name in names:
        expr = getattr(model, name)
        tipo = tipo_map.get(name, "texto")
        ops = {"texto": ("eq", "contains", "starts_with"), "numero": ("eq", "gt", "gte", "lt", "lte"), "fecha": ("eq", "gte", "lte", "between"), "enum": ("eq", "in"), "booleano": ("eq",)}[tipo]
        result[name] = Campo(expr, labels.get(name, name.replace("_", " ").title()), tipo, ops)
    return result


DATASETS = {
    "pacientes": {"label": "Pacientes", "model": Paciente, "joins": (), "fields": campos(Paciente, ("id", "nombres", "apellidos", "ci", "sexo", "fecha_nacimiento", "telefono", "fecha_registro", "estado"), {"id": "numero", "fecha_nacimiento": "fecha", "fecha_registro": "fecha", "estado": "booleano"}, {"id": "paciente_id"})},
    "citas": {"label": "Citas", "model": Cita, "joins": ("paciente", "oftalmologo"), "fields": {**campos(Cita, ("id", "fecha", "hora_inicio", "hora_fin", "motivo", "estado", "canal", "fecha_registro"), {"id": "numero", "fecha": "fecha", "hora_inicio": "fecha", "hora_fin": "fecha", "fecha_registro": "fecha", "estado": "enum"}, {"id": "cita_id"}), "paciente": Campo(Paciente.nombres, "Paciente", "texto", ("eq", "contains", "starts_with")), "oftalmologo": Campo(Oftalmologo.nombres, "Oftalmólogo", "texto", ("eq", "contains", "starts_with"))}},
    "consultas_clinicas": {"label": "Consultas clínicas", "model": ConsultaClinica, "joins": ("historial", "oftalmologo"), "fields": {**campos(ConsultaClinica, ("id", "fecha_consulta", "motivo_consulta", "estado", "cita_id"), {"id": "numero", "fecha_consulta": "fecha", "estado": "booleano", "cita_id": "numero"}, {"id": "consulta_id"}), "paciente": Campo(Paciente.nombres, "Paciente", "texto", ("eq", "contains", "starts_with")), "oftalmologo": Campo(Oftalmologo.nombres, "Oftalmólogo", "texto", ("eq", "contains", "starts_with"))}},
    "diagnosticos": {"label": "Diagnósticos", "model": Diagnostico, "joins": ("consulta", "historial", "oftalmologo"), "fields": {**campos(Diagnostico, ("id", "consulta_clinica_id", "fecha_diagnostico", "nombre", "descripcion", "estado"), {"id": "numero", "consulta_clinica_id": "numero", "fecha_diagnostico": "fecha", "estado": "booleano"}, {"id": "diagnostico_id", "consulta_clinica_id": "consulta_id"}), "paciente": Campo(Paciente.nombres, "Paciente", "texto", ("eq", "contains", "starts_with")), "oftalmologo": Campo(Oftalmologo.nombres, "Oftalmólogo", "texto", ("eq", "contains", "starts_with"))}},
    "usuarios": {"label": "Usuarios", "model": Usuario, "joins": ("rol",), "fields": {**campos(Usuario, ("id", "correo", "estado", "fecha_creacion"), {"id": "numero", "estado": "booleano", "fecha_creacion": "fecha"}, {"id": "usuario_id"}), "rol": Campo(Rol.nombre, "Rol", "texto", ("eq", "contains", "starts_with"))}},
}

STATIC_REPORTS = {
    "pacientes_activos": ("pacientes", ("id", "nombres", "apellidos", "ci", "telefono", "fecha_registro", "estado"), "Pacientes activos"),
    "citas_por_fecha": ("citas", ("id", "fecha", "hora_inicio", "hora_fin", "paciente", "oftalmologo", "motivo", "estado"), "Citas por fecha"),
    "consultas_clinicas": ("consultas_clinicas", ("id", "fecha_consulta", "paciente", "oftalmologo", "motivo_consulta", "estado", "cita_id"), "Consultas clínicas"),
    "diagnosticos_registrados": ("diagnosticos", ("id", "consulta_clinica_id", "fecha_diagnostico", "paciente", "oftalmologo", "nombre", "descripcion", "estado"), "Diagnósticos registrados"),
    "usuarios_por_rol": ("usuarios", ("id", "correo", "rol", "estado", "fecha_creacion"), "Usuarios por rol"),
}
