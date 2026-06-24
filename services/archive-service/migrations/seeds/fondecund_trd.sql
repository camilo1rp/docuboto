-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- ======================================================================
-- TRD Migration: FONDO DE DESARROLLO DE PROYECTOS DE CUNDINAMARCA - FONDECÚN
-- Version: 01
-- Date: 12/06/2024
-- Tenant: fondecund
-- ======================================================================

BEGIN;

-- Insert TRD series (document types/classifications)

INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('ce2e3d7d-014f-4172-9fbe-f901a186b422', '003', 'ACTOS ADMINISTRATIVOS', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('8cd739f0-8d6e-45a3-953b-0105d0407258', '003.01', 'ACUERDOS', 18, 36, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('f5b19912-98aa-40a2-ba4e-f20e9a382a3d', '003.02', 'RESOLUCIONES', 18, 36, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('82f32531-d954-4941-8964-f9e9d588a2b6', '005', 'CIRCULARES', 18, 36, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('0aaf620d-d15e-4f69-9948-893c1bc1ac3c', '016', 'INFORMES', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('0ad5cc9b-efb0-497d-82ba-79bdf55250a4', '016.03', 'INFORMES DE GESTIÓN', 8, 16, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('5320dc76-bd46-4377-9357-af873d94a1a0', '002', 'ACTAS', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('9256d0db-33c3-4784-90d3-8da4c0c943a2', '002.08', 'ACTAS DEL COMITÉ INSTITUCIONAL DE COORDINACIÓN DE CONTROL INTERNO', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('36321843-918c-49d6-97b7-b0cd6279d172', '002.09', 'ACTAS DEL COMITÉ INSTITUCIONAL DE COORDINACIÓN DE CONTROL INTERNO', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('a5d5ee63-1b70-46fd-821c-792bfa58b403', '016', 'INFORMES', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('de2228a3-12d6-4269-a6c3-a76d2a9703d9', '016.01', 'INFORMES A ENTES DE CONTROL', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('4e4f9cbb-fc29-4160-b245-9f4bbdac6a5e', '016.03', 'INFORMES DE GESTIÓN', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('9a15634e-79eb-4881-8566-c782d5b77932', '016.04', 'INFORMES DE LEY', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('71c53b66-87a1-446c-b2e9-01f1db99031e', '024', 'PLANES', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('3e6168bd-44d5-417b-93f4-2f56862fb5cc', '024.05', 'PLANES DE AUDITORÍA', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('5dbfad05-9d14-4701-81c2-e134b97e8030', '024.10', 'PLANES DE MEJORAMIENTO INSTITUCIONAL', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('fd1d9aca-b530-477e-beac-2addbcd5a0c6', '001', 'ACCIONES CONSTITUCIONALES', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('f3c76098-aebe-4143-9500-11ccd30adf73', '001.01', 'ACCIONES DE CUMPLIMIENTO', 18, 36, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('916a80c6-5392-40a8-ac90-b669a4b65504', '001.02', 'ACCIONES DE GRUPO', 18, 36, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('ce51dab3-9bd3-4c93-ad53-770c1409e123', '001.03', 'ACCIONES DE TUTELA', 18, 36, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('ab144066-bcb7-4496-9cb7-fa272308d40f', '001.04', 'ACCIONES POPULARES', 18, 36, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('e4617683-5b04-40c2-9b23-9845cbb5ef56', '002', 'ACTAS', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('5c3985ac-13cc-4527-8692-145190b6bdaa', '002.04', 'ACTAS DEL COMITÉ DE CONCILIACIÓN', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('9dc50c65-5d9a-4351-86f6-7742aa140e52', '002.05', 'ACTAS DEL COMITÉ DE CONCILIACIÓN', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('16ad13b7-e574-4864-9834-babec80d1d27', '002.06', 'ACTAS DEL COMITÉ DE CONTRATACIÓN', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('c13a29a2-e7fe-434a-913f-65247d5a284e', '002.07', 'ACTAS DEL COMITÉ DE NEGOCIOS', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('b7478d72-b4d5-4520-ab74-00e5c52dc0e2', '002.08', 'ACTAS DEL COMITÉ DE NEGOCIOS', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('d1281a2b-7de6-4f21-8da7-0dda84d938ee', '007', 'CONCEPTOS', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('9dc95fa2-175e-41d2-81be-c527f56f2b1c', '007.01', 'CONCEPTOS JURÍDICOS', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('df614bcf-1586-475e-b0c1-8c0b9366a836', '010', 'CONTRATOS', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('99d29131-bd35-42ae-82be-ac17841b46da', '010.01', 'CONTRATOS INTERADMINISTRATIVOS NEGOCIOS', 29, 58, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('a35e5c5d-550d-48c5-9bd8-12e04e566af0', '010.02', 'CONTRATOS POR CONCURSO DE MÉRITOS', 29, 58, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('97b8ff29-2036-40ef-a76e-8b600e0b2cc3', '010.03', 'CONTRATOS POR CONTRATACIÓN DIRECTA – PERSONA JURÍDICA', 29, 58, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('1776a2fe-4149-4bda-a995-a278f0763706', '010.04', 'CONTRATOS POR CONTRATACIÓN DIRECTA – PERSONA NATURAL', 29, 58, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('1a4d48c2-e419-45d4-bb3b-f8169397ddba', '010.05', 'CONTRATOS POR CONVOCATORIA POR CONCURSO', 29, 58, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('fbbba1a0-a344-4612-9845-47337e3cd687', '010.06', 'CONTRATOS POR INVITACIÓN PÚBLICA', 29, 58, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('1f454cdc-69a8-4403-ba43-4f34c67ec090', '010.07', 'CONTRATOS POR LICITACIÓN PÚBLICA', 29, 58, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('7b4e2864-4de9-4faa-bb6f-60417efe8e72', '010.08', 'CONTRATOS POR MÍNIMA CUANTÍA', 29, 58, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('36aa381f-8286-4b87-98af-4898fcb4fa02', '010.09', 'CONTRATOS POR OFERTA DINÁMICA', 29, 58, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('73805ebc-58f6-47b6-97df-217ae09ee09f', '010.10', 'CONTRATOS POR SELECCIÓN ABREVIADA DE MENOR CUANTÍA O SUBASTA INVERSA', 29, 58, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('b9090379-c866-4959-8091-b0a5e5516906', '026', 'PROCESOS CONTRACTUALES DECLARADOS DESIERTOS', 18, 36, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('053dcb91-1769-4f45-93f4-f5cddbb09f2c', '027', 'PROCESOS JURÍDICOS', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('776d3029-44bf-456d-ae1a-bf55fe0f613e', '027.01', 'PROCESOS DISCIPLINARIOS', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('459ce95f-0c65-476b-b8f6-0dfa0c25397c', '027.02', 'PROCESOS JUDICIALES', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('be7a3cc9-bb8d-4c5b-97cd-a524da4f89c4', '028', 'PROCESOS SANCIONATORIOS', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('8471d56f-ca95-4dba-bcf6-28b9ce99ca70', '002', 'ACTAS', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('99b82e00-efd9-4d5d-af52-9a5420a17c93', '002.03', 'ACTAS DE SEGUIMIENTO A PROYECTOS', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('68e1eb23-406e-4b66-8779-2c7f047458fd', '002.04', 'ACTAS DE SEGUIMIENTO A PROYECTOS', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('b4990750-50f7-4b17-b1e7-d4920201a4d0', '016', 'INFORMES', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('500d8c4c-87bf-4d72-bed8-550c589395e3', '016.03', 'INFORMES DE GESTIÓN', 8, 16, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('08596f62-1585-487f-80d4-0317bc7f5be4', '021', 'MANUALES', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('e30e08fd-83fd-48ee-96f0-66cc70747de0', '021.01', 'MANUALES DE GERENCIA INTEGRAL DE PROYECTOS', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('067d7e55-35db-4860-9952-9a8c9b52af6a', '021.04', 'MANUALES DE SUPERVISIÓN E INTERVENTORÍA', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('691aee6e-75fb-4a1b-bd11-2a06e45b4e3f', '002', 'ACTAS', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('160dd47d-1467-4cea-9658-0f7800a7fc65', '002.01', 'ACTAS DE ELIMINACIÓN DOCUMENTAL', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('e9316276-77de-49a1-8e1e-6a5265a232e2', '002.02', 'ACTAS DE ENTREGA DE ELEMENTOS DE PROTECCIÓN PERSONAL', 18, 36, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('a6e305ec-3df9-4ccc-b9b6-998f4767afc7', '002.03', 'ACTAS DE LA JUNTA DIRECTIVA', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('c5d03dad-1db6-4d6c-8bc2-d434bdc98a0a', '002.06', 'ACTAS DEL COMITÉ DE CONVIVENCIA LABORAL', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('340c5f4a-f9e5-4e02-9de8-e030c49bba87', '002.07', 'ACTAS DEL COMITÉ DE CONVIVENCIA LABORAL', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('64924273-ac92-47cc-ab07-276a208cd5ca', '002.09', 'ACTAS DEL COMITÉ INSTITUCIONAL DE GESTIÓN Y DESEMPEÑO', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('6954f724-bec8-47f8-b4a5-8c42a6cfce7d', '002.10', 'ACTAS DEL COMITÉ INSTITUCIONAL DE GESTIÓN Y DESEMPEÑO', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('82841f9d-2ddc-4091-b7f4-b01861f35555', '002.11', 'ACTAS DEL COMITÉ PARITARIO DE SEGURIDAD Y SALUD EN EL TRABAJO', 18, 36, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('4cf609bf-7500-4baf-8814-af78646da63b', '002.12', 'ACTAS DEL COMITÉ TÉCNICO DE SOSTENIBILIDAD DEL SISTEMA CONTABLE', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('ce09ca6e-4f6c-44a2-bff7-b1eb5a5676c6', '004', 'ANTEPROYECTOS DE PRESUPUESTO', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('50bee25f-035b-4236-8019-42056fd4ec75', '006', 'COMPROBANTES CONTABLES', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('8719d57b-f086-4e02-9fc4-cda01c31cfa6', '006.01', 'COMPROBANTES CONTABLES DE EGRESO', 8, 16, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('d0f703d1-6313-4e5b-b7da-9b8b66cb7eb5', '006.02', 'COMPROBANTES CONTABLES DE INGRESO', 8, 16, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('ded2722d-b420-4755-8631-8cc156c1cc11', '006.03', 'COMPROBANTES DE GIROS A TERCEROS', 8, 16, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('e2269e72-fef8-4862-9406-a7ce6e811ba4', '008', 'CONCILIACIONES BANCARIAS', 8, 16, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('deb71412-4c9b-4b45-9ec1-ceeda4d08a10', '009', 'CONSECUTIVOS DE COMUNICACIONES OFICIALES', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('05e1ae37-57bc-4012-913c-debfc4cba560', '009.01', 'CONSECUTIVOS DE COMUNICACIONES OFICIALES ENVIADAS', 8, 16, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('39f8332b-2ff8-4a67-bacf-725b44da725f', '009.02', 'CONSECUTIVOS DE COMUNICACIONES OFICIALES RECIBIDAS', 8, 16, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('4297d8d5-210c-4826-8ff8-31a0de62471e', '011', 'DECLARACIONES TRIBUTARIAS', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('260a73a6-d8f9-49e7-b977-087e0490b20e', '011.01', 'DECLARACIONES DE CUOTAS FOMENTO', 8, 16, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('38e46ae5-358a-4647-96d1-4428d2723213', '011.02', 'DECLARACIONES DE IMPUESTO DE INDUSTRIA Y COMERCIO', 8, 16, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('b3c3f7a6-8e77-4050-b49b-af0bd5bbf605', '011.03', 'DECLARACIONES DE IVA', 8, 16, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('1caef3cb-399f-476a-b837-a8aba11c179a', '011.04', 'DECLARACIONES DE RENTA', 8, 16, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('4d6bfdd1-30d4-452e-8702-77fddeface79', '011.05', 'DECLARACIONES DE RETENCIONES EN LA FUENTE EN EL IMPUESTO DE RENTA', 8, 16, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('4539d86e-33dd-4ef8-9901-5b8598464986', '012', 'DERECHOS DE PETICIÓN', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('55f00394-8e6d-48c9-9d80-79a8bcadda5e', '013', 'ESTADOS FINANCIEROS', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('119e29b3-5662-4ff6-b872-c143d7b5a074', '014', 'HISTORIALES DE VEHÍCULOS', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('f1f10f0a-80f6-4fa7-a9e9-e2baacb8e46c', '015', 'HISTORIAS LABORALES', 98, 196, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('96d8c90a-0639-46e3-9d24-bee5d37b4492', '016', 'INFORMES', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('6ef1244f-aa9d-4a9f-a561-e1edddd16e20', '016.02', 'INFORMES DE EJECUCIÓN PRESUPUESTAL', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('2ac4e8e5-3ba9-4548-addc-c2adc41ef4b8', '016.03', 'INFORMES DE GESTIÓN', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('d9a7b8db-0853-4720-9c37-9c4c2c62a1bb', '016.05', 'INFORMES TRIMESTRALES DEL COMITÉ DE CONVIVENCIA LABORAL', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('61b5b679-9e1b-403d-9e32-0d19ced7f710', '016.06', 'INFORMES SOBRE ACCESO A INFORMACIÓN, QUEJAS Y RECLAMOS', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('ccf80a59-1a8c-45ac-af1e-fdeef6a802b8', '017', 'INSTRUMENTOS ARCHIVÍSTICOS', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('7e3eada9-fa4a-41c2-85c3-842f55624ce3', '017.01', 'BANCOS TERMINOLÓGICOS DE SERIES Y SUBSERIES DOCUMENTALES', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('09090824-d196-4131-8174-c0b1537575a8', '017.02', 'CUADROS DE CLASIFICACIÓN DOCUMENTAL – CCD', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('9106970b-7ce6-4266-a173-543ce56b9750', '017.03', 'INVENTARIOS DOCUMENTALES DE ARCHIVO CENTRAL', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('4f05e340-ea2f-4c1c-b543-bad9af4e376d', '017.04', 'MODELO DE REQUISITOS PARA LA GESTIÓN DE DOCUMENTOS ELECTRÓNICOS', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('48ed909e-58ef-47cd-a55b-51a551975188', '017.05', 'PLANES INSTITUCIONALES DE ARCHIVOS - PINAR', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('b4bea8bd-64b5-4a9e-bb14-ffe5aa2f362f', '017.06', 'PROGRAMAS DE GESTIÓN DOCUMENTAL - PGD', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('10d047f6-f52c-4dc6-979b-5f8e45d0ebaa', '017.07', 'TABLAS DE CONTROL DE ACCESO - TCA', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('730622ef-6c1a-44eb-803c-f116a822ebae', '017.08', 'TABLAS DE RETENCIÓN DOCUMENTAL - TRD', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('cb10b68d-d015-4546-b5bc-f775ecb5a424', '018', 'INSTRUMENTOS DE CONTROL', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('508514ac-5c06-41e0-837f-e7d72bcb4e23', '018.01', 'INSTRUMENTOS DE CONTROL DE COMUNICACIONES OFICIALES', 8, 16, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('e520c01e-83f7-445c-bec9-de153357ca11', '018.02', 'INSTRUMENTOS DE ENTREGA DE EQUIPOS A USUARIOS', 8, 16, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('184190ce-4fd1-4594-868f-5c3683e2b0c2', '018.03', 'INSTRUMENTOS DE ENTREGA DE ÓRDENES DE PAGO', 8, 16, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('a48ea421-2f69-486f-bb2b-3830e2e10be5', '018.04', 'INSTRUMENTOS DE PRÉSTAMO UNIDADES DOCUMENTALES', 8, 16, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('36a53886-eca5-4252-ac90-54d6f85b427f', '018.05', 'INSTRUMENTOS DE SOLICITUD DE CONEXIÓN REMOTA', 8, 16, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('9de39797-da0e-47bf-b5f2-aa200d3099ec', '018.06', 'INSTRUMENTOS DE SOLICITUD DE PUBLICACIONES', 8, 16, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('82aeaf8e-b2cf-436c-bc65-5c03bd9e7dc7', '018.07', 'INSTRUMENTOS DE SOLICITUD RETIRO EQUIPOS DE CÓMPUTO', 8, 16, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('c08c363c-0a9c-4741-b9c1-68abc849a345', '018.08', 'INSTRUMENTOS DE SOLICITUD DE REGISTRO DE USUARIOS', 8, 16, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('5c792205-2793-4d3b-8501-fd1a36277a21', '018.16', 'PLANES ESTRATÉGICOS DE TECNOLOGÍAS DE LA INFORMACIÓN - PETI', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('3c626ab8-cb05-4b46-965f-a65d8405dd5d', '019', 'INVENTARIOS DE ALMACÉN', 8, 16, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('9aed753b-3b8e-4424-8e0f-28ba411cea16', '020', 'LIBROS CONTABLES PRINCIPALES', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('b524e340-817d-444e-8ba6-2bf2c54b8ff0', '020.01', 'LIBRO DIARIO', 8, 16, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('7f138690-2dce-430f-8929-5b4b3e851ae0', '020.02', 'LIBRO MAYOR Y BALANCE', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('18bb40a8-38eb-4bff-840a-635c58f2b9dc', '021', 'MANUALES', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('26a8c5a9-4789-429f-b02b-c8bbdbdc3e3b', '021.02', 'MANUALES DE GESTIÓN DOCUMENTAL', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('cbad4c79-5e1a-4359-b9d5-29e5c9f6fc6e', '021.03', 'MANUALES DE PRESUPUESTO', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('ae2d2dce-9994-402d-96e1-39a575a4e7e8', '021.05', 'MANUALES DEL SISTEMA DE GESTIÓN DE CALIDAD', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('24e157d1-af13-4769-a4f9-3ea4c94dd207', '021.06', 'MANUALES ESPECÍFICOS DE FUNCIONES, REQUISITOS Y COMPETENCIAS LABORALES', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('8c871eca-5cb6-44c2-9a37-6311c64eaf12', '022', 'NÓMINAS', 98, 196, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('58f954da-3131-4b38-a45f-38b24d35c072', '023', 'NOTAS DÉBITO', 8, 16, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('37473270-ada0-403c-a523-48770e75ee6d', '024', 'PLANES', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('e01c50ea-ec0b-4fcc-9200-cfca39a9bed4', '024.01', 'PLANES ANTICORRUPCIÓN Y DE ATENCIÓN AL CIUDADANO', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('b9625365-7fcf-41e1-adda-59dca02cf219', '024.02', 'PLANES ANUALES DE ADQUISICIONES', 18, 36, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('c65d5712-e5ee-4f26-92e5-bbdb17146d34', '024.03', 'PLANES ANUALES DE EMPLEOS VACANTES', 8, 16, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('ebfaa6fa-332d-489a-861b-314e84f5b090', '024.04', 'PLANES DE ACCIÓN INSTITUCIONAL', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('b4760ef7-32f8-488a-8643-0a5a3498e963', '024.06', 'PLANES DE BIENESTAR E INCENTIVOS', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('f338b1a1-27c4-437d-80c5-f01efbb59427', '024.07', 'PLANES DE CONSERVACIÓN DOCUMENTAL', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('a011d91d-6901-4a50-87a5-added02cc578', '024.08', 'PLANES DE GESTIÓN DEL RIESGO', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('573bf732-390a-4420-81f5-67d03397ec76', '024.09', 'PLANES DE MEJORAMIENTO DEL MODELO INTEGRADO DE PLANEACIÓN Y GESTIÓN - MIPG', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('3e3bc4de-d069-40aa-b132-c3d1ec3cb3fd', '024.11', 'PLANES DE PRESERVACIÓN DIGITAL A LARGO PLAZO', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('fff17521-567e-418e-be4d-18772900ecac', '024.12', 'PLANES DE PREVISIÓN DE RECURSOS HUMANOS', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('1f496173-a34c-4dc4-adb7-c1455dfab802', '024.13', 'PLANES DE TRABAJO ANUAL DEL SISTEMA DE GESTIÓN DE SEGURIDAD Y SALUD EN EL TRABAJO - SGSST', 18, 36, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('248f4c7f-2ac7-4dc9-853f-b0d7408765b7', '024.14', 'PLANES DE TRANSFERENCIAS DOCUMENTALES', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('e29fcc32-bcdb-4152-b5e2-839b3d69bf65', '024.15', 'PLANES DEL SISTEMA DE GESTIÓN AMBIENTAL', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('09ee4219-e1e3-41b4-a2c8-aa2c38f94a2a', '024.16', 'PLANES ESTRATÉGICOS DE TECNOLOGÍAS DE LA INFORMACIÓN - PETI', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('950cdc35-9cb1-4f71-82d7-07d7f832549b', '024.17', 'PLANES ESTRATÉGICOS INSTITUCIONALES', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('92085b35-d15c-4c67-b0d9-b94e2f9af021', '024.18', 'PLANES INSTITUCIONALES DE CAPACITACIÓN - PIC', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('ea6afcca-0344-4e8c-bd2f-f3f92dd38425', '025', 'POLÍTICAS', 10, 20, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('943d544d-fa96-4d1b-8c20-ee96b2ba6945', '025.01', 'POLÍTICAS DE OPERACIÓN PARA LA ADMINISTRACIÓN DE RIESGO', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('37467197-3a3e-4d4b-8df9-cdb381145088', '025.02', 'POLÍTICAS DE SEGURIDAD Y PRIVACIDAD DE LA INFORMACIÓN', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('bf88273c-355a-47a8-b258-61f9a30819fa', '025.03', 'POLÍTICAS INSTITUCIONALES DE GESTIÓN DOCUMENTAL', 8, 16, 'conserve', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('9382173d-4c94-4dcd-b2b8-67b84db4c827', '029', 'REGISTROS DE OPERACIONES DE CAJA MENOR', 8, 16, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('3d812a73-94b7-484b-aa9e-471da8677e39', '030', 'REGISTROS DE VIÁTICOS', 8, 16, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('c01f933c-64ca-42fb-9f7e-672d5d0d4932', '031', 'SOLICITUDES DE ELABORACIÓN, MODIFICACIÓN O ELIMINACIÓN DE LA DOCUMENTACIÓN DEL SISTEMA DE GESTIÓN DE CALIDAD', 8, 16, 'eliminate', true);
INSERT INTO trd_series (id, code, name, retention_years, total_retention, disposition, is_active) VALUES ('e3c8a8b2-69c0-41d3-98ff-6d0c8d298b4b', '032', 'TRASLADOS DE RECURSOS FINANCIEROS', 8, 16, 'eliminate', true);

COMMIT;

-- Summary: 40 series, 102 subseries inserted
