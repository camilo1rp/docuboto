# ADR-052: Lo estándar se implementa con una librería, no a mano

**Estado**: Aceptado
**Fecha**: 2026-11-07
**Autores**: Camilo Romero
**Relacionados**: [ADR-003](ADR-003-asyncpg-sin-orm.md) (sin ORM; **se conserva**), [ADR-010](ADR-010-libreria-compartida.md), [ADR-012](ADR-012-migraciones-por-tenant.md) (runner propio; **se revisa**), [ADR-021](ADR-021-reclamacion-deadletter-eventos.md) (dead-letter; **se reimplementa**), [ADR-023](ADR-023-worm-object-lock-preservacion.md) (BagIt/PREMIS; **se difiere**), [ADR-047](ADR-047-un-proceso-y-cuatro-contenedores.md), [ADR-049](ADR-049-eventos-en-postgresql-sin-redis.md)

---

## Contexto

El censo identifica **~19.400 líneas** de implementación propia para problemas estándar, más ~10.700 de duplicación estructural. Varias de esas piezas se escribieron a mano a sabiendas; el código lo dice en su docstring. Otras son accidentales: la dependencia correcta ya está declarada y se usa solo en una prueba.

| Pieza propia | Líneas | Situación |
|---|---:|---|
| Capa de perfil XAdES (LT/LTA, `ArchiveTimeStamp`, `CertificateValues`) | 2.265 | `signxml` ya hace XMLDSig; el perfil encima es propio |
| Dead-letter (módulo compartido + 3 servicios) | 2.510 | Redis Streams ya daba PEL; con ADR-049 hay que rehacerlo igual |
| Migraciones: libro, cerrojo y 10 copias de `runner.py` | 2.505 | 8 copias son exactamente 186 líneas que difieren en una constante |
| Empaquetado AIP/BagIt escrito a mano | 1.733 | `bagit` **ya está declarado** y se importa solo en una prueba |
| Ingesta IMAP | 1.135 | `imap-tools` / `aioimaplib` |
| TOTP (RFC 6238) | 1.055 | `pyotp` son ~10 líneas de uso |
| Token de sello de tiempo RFC 3161 | 961 | `rfc3161ng` |
| Bus de eventos (sobre, grupos, reclamo, trim) | 769 | Con ADR-049 se reduce a sobre + firma |
| Validación PDF/A | 699 | `veraPDF` ya es una librería; lo propio es el envoltorio y la JVM en la imagen |
| Construcción de respuesta OCSP | 587 | `certvalidator`, que ya llega como dependencia transitiva |
| Enrutado y recorrido de rutas del gateway | 554 | Se va con el gateway (ADR-047) |
| Exportador de Prometheus | 404 | `prometheus_client` |
| Límite de tasa | 267 | Se va con el gateway; queda `limit_req` en `edge` |
| Detección de tipo por bytes mágicos (2 tablas) | ~338 | `filetype` |
| Política de conservación triplicada | 311 | Una función |
| ALTCHA | 305 | El widget JS ya es dependencia; el servidor propio se conserva (no hay paquete Python) |

## Decisión

### 1. Regla

Una implementación propia se justifica cuando **no hay librería mantenida**, cuando la librería **impone una dependencia o un servicio** que el cliente no puede operar, o cuando el comportamiento debe ser **demostrablemente distinto** del estándar. En cualquier otro caso se usa la librería, aunque la propia sean 30 líneas menos.

Se conserva a mano lo que ya está justificado en el árbol y este ADR no cambia:

- **SQL a mano con `asyncpg`** (ADR-003). Un ORM cambiaría el modelo de consultas del dominio de archivo, que es donde están las consultas difíciles.
- **Auditoría encadenada por hash** (`shared/audit.py`, 502 líneas). No hay reemplazo directo; `immudb`/`trillian` son servicios.
- **ALTCHA**, porque no existe paquete Python del servidor y el protocolo está fijado a la v1 por el widget.

### 2. Sustituciones, por prioridad

| Pieza | Se sustituye por | Cuándo |
|---|---|---|
| Dead-letter y bus | Implementación propia sobre las tablas de ADR-049 (sobre y firma se conservan) | Fase 4 |
| Migraciones | Se colapsa a **un** runner sobre el libro existente. **Sin Alembic**: ver alternativas | Fase 1 |
| BagIt | `bagit` (ya declarado), solo en el camino de exportación | Fase 8, si se necesita |
| IMAP | `imap-tools` para lectura y `aiosmtplib` (ya presente) para envío | Fase 7 |
| TOTP | `pyotp`, conservando el cifrado del secreto y el consumo de paso de tiempo | Fase 7 |
| TSA RFC 3161 | `rfc3161ng` sobre `asn1crypto` (ya presente) | Fase 7 |
| OCSP | `certvalidator` (ya transitiva de `signxml`) | Fase 1, con la firma |
| Prometheus | `prometheus_client`, conservando la regla de no etiquetar entidad ni usuario | Fase 7 |
| Tipo por bytes | `filetype` | Fase 7 |
| Política de conservación | Una función en el paquete compartido | Fase 3 |
| PDF/A | Validador externo opcional; fuera de la imagen | Fase 5 |
| Enrutado, límite de tasa, caché de validación | Se van con el gateway | Fase 3 |

### 3. La capa XAdES no se sustituye en esta tanda

Es la sustitución más valiosa (2.265 líneas) y la más peligrosa: hoy el perfil LT/LTA está verificado contra **EU DSS**, y reemplazarlo por otra implementación exige repetir esa verificación. Se hace como trabajo propio, con la batería de DSS como puerta, y no se mezcla con el adelgazamiento.

Lo que sí se hace en la fase de firma (ADR-054) es arreglar los defectos de verificación, que son bugs y no decisiones de diseño.

### 4. Guardián

Se añade una prueba de arquitectura que falla si aparece una dependencia declarada y no importada en producción (hoy `pypdf` tiene 0 importaciones y `bagit` solo una de prueba), y si un `requirements.txt` vuelve a declarar `>=` sin fijar. El árbol pasa a tener fijaciones exactas con un archivo de restricciones por imagen.

## Alternativas consideradas

- **Alembic para las migraciones.** Descartado, y conviene dejarlo escrito porque es la respuesta obvia. El proyecto ejecuta migraciones **por esquema de entidad** antes de servir, con un cerrojo global y un libro compartido, y tiene pruebas de arquitectura que leen los archivos SQL. Alembic asume un `search_path` fijo y un `alembic_version` por base; adaptarlo a 266 archivos aplicados en N esquemas es más código que el runner actual, y el guardián del proyecto (`shared/tests/test_la_documentacion_dice_lo_que_hay.py`) ya prohíbe recetar Alembic en la documentación. Se colapsa el runner propio a uno solo.
- **Reescribir la firma con `pyhanko`.** Descartado: `pyhanko` firma PAdES (PDF), no XAdES (XML). El perfil XML exige una implementación XAdES y la que hay ya está verificada contra DSS.
- **Migrar el bus a `arq`/`dramatiq`/`taskiq`.** Descartado tras ADR-049: esas librerías traen su propio broker (Redis), que es justo lo que se retira. La cola en PostgreSQL ya existe y funciona.
- **`passlib` para contraseñas.** Descartado: sin mantenimiento desde 2020; se usa `argon2-cffi` (ADR-048).

## Consecuencias

- **~12.000 líneas menos** de código propio, y cada pieza sustituida queda con el mantenimiento de otro.
- **Aparecen dependencias nuevas** (`pyotp`, `rfc3161ng`, `imap-tools`, `filetype`, `argon2-cffi`, `prometheus_client`). Todas son pequeñas y con licencia compatible; ninguna arrastra un servicio.
- **Cada sustitución mueve pruebas.** Las pruebas existentes se escribieron contra el comportamiento propio; algunas codifican decisiones legítimas (el consumo del paso de tiempo, el mensaje de error en español) y hay que conservarlas al cambiar de librería.
- **El orden importa**: hacer la sustitución antes de la fusión multiplicaría el conflicto. Va al final, por piezas, después de que la topología esté quieta.
- **Riesgo declarado**: cambiar `tsa.py` u `ocsp.py` toca la cadena de firma, que es lo que un tercero verifica. Cada cambio de esa zona exige pasar la batería contra DSS antes de mezclar.
