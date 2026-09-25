# ADR-027: Sustitución del almacén de objetos — de MinIO a RustFS

**Estado**: Aceptado
**Fecha**: 2026-09-25
**Autores**: Giampiero (mantenedor principal)
**Relacionados**: [ADR-023](ADR-023-worm-object-lock-preservacion.md) (WORM con Object Lock), E10 (preservación), E12 (transferencias), Ac. AGN 001/2024 art. 4.3.2.4 P1

---

## Contexto

**MinIO archivó su proyecto de código abierto** y retiró sus imágenes de todos los
registros públicos. Verificado el 2026-09-24, con control positivo para descartar que
fuera un problema de red o de límite de tasa (`alpine`, `node:22-alpine` y
`keycloak:24.0` se descargaban con normalidad en el mismo momento):

| Vía | Resultado |
|---|---|
| `quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z` | `denied` |
| API de tags de quay.io | **cero** tags |
| `docker.io/minio/minio`, `minio/mc` | 404 |
| Rescate por el **digest** anotado en el compose | no resuelve |
| Binario oficial `dl.min.io` | **410 Gone** |
| Fuente en GitHub | **disponible**, AGPL-3.0, repositorio **archivado** |

Consecuencia inmediata: sin la imagen en caché local, `docker compose up` de la pila
completa **fallaba**, y la instalación no se podía reconstruir desde cero. Daño
colateral que no se había notado porque el arranque falla antes: `infra/backup.sh` y
`infra/restore.sh` invocaban la imagen `minio/mc`, **también borrada** — el respaldo y
la restauración estaban rotos, incluido `mc retention set`, que es lo que repone la
retención WORM al restaurar.

**La lección de fondo, que vale más que el incidente**: un pin protege de que te
cambien la versión, **no de que te la quiten**. Un registro público es un canal de
distribución, no un archivo.

## Lo que estaba en juego

El almacén no es intercambiable por su API: sostiene el verbo que la firma no cubre.
El Ac. AGN 001/2024 art. 4.3.2.4 P1 exige que los expedientes no puedan ser
«**modificados, eliminados o reemplazados**». La huella SHA-256 y la firma XAdES del
índice cubren *modificados* y *reemplazados* —detectan cualquier alteración—, pero
**ninguna propiedad criptográfica impide un borrado**. La firma es prueba de
integridad, no control de custodia: dice «esto ya no es lo que era», no «esto sigue
estando». El verbo *eliminados* lo cierra el almacén o no lo cierra nadie.

De ahí que el criterio de selección no pueda ser «tiene Object Lock», sino **que no
exista camino por el que la orden se acepte y no se cumpla**.

## Decisión

**Sustituir MinIO por RustFS 1.0.0 (Apache 2.0), con la adopción condicionada a una
batería de conformidad WORM contra el almacén real.**

### 1. El motor lo eligieron las pruebas, no el folleto

Se escribió `services/storage-service/tests/integration/test_worm_object_lock_real_s3.py`:
siete pruebas contra un S3 **real**, cada una con control positivo y negativo, que
ejercitan los requisitos que el código realmente usa —`COMPLIANCE` (76 usos en el
código), legal hold (122), Object Lock de bucket, versionado con `version_id`
persistido, `copy_object` heredando el candado, y re-sellado de una clave ya
bloqueada—. Resultado medido:

| Motor | Conformidad |
|---|---|
| MinIO (línea base) | **7 / 7** |
| **RustFS 1.0.0** | **7 / 7** |
| Garage v1.0.1 (control negativo) | **0 / 7** |

El control negativo no es decorativo: sin él, una batería que aprobara a todo el mundo
no mediría nada. Garage reprueba las siete porque **no tiene Object Lock y no puede
tenerlo** — su diseño carece de consenso, y así lo declara su propia matriz de
compatibilidad.

La prueba decisiva usa la credencial **raíz**: que el borrado de una versión retenida
falle *ante root* es lo que distingue COMPLIANCE de GOVERNANCE. En GOVERNANCE, quien
tenga la credencial del almacén puede levantar la retención — y en este sistema esa es
**la misma credencial del servicio que escribe**. Un borrado por esa vía no pasaría por
el gate de disposición ni dejaría asiento, así que no cae en ninguna de las dos
excepciones que el Modelo de Requisitos del AGN admite (TRD, o procedimiento auditado).

### 2. Candidatos descartados, con su motivo

- **SeaweedFS**: tiene Object Lock, pero aceptaba `PutObjectRetention` con
  `Mode=COMPLIANCE` devolviendo 200 **y permitía borrar el objeto igualmente**
  (issues 8350, 11333, 7194). Los arreglos (PR 11335/11361/11362) están **fusionados
  pero no publicados**: la última release es anterior. Reevaluable cuando exista una
  versión que los contenga, sometiéndolo a la misma batería.
- **Garage**: sin Object Lock, y estructuralmente imposible en su diseño.
- **Ceph RGW**: maduro y completo, pero deja de ser un servicio de Compose para ser un
  clúster. Plan B honesto si la entidad ya opera Ceph.
- **Compilar MinIO desde el fuente AGPL**: viable —el tag del pin sigue publicado— y
  fue la recomendación de puente mientras hubiera datos que migrar. Descartada al
  confirmarse que **todo el fondo actual es de pruebas**: su único argumento era evitar
  la migración, y su problema —**cero parches de seguridad nunca más**— es el que no
  conviene arrastrar a producción.
- **`bitnamilegacy/minio`**: retrocede de versión, está congelado sin parches y vuelve
  a depender de un tercero que ya demostró que borra. Se usó **solo** para fijar la
  línea base de la batería, que es un uso legítimo y desechable.

### 3. Lo que NO cambia, y por qué

- **Los nombres `MINIO_*`** de las variables de entorno, el servicio `minio` del
  compose y los buckets `orpycamcp-{slug}-documents`. Son el contrato interno del
  cliente S3 (`miniopy-async`, que habla S3 estándar) y aparecen en 13 ficheros de
  código; renombrarlos multiplicaría el riesgo de esta migración sin ganar nada.
  **Léase `MINIO_*` como «el almacén de objetos S3»**, que es lo que significa desde
  aquí. Queda como deuda declarada, no como olvido.
- **El cliente** `miniopy-async`: no se usa ninguna API de administración de MinIO, solo
  S3 con SigV4.
- **El guard de arranque** que rechaza un modo distinto de COMPLIANCE en producción. No
  se relaja para acomodar a ningún candidato: es la traducción correcta de la norma y
  precede a esta decisión.

### 4. Cambios que la migración sí exige

- **Healthcheck**: `/minio/health/live` → `/health`. RustFS responde también al primero
  por compatibilidad, pero usar ese nombre describiría el despliegue con el motor que ya
  no está.
- **Volumen nuevo** (`objectstore-data`). La compatibilidad on-disk de RustFS con MinIO
  la marcan sus propios autores como *preview*, así que el fondo **se resiembra en vez
  de reinterpretarse**. `minio-data` se conserva declarado y sin montar: borrarlo es
  irreversible y, bajo Object Lock en COMPLIANCE, sus objetos no se pueden ni reescribir.
- **Cliente de los scripts**: `mc` → `aws-cli` (Apache 2.0, mantenida), la única
  alternativa disponible que cubre `get-object-retention`/`put-object-retention`.
  **Mejora que vino con el cambio**: `mc retention set` solo aceptaba una duración en
  días, así que reponer una retención la redondeaba **hacia arriba** y movía la fecha de
  vencimiento en cada restauración; `put-object-retention` admite `RetainUntilDate`, así
  que el plazo vuelve a ser el que la entidad fijó una sola vez.

### 5. El read-back, que era una carencia anterior a esta decisión

Al auditar la sustitución apareció que el sistema **ordenaba** la retención y **nunca
volvía a mirarla**: `set_object_retention` en tres ficheros, `get_object_retention` en
**ninguno**. Después escribía `retention_mode='COMPLIANCE'` en
`preservacion_worm_objeto` y un evento PREMIS `FIJACION` con `resultado='ok'`, es decir,
**atestiguaba** una protección jamás comprobada.

Con un almacén que acepte y no cumpla, eso convierte el acta de transferencia —firmada,
con su huella— en una **afirmación falsa**: un documento criptográficamente impecable y
falso en su contenido, peor que no tener WORM. Y tiene un efecto perverso: el gate de
disposición bloquea los borrados **legítimos** apoyándose en esa fila, mientras que el
borrado ilegítimo en el almacén no lo impide nadie.

Se añade `verificar_retencion_aplicada` y se invoca **fail-closed** tras fijar la
retención (protección del índice y del acta), tras sellar el AIP y tras renovar la
retención. Mismo patrón que la verificación de fixity previa: ante la duda, no se fija y
no se asienta.

## Consecuencias

**A favor**

- La pila vuelve a arrancar y a reconstruirse desde cero.
- El respaldo y la restauración funcionan otra vez, y reponen el **instante exacto**.
- El motor tiene mantenimiento activo y licencia permisiva.
- La conformidad WORM pasa de ser una **reputación** a ser una **prueba ejecutable**, y
  queda como condición de adopción de cualquier motor futuro.
- El read-back cierra una brecha que existía con MinIO y que nadie habría visto.

**En contra, declarado**

- **RustFS es joven**: 1.0.0 es de 2026-09-16 y su cadencia de *previews* es alta. La
  batería es la red que lo contiene, y hay que volver a correrla **antes** de cada
  actualización del motor.
- **La compatibilidad on-disk con MinIO es preview**: un despliegue con datos reales
  exigiría migración explícita (`aws s3 sync` u homólogo) y **re-sellar la retención**,
  con el matiz probatorio de que los `version_id` cambian al copiar entre motores. Aquí
  no aplica porque el fondo es de pruebas, pero en una entidad con fondo real **es una
  decisión con acta**, no un efecto colateral de un `up -d`.
- Los nombres `MINIO_*` sobreviven al motor que los nombraba.

**Para la entidad que despliegue**: guardar su propia copia de la imagen
(`docker save`) junto al resto de sus instrumentos de recuperación. Es la conclusión
práctica del incidente: para un almacén de preservación que debe durar décadas, no se
puede depender de que un tercero siga sirviendo unos bytes.
