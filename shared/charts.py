"""Chart defaults: readable type, colour-blind-safe palette, and axis titles with units.

Every helper requires axis titles. Put the unit in the title, e.g. "Parcels (count)",
"Distance (km)", "Stock on hand (kits)". Parcels and meals must never share one axis.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# Okabe-Ito palette: distinguishable with common colour-vision deficiencies.
PALETTE: list[str] = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9", "#F0E442", "#000000"]
STATUS_COLORS: dict[str, str] = {"ok": "#009E73", "warning": "#E69F00", "critical": "#D55E00", "stale": "#7F7F7F"}
FONT_FAMILY = "Segoe UI, Tahoma, 'Noto Naskh Arabic', Arial, sans-serif"


def apply_defaults(fig: go.Figure, x_title: str, y_title: str, title: str | None = None, height: int = 380) -> go.Figure:
    if not x_title or not y_title:
        raise ValueError("Charts need axis titles with units")
    fig.update_layout(
        template="plotly_white",
        title=title,
        font={"family": FONT_FAMILY, "size": 15},
        colorway=PALETTE,
        height=height,
        margin={"l": 60, "r": 20, "t": 50 if title else 20, "b": 60},
        legend={"title": None},
    )
    fig.update_xaxes(title_text=x_title)
    fig.update_yaxes(title_text=y_title)
    return fig


def bar(
    df: pd.DataFrame,
    x: str,
    y: str,
    x_title: str,
    y_title: str,
    color: str | None = None,
    title: str | None = None,
    horizontal: bool = False,
    barmode: str = "group",
) -> go.Figure:
    if horizontal:
        fig = px.bar(df, x=y, y=x, color=color, orientation="h", barmode=barmode, color_discrete_sequence=PALETTE)
        return apply_defaults(fig, y_title, x_title, title)
    fig = px.bar(df, x=x, y=y, color=color, barmode=barmode, color_discrete_sequence=PALETTE)
    return apply_defaults(fig, x_title, y_title, title)


def line(
    df: pd.DataFrame, x: str, y: str, x_title: str, y_title: str, color: str | None = None, title: str | None = None, markers: bool = True
) -> go.Figure:
    fig = px.line(df, x=x, y=y, color=color, markers=markers, color_discrete_sequence=PALETTE)
    return apply_defaults(fig, x_title, y_title, title)


def heatmap(
    matrix: Sequence[Sequence[float]],
    x_labels: Sequence[str],
    y_labels: Sequence[str],
    x_title: str,
    y_title: str,
    z_title: str,
    title: str | None = None,
) -> go.Figure:
    fig = go.Figure(
        go.Heatmap(
            z=matrix,
            x=list(x_labels),
            y=list(y_labels),
            colorscale="Blues",
            colorbar={"title": z_title},
            text=matrix,
            texttemplate="%{text}",
        )
    )
    fig.update_yaxes(autorange="reversed")
    return apply_defaults(fig, x_title, y_title, title, height=460)


def grid_map(
    df: pd.DataFrame,
    label: str,
    color: str | None = None,
    size: str | None = None,
    hover: Sequence[str] = (),
    title: str | None = None,
    areas: pd.DataFrame | None = None,
    height: int = 520,
) -> go.Figure:
    """Points on the invented 100 x 100 km grid (columns x_km, y_km). No real map tiles."""
    fig = px.scatter(
        df, x="x_km", y="y_km", color=color, size=size, text=label, hover_data=list(hover), color_discrete_sequence=PALETTE, size_max=40
    )
    fig.update_traces(textposition="top center")
    if areas is not None:
        fig.add_trace(
            go.Scatter(
                x=areas["x_km"],
                y=areas["y_km"],
                mode="text",
                text=areas["name_en"],
                textfont={"color": "#9a9a9a", "size": 13},
                hoverinfo="skip",
                showlegend=False,
            )
        )
    fig.update_xaxes(range=[0, 100], dtick=20)
    fig.update_yaxes(range=[0, 100], dtick=20, scaleanchor="x", scaleratio=1)
    return apply_defaults(fig, "Distance east (km, invented grid)", "Distance north (km, invented grid)", title, height)
