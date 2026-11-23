# Graph Report - .  (2026-09-22)

## Corpus Check
- Corpus is ~1434 words - fits in a single context window. You may not need a graph.

## Summary
- 60 nodes · 51 edges · 3 communities detected
- Extraction: 92% EXTRACTED · 8% INFERRED · 0% AMBIGUOUS · INFERRED: 4 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output
- Edge kinds: contains: 33 · inherits: 11 · uses: 4 · method: 2 · rationale_for: 1

## God Nodes (most connected - your core abstractions)
1. `AntecedenteClinicoBase` - 5 edges
2. `Registra una consulta clínica realizada por el oftalmólogo autenticado.      E` - 5 edges
3. `ConsultaClinicaCrear` - 4 edges
4. `AntecedenteClinicoCrear` - 3 edges
5. `AntecedenteClinicoActualizar` - 3 edges
6. `HistorialClinicoRespuesta` - 3 edges
7. `HistorialClinico` - 2 edges
8. `AntecedenteClinico` - 2 edges
9. `ConsultaClinica` - 2 edges
10. `AntecedenteClinicoRespuesta` - 2 edges

## Surprising Connections (you probably didn't know these)
- `Registra una consulta clínica realizada por el oftalmólogo autenticado.      E` --uses--> `AntecedenteClinicoActualizar`  [INFERRED]
  C:/SI2_Proyecto/clinica-oftalmologica-api/app/modules/gestion_historial_clinico/services/service.py → C:/SI2_Proyecto/clinica-oftalmologica-api/app/modules/gestion_historial_clinico/schemas/schemas.py
- `Registra una consulta clínica realizada por el oftalmólogo autenticado.      E` --uses--> `AntecedenteClinicoCrear`  [INFERRED]
  C:/SI2_Proyecto/clinica-oftalmologica-api/app/modules/gestion_historial_clinico/services/service.py → C:/SI2_Proyecto/clinica-oftalmologica-api/app/modules/gestion_historial_clinico/schemas/schemas.py
- `Registra una consulta clínica realizada por el oftalmólogo autenticado.      E` --uses--> `ConsultaClinicaCrear`  [INFERRED]
  C:/SI2_Proyecto/clinica-oftalmologica-api/app/modules/gestion_historial_clinico/services/service.py → C:/SI2_Proyecto/clinica-oftalmologica-api/app/modules/gestion_historial_clinico/schemas/schemas.py
- `Registra una consulta clínica realizada por el oftalmólogo autenticado.      E` --uses--> `HistorialClinicoRespuesta`  [INFERRED]
  C:/SI2_Proyecto/clinica-oftalmologica-api/app/modules/gestion_historial_clinico/services/service.py → C:/SI2_Proyecto/clinica-oftalmologica-api/app/modules/gestion_historial_clinico/schemas/schemas.py

## Communities

### Community 0 - "Schemas Pydantic"
Cohesion: 0.28
Nodes (10): BaseModel, AntecedenteClinicoActualizar, AntecedenteClinicoBase, AntecedenteClinicoCrear, AntecedenteClinicoRespuesta, ConsultaClinicaCrear, ConsultaClinicaRespuesta, HistorialClinicoDetalleRespuesta (+2 more)

### Community 3 - "Logica de Servicio"
Cohesion: 0.29
Nodes (1): registrar_consulta_clinica()

### Community 4 - "Modelos ORM"
Cohesion: 0.60
Nodes (4): Base, AntecedenteClinico, ConsultaClinica, HistorialClinico

## Knowledge Gaps
- **Thin community `Logica de Servicio`** (1 nodes): `registrar_consulta_clinica()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Registra una consulta clínica realizada por el oftalmólogo autenticado.      E` connect `Schemas Pydantic` to `Logica de Servicio`?**
  _High betweenness centrality (0.054) - this node is a cross-community bridge._
- **Why does `registrar_consulta_clinica()` connect `Logica de Servicio` to `Schemas Pydantic`?**
  _High betweenness centrality (0.046) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `Registra una consulta clínica realizada por el oftalmólogo autenticado.      E` (e.g. with `AntecedenteClinicoActualizar` and `AntecedenteClinicoCrear`) actually correct?**
  _`Registra una consulta clínica realizada por el oftalmólogo autenticado.      E` has 4 INFERRED edges - model-reasoned connections that need verification._