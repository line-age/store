"""A QGIS project and a default style for the `qgis` views, installed into the database so
that opening Lineage in QGIS takes no setup.

The project and style were made in QGIS 4.2 and templated: `@CONNECTION@` stands for the
PostGIS connection, which differs per deployment and is filled in at install time.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import zipfile
from importlib import resources

from psycopg import Connection

PROJECT_NAME = "Lineage"


def connection_string(host: str, port: int, dbname: str, user: str | None, password: str | None) -> str:
    parts = [f"dbname='{dbname}'", f"host={host}", f"port={port}"]
    if user:
        parts.append(f"user='{user}'")
    if password:
        parts.append(f"password='{password}'")
    return " ".join(parts)


def project_qgz(connection: str) -> bytes:
    qgs = resources.files(__package__).joinpath("lineage.qgs").read_text().replace("@CONNECTION@", connection)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{PROJECT_NAME}.qgs", qgs)
    return buf.getvalue()


def install(conn: Connection, connection: str, dbname: str) -> None:
    """Write the project to qgis.qgis_projects and make the style the default for
    qgis.sections. Safe to run again: both are replaced."""
    meta = {"last_modified_time": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S"),
            "last_modified_user": "lineage-store"}
    conn.execute("""INSERT INTO qgis.qgis_projects (name, metadata, content) VALUES (%s, %s, %s)
                    ON CONFLICT (name) DO UPDATE SET metadata = EXCLUDED.metadata, content = EXCLUDED.content""",
                 (PROJECT_NAME, json.dumps(meta), project_qgz(connection)))
    qml = resources.files(__package__).joinpath("sections.qml").read_text()
    conn.execute("""UPDATE public.layer_styles SET useasdefault = false
                    WHERE f_table_schema = 'qgis' AND f_table_name = 'sections'""")
    conn.execute("""DELETE FROM public.layer_styles
                    WHERE f_table_schema = 'qgis' AND f_table_name = 'sections' AND stylename = %s""", (PROJECT_NAME,))
    conn.execute("""INSERT INTO public.layer_styles (f_table_catalog, f_table_schema, f_table_name, f_geometry_column,
                                                     stylename, styleqml, useasdefault, description, type)
                    VALUES (%s, 'qgis', 'sections', 'geom', %s, %s::xml, true,
                            'Lineage: time slider on time_start and time_end, proposed ends dashed', 'Line')""",
                 (dbname, PROJECT_NAME, qml))
