# Auditoría de Seguridad

Esta página registra los hallazgos de la auditoría semanal de dependencias (job `dependency-audit` en CI).

## Estado actual

Sin vulnerabilidades altas o críticas registradas.

> Esta página se actualiza automáticamente vía Merge Request cuando la auditoría programada detecta vulnerabilidades HIGH o CRITICAL en las dependencias Python del proyecto.

## Auditoría de segunda pasada (septiembre de 2026)

Además de la revisión automática de dependencias, en septiembre de 2026 se hizo una
auditoría manual de segunda pasada sobre catorce ejes que las revisiones anteriores no
cubrían. Las doce auditorías previas se habían concentrado en el control de acceso por
endpoint y en la integridad del registro de auditoría; esta miró el perímetro, la
confianza entre servicios, el aislamiento a nivel de datos, la superficie pública, las
subidas de archivos, la criptografía, las salidas de red, el frontend, los analizadores
de documentos, la mensajería interna, el asistente, la infraestructura, los registros y
la lógica de negocio.

### Método

Cada hallazgo pasó por revisores independientes cuyo cometido era **refutarlo**, y solo
se registra el que sobrevivió a esa revisión. Cada corrección se valida restaurando el
defecto y comprobando que la prueba correspondiente falla: una prueba que no distingue
el código correcto del defectuoso no cubre nada.

### Resultado

Se confirmaron 81 hallazgos: 6 de severidad alta, 30 media y 45 baja. Se refutaron 42.
Todos pasaron por revisión adversarial; ninguno queda sin verificar. **Los 81 están
corregidos**: los seis de severidad alta, los treinta medios y los cuarenta y cinco bajos.

Las áreas con más hallazgos fueron la infraestructura de despliegue, la superficie
pública sin autenticar y la lógica de negocio del circuito de firma. Cuatro hallazgos
afectan solo al entorno de desarrollo y no a la configuración de producción.

### Cambio de comportamiento en la ventanilla ciudadana

La ruta heredada de radicación de PQRS pasa ahora por la ventanilla única, como su
propio contrato ya declaraba. Eso la sujeta a las decisiones de la entidad: el tipo
documental debe estar declarado admisible y debe existir una dependencia que reciba lo
presentado desde fuera.

**Antes de actualizar**, cada entidad debe verificar que tiene declarado al menos un
tipo documental admisible en ventanilla y una dependencia receptora. Sin ellos, la ruta
responderá «ventanilla no habilitada» en lugar de admitir la radicación. Es el
comportamiento correcto —hasta ahora esa puerta admitía documentos en entidades que no
habían habilitado la radicación en línea, dejándolos fuera de la bandeja de toda
dependencia— pero es un cambio visible.

### Cierre de la auditoría del 2 de octubre de 2026 (tanda T1)

El sello institucional de la entidad y el bloqueo de inmutabilidad (WORM) dejaron de ser
alcanzables con la sesión de un usuario: ahora solo los aplica el servicio de archivo, con
credencial interna. Antes, cualquier usuario con permiso de firma podía estampar el sello de
la entidad sobre un documento arbitrario, y uno con permiso de expedientes podía ocupar el
bloqueo irreversible de un expediente ajeno o fijar una retención por litigio sin acto que
la levantara. La retención por litigio ahora se declara y se levanta por un acto propio, con
motivo obligatorio y asiento de auditoría.

### Cierre de la auditoría del 2 de octubre de 2026 (tanda T2)

Administrar usuarios, grupos y permisos ya no se puede hacer con el permiso de solo lectura:
quien lo recibía únicamente para consultar la auditoría podía crear usuarios y concederse
permisos. Tampoco se puede entrar en un grupo con más nivel de acceso a documentos reservados
que el propio. El token de acceso se valida con más rigor (tipo y cliente emisor), la caché de
validación del gateway quedó sellada e indica cuánto le queda al token, la verificación de la
cadena de auditoría deja de revelar a un perfil sin habilitación cuántos asientos clasificados
existen, y quitar el segundo factor a otra persona queda siempre asentado en la misma operación.

### Qué no cubre este resumen

Por su naturaleza, esta página no detalla los vectores concretos. El informe completo,
con la ubicación y el ataque de cada hallazgo, es material interno y no se publica.

<!-- TODO: translate to English -->
