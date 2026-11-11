# ADR-053: Un esquema legible, lanzamientos versionados y migraciones que se pueden deshacer

**Estado**: Aceptado
**Fecha**: 2026-11-07
**Autores**: Camilo Romero
**Relacionados**: [ADR-012](ADR-012-migraciones-por-tenant.md) (migraciones por entidad; **se conserva el mecanismo, se regenera el contenido**), [ADR-041](ADR-041-metricas-operativas-sin-tenant-y-migraciones-con-cerrojo.md) (cerrojo de migraciones; **se conserva**), [ADR-047](ADR-047-un-proceso-y-cuatro-contenedores.md), [ADR-049](ADR-049-eventos-en-postgresql-sin-redis.md)

---

## Contexto

### Tres copias del mismo esquema

| Representación | Archivos | Líneas |
|---|---:|---:|
| Migraciones por servicio | 289 | 20.813 |
| *Baseline* generado (`docuboto/migrations/*/001_baseline.sql`) | 2 | 21.178 |
| Esquema en ejecución por entidad | — | (el mismo DDL aplicado N veces) |

El *baseline* no sustituye a las migraciones: convive con ellas y lo genera `scripts/generar_baseline.py`, que concatena 265 secciones etiquetadas. Tres representaciones de un mismo esquema significan que cambiar una columna obliga a tocar un archivo numerado y a regenerar otro, y que nadie puede leer el esquema de un vistazo.

Las 266 migraciones de entidad existen para llevar instalaciones antiguas hasta hoy. **No hay ninguna instalación con datos.** Por tanto, la única función que cumplen —la historia— no tiene a quién servir.

### No existen lanzamientos

- `git tag` → **0**. Nunca se ha publicado una versión.
- `.gitlab-ci.yml`: el escenario `deploy` contiene `pages` y `notify-website-docs`. **No hay ningún trabajo que despliegue**; `rg 'environment:'` → 0.
- Los manifiestos exigen `ORPYCA_TAG` con `${VAR:?}`, así que se despliega por SHA de commit, a mano.
- Las migraciones **no tienen `down`**: `docs/es/deployment.md:813-821` lo dice. Volver el código no vuelve el esquema, y la única salida es restaurar del respaldo, perdiendo todo lo posterior.

Ese conjunto hace que cada cambio en producción sea una apuesta, y que la apuesta se pague con un respaldo en lugar de con un `git revert`.

### El hardware prometido no coincide con el declarado

`docs/es/deployment.md:22-29` promete 4 núcleos / 16 GB para «una institución pequeña a mediana». El manifiesto de producción declara, para el perfil por defecto, **13,5 CPU / 20,75 GB** de límites. Y `docs/es/getting-started.md:5-7` dice 4 GB / 8 GB recomendados. Tres cifras distintas, ninguna verificada.

## Decisión

### 1. Un esquema, generado desde el árbol, en migraciones legibles

Se regenera la historia en una cadena corta y numerada por dominio, escrita para leerse:

- **Un archivo por tabla o por grupo coherente**, con nombre que dice qué crea (`010_radicados.sql`, `020_expedientes.sql`), no `057_ajuste.sql`.
- **Sin *baseline* generado.** `generar_baseline.py` se retira: si el esquema cabe en migraciones legibles, no hay nada que concatenar.
- **El esquema actual se obtiene de la cadena**, y `pg_dump --schema-only` de una base recién migrada debe ser idéntico al de una instalación nueva. Esa igualdad es una prueba, no una aspiración.
- **La cadena vieja se archiva** en `docs/es/historia/esquema/` como referencia de las decisiones (índices, disparadores, comentarios), no como código ejecutable.

Como no hay datos, el corte es: cadena nueva → crear la entidad → comparar. Si la comparación falla, el defecto es de la cadena nueva y se ve en una prueba, no en un cliente.

### 2. Un runner, no diez

`migrations/runner.py` existe en diez copias; ocho son exactamente 186 líneas que difieren en la constante `SERVICIO`. Se colapsa a **uno** que:

1. Toma un cerrojo de asesoría global (`pg_try_advisory_lock`, ya implementado en `shared/migrations.py`).
2. Aplica `global/` y luego `tenant/` a cada esquema `tenant_*`.
3. Anota en el libro único `public.schema_migrations` con clave `(esquema, archivo)`.
4. Cada archivo, en su propia transacción.

Se **descarta Alembic** y el motivo queda escrito en ADR-052: el mecanismo del proyecto aplica migraciones en N esquemas con un cerrojo y un libro compartidos, y las pruebas de arquitectura leen los archivos SQL. Adaptar Alembic a eso cuesta más que el runner.

### 3. Lanzamientos versionados y despliegue automático

- **Cada `tag` de git produce imágenes fijadas por digest** y un `release-<versión>.yml` que referencia esos digests. No se despliega por SHA suelto.
- **`ORPYCA_TAG` pasa a ser `ORPYCA_VERSION`**, y el manifiesto de producción apunta al `release-*.yml` de la versión.
- **Un trabajo `deploy` por instalación** (SSH + `docker compose -f release-<v>.yml up -d`), disparado por el tag, con el respaldo previo como paso bloqueante: si el respaldo falla, el despliegue no ocurre.
- **La versión declarada se expone** en `/health` y en una migración que siembra `public.schema_version`, para que «qué corre en esta alcaldía» sea una consulta y no una suposición.

### 4. Expandir y contraer: la reversibilidad se demuestra, no se espera

Sin migraciones `down`, la reversibilidad se logra por disciplina de despliegue:

| Paso | Regla |
|---|---|
| Expandir | El esquema nuevo es **compatible con el código anterior**: columnas nuevas nulas o con valor por defecto, tablas nuevas, índices creados `CONCURRENTLY`. |
| Desplegar | El código nuevo escribe en lo nuevo y sigue leyendo lo viejo si falta. |
| Contraer | Solo en el **lanzamiento siguiente**, cuando ningún código lee lo viejo: se borran columnas y tablas. |

Con eso, revertir un lanzamiento es apuntar a la versión anterior: el esquema sigue sirviendo a las dos. La prueba de que la regla se cumple se escribe como un guardián que falla si una migración de un lanzamiento dado contiene `DROP COLUMN`, `DROP TABLE` o `ALTER COLUMN … SET NOT NULL` sobre algo creado en el mismo lanzamiento.

El respaldo deja de ser el mecanismo de reversión y pasa a ser lo que debe ser: la recuperación de una catástrofe.

### 5. Respaldo completo, incluidos los secretos

`infra/backup.sh` respalda PostgreSQL y el almacén de objetos. **No** respalda `infra/.env`, las KEK, `secrets/signing`, `secrets/tsa` ni `secrets/ca-users-public`. Perder `SIGNING_KEY_ENCRYPTION_KEY` hace irrecuperables las claves privadas custodiadas; `infra/.env.example:96-100` lo advierte y el respaldo lo ignora.

Se añade un **paquete de secretos sellado**: un archivo cifrado con una contraseña que el operador guarda fuera del servidor, con todas las claves y el `.env`. Entra en el ensayo de restauración que `infra/verify-restore-semanal.sh` ya ejecuta semanalmente contra un contenedor desechable.

También se corrigen dos omisiones del manifiesto que rompen el alta de una entidad:

- `INTELLIGENCE_CREDENTIAL_KEK` es obligatoria para `init_tenant.py:769` y **no aparece en ningún manifiesto de producción**.
- `KEYCLOAK_GATEWAY_CLIENT_SECRET` caía a `"change-me-in-production"`; desaparece con Keycloak (ADR-048).

### 6. El hardware se declara una vez y se verifica

Se publica una sola cifra, medida sobre el manifiesto final: **2 vCPU / 4 GB / 20 GB SSD** para ≤50 funcionarios, más almacenamiento de objetos dimensionado por el crecimiento del archivo. Se borran las tres cifras contradictorias. Un guardián compara la suma de límites del manifiesto con la cifra publicada y falla si la excede.

## Alternativas consideradas

- **Conservar la cadena de 266 migraciones y añadir el lanzamiento encima.** Descartado: el valor de una migración es llevar datos existentes; sin datos, solo es deuda. Además, nadie puede leer el esquema.
- **Alembic.** Descartado (ADR-052): N esquemas, cerrojo compartido, libro único y pruebas que leen SQL.
- **Migraciones con `down` escritas a mano.** Descartado: 289 archivos × su inverso es el doble de superficie para el mismo resultado que expandir/contraer, y un `down` mal escrito destruye datos en silencio. Expandir/contraer es más barato y más seguro.
- **Seguir desplegando por SHA de commit.** Descartado: no hay forma de decir qué corre, ni de volver a «la anterior» sin recordar un SHA.
- **Sellar el respaldo con la misma KEK del sistema.** Descartado: si se pierde el servidor se pierden ambos. La contraseña del paquete de secretos vive fuera.

## Consecuencias

- **Se puede leer el esquema.** Y cambiarlo es una edición en un archivo con nombre, no una regeneración.
- **~20.800 líneas de SQL se sustituyen por una cadena corta.** El trabajo es real (hay que escribirla) pero mecánico: el DDL correcto ya existe, disperso.
- **Aparece un guardián nuevo**: `pg_dump --schema-only` de una cadena nueva debe coincidir con el de una instalación existente.
- **Primer lanzamiento real del proyecto.** `1.0.0` será el primer tag; hasta hoy no ha habido ninguno.
- **El despliegue deja de ser manual y deja de ser irrelevante.** Un trabajo de CI con SSH a cada instalación es infraestructura nueva que hay que asegurar (clave, host conocido, permisos mínimos).
- **La reversión se vuelve barata solo si la regla se cumple.** El guardián de expandir/contraer es lo que la mantiene honesta; sin él, el primer `DROP COLUMN` en el mismo lanzamiento devuelve el proyecto a restaurar respaldos.
- **Riesgo declarado**: regenerar el esquema es el único paso de todo el rediseño que puede producir una pérdida silenciosa de una restricción (un `UNIQUE`, un disparador `append-only`, un `CHECK`). La comparación de `pg_dump` y las pruebas de dominio existentes son la red; no se hace «a ojo».
