-- migrate:up

-- Reference data: things we draw under the pipelines but don't make claims about.
CREATE SCHEMA IF NOT EXISTS reference;

-- County boundaries as they stood on each date, from the Newberry Library's Atlas of
-- Historical County Boundaries (generalized to 0.001 degree). Each row is one version of a
-- county, in force from start_date to end_date. Loaded by scripts/load-counties.sh, which
-- keeps the versions still in force in 1929 or later.
CREATE TABLE reference.historical_county (
    fid        serial PRIMARY KEY,     -- GDAL's row id; ogr2ogr fills it
    id_num     integer NOT NULL UNIQUE, -- the atlas's id for this county version
    name       text NOT NULL,
    full_name  text,
    state_terr text NOT NULL,
    fips       text,
    start_date date NOT NULL,
    end_date   date NOT NULL,
    change     text,
    citation   text,
    geom       geometry(MultiPolygon, 4326) NOT NULL
);
CREATE INDEX historical_county_geom ON reference.historical_county USING gist (geom);
CREATE INDEX historical_county_dates ON reference.historical_county (start_date, end_date);

-- What QGIS opens: the same time fields as qgis.sections, so one slider moves both.
CREATE VIEW qgis.counties AS
SELECT id_num AS county_id, initcap(full_name) || ', ' || state_terr AS label, state_terr, fips,
       start_date AS time_start, end_date AS time_end, change, citation, geom
FROM reference.historical_county;

-- migrate:down

DROP VIEW qgis.counties;
DROP TABLE reference.historical_county;
DROP SCHEMA reference;
