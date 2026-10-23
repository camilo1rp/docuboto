# Primeros Pasos / Getting Started

## Requisitos / Requirements

- Docker 24+ y Docker Compose v2
- 4 GB RAM mínimo (8 GB recomendado en producción)
- Puertos libres: 19080, 19180, 15432, 19900, 16379

> Los servicios internos siguen escuchando en 8001-8011/8080 dentro de la red Docker
> `orpycamcp-net`; lo que cambia son los puertos **publicados** al host (rango 19xxx /
> 15432 / 16379) para no chocar con otras pilas de desarrollo. Ver tabla completa en
> [Arquitectura](architecture.md).

## Instalación local (desarrollo) / Local Installation (development)

```bash
# 1. Clonar el repositorio
git clone https://gitlab.com/orpyca/orfeo_mcp.git
cd orfeo_mcp

# 2. Levantar la infraestructura completa
docker compose --profile dev up -d

# 2.bis (opcional) Dictado por voz dentro de la instalación: descarga un modelo
# de transcripción y consume CPU, por eso arranca apagado (ADR-020).
docker compose --profile assistant up -d whisper-service

# 3. Verificar que los servicios estén corriendo
docker compose ps
```

## URLs de acceso / Access URLs

| Servicio / Service | URL | Credenciales dev |
|---|---|---|
| API Gateway (docs) | http://localhost:19080/docs | — |
| Frontend (SvelteKit) | http://localhost:19300 | — |
| Keycloak Admin | http://localhost:19180/admin | admin / orpycamcp_dev |
| Almacén de objetos (API S3) | http://localhost:19900 | orpycamcp / orpycamcp_dev |

> El almacén de objetos es **RustFS** desde el ADR-027 (MinIO archivó su proyecto libre y
> retiró sus imágenes). **No tiene consola web** —el 19901 ya no se usa—: para mirar los
> buckets, `aws-cli` contra `http://localhost:19900`.

| PostgreSQL | localhost:15432 | orpycamcp / orpycamcp |
| MailHog (email dev) | http://localhost:8025 | — |

## Primer radicado / First Document Registration

```bash
# 1. Obtener token de acceso (vía el gateway, no directo al servicio)
TOKEN=$(curl -s -X POST 'http://localhost:19080/api/v1/auth/token' \
  -H 'Content-Type: application/json' \
  -d '{"username": "operador", "password": "orpycamcp_dev", "tenant_slug": "demo"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 2. Radicar un documento de entrada
curl -X POST 'http://localhost:19080/api/v1/documents' \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "subject": "Solicitud de información pública",
    "document_type": "entrada",
    "sender_name": "Ciudadano Juan Pérez",
    "sender_email": "juan@ejemplo.com"
  }'
```

La respuesta incluye el número de radicado asignado (ej: `2024-DEMO-E-000001`).

## Agregar un tenant (institución) / Add a tenant (institution)

El alta de una institución es una operación de plataforma: `/api/v1/tenants` **no se expone por el gateway**
(ver [Multi-tenancy](multi-tenancy.md)). Se hace con el script de provisión:

```bash
./scripts/init-tenant.sh mi-entidad "Mi Entidad Pública" MENT
```

Esto aprovisiona el schema `tenant_mi_entidad` en PostgreSQL (el slug admite minúsculas, dígitos y guion;
el guion pasa a guion bajo en el schema), aplica las migraciones y activa la entidad.

## Cargar la TRD inicial (seed) / Load initial TRD (seed)

OrpycaMCP incluye un script reutilizable para sembrar la Tabla de Retención Documental (TRD) de un tenant a partir de un JSON. El proyecto trae como referencia la TRD de FondeCund (150+ series).

**1. Exportar la TRD de tu institución a un JSON compatible:**

```json
{
  "entidad": "Mi Entidad Pública",
  "version": "2024-01-01",
  "dependencias": [
    {
      "nombre": "Departamento",
      "series": [
        {
          "codigo": 100,
          "nombre": "SERIE DOCUMENTAL",
          "retencion_gestion": 5,
          "retencion_central": 10,
          "disposicion_final": "Conservación total",
          "subseries": []
        }
      ]
    }
  ]
}
```

Las disposiciones finales en español se mapean a los códigos de OrpycaMCP (`conserve`, `eliminate`, `transfer`).

**2. Generar el SQL de migración para tu tenant:**

```bash
python3 scripts/migrate_trd_from_fondecund.py \
  mi_entidad_trd.json \
  --tenant-slug mi_entidad \
  --output services/archive-service/migrations/003_trd_mi_entidad.sql
```

**3. Aplicar la migración en el schema del tenant:**

```bash
docker compose exec postgres psql -U orpycamcp -d orpycamcp_db \
  -c "SET search_path TO tenant_mi_entidad;" \
  -f services/archive-service/migrations/003_trd_mi_entidad.sql
```

**Retención y dependencia productora (C-4).** El script exige `retencion_gestion` **y** `retencion_central` en el JSON de origen (se detiene nombrando la serie que no los traiga: no inventa tiempos de retención). Una serie sembrada así queda `migrada` o `borrador` y **sin dependencia productora**; para que gobierne y aparezca en el cuadro de clasificación, abra una versión nueva con `dependencias_productoras` (`POST /api/v1/trd/{code}/versiones`), apruébela y conválidela.

## El cuadro de clasificación documental (C-4)

El cuadro **no se captura**: se deriva del organigrama y de la TRD. Pasos para tenerlo:

1. **Organigrama** (`/admin/dependencias`): cree las dependencias con su jerarquía (la raíz es una *sección*, cada nivel inferior una *subsección*). El organigrama no admite ciclos (`409 dependencia_ciclo`).
2. **Nombre de la entidad** (`/admin/parametros`, `entidad_nombre`): es el *fondo*. Si está vacío el cuadro lo avisa en vez de inventarlo.
3. **TRD** (`/admin/trd`): al crear una serie declare sus **dos fases de retención** (obligatorias; el total lo calcula el sistema) y su(s) **dependencia(s) productora(s)**. Sin productora la serie no se puede aprobar ni convalidar (`409 trd_serie_sin_productora`). Para añadir o cambiar la productora de una serie ya convalidada hay que abrir una versión nueva.
4. **Consulta**: la pestaña «Cuadro de clasificación» de `/admin/trd` (o `GET /api/v1/trd/cuadro`) muestra fondo > sección > subsección > serie > subserie, los **avisos** (productora inactiva, organigrama reorganizado desde que se convalidó) y las series que no se pueden ubicar.

**Publicar de oficio** (Ley 1712, art. 12) exige ahora que el *tipo documental* del radicado esté marcado **«Publicable»** en `/admin/trd` → Tipos documentales (requiere TRD nivel 3). Es una marca distinta de «Admite ventanilla pública»: recibir por la ventanilla no implica difundir.

## Más ejemplos de uso end-to-end

Estos ejemplos asumen que ya tienes `$TOKEN` del paso anterior. El contrato completo
de cada endpoint está en [Referencia de API](api.md); el detalle de negocio detrás de
cada entidad está en [Dominio documental](domain.md).

### Crear un expediente y vincular un radicado

```bash
# 1. Crear el expediente (requiere TRD configurada para la serie)
curl -X POST 'http://localhost:19080/api/v1/expedientes' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"nombre": "Contrato 2024-001 — Suministro de papelería", "serie_id": "uuid-serie"}'
# Respuesta: { "id": "uuid-expediente", "estado": "open", ... }

# 2. Vincular el radicado creado antes
curl -X POST 'http://localhost:19080/api/v1/expedientes/uuid-expediente/radicados' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"radicado_id": "uuid-radicado"}'

# 3. Cerrar el expediente (genera y firma el índice electrónico, ver Ciclo de vida)
curl -X PATCH 'http://localhost:19080/api/v1/expedientes/uuid-expediente' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"estado": "closed"}'
```

### Firmar electrónicamente un radicado

**No hay un «firmar» de un paso.** Firmar es resolver un turno de un circuito, y el
circuito se abre primero — incluso cuando firma una sola persona. Es deliberado: la
firma se ancla a la huella del texto vigente, así que tiene que existir el turno que
dice *qué* se firmó y *quién* lo tenía asignado.

```bash
# 1. Abrir el circuito sobre el radicado (Salida o Interno: se firma lo que la
#    entidad produce; una Entrada la firmó quien la envió)
curl -X POST 'http://localhost:19080/api/v1/signature/requests' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"objeto_tipo": "radicado", "objeto_id": "uuid-radicado",
       "titulo": "Firma del oficio de respuesta",
       "firmantes": ["uuid-firmante"]}'
# Respuesta: { "solicitud_id": "uuid", "estado": "pendiente", "items": [ … ] }

# 2. Pedir el segundo factor del turno propio (OTP por correo o TOTP)
curl -X POST 'http://localhost:19080/api/v1/signature/requests/uuid-solicitud/challenge' \
  -H "Authorization: Bearer $TOKEN"

# 3. Firmar el turno con el código recibido
curl -X POST 'http://localhost:19080/api/v1/signature/requests/uuid-solicitud/sign' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"totp_code": "123456"}'
```

Requiere `PERM_FIRMA`. Si el texto cambió después de abrirse el circuito, el paso 3
responde `409 documento_modificado` en vez de firmar otra cosa.

### Abrir una cadena de firmas sobre un expediente

Varias personas firman **en orden**: el turno 2 no se abre hasta que el 1 esté
resuelto (RF-FIR-04). Cada firmante lo ve en su bandeja `/firmas` cuando le toca.

```bash
curl -X POST 'http://localhost:19080/api/v1/signature/requests' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"objeto_tipo": "expediente", "objeto_id": "uuid-expediente",
       "titulo": "Cierre del expediente",
       "firmantes": ["uuid-primero", "uuid-segundo"]}'
# Respuesta: { "solicitud_id": "uuid", "estado": "pendiente", "items": [ … ] }
```

Desde la interfaz, el mismo acto está en el botón **«Cadena de firmas»**, en la
ficha del expediente (`/expedientes/{id}`, con `USUA_PERM_EXPEDIENTE` +
`PERM_FIRMA`) y en el cajón de un radicado en `/bandeja` (con `PERM_RADI` +
`PERM_FIRMA`). Sobre un radicado solo se ofrece si es **Salida o Interno**: una
cadena de firmas se abre sobre lo que la entidad produce, y una Entrada la firmó
quien la envió. Si el expediente ya tiene una cadena abierta, la pantalla la
muestra y no deja abrir otra: hay que resolverla o rechazarla antes.

> `objeto_tipo` admite `expediente`, `radicado` y `borrador`. Los borradores en
> redacción tienen su propio circuito —visto bueno **y** firma— en
> `/borradores`, porque allí el texto todavía se puede editar.

> **Radicar un borrador**: al confirmar, la pantalla pide el **tipo documental** (y, en una Salida, la **dependencia de origen** entre las suyas activas) más los metadatos propios del tipo; sin una dependencia activa asignada no se ofrece «Radicar». Los metadatos guardados se leen luego en el detalle de `/bandeja` y en la ficha del expediente.

### Consultar al asistente conversacional

> **El asistente y la búsqueda semántica son opt-in por entidad (ADR-037).** Las entidades nuevas nacen con el asistente **apagado**: el administrador de la entidad (`USUA_PERM_ADMIN` nivel 3) lo enciende en **/admin/parametros** (o con `PUT /api/v1/config/asistente {"habilitado": true}`); sin eso, todo lo de abajo responde `403 asistente_deshabilitado`. Además, cada persona necesita el permiso **`PERM_CONOCIMIENTO`** (nivel 1 consulta; nivel 2 también acepta una sugerencia), que se reparte por grupo en **/admin/grupos**. Las entidades que ya existían al migrar quedaron encendidas y sus grupos con el permiso. Apagarlo no detiene la indexación: lo radicado mientras estuvo apagado ya está indexado al reactivar.

```bash
curl -X POST 'http://localhost:19080/api/v1/assistant/chat' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"message": "¿Qué radicados de entrada tengo pendientes esta semana?"}'
# Respuesta: texto en lenguaje natural con citas a los radicados consultados (RAG, solo lectura)
```

### Radicar por la ventanilla pública (verificación automática, C16)

La radicación sin autenticar (`POST /api/v1/public/{tenant}/ventanilla` y la ruta heredada `/pqrs`) exige una **prueba de trabajo ALTCHA** además del honeypot y del límite de tasa: el navegador resuelve solo, en unos segundos, un reto que entrega el servidor; la persona no resuelve nada. Desde un script:

```bash
RETO=$(curl -s http://localhost:19080/api/v1/public/demo/ventanilla/reto)
ALTCHA=$(printf '%s' "$RETO" | python3 scripts/lib/altcha_solver.py)   # un reto vale UNA vez
# `altcha` va dentro del JSON de la parte `datos` del multipart, junto a sitio_web
```

Un reto usado, vencido o de otra entidad se rechaza (`409 antirrobots_usado`, `422 antirrobots_vencido|antirrobots_invalido`): pida otro. `ALTCHA_HMAC_KEY` (≥ 32 bytes, `openssl rand -hex 32`) es obligatoria; en desarrollo `docker-compose.yml` trae una de ejemplo. `scripts/verificar-ventanilla.sh` comprueba también que el reto se emita.

Quien se identifica con documento deja una **ficha provisional**, no se asocia a una ficha existente por número. En **/admin/terceros → «Fichas por conciliar»** (permiso `PERM_RADI` nivel 3) se vincula a la ficha existente, se confirma como ficha nueva o se descarta con motivo (ver [Dominio](domain.md)).

### Desclasificar un radicado clasificado (segunda persona) y publicar el índice

Un radicado **clasificado** (nivel 3, Ley 1712 art. 18) no se baja con un `PATCH`: responde `409 desclasificacion_requiere_aprobacion`. Una persona **solicita**, otra con `PERM_RECLASIFICAR` nivel 4 **aprueba**, y al aprobar el nivel baja en el acto:

```bash
# 1) Quien solicita (PERM_RECLASIFICAR nivel 3)
curl -X POST "$API/api/v1/documents/$ID/desclasificacion" -H "Authorization: Bearer $TOKEN_A" \
  -H 'Content-Type: application/json' \
  -d '{"nivel_seguridad": 1, "motivo": "Cesó la causa de la clasificación"}'
# 2) Quien aprueba (otra persona, PERM_RECLASIFICAR nivel 4): ve la cola, abre el radicado y decide
curl "$API/api/v1/desclasificaciones" -H "Authorization: Bearer $TOKEN_B"
curl -X POST "$API/api/v1/desclasificaciones/$SOLICITUD/aprobar" -H "Authorization: Bearer $TOKEN_B"
```

Si quien aprueba es quien solicitó, `409 separacion_de_funciones`; la solicitud caduca a los 15 días. En el demo, `scripts/seed_demo.py` concede `PERM_RECLASIFICAR` nivel 4 a los grupos de gestión: sin ningún perfil con nivel 4 nadie podría aprobar. Al reservar o clasificar hay que elegir una **causal del catálogo cerrado** (`GET /api/v1/documents/causales-reserva`); la reservada (art. 19) lleva además un plazo de 1 a 180 meses.

El **índice público de información clasificada y reservada por serie** (obligación del art. 20) se consulta sin autenticar: `curl http://localhost:19080/api/v1/public/archive/indice-clasificados/demo` (o `….csv`). No lleva números de radicado ni conteos.

### Prestar una unidad de archivo físico, fijar una retención legal y registrar la valoración de una TVD (C-5)

1. **Préstamo**: en `/archivo-fisico`, «Registrar préstamo» pide el **solicitante**: *Interno* (se busca entre los usuarios que pueden recibir esa unidad) o *Externo* (nombre y documento obligatorios). La pantalla nombra la unidad cuando el préstamo se rechaza (ya prestada, en una transferencia o lote de eliminación, sin acceso del solicitante).
2. **Retención legal**: en el expediente, pestaña «Retenciones legales» (administración): fijar con motivo, fundamento y acta; levantar repitiendo el motivo. La `valoracion_pendiente` se ve pero no se opera.
3. **Valoración**: en `/admin/tvd`, con la TVD aprobada o convalidada, «Valoración del Comité» registra el acta y libera los expedientes del fondo vencido (pendientes, aplicadas y agotadas se ven ahí).
4. **Lote de eliminación**: al proponerlo, la pantalla lista las constancias que perderían su soporte; un anexo citado por una constancia vigente no se puede borrar (`anexo_citado_por_constancia`).


## La interfaz web: primeros pasos

Con la plataforma levantada, el frontend queda en <http://localhost:19300>.

### 1. La landing pública (`/`)

La raíz del sitio es una **página pública** que no requiere sesión: describe qué es
OrpycaMCP, el marco normativo que implementa (Ley 594/2000, Acuerdo AGN 060/2001), sus
capacidades y su linaje respecto a Orfeo, y termina en la licencia AGPL v3. Es la puerta
de entrada del producto y el único lugar del sitio donde se usa el logotipo completo.

Si el navegador ya tiene una sesión activa, `/` **redirige automáticamente (302) a
`/dashboard`**: un usuario autenticado nunca ve la landing. Para volver a verla, cerrar
sesión desde el menú de usuario.

Desde la landing, «Iniciar sesión» lleva a `/login`, que arranca el flujo OAuth2
Authorization Code + PKCE contra Keycloak (el intercambio del código y la custodia del
token ocurren **server-side**; el JWT vive en una cookie `httpOnly` y nunca es visible al
JavaScript del navegador).

### 2. El panel personal (`/dashboard`)

Tras iniciar sesión, el panel abre con **cuatro módulos personales** —lo que le toca
hacer a *este* usuario, no las cifras del tenant:

| Módulo | Qué muestra |
|---|---|
| 01 Radicados pendientes | Los pasos de trámite asignados a la persona o a su dependencia — la **misma** consulta que el tablero de `/bandeja` (`GET /workflows/inbox`), así que los dos cuentan igual |
| 02 Actividad reciente | Últimos radicados sobre los que hubo movimiento |
| 03 Alertas | Radicados abiertos —ni cerrados ni anulados, **incluidos los recién radicados**— cuya fecha de vencimiento (`due_date`, RF-RAD-04) está próxima o vencida, el más vencido primero |
| 04 Favoritos | Los destinos de navegación que el usuario haya fijado en el sidebar |

Las **métricas agregadas del tenant** (radicados por estado, top dependencias, actividad
mensual) siguen disponibles, pero en una sección secundaria colapsable, cerrada por
defecto, y **solo si el usuario tiene el permiso `SGD_PERM_ESTADISTICA`** — el mismo que
habilita la vista `/reportes`.

### Entidad suspendida o no lista

Si la entidad está suspendida, aún no está aprovisionada o no se pudo comprobar su estado, la sesión muestra una pantalla propia («La entidad está suspendida», «La entidad aún no está lista», «No se pudo comprobar el estado de la entidad») con **Reintentar** (cuando el estado puede cambiar) y **Cerrar sesión**. En la ventanilla pública (`/radicacion`, `/eliminacion`) esos mismos casos se ven como «Esta entidad no tiene ventanilla disponible».

### 3. Búsqueda global desde cualquier pantalla

La barra superior lleva un campo de **búsqueda global** disponible en todas las pantallas
autenticadas. Se puede enfocar con el atajo de teclado <kbd>/</kbd> (el atajo se inhibe
si el foco ya está dentro de un campo de texto, para no interferir con la escritura). En
pantallas estrechas el campo se despliega desde un icono de lupa.

Al enviar, la búsqueda navega a `/busqueda?q=<término>`, que es la misma vista de
búsqueda full-text (FTS de PostgreSQL, `GET /api/v1/search` a través del gateway) con sus
filtros avanzados. Como el término viaja en la URL, cualquier búsqueda es **enlazable y
compartible**, y el botón "atrás" del navegador funciona como se espera.

### 4. Favoritos en la barra lateral

Cada entrada del menú lateral tiene un control de **fijar/desfijar**. Los ítems fijados
suben a una sección «Favoritos» al principio del menú y aparecen también en el módulo 04
del panel. Los favoritos se guardan en el navegador, separados por institución y usuario,
así que dos usuarios del mismo equipo no comparten lista.

Los favoritos **no eluden el RBAC**: si a un usuario se le retira un permiso, la entrada
correspondiente desaparece tanto del menú como de sus favoritos, sin necesidad de
limpiarla a mano. Como en todo el frontend, esto es defensa en profundidad: el backend
revalida permiso y clearance en cada petición.

Ver también: [Pantallas del sistema](pantallas.md) para la contraparte de cada uno de
estos ejemplos en la interfaz web.


