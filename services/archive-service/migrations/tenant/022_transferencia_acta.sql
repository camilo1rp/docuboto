-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E12 H-I — Ac. AGN 001/2024 Anexo FUID; Ley 594/2000 arts. 15/26):
-- congela el FUID como ACTA DE ENTREGA en el momento del traspaso (recibir()).
-- El acta es un artefacto probatorio inmutable 1:1 con la transferencia recibida;
-- se escribe UNA vez dentro de la tx atómica de recepción (misma que fixity+congelación
-- del expediente). Tabla separada (no inline) para no arrastrar el XML por el hot path
-- de `transferencias` y para aislar el snapshot inmutable de la fila de estado mutable.

CREATE TABLE IF NOT EXISTS transferencia_acta (
    transferencia_id  UUID PRIMARY KEY REFERENCES transferencias(id) ON DELETE CASCADE,
    -- Cuerpo congelado: XML canónico (C14N) del inventario FUID del expediente,
    -- generado con CLEARANCE_SIN_RESTRICCION (custodia, ortogonal a lectura).
    acta_fuid_xml     TEXT        NOT NULL,
    -- Fixity del acta: SHA-256 hex de acta_fuid_xml (verifica el acta inalterada
    -- despues; el XML embebe su propio `generado`=congelada_at).
    acta_fuid_sha256  TEXT        NOT NULL,
    -- Tres responsables del Anexo FUID (snapshot de columnas inmutables de
    -- transferencias, para que el acta sea autocontenida):
    elaborado_por     TEXT,               -- = transferencias.creada_por
    entregado_por     TEXT,               -- = transferencias.enviada_por
    recibido_por      TEXT,               -- = transferencias.decidida_por
    elaborado_at      TIMESTAMPTZ,        -- = transferencias.created_at
    entregado_at      TIMESTAMPTZ,        -- = transferencias.enviada_at
    recibido_at       TIMESTAMPTZ NOT NULL, -- = transferencias.recibida_at (instante del traspaso)
    -- H-H (parcial, gratis): versión del índice electrónico transferido, capturada
    -- dentro de la tx. El pin completo (indice_firma_id + validación) queda diferido.
    indice_version    INT,
    -- Instante de congelación = el `generado` embebido en acta_fuid_xml (coherente
    -- con el SHA-256). Ya NO se regenera al vuelo con now().
    congelada_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
