-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Esquema de publicación para EXPEDIENTES — Ley 1712/2014 art. 12.
-- Espejo de `document-service tenant/019_radicado_publicable.sql`.
--
-- ── Por qué esta migración existe ────────────────────────────────────────────
-- La 019 corrigió la premisa para radicados: «público» dejaba de deducirse de la
-- ausencia de reserva y pasaba a exigir un acto de la entidad. Pero el acto se
-- aplicó a UN canal de tres. Este servicio conservó
-- `oai_repository._PUBLIC = "nivel_seguridad = 1"`, de modo que el OAI EAD
-- multinivel seguía difundiendo de oficio el código, el nombre, la descripción,
-- las fechas extremas y el conteo de radicados de **todo expediente no
-- clasificado** — que es exactamente el valor por defecto de la columna.
--
-- Lo señalaron por separado los dos auditores (seguridad y conformidad) de la
-- revisión de 2026-08-13. La asimetría era además el peor tipo de fallo de esta
-- familia: implícita. Quien leyera el `_PUBLIC` de document-service supondría,
-- con toda razón, que archive-service hace lo mismo.
--
-- ── El diseño, y qué NO se copia ─────────────────────────────────────────────
-- `DEFAULT FALSE`: ningún expediente existente ni futuro se difunde por no
-- haberse dicho nada. El quién y el cuándo se guardan aquí para poder responder
-- «desde cuándo es público este expediente»; el porqué vive en `audit_log`.
--
-- A diferencia de los radicados, el recorte NO incluye una condición de estado.
-- Un radicado anulado se retira de la circulación y por eso `status <> 'anulado'`
-- entra en su recorte; un expediente `closed` o `transferred` no está retirado —
-- está cerrado o transferido, que son estados normales de su ciclo de vida y en
-- los que la difusión sigue siendo legítima. Copiar la condición por simetría
-- habría despublicado en silencio todo expediente cerrado.

ALTER TABLE expedientes
    ADD COLUMN IF NOT EXISTS publicable    BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS publicado_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS publicado_por UUID;

COMMENT ON COLUMN expedientes.publicable IS
    'Esquema de publicación (Ley 1712/2014 art. 12): acto EXPLÍCITO por el que la '
    'entidad difunde el expediente de oficio (OAI-PMH EAD multinivel). FALSE por '
    'defecto — la ausencia de reserva no equivale a una decisión de publicar. '
    'Condición NECESARIA pero no suficiente: el recorte exige además '
    'nivel_seguridad = 1.';

-- Índice parcial alineado con el recorte público, como el de radicados: las
-- consultas de cosecha filtran siempre por este conjunto, que es una fracción
-- pequeña del total.
CREATE INDEX IF NOT EXISTS ix_expedientes_publicables
    ON expedientes (opened_at)
    WHERE publicable AND nivel_seguridad = 1;
