-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (11.1): esquema de publicación — Ley 1712/2014 art. 12.
--
-- ── El problema ──────────────────────────────────────────────────────────────
-- Hasta ahora, «público» se DEDUCÍA: un radicado con `nivel_seguridad = 1` y no
-- anulado era cosechable por OAI-PMH y servible por CMIS. Es decir, la ausencia
-- de una decisión de reserva se interpretaba como una decisión de publicación.
--
-- No son lo mismo, ni jurídica ni operativamente. El art. 12 de la Ley 1712
-- exige que el sujeto obligado adopte un **esquema de publicación**: un acto
-- por el que la entidad decide qué información difunde de oficio. Deducirlo del
-- nivel de clasificación significa que un documento se publica por omisión —
-- por no haberse clasificado todavía, por haberse creado con el default, o por
-- una desclasificación hecha con otro propósito.
--
-- El bloque 2 puso un kill-switch de canal (`ENABLE_PUBLIC_INTEROP`) que apaga
-- la interoperabilidad pública entera. Eso ataja la urgencia pero no distingue:
-- o todo o nada. Esta columna es el control fino que faltaba.
--
-- ── El diseño ────────────────────────────────────────────────────────────────
-- `publicable` es un acto POSITIVO y explícito, con `DEFAULT FALSE`: ningún
-- documento existente ni futuro se publica por no haber dicho nada. Se combina
-- con el filtro de clasificación (no lo sustituye — ver `oai_repository._PUBLIC`):
-- publicar algo reservado sigue siendo imposible aunque alguien marque la
-- casilla, porque ambas condiciones tienen que cumplirse.
--
-- El quién y el cuándo se guardan aquí para poder responder «desde cuándo es
-- pública esta información» sin recorrer la cadena de auditoría; el POR QUÉ y la
-- reversión viven en `audit_log`, que es el registro con valor probatorio.

ALTER TABLE radicados
    ADD COLUMN IF NOT EXISTS publicable    BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS publicado_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS publicado_por UUID;

COMMENT ON COLUMN radicados.publicable IS
    'Esquema de publicación (Ley 1712/2014 art. 12): acto EXPLÍCITO por el que la '
    'entidad difunde el documento de oficio. FALSE por defecto — la ausencia de '
    'reserva no equivale a una decisión de publicar. Condición NECESARIA pero no '
    'suficiente: el recorte público exige además nivel_seguridad = 1 y no anulado.';

-- Índice parcial: las consultas públicas (OAI-PMH, CMIS, portal) filtran siempre
-- por este conjunto, que es una fracción pequeña del total de radicados.
CREATE INDEX IF NOT EXISTS ix_radicados_publicables
    ON radicados (registered_at)
    WHERE publicable AND nivel_seguridad = 1 AND status <> 'anulado';
