#!/bin/sh
# Load historical county boundaries into reference.historical_county.
#
# Source: Newberry Library, Atlas of Historical County Boundaries
# (https://digital.newberry.org/ahcb/), free to reuse for any lawful purpose. We use the
# national file generalized to 0.001 degree (about 100 m), and keep the boundary versions
# still in force in 1929 or later. Runs in the GDAL image; PG_URL is a GDAL PostgreSQL
# connection ("PG:host=... dbname=..."). Does nothing if the table already has rows, unless
# FORCE=1.
set -eu

URL="${COUNTIES_URL:-https://digital.newberry.org/ahcb/downloads/gis/US_AtlasHCB_Counties_Gen001.zip}"
FROM="${COUNTIES_FROM:-19290101}"
LAYER=US_HistCounties_Gen001

have=$(ogrinfo -ro -q -sql "SELECT COUNT(*) AS n FROM reference.historical_county" "$PG_URL" | sed -n 's/.*n (Integer[0-9]*) = //p')
if [ "${FORCE:-0}" != 1 ] && [ "${have:-0}" -gt 0 ]; then
    echo "reference.historical_county already has $have rows; FORCE=1 reloads"
    exit 0
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
curl -fsSL -o "$work/counties.zip" "$URL"
# The zip has stray bytes at the front: unzip copes (and says so), GDAL's /vsizip doesn't.
unzip -qjo "$work/counties.zip" "*/${LAYER}.*" -d "$work" || [ -f "$work/${LAYER}.shp" ]

OGR_TRUNCATE=YES ogr2ogr -append -f PostgreSQL "$PG_URL" "$work/${LAYER}.shp" \
    -nln reference.historical_county -nlt MULTIPOLYGON -t_srs EPSG:4326 \
    -dialect OGRSQL -sql "SELECT ID_NUM AS id_num, NAME AS name, FULL_NAME AS full_name,
        STATE_TERR AS state_terr, FIPS AS fips, START_DATE AS start_date, END_DATE AS end_date,
        CHANGE AS change, CITATION AS citation FROM ${LAYER} WHERE END_N >= ${FROM}"

echo "loaded $(ogrinfo -ro -q -sql "SELECT COUNT(*) AS n FROM reference.historical_county" "$PG_URL" | sed -n 's/.*n (Integer[0-9]*) = //p') county versions"
