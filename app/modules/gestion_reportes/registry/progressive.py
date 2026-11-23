REPORTES_PROGRESIVOS = {
    "citas_creadas": {
        "codigo": "Q11", "etiqueta": "Citas creadas", "fuente": "cita.fecha_registro",
        "campo_temporal": "fecha_registro", "calificador": None, "disponible": True,
        "limitaciones": ["Mide demanda registrada, no ocupación ni atención."],
    },
    "citas_programadas": {
        "codigo": "Q01", "etiqueta": "Citas programadas", "fuente": "cita.fecha",
        "campo_temporal": "fecha de atención", "calificador": None, "disponible": True,
        "limitaciones": ["La fecha corresponde al horario programado."],
    },
    "citas_atendidas": {
        "codigo": "Q02", "etiqueta": "Citas atendidas", "fuente": "cita.fecha + cita.estado=ATENDIDA",
        "campo_temporal": "fecha de atención", "calificador": None, "disponible": True,
        "limitaciones": ["Usa el estado final actual; no existe historial estructurado de transiciones."],
    },
    "cancelaciones": {
        "codigo": "Q03", "etiqueta": "Cancelaciones", "fuente": "cita.fecha + cita.estado=CANCELADA",
        "campo_temporal": "fecha de atención", "calificador": None, "disponible": True,
        "limitaciones": ["No representa la fecha en que se produjo la cancelación."],
    },
    "capacidad_estimada": {
        "codigo": "Q04", "etiqueta": "Capacidad estimada", "fuente": "configuración de agenda",
        "campo_temporal": "fecha de agenda", "calificador": "ESTIMADO", "disponible": False,
        "limitaciones": ["No se publica sin una consulta de capacidad autorizada."],
    },
    "integridad_agenda": {
        "codigo": "Q07", "etiqueta": "Integridad de agenda", "fuente": "cita.fecha/hora_inicio/hora_fin",
        "campo_temporal": "fecha de atención", "calificador": None, "disponible": False,
        "rango_maximo_dias": 92, "limitaciones": ["Q07 conserva el máximo de tres meses."],
    },
    "reprogramaciones_proxy": {
        "codigo": "Q08", "etiqueta": "Reprogramaciones", "fuente": "cita.fecha_actualizacion",
        "campo_temporal": "fecha de atención", "calificador": "PROXY", "disponible": True,
        "limitaciones": ["Una actualización no demuestra por sí sola un cambio de horario."],
    },
    "inasistencias_descriptivas": {
        "codigo": "Q09", "etiqueta": "Inasistencias descriptivas", "fuente": "cita.fecha + cita.estado=NO_ASISTIO",
        "campo_temporal": "fecha de atención", "calificador": "DESCRIPTIVO / BLOQUEADO_POR_DATOS para ML",
        "disponible": True, "limitaciones": ["No constituye una etiqueta fiable de entrenamiento."],
    },
    "calidad_cobertura": {
        "codigo": "Q10", "etiqueta": "Calidad y cobertura", "fuente": "cita.fecha_registro/cita.fecha/cita.estado",
        "campo_temporal": "según indicador", "calificador": None, "disponible": True,
        "limitaciones": ["La cobertura productiva por tenant aún no fue perfilada."],
    },
    "consultas_clinicas": {
        "codigo": None, "etiqueta": "Consultas clínicas registradas", "fuente": "consulta_clinica.fecha_consulta",
        "campo_temporal": "fecha de consulta", "calificador": None, "disponible": True,
        "limitaciones": ["Solo conteos agregados; no incluye contenido clínico."],
    },
}

INDICADORES_DISPONIBLES = tuple(
    key for key, value in REPORTES_PROGRESIVOS.items()
    if value["disponible"] and key != "calidad_cobertura"
)
