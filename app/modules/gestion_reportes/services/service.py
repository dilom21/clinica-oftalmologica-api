from fastapi import HTTPException

from app.modules.gestion_reportes.registry.datasets import DATASETS, STATIC_REPORTS
from app.modules.gestion_reportes.repositories.repository import execute
from app.modules.gestion_reportes.schemas.schemas import serializar_valor


def validate(dataset, columns, filters, orders):
    if dataset not in DATASETS:
        raise HTTPException(422, "Dataset inválido")
    fields = DATASETS[dataset]["fields"]
    if not columns or any(column not in fields for column in columns):
        raise HTTPException(422, "Columna inválida")
    if len(orders) > 3:
        raise HTTPException(422, "Máximo tres criterios de orden")
    for item in filters:
        if item.campo not in fields or item.operador not in fields[item.campo].operadores:
            raise HTTPException(422, "Filtro u operador inválido")
        if item.operador == "between" and (not isinstance(item.valor, list) or len(item.valor) != 2):
            raise HTTPException(422, "between requiere dos valores")
        if item.operador == "in" and not isinstance(item.valor, list):
            raise HTTPException(422, "in requiere una lista")
    for item in orders:
        if item.campo not in fields or item.direccion not in {"asc", "desc"}:
            raise HTTPException(422, "Orden inválido")


def run(db, dataset, columns, filters, orders, limit, export=False):
    validate(dataset, columns, filters, orders)
    if export:
        limit = 5001
    rows, total = execute(db, dataset, filters, orders, 5001 if export else limit, columns)
    if export and total > 5000:
        raise HTTPException(422, "El reporte supera el máximo de 5000 filas")
    if export:
        rows = rows[:5000]
    fields = DATASETS[dataset]["fields"]
    result = []
    for row in rows:
        result.append({key: serializar_valor(value) for key, value in zip(columns, row)})
    return [{"key": key, "label": fields[key].label} for key in columns], result, total
