#!/usr/bin/env python3
# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
#
"""
Migrate TRD (Tabla de Retención Documental) from FondeCund to OrpycaMCP.

Reads trdunificado.json and generates SQL INSERT statements compatible with
OrpycaMCP archive-service schema.

Usage:
    python3 migrate_trd_from_fondecund.py \
        /path/to/trdunificado.json \
        --tenant-slug fondecund \
        --output migrations/002_trd_fondecund.sql
"""

import json
import argparse
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4


def map_disposition(spanish_text: str | None) -> str:
    """Map Spanish disposition text to OrpycaMCP disposition codes."""
    if not spanish_text:
        return "eliminate"

    text_lower = spanish_text.lower().strip()

    if "conservación total" in text_lower or "conservar" in text_lower:
        return "conserve"
    elif "eliminación" in text_lower or "destruir" in text_lower:
        return "eliminate"
    elif "transferencia" in text_lower or "transferir" in text_lower:
        return "transfer"
    else:
        return "eliminate"


def generate_sql_from_json(
    json_file: Path, tenant_slug: str
) -> str:
    """Generate SQL migration from TRD JSON."""
    with open(json_file) as f:
        trd = json.load(f)

    institution = trd.get("entidad", "Unknown Institution")
    version = trd.get("version", "01")
    vigency_date = trd.get("vigencia", "2024-01-01")

    lines = [
        "-- " + "=" * 70,
        f"-- TRD Migration: {institution}",
        f"-- Version: {version}",
        f"-- Date: {vigency_date}",
        f"-- Tenant: {tenant_slug}",
        "-- " + "=" * 70,
        "",
        "BEGIN;",
        "",
        "-- Insert TRD series (document types/classifications)",
        "",
    ]

    series_count = 0
    subseries_count = 0

    for dependencia in trd.get("dependencias", []):
        for serie in dependencia.get("series", []):
            serie_code = str(serie.get("codigo", "")).zfill(3)
            serie_name = serie.get("nombre", "UNNAMED").upper()
            serie_retention = serie.get("retencion_central") or serie.get(
                "retencion_gestion"
            ) or 10
            serie_disposition = map_disposition(serie.get("disposicion_final"))

            # If serie has subseries, use series as parent; otherwise, insert it directly
            if serie.get("subseries"):
                # Series as parent (parent_serie_id = NULL)
                serie_id = f"'{uuid4()}'"
                lines.append(
                    f"INSERT INTO trd_series "
                    f"(id, code, name, retention_years, total_retention, disposition, is_active) "
                    f"VALUES ({serie_id}, '{serie_code}', '{escape_sql(serie_name)}', "
                    f"{serie_retention}, {serie_retention * 2}, '{serie_disposition}', true);"
                )
                series_count += 1

                # Insert subseries
                for subserie in serie.get("subseries", []):
                    subserie_code = f"{serie_code}.{str(subserie.get('codigo', '')).zfill(2)}"
                    subserie_name = subserie.get("nombre", "UNNAMED").upper()
                    subserie_retention = (
                        subserie.get("retencion_central")
                        or subserie.get("retencion_gestion")
                        or serie_retention
                    )
                    subserie_disposition = map_disposition(
                        subserie.get("disposicion_final")
                    )

                    subserie_id = f"'{uuid4()}'"
                    lines.append(
                        f"INSERT INTO trd_series "
                        f"(id, code, name, retention_years, total_retention, disposition, is_active) "
                        f"VALUES ({subserie_id}, '{subserie_code}', "
                        f"'{escape_sql(subserie_name)}', {subserie_retention}, "
                        f"{subserie_retention * 2}, '{subserie_disposition}', true);"
                    )
                    subseries_count += 1
            else:
                # Series without subseries — insert directly
                serie_id = f"'{uuid4()}'"
                lines.append(
                    f"INSERT INTO trd_series "
                    f"(id, code, name, retention_years, total_retention, disposition, is_active) "
                    f"VALUES ({serie_id}, '{serie_code}', '{escape_sql(serie_name)}', "
                    f"{serie_retention}, {serie_retention * 2}, '{serie_disposition}', true);"
                )
                series_count += 1

    lines.extend(
        [
            "",
            "COMMIT;",
            "",
            f"-- Summary: {series_count} series, {subseries_count} subseries inserted",
            "",
        ]
    )

    return "\n".join(lines)


def escape_sql(text: str) -> str:
    """Escape single quotes for SQL."""
    return text.replace("'", "''")


def main():
    parser = argparse.ArgumentParser(
        description="Migrate TRD from FondeCund JSON to OrpycaMCP SQL"
    )
    parser.add_argument("json_file", type=Path, help="Path to trdunificado.json")
    parser.add_argument(
        "--tenant-slug", default="fondecund", help="Tenant slug for the migration"
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output SQL file (default: print to stdout)",
    )

    args = parser.parse_args()

    if not args.json_file.exists():
        print(f"Error: File not found: {args.json_file}", file=sys.stderr)
        sys.exit(1)

    sql = generate_sql_from_json(args.json_file, args.tenant_slug)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(sql)
        print(f"✓ Generated {args.output}")
    else:
        print(sql)


if __name__ == "__main__":
    main()
