# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de transferencias documentales (E12)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class TransferenciaCreate(BaseModel):
    tipo: Literal["primaria", "secundaria"]
    expediente_id: uuid.UUID
    ubicacion_origen_id: uuid.UUID | None = None
    ubicacion_destino_id: uuid.UUID | None = None
    observacion: str | None = Field(default=None, max_length=2000)
    # `objeto` (migración 052, Anexo FUID del Ac. AGN 001/2024): el propósito de
    # la transferencia, en la cabecera del acta de entrega. OBLIGATORIO y a
    # diferencia de `observacion` -- que es una nota de trabajo y puede faltar
    # -- este es uno de los tres campos con los que el Anexo FUID abre. Lo
    # escribe quien prepara porque es un dato HUMANO: componerlo a partir del
    # expediente y las ubicaciones produciría siempre la misma frase, que es
    # tanto como dejarlo vacío con más letras.
    objeto: str = Field(min_length=3, max_length=500)

    @field_validator("objeto")
    @classmethod
    def _objeto_no_blank(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("objeto de la transferencia requerido (mín. 3 caracteres)")
        return v


class TransferenciaRechazo(BaseModel):
    """Payload de `rechazar` (E12 H-K). El rechazo es una decisión de fondo
    (estado TERMINAL) y su motivo es el insumo de la subsanación -- a
    diferencia de `TransferenciaDecision` (retirado), `motivo` es
    OBLIGATORIO: antes se admitía un rechazo inmotivado (`observacion` con
    default `None`), lo que dejaba el porqué del rechazo sin registro
    reconstruible."""

    motivo: str = Field(min_length=3, max_length=2000)

    @field_validator("motivo")
    @classmethod
    def _no_blank(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("motivo de rechazo requerido (mín. 3 caracteres)")
        return v


class TransferenciaResponse(BaseModel):
    id: uuid.UUID
    tipo: str
    estado: str
    expediente_id: uuid.UUID
    ubicacion_origen_id: uuid.UUID | None
    ubicacion_destino_id: uuid.UUID | None
    creada_por: str | None
    observacion: str | None
    created_at: datetime
    enviada_at: datetime | None
    # `enviada_por` (migración 020, H-B/H-C): responsable de la REMISIÓN —
    # gemelo de `creada_por`/`decidida_por`, poblado desde `Actor.audit_actor`
    # (X-User-Id), nunca desde un username (RF-SEG-08 / Ac. AGN 001/2024 art.
    # 4.3.2.2, ver `app/core/actor.py`).
    enviada_por: str | None
    recibida_at: datetime | None
    decidida_por: str | None
    # `motivo_rechazo`/`rechazada_at` (migración 021, H-K/H-J): motivo
    # OBLIGATORIO del rechazo en columna propia (nunca pisa `observacion`,
    # el dato de preparación) + sello temporal propio del acto, gemelo de
    # `enviada_at`/`recibida_at`.
    motivo_rechazo: str | None = None
    rechazada_at: datetime | None = None
    # Cabecera del FUID (migración 052): `objeto` lo escribe quien prepara; la
    # oficina productora y su unidad administrativa son el SNAPSHOT de la
    # dependencia activa de esa misma persona en ese instante. `None` en las
    # transferencias anteriores a la migración y cuando la dependencia no se
    # pudo resolver -- vacío honesto, nunca un valor compuesto.
    objeto: str | None = None
    oficina_productora_codigo: int | None = None
    oficina_productora_nombre: str | None = None
    unidad_administrativa_codigo: int | None = None
    unidad_administrativa_nombre: str | None = None


class TransferenciaActaFirmaInfo(BaseModel):
    """Estado del sellado XAdES del acta de entrega (H-H, E06/F4) --
    aditivo/retrocompat sobre `TransferenciaActaResponse` (migración 026,
    `transferencia_acta_firma`, tabla MUTABLE hermana del acta append-only).

    `presente=True` cuando `estado='firmado'`. `valida` es SIEMPRE `None`
    salvo que el llamante pida `?verify=true` (cómputo on-demand: descarga
    el XML firmado + `signature-service /verify` -- ver
    `TransferenciaService._build_firma_info`); por defecto esta lectura NO
    re-verifica, mismos campos baratos que ya persiste `transferencia_acta_firma`."""

    presente: bool
    valida: bool | None = None
    xades_level: str | None = None
    acreditado: bool = False
    motivo: str | None = None
    # Avisos de una firma que SIGUE siendo válida (signature-service `/verify`); solo se
    # pueblan con `verify=True`. Vacío = nada que avisar.
    advertencias: list[str] = Field(default_factory=list)


class FirmaInobtenibleRequest(BaseModel):
    """Body de `POST /transferencias/{id}/acta/firma-inobtenible` (AREA-07-10): acto
    administrativo que declara que UNA parte del acta no puede firmar. La parte (su
    identidad) la deriva el servidor del `rol`; el cliente solo dice QUÉ rol y POR QUÉ.

    El soporte documental NO es texto libre: `referencia_radicado_id` es el UUID de un radicado
    y `referencia_anexo_id` el `file_id` de uno de sus anexos (OBLIGATORIO desde FIRMA-2: la
    constancia lleva el SHA-256 del anexo, no una referencia a algo que quizá no existe). El
    servidor los resuelve contra document-service con el clearance del declarante (422
    `referencia_documental_invalida` si no existen o no los puede ver: misma respuesta, sin
    oráculo) y trae el SHA-256 desde storage (503 si no puede).

    `totp_code` es el segundo factor de la firma PERSONAL del declarante sobre la constancia:
    obligatorio si el declarante tiene un certificado de firma activo (FIRMA-2: quien puede
    firmar, firma) o si la política XAdES de la entidad lo exige para el nivel del expediente
    (`firma_personal_require_xades` = `todos`, o `clasificados` con nivel >= 2). Sin certificado
    ni política, no se usa."""

    rol: Literal["entrega", "recibe", "elabora"]
    motivo: Literal["fallecimiento", "retiro", "incapacidad", "otro"]
    fundamento: str = Field(min_length=20, max_length=2000)
    referencia_radicado_id: uuid.UUID
    referencia_anexo_id: uuid.UUID
    totp_code: str | None = Field(default=None, min_length=1, max_length=16)

    @field_validator("fundamento")
    @classmethod
    def _no_solo_blancos(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("no puede estar vacío")
        return v


class RevocarFirmaInobtenibleRequest(BaseModel):
    """Body de `POST /transferencias/{id}/acta/firma-inobtenible/revocar` (AREA-07-10): deja sin
    efecto la excepción VIGENTE de UN rol (error de declaración, la parte sí puede firmar). Es
    una fila nueva append-only, firmada y protegida WORM; la constancia original no se toca y se
    puede volver a declarar (cadena, FIRMA-2) una vez la revocación esté protegida.

    `totp_code`: segundo factor de la firma PERSONAL de quien revoca; obligatorio si tiene un
    certificado de firma activo."""

    rol: Literal["entrega", "recibe", "elabora"]
    # Vocabulario cerrado (A6): viaja dentro del XML firmado de la revocación. El detalle libre
    # va en `fundamento`, que NO viaja (solo su compromiso con sal).
    causa: Literal["error_de_declaracion", "parte_puede_firmar", "otro"]
    fundamento: str = Field(min_length=20, max_length=2000)
    totp_code: str | None = Field(default=None, min_length=1, max_length=16)

    @field_validator("fundamento")
    @classmethod
    def _no_solo_blancos(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("no puede estar vacío")
        return v


class TransferenciaActaFirmaExcepcion(BaseModel):
    """Eslabón de la cadena de constancias de una excepción de firma (fila de
    `transferencia_acta_firma_excepcion`, migraciones 059/061/062) con su revocación, si la tiene.
    Inmutable; el acta congelada NO la contiene (es un hecho posterior).

    DATO SENSIBLE: `fundamento` (y `revocacion_fundamento`) pueden referir salud o
    fallecimiento. Solo se devuelven completos a `USUA_PERM_ADMIN` nivel >= 3; al resto
    viajan `None` y quedan el compromiso (`fundamento_compromiso` = SHA-256(sal || texto): con
    sal por registro no se ataca por diccionario y un tercero lo verifica si se le entregan texto
    y sal), el motivo y la referencia. La SAL (`fundamento_sal`, hex) es tan sensible como el
    texto: solo viaja a administración nivel 3.

    La REFERENCIA (radicado y anexo de soporte) se recorta por el clearance del lector: si no
    puede ver el radicado, todos los campos `referencia_*` viajan `None` —igual que si no
    existiera o document-service no respondiera—."""

    id: uuid.UUID
    transferencia_id: uuid.UUID
    rol: Literal["recibe", "entrega", "elabora"]
    parte_id: uuid.UUID
    motivo: Literal["fallecimiento", "retiro", "incapacidad", "otro"]
    fundamento: str | None = None
    # --- cadena (migración 062) ---
    ordinal: int = 1
    sustituye_a: uuid.UUID | None = None
    # 1 = anterior a FIRMA-2 (sin identidad ni soporte autosuficientes); 2 = autosuficiente con
    # HMAC de plataforma (LEGADA, no verificable por compromiso); 3 = autosuficiente con
    # compromiso salado del fundamento.
    constancia_version: int = 1
    # Compromiso del fundamento: SHA-256(sal || texto_utf8) en hex. `None` en v1/v2: se declara,
    # no se esconde, y NO se sustituye por un SHA-256 plano.
    fundamento_compromiso: str | None = None
    fundamento_compromiso_algoritmo: str | None = None
    # Sal (hex, 32 bytes) del compromiso: SOLO administración nivel 3 (`None` para el resto).
    fundamento_sal: str | None = None
    # Forma canónica: `radicado:<uuid>` o `radicado:<uuid>#anexo:<uuid>`; None si el lector no
    # puede ver el radicado.
    referencia_documental: str | None = None
    referencia_radicado_id: uuid.UUID | None = None
    referencia_radicado_numero: str | None = None
    referencia_anexo_id: uuid.UUID | None = None
    referencia_anexo_sha256: str | None = None
    declarada_por: uuid.UUID
    declarada_en: datetime
    # Instantánea de identidad resuelta por el servidor al declarar.
    declarante_nombre: str | None = None
    declarante_cargo: str | None = None
    declarante_fuente: str | None = None
    parte_nombre: str | None = None
    parte_cargo: str | None = None
    parte_fuente: str | None = None
    # --- la constancia como PRUEBA (migración 061) ---
    # `firmada=False` = constancia legada (anterior a la 061): sin sello ni WORM; se declara,
    # no se esconde.
    firmada: bool = False
    constancia_sha256: str | None = None
    xades_level: str | None = None
    firma_personal: bool = False
    xades_level_personal: str | None = None
    firma_personal_requerida: bool = False
    # Object-Lock confirmado del sello / de la firma personal (`None` = no hay ese artefacto).
    worm_protegido: bool | None = None
    worm_personal_protegido: bool | None = None
    # Hasta cuándo está retenido el sello (espejo local; todos los eslabones de la cadena del rol
    # comparten ancla de retención, A3). `None` = no protegido.
    worm_retain_until: datetime | None = None
    # Sobre de revelación del fundamento (064): objeto propio, sellado y bajo Object-Lock. `False`
    # = constancia anterior a la 064 (sin sobre). El sobre NO se sirve aquí: solo
    # `GET .../{excepcion_id}/sobre` (administración nivel 3, con asiento de lectura).
    sobre: bool = False
    sobre_sha256: str | None = None
    sobre_xades_level: str | None = None
    worm_sobre_protegido: bool | None = None
    # Verificación cripto (solo `GET /acta/verificacion`; `None` en el resto).
    sello_valida: bool | None = None
    firma_personal_valida: bool | None = None
    # Enlaces de la cadena (solo `GET /acta/verificacion`): `True` = la huella guardada, el XML
    # y el artefacto firmado coinciden y los vínculos (sustituyeA, revocacionSha256,
    # constanciaSha256) cuadran con las filas; `False` = enlace roto (ver `enlace_motivo`);
    # `None` = no comprobable (constancia anterior a la v3 o sin XML).
    enlace_valido: bool | None = None
    enlace_motivo: str | None = None
    advertencias: list[str] = Field(default_factory=list)
    # --- revocación (hecho posterior, fila aparte; prueba firmada desde la 062) ---
    revocada: bool = False
    revocacion_id: uuid.UUID | None = None
    revocada_por: uuid.UUID | None = None
    revocada_en: datetime | None = None
    revocacion_fundamento: str | None = None
    # Causa de vocabulario cerrado de la revocación (v3); `None` en revocaciones anteriores.
    revocacion_causa: str | None = None
    revocacion_fundamento_compromiso: str | None = None
    revocacion_fundamento_compromiso_algoritmo: str | None = None
    revocacion_fundamento_sal: str | None = None  # solo administración nivel 3
    revocante_nombre: str | None = None
    revocante_cargo: str | None = None
    revocante_fuente: str | None = None
    revocacion_firmada: bool = False
    revocacion_sha256: str | None = None
    revocacion_xades_level: str | None = None
    revocacion_firma_personal: bool = False
    revocacion_worm_protegido: bool | None = None
    revocacion_worm_personal_protegido: bool | None = None
    revocacion_worm_retain_until: datetime | None = None
    revocacion_sobre: bool = False
    revocacion_sobre_sha256: str | None = None
    revocacion_sobre_xades_level: str | None = None
    revocacion_worm_sobre_protegido: bool | None = None
    revocacion_sello_valida: bool | None = None
    revocacion_firma_personal_valida: bool | None = None
    # --- soporte documental (C14, ADR-043). DERIVADO, la constancia firmada no se toca ---
    # `vigente` = el anexo citado no se puede purgar; `baja_avalada` = un lote de eliminación
    # avalado por el Comité dio de baja el soporte (aún no destruido); `eliminado_por_trd` = el
    # soporte se destruyó con ese lote. `None` = la constancia no cita anexo (legada).
    soporte_estado: Literal["vigente", "baja_avalada", "eliminado_por_trd"] | None = None
    soporte_lote_codigo: str | None = None
    soporte_acta_sha256: str | None = None
    soporte_eliminado_at: datetime | None = None


class FundamentoVerificacion(BaseModel):
    """`GET /transferencias/{id}/acta/firma-inobtenible/{excepcion_id}/fundamento/verificacion`:
    recalcula LOCALMENTE el compromiso SHA-256(sal || texto) del fundamento guardado y lo compara
    contra TRES copias independientes —la columna, el XML firmado y el asiento de auditoría—.
    `coincide` es la conjunción de todas las comparables; `None` en una copia = no existe o no
    se pudo comprobar (nunca se da por buena). Solo administración nivel 3: no devuelve el texto
    ni la sal."""

    excepcion_id: uuid.UUID
    verificable: bool
    motivo: str | None = None
    algoritmo: str | None = None
    columna: bool | None = None
    xml: bool | None = None
    asiento: bool | None = None
    revocacion_columna: bool | None = None
    revocacion_xml: bool | None = None
    revocacion_asiento: bool | None = None
    # Cuarta copia (064): el sobre sellado y bajo Object-Lock. `None` = la constancia / revocación
    # no tiene sobre (anterior a la 064) o no se pudo descargar: no cuenta como coincidencia
    # cuando existe y no se pudo leer.
    sobre: bool | None = None
    revocacion_sobre: bool | None = None
    coincide: bool = False


class FundamentoLectura(BaseModel):
    """`GET .../firma-inobtenible/{excepcion_id}/fundamento?acto=constancia|revocacion` — el texto
    del fundamento (dato de salud o fallecimiento) a petición, solo administración nivel 3 y con
    asiento de lectura. La sal del compromiso no se devuelve por ninguna vía de lectura."""

    excepcion_id: uuid.UUID
    acto: Literal["constancia", "revocacion"]
    fundamento: str


class SobreFundamentoResponse(BaseModel):
    """`GET .../firma-inobtenible/{excepcion_id}/sobre?acto=constancia|revocacion` — el sobre de
    revelación del fundamento tal como está bajo Object-Lock: el XML sellado con el texto en claro
    y su sal. SOLO administración nivel 3, y cada lectura deja el asiento
    `transferencia.acta_fundamento_leido` (via=`sobre`) ANTES de servirlo. Nunca por OAI-PMH, EAD,
    CMIS ni verificación pública."""

    excepcion_id: uuid.UUID
    acto: Literal["constancia", "revocacion"]
    sobre_sha256: str | None = None
    xades_level: str | None = None
    worm_protegido: bool | None = None
    xml_firmado: str


class WormReintentoResponse(BaseModel):
    """`POST .../firma-inobtenible/{excepcion_id}[/revocacion]/worm/reintentar` (A4): salida de
    `agotado`. `artefactos` dice el estado REAL de cada artefacto al terminar
    (`protegido` | `pendiente` | `agotado` | `omitido`); `repuestos` cuántos estaban agotados y
    se repusieron a pendiente."""

    excepcion_id: uuid.UUID
    acto: Literal["excepcion", "revocacion"]
    repuestos: int
    artefactos: dict[str, str]


class TransferenciaActaResponse(BaseModel):
    """Acta de entrega CONGELADA (E12 H-I, Ac. AGN 001/2024 Anexo FUID) --
    devuelta tal cual se escribió al recibir la transferencia, nunca
    regenerada (ver `TransferenciaService.acta`). Los tres responsables
    (`elaborado_por`/`entregado_por`/`recibido_por`) son el snapshot de las
    columnas de identidad de `transferencias` en el instante del traspaso."""

    transferencia_id: uuid.UUID
    expediente_id: uuid.UUID
    acta_fuid_xml: str
    acta_fuid_sha256: str
    elaborado_por: str | None
    entregado_por: str | None
    recibido_por: str | None
    # Resueltos al LEER (`TransferenciaRepository.get_acta`), no congelados: el
    # acta guarda el identificador —es probatoria y no se toca— pero una ficha
    # que dice quién elaboró, entregó y recibió no puede contestarlo con un
    # UUID. `None` cuando el responsable no es un usuario del catálogo.
    elaborado_por_nombre: str | None = None
    entregado_por_nombre: str | None = None
    recibido_por_nombre: str | None = None
    elaborado_at: datetime | None
    entregado_at: datetime | None
    recibido_at: datetime
    # H-H (parcial): versión del índice electrónico pinneada dentro de la tx
    # de recepción (`fx.version`) -- el pin completo (`indice_firma_id` +
    # validación) queda diferido en roadmap.
    indice_version: int | None = None
    # AREA-07-04 (migración 053): el sello que ampara el índice entregado y la huella
    # del XML que cubre, leídos de la MISMA fila que la fixity. `None` en las actas
    # anteriores a esa migración (no se inventa un vínculo en un acta ya sellada).
    indice_firma_id: uuid.UUID | None = None
    indice_sha256: str | None = None
    congelada_at: datetime
    # AREA-07-11: derivado al LEER, nunca guardado. `False` cuando el XML congelado trae
    # vacía alguna de las tres entradas de la cabecera del Anexo FUID (unidad
    # administrativa, oficina productora, objeto) -- las actas anteriores a la
    # migración 052 se sellaron así y no se rehacen (son probatorias); se DECLARA el
    # hueco en vez de esconderlo.
    cabecera_completa: bool = True
    # AREA-07-10: constancias de «firma inobtenible» declaradas sobre esta acta (hechos
    # POSTERIORES a la congelación: el XML y su huella no cambian). Vacía = ninguna.
    excepciones_firma: list[TransferenciaActaFirmaExcepcion] = Field(default_factory=list)
    # H-H (E06/F4): estado del sellado XAdES del acta -- aditivo, SIEMPRE
    # presente en la respuesta (calculado por el servicio, nunca un campo
    # crudo de `transferencia_acta`).
    firma: TransferenciaActaFirmaInfo


class FirmarPersonalRequest(BaseModel):
    """Body de `POST /transferencias/{id}/acta/firmar-personal` (E17/F4) -- el
    `totp_code` de la propia PARTE (remitente o receptor), verificado-y-
    consumido ATÓMICAMENTE por auth-service junto con la firma (D1 del
    contrato de firma personal, signature-service).

    `rol` es OPCIONAL y solo desempata el AUTO-TRASLADO (misma persona envió y
    recibió): NUNCA concede un rol -- el rol efectivo se deriva de la identidad
    congelada (`enviada_por`/`decidida_por`) en el servicio. Fuera del
    auto-traslado se ignora si coincide con la identidad y se rechaza (403) si
    no."""

    totp_code: str = Field(min_length=1, max_length=32)
    rol: Literal["entrega", "recibe", "elabora"] | None = None


class TransferenciaActaFirmaPersonalResponse(BaseModel):
    """Firma PERSONAL PKI por-usuario de un ROL del acta (E17/F4 Inc.1) --
    fila de `transferencia_acta_firma_personal` (migración 027), ADITIVA
    sobre el sello institucional (`TransferenciaActaFirmaInfo`)."""

    transferencia_id: uuid.UUID
    rol: Literal["recibe", "entrega", "elabora"]
    firmante_id: uuid.UUID
    firma_id: uuid.UUID
    minio_key_firmado: str | None = None
    xades_level: str | None = None
    # RF-FIR-15: SIEMPRE False en este incremento (art. 7 Ley 527/1999, no
    # acreditada ONAC) -- nunca insinúa firma digital cualificada (art. 28).
    acreditado: bool = False
    creada_en: datetime


class TransferenciaActaFirmaPersonalVerif(BaseModel):
    """Bloque por-rol de `GET /acta/verificacion` -- cripto+revocación
    verificadas ON-DEMAND (nunca cacheadas), gemelo del bloque `sello`."""

    rol: Literal["recibe", "entrega", "elabora"]
    firmante_id: uuid.UUID
    # `None` cuando no se pudo verificar (XML firmado no descargable, o
    # signature-service inalcanzable) -- NUNCA se infiere `True` por defecto.
    valida: bool | None = None
    # `vigente`/`revocado`/`no_verificable`/`desconocido` (ver
    # signature-service `VerifyResponse.revocacion_status`) -- `None` si la
    # cripto ya falló (no tiene sentido evaluar revocación) o el canal no
    # respondió.
    revocacion_status: str | None = None
    # Avisos de una firma que SIGUE siendo válida (signature-service `/verify`).
    advertencias: list[str] = Field(default_factory=list)


class TransferenciaActaVerificacionResponse(BaseModel):
    """`GET /transferencias/{id}/acta/verificacion` (E17/F4) -- compone la
    verificación del sello institucional (siempre presente si el acta se
    selló) y de cada firma personal presente. `completitud` refleja la
    presencia FACTUAL de las firmas personales de las dos partes del FUID
    (remitente 'entrega' + receptor 'recibe'), que se firman en cualquier
    orden (Inc.2)."""

    sello: TransferenciaActaFirmaInfo
    firmas_personales: list[TransferenciaActaFirmaPersonalVerif]
    # 'sellada' -- solo el sello institucional (gate en 'off', o ninguna firma
    #   personal producida todavía y el gate no la exige).
    # 'firma_personal_pendiente' -- el gate (`acta_firma_personal_require`)
    #   exige firma personal para el nivel_seguridad de este expediente y
    #   NINGUNA parte ha firmado todavía.
    # 'firmada_receptor' -- solo el receptor ('recibe') firmó personalmente.
    # 'firmada_remitente' -- solo el remitente ('entrega') firmó personalmente.
    # 'firmada_con_excepcion' -- cada parte (entrega, recibe) firmó o tiene una
    #   excepción de firma declarada (al menos UNA de las dos firmó), y existe alguna
    #   excepción VIGENTE (no revocada) de CUALQUIER rol
    #   (también la del elaborador): con una excepción declarada nunca es
    #   `firmada_completa` (AREA-07-10).
    # 'sellada_con_excepcion' -- ninguna de las dos partes (entrega, recibe) firmó
    #   personalmente: cada una está cubierta SOLO por una excepción declarada. El acta
    #   conserva únicamente el sello institucional; llamarla `firmada_*` sería mentir.
    # 'firmada_completa' -- AMBAS partes firmaron personalmente (bajo el gate
    #   `clasificados`/`todos`, el veredicto de conformidad exige este estado).
    completitud: Literal[
        "sellada",
        "firma_personal_pendiente",
        "firmada_receptor",
        "firmada_remitente",
        "firmada_completa",
        "firmada_con_excepcion",
        "sellada_con_excepcion",
    ]
    # Cripto+revocación de TODO lo presente (sello + firmas personales
    # presentes) -- vacuously True si no hay nada que verificar más allá del
    # sello. `False` si el sello no está presente/válido, o si CUALQUIER
    # firma personal presente no valida.
    valida: bool
    acreditado: bool = False
    # Auto-traslado: la MISMA identidad envió y recibió la transferencia
    # (`enviada_por == decidida_por`). Si además `completitud=='firmada_completa'`,
    # ambas firmas personales son de la misma persona -> SIN control dual
    # (separación de funciones). Se expone como OBSERVACIÓN de conformidad
    # (dictamen del auditor archivístico) -- el par de roles del FUID no exige
    # personas naturales distintas, así que no se impide, solo se señala.
    auto_traslado: bool = False
    # Firma personal del ELABORADOR ("Elaborado por" del FUID = quien creó la
    # transferencia, Inc.3). ORTOGONAL a `completitud`: el elaborador es un
    # firmante ADITIVO, no una parte del traspaso, así que su firma NO
    # condiciona `firmada_completa` (entrega+recibe) -- se reporta aparte.
    elaborador_firmado: bool = False
    # AREA-07-10: excepciones de firma declaradas (constancias). `completitud ==
    # 'firmada_con_excepcion'` solo si cada PARTE (entrega, recibe) tiene firma personal o
    # excepción y existe alguna excepción (de cualquier rol). NUNCA `firmada_completa`.
    excepciones: list[TransferenciaActaFirmaExcepcion] = Field(default_factory=list)
    # Avisos agregados de TODAS las verificaciones del acta (sello, firmas personales y
    # constancias), sin repetir. Vacío = nada que avisar.
    advertencias: list[str] = Field(default_factory=list)
    # AREA-07-10: constancias VIGENTES sin firma (legadas, anteriores a la migración 061). El
    # acta no puede declararse íntegramente válida mientras una excepción no tenga sello.
    constancias_sin_firma: int = 0
    # A2: motivos por los que la CADENA de constancias no cuadra (enlace roto: huella distinta,
    # sustituyeA/revocacionSha256/constanciaSha256 que no coinciden con las filas o con el artefacto
    # firmado). Vacío = cuadra. Cualquier motivo fuerza `valida=false`.
    cadena_motivos: list[str] = Field(default_factory=list)
    # Modo de la política XAdES de la ENTIDAD con que se calculó `completitud`
    # (`off`/`clasificados`/`todos`): parámetro del tenant, entorno como respaldo.
    politica_xades: str = "off"
