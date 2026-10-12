# ADR-034: La firma XAdES se declara como se aplica — XMLDSig conforme, autoverificación y entrada del ArchiveTimeStamp según EN 319 132-1

**Estado**: Aceptado
**Fecha**: 2026-10-11
**Autores**: Giampiero (mantenedor principal); triage B7
**Relacionados**: [ADR-016](ADR-016-firma-electronica.md) (firma electrónica; enmendado por este ADR), [ADR-030](ADR-030-constancia-firma-inobtenible.md) (constancia; sus huellas no cambian), ETSI EN 319 132-1 §5.5.2, W3C XML Signature Syntax and Processing §4.4.3.2

---

## Contexto

La verificación en vivo de B7 sometió lo que firmamos a **EU DSS** (el validador de referencia de la UE) y **todo** dio
`TOTAL_FAILED / HASH_FAILURE`: el sello del índice, las constancias, las actas y las firmas personales. Nuestro propio
`/verify` decía `valida=true`.

**Causa** (comprobada en el código de `signxml` 5.1.0, `XAdESSigner._add_reference_to_signed_info`): las referencias a
`xades:SignedProperties` y a `ds:KeyInfo` se crean **sin `ds:Transforms`** pero su digest se calcula con la canonicalización del
firmante (exclusiva). XMLDSig §4.4.3.2 dice que una referencia *same-document* sin `Transforms` se convierte a octetos con
**C14N 1.0 inclusiva**: un verificador conforme recalcula otro digest. El defecto era **nuestro** —declarábamos una cosa y
aplicábamos otra—, no «un bug de signxml», como decía el docstring de `local.py`. Y lo escondíamos: `verify_xades` pasaba un
`expect_config` con `default_reference_c14n_method=EXCLUSIVE`, que le decía a nuestro propio verificador «asume exclusiva», de modo
que lo firmado solo lo verificaba OrpycaMCP.

Una segunda divergencia, la misma familia: la entrada que sellábamos en el `xadesv141:ArchiveTimeStamp`
(`_archive_ts_covered_bytes`) era una concatenación propia (`SignedInfo ‖ SignatureValue ‖ KeyInfo ‖ SignedProperties ‖ sellos ‖ …`),
sin los octetos de las referencias y con `SignedProperties` donde la norma no lo pone. Ningún artefacto del corpus llevaba
ArchiveTimeStamp (CA de LT sin montar), así que DSS nunca lo evaluó; medido ahora, su `MESSAGE_IMPRINT` daba **no íntegro**.

## Decisión

### 1. Declarar los `Transforms` que se aplican y firmar el `SignedInfo` final

Alternativa A (cambiar a C14N 1.0 inclusiva): **descartada**. Cambia los digests, obliga a tocar tres `exclusive=True` fijos
(sello de tiempo, ArchiveTimeStamp, split de firma personal), el contrato de digest con auth-service, y la inclusiva arrastra los
namespaces del ancestro al digest: incrustar el XML firmado en otro (paquete AIP/SIP) rompe la firma. La exclusiva está pensada
para sobrevivir al reempaquetado.

Alternativa B (elegida), en `services/signature-service/app/providers/local.py`:

1. `XAdESSigner.sign(...)` (sello: con su clave; firma personal: clave efímera + certificado real, como antes).
2. `_declarar_transforms`: cada `ds:Reference` sin `ds:Transforms` recibe, como primer hijo, `Transform exc-c14n`. Los digests **no
   cambian** (ya eran exclusivos): se declara lo que se aplicó.
3. `_firmar_signed_info`, **un solo camino para los dos firmantes**: canonicaliza `ds:SignedInfo` con el algoritmo que ESE elemento
   declara (no un `exclusive=True` fijo) y firma **después** del parcheo. Sello: `PKCS1v15/SHA-256` con la clave en proceso;
   personal: `remote_sign(SHA-256(octetos))`. El digest que recibe auth-service sale del `SignedInfo` **final**; el contrato con
   auth no cambia (recibe un digest opaco), solo cambia el orden.
4. `_autoverificar`: la firma se verifica con `XAdESVerifier()` en su **configuración por defecto** antes de sellar el tiempo o
   persistir nada. Si falla, `XmldsigNoConformeError` → `503 firma_no_conforme_xmldsig`: nunca se persiste una firma que un tercero
   no podría comprobar. **Sin salvedades**: el `verification_time` es el por defecto (ahora). Una versión anterior de este ADR
   lo acotaba a la vigencia del certificado para que un sello caducado «firmara y se rechazara al verificar»; eso fue un error de
   diseño (el sistema emitía y archivaba en WORM firmas que nacían inverificables) y se retiró: la vigencia se exige ANTES de
   firmar (punto 4).

TSA, `CertificateValues`, `RevocationValues`, OCSP y `ArchiveTimeStamp` son propiedades **no firmadas** (fuera de `SignedInfo`) y
se añaden después, como antes.

**Dónde cae la autoverificación en la ruta personal (SEG-B2/ARC-B1).** `XmldsigNoConformeError` se traduce a `503
firma_no_conforme_xmldsig` también en `sign_personal_xml` (la comparten `/sign-personal` y la cadena), no solo en `/sign-xml`: antes
era un 500. **Limitación declarada**: la autoverificación necesita el `SignatureValue`, que solo produce auth-service al verificar y
consumir el TOTP (`remote_sign`); por eso en esa ruta el factor ya está consumido cuando la autoverificación falla. No se adelantó
(verificar antes con una clave efímera exigiría fabricar un certificado equivalente: más superficie que el riesgo que cubre, porque
tras exigir la vigencia antes de firmar, solo una regresión del propio código puede producir una firma no conforme). El firmante
reintenta con un código nuevo.

### 4. No se firma con un certificado fuera de vigencia (ARC-M2, SEG-B3)

`LocalSigner.sign` exige la vigencia del certificado **antes de hacer nada** (sin TSA, sin `remote_sign`):

- **Sello institucional** caducado o aún no vigente → `SelloInstitucionalVencidoError` → `503 sello_institucional_vencido`
  (`/sign-xml`, `/internal/sign-indice` y todo lo que firma con el sello: índices, actas, constancias, revocaciones). El llamador
  best-effort deja el artefacto `pendiente_firma`; no se archiva nada inverificable.
- **Firma personal** → `CertificadoPersonalVencidoError` → `422 certificado_personal_vencido`, **antes** de `remote_sign`: auth-service
  no verifica ni consume el TOTP ni toca el contador anti-replay.

Antes, firmar con un sello caducado funcionaba y solo `/verify` lo rechazaba después, con el artefacto ya en WORM.

### 5. `/verify` de un XAdES-LTA cuyo certificado caducó después de firmar (ARC-M1)

Regla: el certificado firmante se juzga a «ahora». **Única excepción**: si ya caducó y la firma lleva un `ArchiveTimeStamp` que
**valida** (nivel verificado `XAdES-LTA`), se juzga a la fecha `genTime` del `SignatureTimeStamp` —que `tsa.verify_timestamp_token`
ya exige dentro de la vigencia del certificado (AREA-04-08)—, y la respuesta lleva la advertencia
`certificado_vencido_vigencia_juzgada_al_sello_de_firma`. Las CA de la cadena se juzgan a la misma fecha en esa pasada.
Mecánica: una segunda pasada de `verify_xades` (`_vigencia_diferida`) que solo se acepta si el nivel verificado es LTA.

- **Sin LTA válido** (B, T, LT, o un `ArchiveTimeStamp` que no valida) se mantiene `certificado_fuera_de_vigencia`.
- Un documento alterado sigue siendo inválido: la comprobación criptográfica corre igual.
- Qué NO es: no renueva nada. **El `ArchiveTimeStamp` no se renueva** (EN 319 132-1 §5.5.3, `ArchiveTimeStamp` encadenado antes de que
  caduquen el algoritmo o la TSA): la prueba descansa en el primero. Deuda declarada.

### 6. La verificación aplica la canonicalización que el XML declara (ARC-B2)

El imprint del `SignatureTimeStamp` y la entrada del `ArchiveTimeStamp` se recalculan con el `ds:CanonicalizationMethod` que cada
sello **declara**, no con un exc-c14n fijo (`_c14n_declarado`, `_archive_ts_covered_bytes(root, algoritmo)`). Sin método declarado
o con uno no soportado el sello se rechaza (fail-closed). Hoy el firmante siempre declara exc-c14n, así que el comportamiento
observable no cambia; lo que cambia es que declarar y aplicar dejan de poder divergir en silencio.

### 2. `verify_xades` verifica solo conforme

Se elimina `_VERIFY_CONFIG` y el atajo. `verify_xades` llama a `XAdESVerifier().verify(root, x509_cert=…)` por defecto.
**No hay rama «legado», ni columna `perfil_xmldsig`, ni backfill, ni constancia de verificación**: todo lo anterior es corpus de
prueba (decisión del usuario, 2026-10-11). Las firmas anteriores a este ADR (referencias sin `Transforms`) **dejan de
verificar** —`firma_invalida`— y eso es lo correcto: nunca las habría verificado un tercero. Se regeneran si hace falta.

### 3. El `ArchiveTimeStamp` se calcula como dice EN 319 132-1 §5.5.2 (o no se declara LTA)

La opción «dejar de declarar LTA» era el plan B. No hizo falta: se probaron contra DSS ocho composiciones y exactamente una da
`MESSAGE_IMPRINT` íntegro, la de la norma. `_archive_ts_covered_bytes` concatena ahora:

1. los **octetos de cada `ds:Reference`** de `SignedInfo`, en su orden (documento sin la firma, `SignedProperties`, `KeyInfo`),
   tras sus `Transforms` (`_octetos_de_referencia`);
2. C14N de `ds:SignedInfo`, `ds:SignatureValue` y `ds:KeyInfo`;
3. C14N de cada propiedad no firmada **previa** al `ArchiveTimeStamp`, en orden de documento (`SignatureTimeStamp`,
   `CertificateValues`, `RevocationValues`, con `OCSPValues` dentro).

Build y verify comparten la misma función. Un sello construido con la composición antigua **ya no valida como LTA**
(`archive_timestamp_invalido`).

## Evidencia (EU DSS 6.5, en vivo contra la pila)

| Artefacto | Resultado |
|---|---|
| Índice de expediente sellado (XAdES-T, pila real) | `SignatureIntact=true`; referencias documento, `SignedProperties` y `KeyInfo` íntegras; sello de tiempo íntegro. `INDETERMINATE / NO_CERTIFICATE_CHAIN_FOUND` |
| Firma personal (JEFE, XAdES-T, pila real) | ídem |
| Sello XAdES-LTA (CA de pruebas local) | ídem, y el `ARCHIVE_TIMESTAMP` íntegro (antes no) |
| Firma anterior al arreglo (control negativo) | `TOTAL_FAILED / HASH_FAILURE` |

`INDETERMINATE / NO_CERTIFICATE_CHAIN_FOUND` es el resultado esperado: la CA de desarrollo no está en ninguna lista de confianza.
DSS **no** dice que la firma sea cualificada ni acreditada, y OrpycaMCP tampoco (`acreditado=false`, RF-FIR-15). Lo que cambia es que
la **criptografía** pasa en un validador independiente.

## Consecuencias

**Positivas**: lo que firmamos lo verifica un tercero con herramientas estándar (con la salvedad de la confianza del certificado);
`/verify` ya no esconde defectos de construcción; una regresión del parcheo o del orden (digest antes de parchear) hace fallar la
firma al emitirla, no al verificarla meses después.

**Negativas / límites**:

- Las firmas anteriores del corpus de prueba no verifican. En una instalación con datos reales haría falta una política de legado
  (rama de verificación rotulada «solo OrpycaMCP», constancia de verificación institucional): **no se implementó** porque no hay tal
  instalación (ver «Decisiones descartadas»).
- ADR-016 y ADR-030 afirmaban que la firma era verificable por terceros. Hasta este ADR **no lo era**; ambos llevan ahora la nota.
- El humo contra DSS (`tests/test_dss_smoke.py`) no forma parte del gate: requiere `DSS_URL`.
- El `ArchiveTimeStamp` no se renueva (ver punto 5), y en la ruta personal el TOTP ya está consumido cuando la autoverificación
  falla (ver punto 1).
- La confianza (cadena hasta una raíz de una lista de confianza, ONAC) sigue siendo un asunto aparte.

### Decisiones descartadas

Columna `firma.perfil_xmldsig`, backfill, rama «legado» en `verify_xades`, `verificable_por_terceros` en `VerifyResponse` y
constancia de verificación por cada firma anterior: descartadas por el usuario al ser todo corpus de prueba. Si algún día
existiera una instalación productiva con firmas previas, el diseño original (rama de verificación rotulada y constancia
institucional sellada, nunca re-firma) está en el historial de la tanda B7.
