-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E12, H-K/H-J — conformidad Ac. AGN 001/2024 Tít. 4.4):
--
-- H-K: el rechazo de una transferencia es una decisión de fondo (estado
-- TERMINAL) y su motivo es el insumo de la subsanación -- antes se admitía
-- rechazo INMOTIVADO y el motivo, cuando llegaba, PISABA `observacion` (el
-- dato de preparación). `motivo_rechazo` es una columna propia: NULLable en
-- BD (retro-compat con filas históricas y transferencias no-rechazadas) y
-- obligatoria en la capa API (`TransferenciaRechazo.motivo`, min_length),
-- mismo patrón NULLable-DB/required-API ya usado en el proyecto.
--
-- H-J: `rechazada_at` completa el sello temporal propio del acto -- gemela de
-- `enviada_at`/`recibida_at` (012_transferencias.sql); antes el rechazo solo
-- quedaba fechado por `audit_log.created_at` (Anexo FUID Ac. 001/2024, fecha
-- del acto).

ALTER TABLE transferencias ADD COLUMN IF NOT EXISTS motivo_rechazo TEXT;
ALTER TABLE transferencias ADD COLUMN IF NOT EXISTS rechazada_at   TIMESTAMPTZ;
