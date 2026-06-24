# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Proveedor de embeddings pluggable (E21, ADR-006).

Por defecto un STUB local determinista: NO usa modelo/LLM; mapea el texto a un
vector de dimensión fija por hashing de tokens (normalizado L2). Es suficiente
para el groundwork (ingesta + recuperación con pre-filtrado ACL) y testeable sin
infraestructura. Un proveedor real (modelo local / API) se conecta aquí sin
cambiar repositorio ni endpoints.
"""

from __future__ import annotations

import hashlib
import math
import re

from app.core.config import settings


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def embed(text: str, dim: int | None = None) -> list[float]:
    dim = dim or settings.embedding_dim
    vec = [0.0] * dim
    for tok in _tokens(text):
        h = int(hashlib.sha256(tok.encode()).hexdigest(), 16)
        idx = h % dim
        sign = 1.0 if (h >> 8) & 1 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


def to_pgvector(vec: list[float]) -> str:
    """Serializa a literal pgvector: '[v1,v2,...]'."""
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"
