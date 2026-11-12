-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E10 Increment A cierre — Inc.1, cableado WORM del
-- índice electrónico desde archive-service): cierra el hueco de idempotencia
-- de `POST /proteger-indice`.
--
-- Hoy la tabla no guarda `file_id` (solo el `object_key`/`bucket` YA
-- resueltos) ni tiene ninguna restricción que impida dos filas para el MISMO
-- expediente. Con el disparo automático (síncrono al cierre + barrida de
-- reconciliación), dos llamadas al mismo `expediente_id` — intento síncrono
-- que "cuelga" y luego reintenta, o carrera entre el intento síncrono y una
-- barrida concurrente — crearían DOS objetos WORM COMPLIANCE distintos para
-- el mismo índice: irreversibles, sin forma de deshacer el duplicado.
--
-- `file_id` se añade para que la fila registre TAMBIÉN la clave de negocio
-- (antes solo la resolución física bucket/object_key). El respaldo de
-- idempotencia en BD es un ÍNDICE ÚNICO PARCIAL, no un `UNIQUE(expediente_id,
-- tipo)` liso: ese constraint amplio también alcanzaría a `tipo='aip'`
-- (Increment B), donde SÍ es legítimo re-empaquetar el AIP de un expediente
-- más de una vez a lo largo del tiempo (nuevo documento incorporado, nueva
-- migración de formato) — nada en Increment B asume o prueba "un solo AIP
-- por expediente para siempre". El índice electrónico, en cambio, es un
-- SINGLETON por expediente (append-only, una sola versión llega a
-- `estado='firmado'`, ver `expediente_indice` en archive-service) — de ahí
-- que el respaldo se acote a `tipo='indice'`.
--
-- Aditiva/idempotente: `ADD COLUMN IF NOT EXISTS` + índice único parcial
-- creado solo si no existe ya.

ALTER TABLE preservacion_worm_objeto
    ADD COLUMN IF NOT EXISTS file_id UUID;

COMMENT ON COLUMN preservacion_worm_objeto.file_id IS
    'file_id de `files` cuyo contenido quedó protegido bajo WORM (Inc.1, cableado) — antes solo se registraba la resolución física bucket/object_key, sin la clave de negocio.';

CREATE UNIQUE INDEX IF NOT EXISTS ux_preservacion_worm_objeto_expediente_indice
    ON preservacion_worm_objeto (expediente_id)
    WHERE tipo = 'indice';

-- Rollback (manual, no automatizado por el runner):
-- DROP INDEX IF EXISTS ux_preservacion_worm_objeto_expediente_indice;
-- ALTER TABLE preservacion_worm_objeto DROP COLUMN IF EXISTS file_id;
