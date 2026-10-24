import { geoGraticule10, geoNaturalEarth1, geoPath } from "d3-geo";
import type { Feature, FeatureCollection, Geometry } from "geojson";
import { useMemo } from "react";
import { feature } from "topojson-client";
import type { GeometryCollection, Topology } from "topojson-specification";
import world from "world-atlas/countries-110m.json";

const WIDTH = 960;
const HEIGHT = 500;

export interface MapLabel {
  lon: number;
  lat: number;
  text: string;
}

export interface MapFocus {
  code: string;
  lon: number;
  lat: number;
  zoom: number;
}

const topology = world as unknown as Topology<{ countries: GeometryCollection }>;
const countries = feature(topology, topology.objects.countries) as FeatureCollection<Geometry>;
const projection = geoNaturalEarth1().fitSize([WIDTH, HEIGHT], countries);
const path = geoPath(projection);
const graticule = path(geoGraticule10()) ?? "";
const shapes = countries.features.map((f: Feature<Geometry>) => ({
  id: String(f.id ?? ""),
  d: path(f) ?? "",
}));

/** Computes the offset so the focus point appears centred and zoomed in. */
export function focusTransform(focus: MapFocus | null): { x: number; y: number; k: number } {
  if (!focus) return { x: 0, y: 0, k: 1 };
  const point = projection([focus.lon, focus.lat]);
  if (!point) return { x: 0, y: 0, k: 1 };
  const k = Math.min(10, Math.max(1, focus.zoom));
  return { x: WIDTH / 2 - point[0] * k, y: HEIGHT / 2 - point[1] * k, k };
}

/** Holographic world map (bundled locally, no external tiles). */
export function WorldMap({
  focus,
  visited = [],
  heat,
  labels = [],
}: {
  focus: MapFocus | null;
  visited?: string[];
  heat?: Record<string, number>;
  labels?: MapLabel[];
}) {
  const maxHeat = heat ? Math.max(1, ...Object.values(heat)) : 1;
  const { x, y, k } = focusTransform(focus);
  const marker = useMemo(() => (focus ? projection([focus.lon, focus.lat]) : null), [focus]);
  return (
    <svg className="worldmap" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label="World map">
      <g className="worldmap-zoom" style={{ transform: `translate(${x}px, ${y}px) scale(${k})` }}>
        <path className="graticule" d={graticule} />
        {shapes.map((s) => (
          <path
            key={s.id || s.d.slice(0, 12)}
            d={s.d}
            className={`country${s.id === focus?.code ? " active" : ""}${
              visited.includes(s.id) ? " visited" : ""
            }${heat?.[s.id] ? " heat" : ""}`}
            style={
              heat?.[s.id]
                ? { fillOpacity: 0.25 + 0.65 * ((heat[s.id] ?? 0) / maxHeat) }
                : undefined
            }
          >
            {heat?.[s.id] ? <title>{heat[s.id]} failed attempts</title> : null}
          </path>
        ))}
        {labels.map((label) => {
          const point = projection([label.lon, label.lat]);
          if (!point) return null;
          return (
            <g
              key={`${label.lon},${label.lat}`}
              className="map-label"
              transform={`translate(${point[0]} ${point[1]}) scale(${1 / k})`}
            >
              <text y={-10}>{label.text}</text>
            </g>
          );
        })}
        {marker && (
          <g transform={`translate(${marker[0]} ${marker[1]}) scale(${1 / k})`}>
            <circle className="ping" r="18" />
            <circle className="dot" r="4" />
          </g>
        )}
      </g>
    </svg>
  );
}
