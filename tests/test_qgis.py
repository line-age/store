"""The QGIS project and default style that `lineage-store qgis install` puts in the database."""

from __future__ import annotations

import io
import zipfile

from lineage_store import cli
from lineage_store.qgis import PROJECT_NAME, connection_string, project_qgz


def test_connection_string():
    assert connection_string("db.example", 5432, "lineage", None, None) == "dbname='lineage' host=db.example port=5432"
    assert connection_string("localhost", 47119, "lineage", "u", "p").endswith("user='u' password='p'")


def test_project_is_a_qgz_with_every_layer_connected():
    qgs = zipfile.ZipFile(io.BytesIO(project_qgz("dbname='x' host=h port=1"))).read(f"{PROJECT_NAME}.qgs").decode()
    assert "@CONNECTION@" not in qgs
    for view in ("sections", "removals", "proposed_links"):
        assert f'table=&quot;qgis&quot;.&quot;{view}&quot;' in qgs or f'table="qgis"."{view}"' in qgs
    assert 'startField="time_start"' in qgs and 'endField="time_end"' in qgs
    assert "setNavigationMode" in qgs                       # the open macro
    assert "password" not in qgs


def test_install_twice(database_url, sql, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", database_url)
    for _ in range(2):
        cli.main(["qgis", "install", "--host", "db.example", "--port", "5432"])
    assert "installed" in capsys.readouterr().out
    [p] = sql.execute("SELECT name, content FROM qgis.qgis_projects").fetchall()
    assert p["name"] == PROJECT_NAME and zipfile.is_zipfile(io.BytesIO(p["content"]))
    styles = sql.execute("SELECT stylename, useasdefault, styleqml::text AS qml FROM public.layer_styles "
                         "WHERE f_table_schema = 'qgis' AND f_table_name = 'sections'").fetchall()
    assert [(s["stylename"], s["useasdefault"]) for s in styles] == [(PROJECT_NAME, True)]
    assert 'startField="time_start"' in styles[0]["qml"]
