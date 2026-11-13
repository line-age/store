"""The QGIS project and default style that `lineage-store qgis install` puts in the database."""

from __future__ import annotations

import io
import re
import zipfile

from lineage_store import cli
from lineage_store.qgis import (
    DEV_CONNECTION,
    DEV_CONNECTION_NO_PASSWORD,
    PROJECT_NAME,
    connection_string,
    project_qgz,
)


def test_connection_string():
    assert connection_string("db.example", 5432, "lineage", None, None) == "dbname='lineage' host=db.example port=5432"
    assert connection_string("localhost", 47119, "lineage", "u", "p").endswith("user='u' password='p'")


def test_project_is_a_qgz_with_every_layer_connected():
    qgs = zipfile.ZipFile(io.BytesIO(project_qgz("dbname='x' host=h port=1"))).read(f"{PROJECT_NAME}.qgs").decode()
    assert "host=localhost" not in qgs
    for view in ("sections", "removals", "proposed_links", "counties", "claims"):
        assert re.search(rf"<datasource>dbname='x' host=h port=1 key='\w+' [^<]*table=\"qgis\".\"{view}\"", qgs), view
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


def test_the_repo_file_opens_on_the_compose_database():
    from importlib import resources
    qgs = resources.files("lineage_store.qgis").joinpath("lineage.qgs").read_text()
    assert qgs.count("<datasource>" + DEV_CONNECTION) == 5  # sections, removals, proposed links, counties, claims
    assert "host=" not in qgs.replace(DEV_CONNECTION, "").replace(DEV_CONNECTION_NO_PASSWORD, "")
    assert "<DefaultViewExtent" in qgs and "<mapcanvas" in qgs     # opens on the US, not on 0,0
    assert 'checked="Qt::Unchecked" expanded="1" id="Counties__' in qgs     # county lines start off
    assert 'checked="Qt::Checked" expanded="1" id="Removals__' in qgs                 # removals start on
    assert "&quot;qgis&quot;.&quot;counties&quot;" in qgs or '"qgis"."counties"' in qgs
    assert "tile.openstreetmap.org" in qgs
    assert 'name="Claims behind this section"' in qgs and 'name="Claims behind this removal"' in qgs


def test_counties_view_has_the_slider_fields(sql):
    sql.execute("""INSERT INTO reference.historical_county
                   (id_num, name, full_name, state_terr, start_date, end_date, geom)
                   VALUES (1, 'LA PAZ', 'LA PAZ', 'Arizona', '1983-04-27', '2000-12-31',
                           ST_Multi(ST_MakeEnvelope(-114.7, 33.4, -113.3, 34.3, 4326)))""")
    row = sql.execute("SELECT label, time_start, time_end FROM qgis.counties WHERE county_id = 1").fetchone()
    assert row["label"] == "La Paz, Arizona"
    assert (str(row["time_start"]), str(row["time_end"])) == ("1983-04-27", "2000-12-31")
    sql.execute("DELETE FROM reference.historical_county WHERE id_num = 1")
