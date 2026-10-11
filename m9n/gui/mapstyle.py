"""The bundled map style for offline map files (OpenMapTiles schema), in light and dark.

Written for this app and licensed with it. It draws water, land cover and use, parks,
buildings, roads by class (with names), railways, boundaries and place names, tuned to be
read at a glance on the move. Both variants are generated from one layer list, so they
always draw the same features; only the palette differs.
"""
import json

SOURCE = "openmaptiles"

PALETTES = {
    "light": {
        "background": "#f3f0ea", "water": "#a9cbe8", "wood": "#cfe3c0", "grass": "#dcebcf", "park": "#c6e5b3",
        "residential": "#ebe6df", "industrial": "#e6e0e8", "building": "#dcd3cb", "building_line": "#c9bfb6",
        "casing": "#b9b1a8", "motorway": "#e9905e", "trunk": "#f2b25f", "primary": "#f7d070",
        "secondary": "#fbe6a2", "minor": "#ffffff", "path": "#a7907a", "rail": "#9b9b9b", "boundary": "#a58cb5",
        "text": "#2e2a26", "text_halo": "#ffffff", "water_text": "#3d6a91", "road_text": "#3a342e",
    },
    "dark": {
        "background": "#26262a", "water": "#1d3550", "wood": "#24352a", "grass": "#283a2c", "park": "#24402b",
        "residential": "#2e2e33", "industrial": "#33303a", "building": "#3a3a41", "building_line": "#45454d",
        "casing": "#18181b", "motorway": "#c46d3e", "trunk": "#b98840", "primary": "#a79242",
        "secondary": "#7e7550", "minor": "#55555e", "path": "#7d7266", "rail": "#6e6e75", "boundary": "#8d78a3",
        "text": "#ecebe8", "text_halo": "#1c1c1f", "water_text": "#8fb6dc", "road_text": "#e3e1dc",
    },
}

MAJOR = ["motorway", "trunk", "primary", "secondary"]
MINOR = ["tertiary", "minor", "service"]


def _zoom(*stops):
    """Linear interpolation of a value over zoom levels: _zoom(10, 1, 16, 8)."""
    return ["interpolate", ["linear"], ["zoom"], *stops]


def _layer(id_, type_, source_layer=None, paint=None, filter_=None, layout=None, minzoom=None):
    layer = {"id": id_, "type": type_}
    if source_layer:
        layer.update(source=SOURCE, **{"source-layer": source_layer})
    if filter_:
        layer["filter"] = filter_
    if minzoom is not None:
        layer["minzoom"] = minzoom
    if layout:
        layer["layout"] = layout
    layer["paint"] = paint or {}
    return layer


def _road(id_, classes, colour, width, minzoom=None, casing=False, c=None):
    paint = {"line-color": c["casing"] if casing else colour,
             "line-width": _zoom(*[v * (1 if not casing else 1) + (1.6 if casing and i % 2 else 0)
                                   for i, v in enumerate(width)])}
    return _layer(id_, "line", "transportation", paint,
                  ["in", ["get", "class"], ["literal", classes]], {"line-cap": "round", "line-join": "round"},
                  minzoom)


def layers(c):
    road_widths = {"motorway": (6, 1.5, 12, 4, 18, 22), "trunk": (6, 1.2, 12, 3.5, 18, 20),
                   "primary": (8, 1, 12, 3, 18, 18), "secondary": (9, 0.8, 12, 2.5, 18, 16),
                   "minor": (12, 0.6, 15, 2.5, 18, 12)}
    road_colours = {"motorway": c["motorway"], "trunk": c["trunk"], "primary": c["primary"],
                    "secondary": c["secondary"], "minor": c["minor"]}
    road_classes = {"motorway": ["motorway"], "trunk": ["trunk"], "primary": ["primary"],
                    "secondary": ["secondary"], "minor": MINOR}
    road_minzoom = {"motorway": 5, "trunk": 6, "primary": 8, "secondary": 9, "minor": 12}
    order = ["minor", "secondary", "primary", "trunk", "motorway"]  # Bigger roads drawn on top.
    result = [
        _layer("background", "background", paint={"background-color": c["background"]}),
        _layer("landuse-residential", "fill", "landuse", {"fill-color": c["residential"]},
               ["in", ["get", "class"], ["literal", ["residential", "suburb", "neighbourhood"]]]),
        _layer("landuse-industrial", "fill", "landuse", {"fill-color": c["industrial"]},
               ["in", ["get", "class"], ["literal", ["industrial", "commercial", "retail", "railway"]]]),
        _layer("landcover-wood", "fill", "landcover", {"fill-color": c["wood"]},
               ["==", ["get", "class"], "wood"]),
        _layer("landcover-grass", "fill", "landcover", {"fill-color": c["grass"]},
               ["in", ["get", "class"], ["literal", ["grass", "farmland", "wetland"]]]),
        _layer("park", "fill", "park", {"fill-color": c["park"]}),
        _layer("water", "fill", "water", {"fill-color": c["water"]}),
        _layer("waterway", "line", "waterway", {"line-color": c["water"], "line-width": _zoom(8, 0.5, 16, 3)}),
        _layer("building", "fill", "building", {"fill-color": c["building"], "fill-outline-color": c["building_line"]},
               minzoom=13),
        _layer("path", "line", "transportation", {"line-color": c["path"], "line-width": _zoom(14, 0.5, 18, 2)},
               ["in", ["get", "class"], ["literal", ["path", "track"]]], minzoom=14),
        _layer("rail", "line", "transportation", {"line-color": c["rail"], "line-width": _zoom(10, 0.5, 18, 3)},
               ["==", ["get", "class"], "rail"], minzoom=10),
    ]
    for kind in order:  # Casings first, so junctions join cleanly.
        lo, lo_w, mid, mid_w, hi, hi_w = road_widths[kind]
        result.append(_layer(f"road-{kind}-casing", "line", "transportation",
                             {"line-color": c["casing"], "line-width": _zoom(lo, lo_w + 0.8, mid, mid_w + 1.6,
                                                                             hi, hi_w + 3)},
                             ["in", ["get", "class"], ["literal", road_classes[kind]]],
                             {"line-cap": "round", "line-join": "round"}, max(road_minzoom[kind], 10)))
    for kind in order:
        lo, lo_w, mid, mid_w, hi, hi_w = road_widths[kind]
        result.append(_layer(f"road-{kind}", "line", "transportation",
                             {"line-color": road_colours[kind], "line-width": _zoom(lo, lo_w, mid, mid_w, hi, hi_w)},
                             ["in", ["get", "class"], ["literal", road_classes[kind]]],
                             {"line-cap": "round", "line-join": "round"}, road_minzoom[kind]))
    halo = {"text-color": c["text"], "text-halo-color": c["text_halo"], "text-halo-width": 1.5}
    result += [
        _layer("boundary", "line", "boundary", {"line-color": c["boundary"], "line-width": _zoom(3, 0.6, 10, 1.6)},
               ["<=", ["get", "admin_level"], 4]),
        _layer("water-name", "symbol", "water_name",
               {"text-color": c["water_text"], "text-halo-color": c["text_halo"], "text-halo-width": 1.2},
               layout={"text-field": ["get", "name"], "text-size": 13}),
        _layer("road-name", "symbol", "transportation_name", {**halo, "text-color": c["road_text"]},
               layout={"symbol-placement": "line", "text-field": ["get", "name"], "text-size": _zoom(13, 11, 18, 15)},
               minzoom=13),
        _layer("place-village", "symbol", "place", halo,
               ["in", ["get", "class"], ["literal", ["village", "suburb", "hamlet", "neighbourhood"]]],
               {"text-field": ["get", "name"], "text-size": _zoom(10, 11, 16, 15)}, minzoom=10),
        _layer("place-town", "symbol", "place", halo, ["==", ["get", "class"], "town"],
               {"text-field": ["get", "name"], "text-size": _zoom(8, 12, 14, 18)}, minzoom=7),
        _layer("place-city", "symbol", "place", halo, ["==", ["get", "class"], "city"],
               {"text-field": ["get", "name"], "text-size": _zoom(5, 13, 12, 22)}),
    ]
    return result


# libshumate requires a tile URL in the style even though the app supplies the tiles itself
# from the map file. A file: URL can never reach the network; tests prove it is never read.
PLACEHOLDER_TILES = "file:///nonexistent/neo-m9n-offline/{z}/{x}/{y}.pbf"


def style(variant="light", max_zoom=14, tiles_url=PLACEHOLDER_TILES):
    """The MapLibre style JSON for a variant; max_zoom is the file's, beyond which tiles are over-zoomed."""
    return json.dumps({
        "version": 8, "name": f"NEO-M9N Monitor offline ({variant})",
        "sources": {SOURCE: {"type": "vector", "tiles": [tiles_url], "minzoom": 0, "maxzoom": max_zoom}},
        "layers": layers(PALETTES[variant]),
    })
