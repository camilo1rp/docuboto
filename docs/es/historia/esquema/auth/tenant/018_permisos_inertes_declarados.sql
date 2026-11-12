-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- SIETE permisos otorgables que no restringen nada, dicho en su descripción.
--
-- Barrido inverso del guardián de permisos (2026-08-17): de los 26 permisos
-- sembrados, **nueve** no los exige ningún gate ni ninguna fila. De esos nueve:
--
--   * `USUA_PERM_ROOT` es legítimo — no se comprueba con `require_permission`
--     sino por `is_root`, que es el bypass. No entra aquí.
--   * `SGD_PERM_ESTADISTICA` era un hueco REAL y se cerró: la interfaz lo
--     honraba (menú «Reportes») y el backend no. Ya lo exigen
--     `GET /api/v1/reports/radicados` y su gemelo CSV. Tampoco entra aquí.
--   * Los SIETE restantes no tienen ni una sola referencia en todo el
--     repositorio. Son nombres heredados de Orfeo que nunca se cablearon.
--
-- Por qué se corrige la DESCRIPCIÓN y no se borran las filas:
--
--   1. `auth_group_permissions.permission_id` es `ON DELETE CASCADE`. Un
--      `DELETE` arrastraría en silencio las concesiones que una entidad ya haya
--      hecho — silencioso es precisamente lo que estamos quitando de en medio.
--   2. Varios corresponden a funciones que SÍ existen pero están gateadas con
--      un permiso más grueso: `nrr` va por `PERM_TRAMITAR`, la descarga del
--      expediente por `USUA_PERM_EXPEDIENTE`, la anulación por
--      `PERM_SOL_ANULAR`/`PERM_PANU_CODI`. Cablear cada uno a su permiso fino
--      es una decisión de producto por función, no un arreglo; borrarlos
--      cerraría esa puerta sin que nadie la hubiera decidido.
--
-- Lo que NO puede quedarse es un catálogo que le dice a un administrador que
-- está repartiendo un control que el sistema no ejerce. El texto es lo que esa
-- persona lee en la pantalla de grupos justo antes de conceder.
--
-- `shared/tests/test_permisos_existen.py` vigila que esta lista no caduque: si
-- alguien cablea uno de estos permisos, el test exige quitarlo de aquí; y si
-- aparece un permiso inerte nuevo, exige añadirlo. Una lista a mano solo es
-- segura cuando algo deriva la verdad y la compara.
--
-- Cambio no destructivo: solo `descripcion`. Ninguna concesión se toca.

UPDATE auth_permissions SET descripcion =
  'RESERVADO — hoy NO restringe nada. Aprobar una anulación exige PERM_PANU_CODI.'
 WHERE nombre = 'PERM_APRO_ANULAR';

UPDATE auth_permissions SET descripcion =
  'RESERVADO — hoy NO restringe nada: ningún gate del sistema lo consulta.'
 WHERE nombre = 'PERM_BORRAR_ANEXO';

UPDATE auth_permissions SET descripcion =
  'RESERVADO — hoy NO restringe nada. Descargar un expediente exige USUA_PERM_EXPEDIENTE.'
 WHERE nombre = 'PERM_DESCARGAEXP';

UPDATE auth_permissions SET descripcion =
  'RESERVADO — hoy NO restringe nada. La transacción «no requiere respuesta» exige PERM_TRAMITAR.'
 WHERE nombre = 'PERM_NRR';

UPDATE auth_permissions SET descripcion =
  'RESERVADO — hoy NO restringe nada: ningún gate del sistema lo consulta.'
 WHERE nombre = 'PERM_TIPIF_ANEXO';

-- Los dos de la migración 017 se reescriben aquí para que las siete
-- descripciones tengan la misma forma y se reconozcan de un vistazo como lo que
-- son. El texto de 017 decía lo mismo con otras palabras.
UPDATE auth_permissions SET descripcion =
  'RESERVADO — hoy NO restringe nada: los tres tipos de radicado exigen solo PERM_RADI.'
 WHERE nombre IN ('PERM_RADI_ENTRADA', 'PERM_RADI_INTERNO');

-- Y el que SÍ está cableado, cuyo nombre engaña: no gatea radicar Salidas, sino
-- los seis endpoints de envío postal (`document-service/app/routers/postal.py`).
-- Un administrador que lo conceda para «dejar radicar salidas» está concediendo
-- otra cosa, y por eso el trío no se puede aplicar por tipo sin rediseñar antes
-- este nombre.
UPDATE auth_permissions SET descripcion =
  'Gestionar ENVÍOS POSTALES de salida (no restringe radicar Salidas: eso exige solo PERM_RADI).'
 WHERE nombre = 'PERM_RADI_SALIDA';
