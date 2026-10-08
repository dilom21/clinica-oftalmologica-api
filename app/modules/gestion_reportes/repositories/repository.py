from sqlalchemy import select, func

from app.modules.gestion_agenda_citas.models.models import Cita, Oftalmologo
from app.modules.gestion_historial_clinico.models.models import ConsultaClinica, Diagnostico, HistorialClinico
from app.modules.gestion_pacientes.models.models import Paciente
from app.modules.gestion_usuarios_seguridad.models.models import Usuario, Rol
from app.modules.gestion_reportes.registry.datasets import DATASETS


def statement(dataset: str):
    config = DATASETS[dataset]
    model = config["model"]
    stmt = select(model)
    joins = config["joins"]
    if dataset == "citas":
        stmt = stmt.join(Paciente, Cita.paciente_id == Paciente.id).join(Oftalmologo, Cita.oftalmologo_id == Oftalmologo.id)
    elif dataset == "consultas_clinicas":
        stmt = stmt.join(HistorialClinico, ConsultaClinica.historial_clinico_id == HistorialClinico.id).join(Paciente, HistorialClinico.paciente_id == Paciente.id).join(Oftalmologo, ConsultaClinica.oftalmologo_id == Oftalmologo.id)
    elif dataset == "diagnosticos":
        stmt = stmt.join(ConsultaClinica, Diagnostico.consulta_clinica_id == ConsultaClinica.id).join(HistorialClinico, ConsultaClinica.historial_clinico_id == HistorialClinico.id).join(Paciente, HistorialClinico.paciente_id == Paciente.id).join(Oftalmologo, ConsultaClinica.oftalmologo_id == Oftalmologo.id)
    elif dataset == "usuarios":
        stmt = stmt.join(Rol, Usuario.rol_id == Rol.id)
    return stmt


def execute(db, dataset, filters, orders, limit=None, columns=None):
    stmt = statement(dataset)
    fields = DATASETS[dataset]["fields"]
    for condition in filters:
        field = fields[condition.campo]
        expr, op, value = field.expr, condition.operador, condition.valor
        if op == "eq": stmt = stmt.where(expr == value)
        elif op == "contains": stmt = stmt.where(expr.ilike(f"%{value}%", escape="\\"))
        elif op == "starts_with": stmt = stmt.where(expr.ilike(f"{value}%", escape="\\"))
        elif op == "gt": stmt = stmt.where(expr > value)
        elif op == "gte": stmt = stmt.where(expr >= value)
        elif op == "lt": stmt = stmt.where(expr < value)
        elif op == "lte": stmt = stmt.where(expr <= value)
        elif op == "between": stmt = stmt.where(expr.between(value[0], value[1]))
        elif op == "in": stmt = stmt.where(expr.in_(value))
    for order in orders:
        expr = fields[order.campo].expr
        stmt = stmt.order_by(expr.asc() if order.direccion == "asc" else expr.desc())
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = db.scalar(count_stmt)
    if columns:
        stmt = stmt.with_only_columns(*(fields[key].expr.label(key) for key in columns))
    if limit is not None:
        stmt = stmt.limit(limit)
    return db.execute(stmt).all(), total
