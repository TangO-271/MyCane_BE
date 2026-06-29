"""Shared helpers for the per-layer tile renderers.

Extracted from hotspot/disease/drought/flood/burn_scar which each carried
identical plot-id parsing and Polygon/MultiPolygon drawing code.
"""
from app.utils.format import parse_plot_id
import app.core.constants as const


def parse_tile_filters(plot_id, user_id):
    """Parse the optional plot_id / user_id query params to ints.

    plot_id is a PLT-xxx display id (via parse_plot_id); user_id is a plain int.
    user_id is only used when no plot_id is given. Returns (plot_id_int, user_id_int),
    either of which may be None.
    """
    plot_id_int = None
    if plot_id:
        try:
            plot_id_int = parse_plot_id(plot_id)
        except Exception:
            pass

    user_id_int = None
    if user_id and plot_id_int is None:
        try:
            user_id_int = int(user_id)  # user_id is a plain integer, not a PLT-xxx plot id
        except Exception:
            pass

    return plot_id_int, user_id_int


def draw_filled_polygon(draw, to_pixels, geom, fill_color, outline_color, width=2):
    """Draw a shapely Polygon/MultiPolygon as filled pixels.

    Recurses into MultiPolygon parts and punches transparent holes for interiors.
    """
    if geom.geom_type == 'Polygon':
        ext_coords = [to_pixels(lon, lat) for lon, lat in geom.exterior.coords]
        if len(ext_coords) >= 3:
            draw.polygon(ext_coords, fill=fill_color, outline=outline_color, width=width)
        for interior in geom.interiors:
            int_coords = [to_pixels(lon, lat) for lon, lat in interior.coords]
            if len(int_coords) >= 3:
                draw.polygon(int_coords, fill=(0, 0, 0, 0), outline=outline_color, width=width)
    elif geom.geom_type == 'MultiPolygon':
        for poly in geom.geoms:
            draw_filled_polygon(draw, to_pixels, poly, fill_color, outline_color, width)


def risk_colors(score):
    """Map a 0–1 risk score to (fill, outline) RGBA using the shared cutoffs."""
    if score >= const.RISK_COLOR_DANGER_CUTOFF:
        return const.RISK_FILL_DANGER, const.RISK_OUTLINE_DANGER
    if score >= const.RISK_COLOR_WARN_CUTOFF:
        return const.RISK_FILL_WARN, const.RISK_OUTLINE_WARN
    return const.RISK_FILL_OK, const.RISK_OUTLINE_OK
