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

### Qué no cubre este resumen

Por su naturaleza, esta página no detalla los vectores concretos. El informe completo,
con la ubicación y el ataque de cada hallazgo, es material interno y no se publica.

<!-- TODO: translate to English -->
