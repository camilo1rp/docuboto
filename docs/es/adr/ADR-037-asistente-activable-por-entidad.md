# ADR-037: El asistente y la búsqueda semántica se activan por entidad, y consultarlos es un permiso

**Estado**: Aceptado
**Fecha**: 2026-10-20
**Autores**: Giampiero (mantenedor principal); triage 2026-10-09, tanda C-1 (C1 y C2)
**Relacionados**: [ADR-035](ADR-035-estado-de-la-entidad-cumplimiento-en-el-borde.md) (`public.tenants` como registro leído en cada petición), [ADR-014](ADR-014-busqueda-postgresql-fts.md) (la búsqueda exacta no depende del asistente), [ADR-019](ADR-019-capa-mcp.md) (asistente), [ADR-021](ADR-021-reclamacion-deadletter-eventos.md) (ingesta por eventos)

---

## Contexto

Hasta hoy las cuatro rutas de consulta de knowledge-service (`/search`, `/antecedentes`, `/rag`, `/suggestions/{id}/aceptar`) no pedían ningún permiso: cualquier autenticado del tenant las usaba, y el asistente conversacional (`/api/v1/assistant/*`) y sus tres tools de conocimiento estaban disponibles para toda entidad. Dos problemas distintos:

1. **Quién, dentro de la entidad.** El clearance (RF-SEG-08) dice *qué* puede ver una persona; nada decía *si puede consultar con IA*. Un guardián (`test_toda_ruta_lleva_gate`) lo tenía declarado como excepción.
2. **Si la entidad lo quiere.** Usar un proveedor de lenguaje externo (Anthropic, `ollama_cloud`…) es un tratamiento de datos que la **entidad** decide como responsable (Ley 1581, art. 4 lit. b —finalidad— y art. 17 —deberes del responsable—). Enviar esos datos a Anthropic u otro proveedor que los trata por cuenta de la entidad es una **transmisión** a un encargado (Decreto Único 1074 de 2015, art. 2.2.2.25.5.1 y 2.2.2.25.5.2 [confirmar numerales]), con su contrato de transmisión; no es la transferencia internacional del art. 26. No había interruptor por entidad: era todo o nada por despliegue.

## Decisión

### 1. Un permiso, dos niveles: `PERM_CONOCIMIENTO`

- **Nivel 1 (Leer)** —acceso y circulación restringida, Ley 1581 art. 4 lit. f—: `/search`, `/antecedentes`, `/rag` y todas las rutas de `/api/v1/assistant/*` (mensaje, alcance, confirmar, transcribir). Un solo permiso significa «consultar con IA», concedido por persona.
- **Nivel 2 (Editar)**: aceptar una sugerencia (`/suggestions/{id}/aceptar`), que escribe `kb_suggestions` y deja asiento.
- La migración `auth-service/migrations/tenant/020_permiso_conocimiento.sql` lo concede a **todo grupo que exista** al aplicarla, con el nivel `LEAST(2, GREATEST(1, max(crud) del grupo))`: quien solo tenía permisos de lectura consulta, quien escribía además acepta. En un tenant nuevo corre durante la provisión, antes de que haya grupos, y no concede nada: el administrador lo reparte a conciencia. ROOT lo tiene por `is_root`.
- El clearance y la ACL siguen en el `WHERE` de `KnowledgeRepository.search`: el permiso es aditivo, no relaja nada.

Alternativas descartadas: dos permisos (consultar / aceptar) —aceptar solo existe sobre una sugerencia propia, no hay «puede aceptar sin poder consultar»—; tres permisos (buscar / antecedentes / RAG) —la diferencia real entre buscar y RAG es el egreso a un proveedor, y eso lo gobierna la barrera de residencia (`is_generatable`), no un permiso.

### 2. Una bandera por entidad: `public.tenants.asistente_habilitado`

- Columna `BOOLEAN NOT NULL` (migración global `tenant-service/migrations/global/004`): las entidades que existen al migrar quedan **encendidas** (`DEFAULT true` al añadirla) y toda entidad que se registre después nace **apagada** (`SET DEFAULT false`). La regla «actuales encendidas, nuevas apagadas» vive en el esquema, sin heurística. El `IF NOT EXISTS` impide que una reejecución vuelva a encender a quien apagó.
- **La cambia la entidad**: `PUT /api/v1/config/asistente` (tenant-service), `USUA_PERM_ADMIN` nivel 3, `SELECT … FOR UPDATE` + `UPDATE` + asiento `tenant.asistente_cambiado` (`antes`/`despues`) en **una** transacción; el slug sale de la cabecera validada, nunca del cuerpo; pedir el valor vigente no es un acto (200 sin asiento). `GET` del mismo recurso para cualquier autenticado.
- **La autoridad es knowledge-service**: `require_asistente_habilitado` lee la bandera de la base en **cada** consulta, sin caché (apagar es el acto que debe ser inmediato). Si el registro no responde: `503 asistente_estado_no_disponible` (fail-closed). Apagada: `403 asistente_deshabilitado`, también para ROOT, y **antes** de evaluar el permiso (el 403 honesto «la entidad no lo tiene» gana al «usted no tiene el permiso»).
- **auth-service la publica** en `GET /auth/me` (`asistente_habilitado: bool`, `false` si no hay tenant o la lectura falla) para que la UI y mcp-server decidan qué mostrar y negar temprano. Es información para mostrar y para negar temprano. **Para el asistente (mcp-server) `/auth/me` SÍ es la decisión**: se lee en cada turno, sin caché, y el turno corre con ese valor; knowledge-service sigue siendo la autoridad para sus propias rutas (las vuelve a leer de la base), pero el asistente no pasa por ellas hasta que el modelo llama a una tool. Por eso `/auth/me` falla cerrado (`false`) y no se cachea.

### 3. Qué apaga la bandera (y qué no)

| Superficie | Con la bandera apagada |
|---|---|
| knowledge `/search`, `/antecedentes`, `/rag`, `/suggestions/{id}/aceptar` | `403 asistente_deshabilitado` |
| mcp-server `/api/v1/assistant/*` (las 5 rutas) | `403 asistente_deshabilitado` antes de tocar Anthropic o Whisper; `403 forbidden` si falta `PERM_CONOCIMIENTO` |
| Las 3 tools de conocimiento (E21: `buscar_conocimiento`, `buscar_antecedentes`, `redactar_con_antecedentes`) | No se listan ni se despachan (`asistente_deshabilitado`), tampoco para ROOT |
| **Ingesta** (`POST /ingest` —interno, sin gate de bandera— y el consumidor de `document.radicado.created`), `reclassified`/`annulled`/`purged`, replay de la DLQ, reconciliación de nivel y `reembed` | **Siguen funcionando mientras el proveedor de embeddings sea local** (`stub`/`local_st`; sin egreso): con uno no local se saltan con asiento (ver abajo). Indexar con un proveedor local no es lo que la entidad decide apagar: indexar no es lo que la entidad decide apagar, y mantener el índice al día elimina el hueco al reactivar (nada radicado mientras estuvo apagado queda sin indexar) |
| MCP genérico (resto de tools) | No se apaga: es la API *as-the-user* por otro protocolo; la entidad no puede impedir a su funcionario llamar al REST equivalente |

La búsqueda exacta (FTS, ADR-014) no depende de la bandera. Los datos ya indexados se conservan al apagar (capa derivada, ACL intacta); simplemente no se sirven.

**Regla de la ingesta con la bandera apagada.** El índice semántico **local** es parte de la finalidad «gestión y búsqueda documental» de la entidad y es independiente de la bandera: con un proveedor de embeddings local (`stub`, `local_st`) se indexa siempre, y ni siquiera se lee la bandera. Con un proveedor de embeddings **no local** (p. ej. un Ollama remoto) indexar es transmitir texto de los radicados a un tercero, y una entidad que apagó el asistente no lo autorizó: el worker de `created` (y por tanto el replay de la DLQ, que lo reusa), `POST /ingest` y `reembed` **saltan con asiento** (`knowledge.ingest_skipped_asistente_apagado` / `knowledge.reembed_skipped_asistente_apagado`; `/ingest` responde `403 asistente_deshabilitado`). Sin egreso con la bandera apagada. Lo que se salta así no se indexa retroactivamente al reactivar: con proveedor no local habrá que reindexar desde la fuente (deuda declarada). Si el registro de entidades no responde, el evento falla y se reintenta (fail-closed).

**Encender es un acto con fricción; apagar, no.** `PUT /config/asistente` exige para encender `declaracion_transmision: true` (`422 declaracion_requerida`) y que la entidad tenga `politica_tratamiento_version` no vacía (`409 politica_no_publicada`, el mismo código de la ventanilla); `motivo` es opcional. El asiento `tenant.asistente_cambiado` lleva `antes`, `despues`, `motivo` y, al encender, `politica_tratamiento_version`, `proveedores` (los efectivos: `asistente: anthropic`, `modelo`, `rag`, `embeddings`, `embeddings_local`) y `declaracion_transmision`. `GET /config/asistente` devuelve esos mismos proveedores para que la pantalla los muestre antes de encender.

## Consecuencias

- **Cambio incompatible**: las rutas de consulta de knowledge y el asistente exigen `PERM_CONOCIMIENTO`; `X-User-Id` es obligatorio (antes un llamante anónimo caía a clearance 1 en silencio). Las entidades registradas desde ahora nacen sin asistente.
- La UI muestra el botón/dock del asistente solo con `asistente_habilitado` y las pestañas Semántica/Antecedentes de `/busqueda` solo con la bandera y el permiso.
- `GET /api/v1/assistant/transcribe` deja de ser anónimo: hace falta saber de qué entidad se pregunta para poder negarlo.
- Guardián `shared/tests/test_conocimiento_mira_la_bandera.py`: toda ruta de `knowledge.py` declara la bandera, toda ruta de `assistant.py` llama al chequeo (y `run_turn` lo hace antes de armar el marco o llamar al modelo), y todo llamante del catálogo MCP pasa `asistente_habilitado=` explícito.

## Residuos declarados

- **Apagado forzado por el operador**: la bandera es de la entidad; la plataforma no puede apagarla por encima de la voluntad de su administrador (se declara en el roadmap).
- **El MCP genérico no se apaga** (ver tabla): decisión consciente, no omisión.
- **La DLQ admin no mira la bandera**: `/api/v1/knowledge/admin/deadletter` se gobierna con `PERM_DLQ_ADMIN` y el clearance del radicado referenciado; no consulta ni genera nada para la persona. Su `replay` de un `created` reusa el worker, que aplica la regla de la ingesta de arriba.
- **Evidencia del estado inicial y de la concesión**: que las entidades existentes quedaran encendidas (global `004`) y que sus grupos recibieran `PERM_CONOCIMIENTO` (tenant `020`, solo grupos con algún permiso de nivel ≥ 1) queda evidenciado por el libro de migraciones (qué migración se aplicó, en qué esquema y cuándo); no hay asiento por entidad de ese estado inicial.
- **ROOT en mcp-server** es `permission_levels.USUA_PERM_ROOT > 0` (como lo emite auth-service); el rol JWT «ROOT» no existe en el realm.
