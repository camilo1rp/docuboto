# OrpycaMCP — Sistema de Gestión Documental Libre

**OrpycaMCP** es un Sistema de Gestión Documental (SGD) de código abierto para instituciones públicas y privadas de América Latina, publicado bajo licencia **AGPL v3**.

Su propósito es gestionar el ciclo de vida completo de los documentos institucionales: desde la radicación de correspondencia, pasando por su distribución y seguimiento interno, hasta su organización en expedientes y disposición final según la Tabla de Retención Documental (TRD).

---

## ¿Qué es un SGD y por qué importa?

Un Sistema de Gestión Documental no es un gestor de archivos. Es la herramienta que garantiza que **cada documento que entra o sale de una institución queda registrado, trazado y gestionado conforme a la ley**.

En Colombia, la gestión documental de entidades públicas está regulada por:

- **Ley 594 de 2000** — Ley General de Archivos: obliga a las entidades públicas a organizar sus documentos mediante TRD y a garantizar su conservación.
- **Acuerdo AGN 060 de 2001** — establece pautas para la administración de comunicaciones oficiales.
- **Decreto 1080 de 2015** — Decreto Único Reglamentario del sector Cultura, que integra la normativa archivística.
- **MIPG** — Modelo Integrado de Planeación y Gestión, que exige trazabilidad documental como parte de la gestión pública.

OrpycaMCP implementa estas normas en software libre, permitiendo a cualquier institución cumplir sus obligaciones archivísticas sin depender de soluciones propietarias.

---

## Ciclo de vida del documento en OrpycaMCP

```
                    ┌─────────────┐
   Ciudadano/       │             │
   entidad externa  │  RADICACIÓN │  Entrada (E) — Salida (S) — Interno (I)
   ──────────────► │             │  Número único e inmutable asignado
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │ DISTRIBUCIÓN│  Flujo entre dependencias
                    │  (Flujos)   │  Trazabilidad de cada paso
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │  TRÁMITE    │  El responsable gestiona, responde
                    │             │  o transfiere el documento
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │ EXPEDIENTE  │  Agrupación de radicados por asunto
                    │             │  Estados: Abierto → Cerrado → Transferido
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │  ARCHIVO    │  TRD determina retención y disposición final
                    │   (TRD)     │  Conservar / Eliminar / Digitalizar
                    └─────────────┘
```

---

## Módulos del sistema

| Módulo | Descripción |
|---|---|
| **Radicación** | Registro oficial de documentos con número único. Tipos: Entrada, Salida, Interno |
| **Anexos** | Archivos adjuntos almacenados con integridad garantizada (SHA-256) |
| **Distribución y flujos** | Enrutamiento de documentos entre dependencias con trazabilidad completa |
| **Expedientes** | Agrupación de radicados por asunto o caso, con ciclo de vida propio |
| **TRD** | Tabla de Retención Documental — clasifica y determina disposición final |
| **Multi-entidad** | Una instalación sirve múltiples instituciones con datos completamente aislados |
| **Autenticación** | Keycloak con soporte LDAP para integración con directorios institucionales |

---

## ¿Para quién es OrpycaMCP?

- **Entidades públicas colombianas** que necesitan cumplir la Ley 594/2000 con software libre
- **Instituciones latinoamericanas** con normativas similares de gestión documental
- **Municipios y gobernaciones** que no pueden costear soluciones propietarias
- **Desarrolladores y comunidades** que quieren contribuir a un SGD moderno y mantenible
- **Integradores** que necesitan una API REST bien documentada para conectar otros sistemas

---

## Diferencias con el Orfeo original

OrpycaMCP reimplementa el [Orfeo GPL](https://github.com/orfeosgd/orfeo) original (PHP + PostgreSQL monolítico) con una arquitectura moderna:

| Aspecto | Orfeo Original | OrpycaMCP |
|---|---|---|
| Stack | PHP 7 + Apache | FastAPI (Python 3.11) |
| Arquitectura | Monolítico | Microservicios |
| Almacenamiento de archivos | Sistema de archivos del servidor | MinIO (S3-compatible) |
| Autenticación | Sesiones PHP + LDAP opcional | Keycloak (SSO, LDAP, OAuth2) |
| Multi-entidad | Una instalación por entidad | Multi-tenant nativo |
| API | Sin API REST estándar | OpenAPI / REST completo |
| Documentación | Limitada | Automática, bilingüe, publicada |

La lógica de negocio (radicados, expedientes, TRD, flujos) se preserva fielmente del sistema original.

---

## Inicio rápido / Quick Start

```bash
git clone https://gitlab.com/orpyca/orpyca-mcp.git
cd orpyca-mcp
docker compose up -d
```

Explora la API en `http://localhost:8080/docs`
Administra Keycloak en `http://localhost:8180/admin` (admin / orpycamcp_dev)

---

## Estado del proyecto

| Fase | Contenido | Estado |
|---|---|---|
| Fase 1 — Framework | Arquitectura, CI/CD, documentación base | ✅ En progreso |
| Fase 2 — Core SGD | Radicación E/S/I, Anexos, TRD | 🔲 Planificado |
| Fase 3 — Tramitación | Workflows, Expedientes, Notificaciones | 🔲 Planificado |
| Fase 4 — Comunidad | Docs públicas, GitHub, orpyca.com | 🔲 Planificado |

---

## Licencia

**AGPL v3** — igual que el Orfeo GPL original. Toda modificación debe publicarse con la misma licencia. Esto garantiza que el software permanezca libre para siempre.
