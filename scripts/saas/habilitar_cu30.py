"""Preparar permisos CU30; la escritura exige --apply y una huella revisada."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys


def main(argv=None, *, control_factory=None, registry=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument("--plan", action="store_true", help="Solo lectura; es el modo predeterminado")
    modo.add_argument("--apply", action="store_true", help="Aplicar exclusivamente el plan aprobado")
    parser.add_argument("--confirm-plan", help="SHA256 del plan revisado, obligatorio para --apply")
    parser.add_argument("--empresa", action="append", help="Código de empresa del Control Plane; se puede repetir")
    parser.add_argument("--expected-companies", type=int, default=7, help="Cantidad esperada de empresas seleccionadas (predeterminado: 7)")
    args = parser.parse_args(argv)
    if args.expected_companies < 1:
        parser.error("--expected-companies debe ser positivo")
    if args.apply and not re.fullmatch(r"[a-f0-9]{64}", args.confirm_plan or ""):
        parser.error("--apply requiere --confirm-plan con la huella SHA256 del plan aprobado")
    if not args.apply and args.confirm_plan:
        parser.error("--confirm-plan solo se utiliza junto con --apply")
    codigos = tuple(codigo.strip() for codigo in args.empresa) if args.empresa else None
    if codigos is not None and (any(not codigo for codigo in codigos) or len(codigos) != len(set(codigos))):
        parser.error("Los códigos de empresa deben ser distintos y no vacíos")

    repository_root = str(Path(__file__).resolve().parents[2])
    if repository_root not in sys.path:
        sys.path.insert(0, repository_root)
    from app.modules.gestion_inventario_proveedores.services.permisos_inventario import (
        HabilitacionCU30Error, aplicar_plan, preparar_plan,
    )

    try:
        if control_factory is None:
            from app.database.session import open_control_session
            control_factory = open_control_session
        if registry is None:
            from app.core.tenancy.registry_provider import get_tenant_engine_registry
            registry = get_tenant_engine_registry()
        opciones = {"codigos": codigos, "expected_companies": args.expected_companies}
        resultado = (aplicar_plan(control_factory, registry, plan_hash=args.confirm_plan, **opciones)
                     if args.apply else preparar_plan(control_factory, registry, **opciones).to_dict())
        print(json.dumps(resultado, ensure_ascii=False, sort_keys=True))
        return 0 if resultado.get("estado") in {None, "COMPLETADO"} else 3
    except HabilitacionCU30Error as error:
        print(json.dumps(error.to_dict(), ensure_ascii=False, sort_keys=True))
        return 4
    except Exception:
        print(json.dumps({"estado": "FALLIDO", "codigo": "PREPARACION_NO_DISPONIBLE",
                          "mensaje": "No se pudo completar la operación; no se muestran datos de conexión."}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
