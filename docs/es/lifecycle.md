# Ciclo de Vida del Documento

En gestión documental, todo documento pasa por etapas bien definidas desde su creación hasta su disposición final. OrpycaMCP gestiona este ciclo completo.

---

## Las tres edades del documento

```
┌─────────────────────────────────────────────────────────┐
│  ARCHIVO DE GESTIÓN         │  Producción y trámite      │
│  (Oficina productora)       │  Retención: 2-5 años       │
│                             │  Módulos: Radicación,       │
│                             │  Flujos, Expedientes        │
├─────────────────────────────────────────────────────────┤
│  ARCHIVO CENTRAL            │  Documentos cerrados        │
│  (Depósito institucional)   │  Retención: 5-20 años      │
│                             │  Módulo: Transferencias     │
├─────────────────────────────────────────────────────────┤
│  ARCHIVO HISTÓRICO          │  Valor permanente           │
│  (Patrimonio)               │  Conservación Total (CT)   │
│                             │  o eliminación definitiva   │
└─────────────────────────────────────────────────────────┘
```

---

## Flujo de un documento de Entrada (E)

El caso más común: un ciudadano o entidad externa envía una comunicación.

```
1. RECEPCIÓN
   El documento llega físicamente o por correo electrónico.
   La ventanilla lo recibe y verifica que esté completo.

2. RADICACIÓN
   Se asigna el número: 2024-ENTIDAD-E-000001
   Se registra: remitente, fecha, asunto, tipo documental, dependencia destino.
   Se digitalizan los anexos (PDF) → almacenados en MinIO con SHA-256.

3. DISTRIBUCIÓN (Flujo)
   El sistema o el operador envía el radicado a la dependencia responsable.
   Queda registrado: quién lo envió, a quién, cuándo.

4. TRÁMITE
   La dependencia gestiona el documento.
   Puede reasignarlo, agregar observaciones, vincular a un expediente.
   Si requiere respuesta, se radica un documento de Salida (S) vinculado.

5. ARCHIVO EN EXPEDIENTE
   El radicado se vincula al expediente del asunto correspondiente.
   El expediente agrupa todos los documentos del mismo caso.

6. CIERRE Y TRANSFERENCIA
   Cuando el trámite termina, el expediente se cierra.
   Según la TRD, se transfiere al archivo central o se elimina.
```

---

## Flujo de un documento de Salida (S)

La institución genera y envía una comunicación oficial.

```
1. ELABORACIÓN
   El funcionario redacta el documento (puede usar plantillas).

2. RADICACIÓN DE SALIDA
   Se asigna número: 2024-ENTIDAD-S-000042
   Se registran los destinatarios (puede ser uno o varios).
   Se adjuntan los archivos firmados digitalmente.

3. ENVÍO
   El sistema registra el método de envío: correo físico, email, mensajero.
   Se genera la constancia de radicado para entregar al destinatario.

4. TRAZABILIDAD
   Queda el registro de cuándo se envió, a quién y por qué canal.
   El radicado de salida puede vincularse al de entrada que lo originó.
```

---

## Vinculación Entrada → Salida

OrpycaMCP permite vincular un radicado de salida al de entrada que lo originó, formando el hilo completo de la comunicación:

```
E-000001 (Solicitud del ciudadano)
    └── S-000042 (Respuesta de la entidad)
        └── E-000156 (Respuesta del ciudadano a la respuesta)
```

Esta vinculación es la base para medir los **tiempos de respuesta** y cumplir con los plazos legales.

---

## Plazos legales de respuesta

OrpycaMCP puede configurar alertas basadas en los plazos del Código de Procedimiento Administrativo (CPACA - Ley 1437 de 2011):

| Tipo de solicitud | Plazo legal |
|---|---|
| Derecho de petición (información) | 15 días hábiles |
| Derecho de petición (documentos) | 10 días hábiles |
| Queja o reclamo | 15 días hábiles |
| Consulta a autoridad | 30 días hábiles |

---

## El expediente como unidad documental

Un expediente no es solo una carpeta de documentos. Es la **memoria completa de un trámite**:

```
EXPEDIENTE: Contrato 2024-001 — Suministro de papelería
│
├── Radicado E-000023 — Invitación a cotizar
├── Radicado E-000089 — Cotización proveedor A
├── Radicado E-000091 — Cotización proveedor B
├── Radicado I-000045 — Cuadro comparativo de ofertas
├── Radicado I-000048 — Acta de selección
├── Radicado S-000067 — Notificación al proveedor seleccionado
├── Radicado E-000112 — Contrato firmado
└── Radicado I-000089 — Acta de inicio
```

**Estado:** Abierto → (cuando termina el contrato) Cerrado → (según TRD) Transferido al archivo central

---

## Disposición final según TRD

Cuando vence el tiempo de retención definido en la TRD, cada expediente tiene una de estas disposiciones:

| Disposición | Código | Qué significa |
|---|---|---|
| Conservación Total | CT | Se conserva permanentemente (valor histórico o legal) |
| Eliminación | E | Se destruye siguiendo el procedimiento de eliminación documental |
| Microfilmación | MT | Se digitaliza/microfilma y luego se elimina el papel |
| Selección | S | Se conserva una muestra representativa, el resto se elimina |

El proceso de eliminación documental requiere un **Acta de Eliminación** firmada, que también se archiva en el sistema.

<!-- TODO: translate to English -->
