-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Dos permisos otorgables que NO limitan nada, y su descripción decía que sí.
--
-- `PERM_RADI_ENTRADA` y `PERM_RADI_INTERNO` se siembran desde la migración 004
-- con las descripciones «Radicar documentos de Entrada» y «Radicar documentos
-- Internos». Ningún gate del sistema los consulta: los tres tipos de radicado
-- exigen `PERM_RADI` y nada más (verificado: 0 apariciones en
-- `require_permission` de cualquier router). Una entidad puede concederlos
-- creyendo que restringe a un funcionario a radicar solo entradas, y no
-- restringe absolutamente nada.
--
-- Lo que se corrige aquí es **la promesa**, no el comportamiento. Las dos
-- alternativas reales son decisiones de producto, no arreglos, y ninguna es
-- inocua:
--
--   * APLICARLOS es una funcionalidad nueva, no un parche. Y choca con la
--     realidad del tercer hermano: `PERM_RADI_SALIDA` sí se usa —en los 6
--     endpoints de envíos postales—, o sea que hoy significa «gestionar
--     envíos», no «radicar salidas». Exigirlo al radicar una Salida cambiaría
--     su significado para quien ya lo tiene concedido.
--   * RETIRARLOS es un borrado de datos: si una entidad ya los concedió a un
--     grupo, eliminar la fila arrastra su concesión.
--
-- Mientras esa decisión no se tome, lo que NO puede quedarse es un catálogo de
-- permisos que le dice a un administrador que está repartiendo un control que
-- el sistema no ejerce. El texto es lo que esa persona lee en la pantalla de
-- grupos antes de conceder, así que el texto es el sitio donde decirlo.
--
-- Cambio no destructivo: solo `descripcion`. Ninguna concesión se toca.

UPDATE auth_permissions
   SET descripcion = 'RESERVADO — hoy NO restringe nada: los tres tipos de radicado '
                     'exigen únicamente PERM_RADI. Concederlo no limita a Entradas.'
 WHERE nombre = 'PERM_RADI_ENTRADA';

UPDATE auth_permissions
   SET descripcion = 'RESERVADO — hoy NO restringe nada: los tres tipos de radicado '
                     'exigen únicamente PERM_RADI. Concederlo no limita a Internos.'
 WHERE nombre = 'PERM_RADI_INTERNO';
