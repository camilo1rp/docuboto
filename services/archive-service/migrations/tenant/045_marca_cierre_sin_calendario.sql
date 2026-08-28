-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E04, RF-TRD-09, 2026-08-28): recoge los expedientes que
-- se cerraron SIN calendario de retención entre la migración 037 y hoy.
--
-- La 037 hizo exactamente este mismo UPDATE, y funcionó. Lo que no había era
-- nada que escribiera `cierre_sin_snapshot_de_retencion` en el camino normal
-- de cierre: el motivo existía en el modelo, la tercera pestaña de
-- `/admin/trd` sabía presentarlo y explicaba que no tiene resolución
-- automática, y **nunca le llegaba ninguno**. Así que cada expediente cerrado
-- sin serie desde entonces volvió a caer en el mismo agujero: `disposition`
-- NULL, sin marca, y por tanto fuera de toda cola — ni la de revisión, ni la
-- de vencimientos que vendrá después.
--
-- El código ya está corregido (`ArchiveService.close_expediente` marca, y
-- `transition_expediente` escribe las marcas al CERRAR en vez de solo cuando
-- fija el pin de la serie, que era justo el caso contrario). Esta migración
-- solo barre lo que se coló mientras tanto: **reparar el pasado y proteger el
-- futuro son dos actos distintos, y hacer solo uno de los dos ya salió caro**
-- una vez aquí.
--
-- Idempotente por el `AND NOT trd_revision_requerida`: reejecutarla no pisa
-- ninguna marca existente ni inventa ningún calendario (la 037 tampoco lo
-- hacía, y por el mismo motivo — un calendario no se deduce a posteriori: hay
-- que decidirlo clasificando el expediente).

UPDATE expedientes
   SET trd_revision_requerida = true,
       trd_revision_motivo    = 'cierre_sin_snapshot_de_retencion'
 WHERE status IN ('closed', 'transferred')
   AND disposition IS NULL
   AND NOT trd_revision_requerida;
