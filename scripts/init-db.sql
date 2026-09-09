-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- OrpycaMCP — Database Initialization
-- Runs once on first postgres container start via docker-entrypoint-initdb.d

-- Schema para Keycloak
CREATE SCHEMA IF NOT EXISTS keycloak;

-- Extensión pgvector (capa de conocimiento E21: embeddings en kb_chunks).
-- Requiere la imagen pgvector/pgvector:pg15. Se crea a nivel de BD; el tipo
-- `vector` queda disponible para todos los schemas tenant_{slug}.
-- Nota: este script solo corre en la PRIMERA inicialización del volumen; las
-- migraciones de E21 también ejecutan CREATE EXTENSION IF NOT EXISTS de forma defensiva.
CREATE EXTENSION IF NOT EXISTS vector;

-- Tabla de tracking de migraciones (usada por migrations/runner.py de cada servicio)
CREATE TABLE IF NOT EXISTS public._migrations (
    id         SERIAL PRIMARY KEY,
    filename   TEXT NOT NULL UNIQUE,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
