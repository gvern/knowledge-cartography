from __future__ import annotations

import html
import json
import logging
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
from sklearn.neighbors import NearestNeighbors

from .config import Settings
from .schema import ClusteredItem, SourcePlatform

logger = logging.getLogger(__name__)

# Categorical color is identity — with ~285 clusters, per-cluster color is not a
# viable channel (8-hue ceiling; scatter's all-pairs CVD check only clears 3 slots
# even then). Color encodes platform instead; cluster identity rides direct labels,
# hover text, and the sidebar/search panel. Slots 1/2/3 of the validated dark
# categorical palette (see the dataviz skill's palette.md) — the only three that
# clear the all-pairs CVD floor together.
_SURFACE = "#1a1a19"
_PAGE = "#0d0d0d"
_TEXT_PRIMARY = "#ffffff"
_TEXT_SECONDARY = "#c3c2b7"
_TEXT_MUTED = "#898781"
_ACCENT = "#3987e5"  # categorical slot 1 (blue) — HUD chrome accent, not data
_HAIRLINE = "#2c2c2a"

_PLATFORM_COLORS: dict[SourcePlatform, str] = {
    SourcePlatform.INSTAGRAM: "#3987e5",  # slot 1 blue
    SourcePlatform.FACEBOOK: "#d95926",  # slot 2 orange
    SourcePlatform.MESSENGER: "#d95926",  # same Meta family as Facebook — no 4th hue, see comment above
    SourcePlatform.GOOGLE_SEARCH: "#199e70",  # slot 3 aqua
    SourcePlatform.YOUTUBE: "#199e70",
    SourcePlatform.CHROME_HISTORY: "#199e70",
    SourcePlatform.BOOKMARK: "#199e70",
}
_NOISE_COLOR = "#4a4a47"  # muted, off the categorical set — "no cluster", not an identity
_MAX_DIRECT_LABELS = 40


def _defer_script(html: str, script_id: str) -> str:
    """Swap `to_html`'s single executable `<script>` (present exactly once
    when `include_plotlyjs=False`, confirmed by inspection of Plotly's own
    output) for an inert one carrying an id — the browser parses it but
    never executes it (unrecognized `type`), so neither the JSON payload
    gets deserialized nor the WebGL/SVG figure gets built, until the
    tab-switch JS below explicitly re-activates it by id."""
    return html.replace("<script>", f'<script type="text/plotly-deferred" id="{script_id}">', 1)


def build_map(
    items: list[ClusteredItem], settings: Settings, output_name: str = "knowledge_map.html"
) -> Path:
    settings.ensure_dirs()

    fig = go.Figure()
    for platform in sorted({item.source for item in items}, key=lambda s: s.value):
        clustered = [item for item in items if item.source == platform and item.cluster_id != -1]
        noise = [item for item in items if item.source == platform and item.cluster_id == -1]
        color = _PLATFORM_COLORS.get(platform, _ACCENT)
        if clustered:
            fig.add_trace(_platform_trace(clustered, platform.value, color, is_noise=False))
        if noise:
            fig.add_trace(_platform_trace(noise, platform.value, _NOISE_COLOR, is_noise=True))

    summaries = _cluster_summaries(items)
    top_clusters = sorted(summaries.values(), key=lambda c: -c["count"])[:_MAX_DIRECT_LABELS]
    edges_trace = _constellation_trace(summaries)
    if edges_trace is not None:
        fig.add_trace(edges_trace)
    fig.add_trace(_anchor_trace(summaries.values()))
    fig.add_trace(_collection_highlight_trace())

    collections = _collections_summary(items)

    fig.update_layout(
        scene=dict(
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
            zaxis=dict(visible=False),
            bgcolor=_SURFACE,
            annotations=[_cluster_annotation(c) for c in top_clusters],
        ),
        showlegend=True,
        legend=dict(
            itemsizing="constant",
            bgcolor="rgba(0,0,0,0)",
            font=dict(color=_TEXT_SECONDARY),
        ),
        paper_bgcolor=_PAGE,
        autosize=True,
        margin=dict(l=0, r=0, t=0, b=0),
        hoverlabel=dict(bgcolor=_SURFACE, font=dict(color=_TEXT_PRIMARY)),
    )

    config = {
        "scrollZoom": True,
        "displayModeBar": True,
        "displaylogo": False,
        "responsive": True,
    }

    conversations = _conversation_summaries(items)
    timeline_fig = _build_timeline_figure(items, conversations)
    # Whichever tab isn't shown first loads its data but doesn't build the
    # actual Plotly figure (WebGL buffers, hover/SVG layers) until the user
    # switches to it — see the deferred-script trick below and the tab-switch
    # JS. Eagerly building BOTH a 250k-point 3D scene and a same-scale 2D
    # timeline at once is enough to crash the tab outright on a real dataset
    # this size; only one is ever needed at a time.
    default_tab = "conversations" if conversations else "clusters"

    def _html(figure: go.Figure, div_id: str, *, eager: bool) -> str:
        html = figure.to_html(
            include_plotlyjs="cdn" if eager else False,
            config=config,
            full_html=False,
            default_width="100%",
            default_height="100%",
            div_id=div_id,
        )
        return html if eager else _defer_script(html, f"{div_id}-script")

    plot_html = _html(fig, "cg-plot", eager=default_tab == "clusters")
    timeline_html = _html(timeline_fig, "cg-timeline-plot", eager=default_tab == "conversations")

    page = _render_page(
        plot_html,
        timeline_html,
        summaries,
        collections,
        conversations,
        total_items=len(items),
        default_tab=default_tab,
    )
    output_path = settings.output_dir / output_name
    output_path.write_text(page, encoding="utf-8")
    logger.info("Wrote knowledge map to %s", output_path)
    return output_path


def _platform_trace(
    items: list[ClusteredItem], platform_name: str, color: str, is_noise: bool
) -> go.Scatter3d:
    return go.Scatter3d(
        x=[item.x for item in items],
        y=[item.y for item in items],
        z=[item.z for item in items],
        mode="markers",
        name=f"{platform_name} (unclustered)" if is_noise else platform_name,
        showlegend=not is_noise,
        legendgroup=platform_name,
        marker=dict(
            size=3 if is_noise else 5,
            opacity=0.25 if is_noise else 0.8,
            color=color,
            line=dict(width=0.3, color=_SURFACE),
        ),
        text=[_hover_text(item) for item in items],
        hoverinfo="text",
        customdata=[_item_detail(item) for item in items],
    )


def _anchor_trace(cluster_values) -> go.Scatter3d:
    """One clickable beacon per cluster centroid — the on-map selection target."""
    clusters: list[dict] = list(cluster_values)
    return go.Scatter3d(
        x=[c["cx"] for c in clusters],
        y=[c["cy"] for c in clusters],
        z=[c["cz"] for c in clusters],
        mode="markers",
        name="cluster-anchors",
        showlegend=False,
        customdata=[c["id"] for c in clusters],
        text=[f"{c['label']} ({c['count']})" for c in clusters],
        hoverinfo="text",
        marker=dict(
            symbol="diamond",
            size=6,
            color=_ACCENT,
            opacity=0.9,
            line=dict(width=1, color=_TEXT_PRIMARY),
        ),
    )


def _constellation_trace(summaries: dict[int, dict]) -> go.Scatter3d | None:
    """Thin lines from each cluster centroid to its ~2 nearest neighboring
    centroids — turns the map from a cloud of dots into an actual
    *cartography*, where topically adjacent regions are visibly connected.
    Same nearest-neighbor-over-centroids approach as a k-NN graph, just drawn
    rather than exported; one line-mode trace with `None` separators keeps
    this to a single draw call instead of one trace per edge.
    """
    clusters = list(summaries.values())
    if len(clusters) < 2:
        return None

    coords = np.array([[c["cx"], c["cy"], c["cz"]] for c in clusters])
    n_neighbors = min(3, len(clusters))
    _, indices = NearestNeighbors(n_neighbors=n_neighbors).fit(coords).kneighbors(coords)

    seen: set[tuple[int, int]] = set()
    xs: list[float | None] = []
    ys: list[float | None] = []
    zs: list[float | None] = []
    for i, neighbor_idx in enumerate(indices):
        for j in neighbor_idx:
            if i == j:
                continue
            pair = (i, int(j)) if i < j else (int(j), i)
            if pair in seen:
                continue
            seen.add(pair)
            a, b = clusters[pair[0]], clusters[pair[1]]
            xs += [a["cx"], b["cx"], None]
            ys += [a["cy"], b["cy"], None]
            zs += [a["cz"], b["cz"], None]

    return go.Scatter3d(
        x=xs,
        y=ys,
        z=zs,
        mode="lines",
        name="constellation-edges",
        showlegend=False,
        hoverinfo="skip",
        line=dict(width=1.5, color=_ACCENT),
        opacity=0.22,
    )


def _collection_highlight_trace() -> go.Scatter3d:
    """Empty at load; the JS side populates x/y/z via Plotly.restyle when a
    collection is selected. Collections are the user's own curation and aren't
    spatially coherent the way an HDBSCAN cluster is, so instead of zooming to
    a (possibly huge, scattered) bounding box, selected items are highlighted
    in place across the whole map.
    """
    return go.Scatter3d(
        x=[],
        y=[],
        z=[],
        mode="markers",
        name="collection-highlight",
        showlegend=False,
        hoverinfo="skip",
        marker=dict(
            size=8,
            color="rgba(0,0,0,0)",
            line=dict(width=2, color=_TEXT_PRIMARY),
        ),
    )


def _collections_summary(items: list[ClusteredItem]) -> dict[str, dict]:
    summaries: dict[str, dict] = {}
    for item in items:
        for name in item.collections:
            summary = summaries.setdefault(name, {"name": name, "count": 0, "x": [], "y": [], "z": []})
            summary["count"] += 1
            summary["x"].append(item.x)
            summary["y"].append(item.y)
            summary["z"].append(item.z)
    return summaries


def _cluster_summaries(items: list[ClusteredItem]) -> dict[int, dict]:
    summaries: dict[int, dict] = {}
    for item in items:
        if item.cluster_id == -1:
            continue
        summary = summaries.setdefault(
            item.cluster_id,
            {
                "id": item.cluster_id,
                "label": item.cluster_label or f"Cluster {item.cluster_id}",
                "count": 0,
                "x_min": item.x,
                "x_max": item.x,
                "y_min": item.y,
                "y_max": item.y,
                "z_min": item.z,
                "z_max": item.z,
                "x_sum": 0.0,
                "y_sum": 0.0,
                "z_sum": 0.0,
            },
        )
        summary["count"] += 1
        summary["x_min"] = min(summary["x_min"], item.x)
        summary["x_max"] = max(summary["x_max"], item.x)
        summary["y_min"] = min(summary["y_min"], item.y)
        summary["y_max"] = max(summary["y_max"], item.y)
        summary["z_min"] = min(summary["z_min"], item.z)
        summary["z_max"] = max(summary["z_max"], item.z)
        summary["x_sum"] += item.x
        summary["y_sum"] += item.y
        summary["z_sum"] += item.z

    for summary in summaries.values():
        summary["cx"] = summary["x_sum"] / summary["count"]
        summary["cy"] = summary["y_sum"] / summary["count"]
        summary["cz"] = summary["z_sum"] / summary["count"]
        del summary["x_sum"], summary["y_sum"], summary["z_sum"]

    return summaries


def _conversation_summaries(items: list[ClusteredItem]) -> dict[str, dict]:
    """One entry per thread_id, ordered by nothing yet (caller sorts) — items
    with no thread (not a conversation-shaped source) or no timestamp (can't
    place on a time axis) are excluded."""
    summaries: dict[str, dict] = {}
    for item in items:
        # comparable_timestamp (not timestamp): a thread can mix items from
        # Messenger's naive-HTML and aware-JSON export formats, and min/max
        # across a naive and an aware datetime raises TypeError — see
        # KnowledgeItem.comparable_timestamp.
        when = item.comparable_timestamp
        if not item.thread_id or when is None:
            continue
        summary = summaries.setdefault(
            item.thread_id,
            {
                "thread_id": item.thread_id,
                "label": item.thread or item.thread_id,
                "count": 0,
                "first": when,
                "last": when,
            },
        )
        summary["count"] += 1
        summary["first"] = min(summary["first"], when)
        summary["last"] = max(summary["last"], when)
    return summaries


def _self_sender(items: list[ClusteredItem]) -> str:
    """Heuristic: whoever sent the most messages overall is very likely the
    export's owner, since they appear in every one of their own threads.
    Nothing in the export says so explicitly."""
    counts: dict[str, int] = {}
    for item in items:
        if item.sender:
            counts[item.sender] = counts.get(item.sender, 0) + 1
    return max(counts, key=lambda s: counts[s]) if counts else ""


def _build_timeline_figure(items: list[ClusteredItem], conversations: dict[str, dict]) -> go.Figure:
    conv_items = [item for item in items if item.thread_id and item.timestamp is not None]
    self_sender = _self_sender(conv_items)
    other_items = [item for item in conv_items if item.sender != self_sender]
    self_items = [item for item in conv_items if item.sender == self_sender]

    fig = go.Figure()
    if other_items:
        fig.add_trace(_timeline_trace(other_items, "Them", _TEXT_MUTED))
    if self_items:
        fig.add_trace(_timeline_trace(self_items, "Me", _ACCENT))

    ordered = sorted(conversations.values(), key=lambda c: c["last"], reverse=True)
    ordered_ids = [c["thread_id"] for c in ordered]
    fig.update_layout(
        xaxis=dict(color=_TEXT_SECONDARY, gridcolor=_HAIRLINE, zeroline=False),
        yaxis=dict(visible=False, categoryorder="array", categoryarray=ordered_ids),
        showlegend=True,
        legend=dict(itemsizing="constant", bgcolor="rgba(0,0,0,0)", font=dict(color=_TEXT_SECONDARY)),
        paper_bgcolor=_PAGE,
        plot_bgcolor=_SURFACE,
        autosize=True,
        margin=dict(l=0, r=0, t=10, b=30),
        hoverlabel=dict(bgcolor=_SURFACE, font=dict(color=_TEXT_PRIMARY)),
    )
    return fig


def _timeline_trace(items: list[ClusteredItem], name: str, color: str) -> go.Scattergl:
    return go.Scattergl(
        x=[item.timestamp for item in items],
        y=[item.thread_id for item in items],
        mode="markers",
        name=name,
        marker=dict(size=6, opacity=0.7, color=color, line=dict(width=0.3, color=_SURFACE)),
        text=[_timeline_hover(item) for item in items],
        hoverinfo="text",
        customdata=[_item_detail(item) for item in items],
    )


def _timeline_hover(item: ClusteredItem) -> str:
    summary = " ".join(item.text.split()) if item.text else "(no text)"
    if len(summary) > _HOVER_SUMMARY_MAX_CHARS:
        summary = summary[: _HOVER_SUMMARY_MAX_CHARS - 1].rstrip() + "…"
    when = item.timestamp.strftime("%d %b %Y %H:%M") if item.timestamp else ""
    sender = html.escape(item.sender) if item.sender else "Unknown"
    return f"<b>{sender}</b> · {when}<br>{html.escape(summary)}"


def _cluster_annotation(cluster: dict) -> dict:
    return dict(
        x=cluster["cx"],
        y=cluster["cy"],
        z=cluster["cz"],
        text=html.escape(cluster["label"]),
        showarrow=False,
        font=dict(color=_ACCENT, size=11, family="system-ui, -apple-system, sans-serif"),
        bgcolor="rgba(13,13,13,0.65)",
        bordercolor="rgba(57,135,229,0.4)",
        borderwidth=1,
        borderpad=3,
        opacity=0.9,
    )


_HOVER_SUMMARY_MAX_CHARS = 160


def _hover_text(item: ClusteredItem) -> str:
    summary = " ".join(item.text.split()) if item.text else "(no text)"
    if len(summary) > _HOVER_SUMMARY_MAX_CHARS:
        summary = summary[: _HOVER_SUMMARY_MAX_CHARS - 1].rstrip() + "…"

    lines = [
        f"<b>{html.escape(summary)}</b>",
        f"{item.cluster_label or f'Cluster {item.cluster_id}'} · {_humanize(item.source.value)}"
        f" ({_humanize(item.item_type.value)})",
    ]
    if item.url:
        lines.append(item.url)
    return "<br>".join(lines)


def _humanize(value: str) -> str:
    return value.replace("_", " ").capitalize()


def _item_detail(item: ClusteredItem) -> dict:
    """Full (untruncated) per-point data for the click-to-inspect panel and
    client-side full-text search — unlike `_hover_text`, which stays short
    for the hover tooltip."""
    return {
        "url": item.url or "",
        "text": item.text or "",
        "source": _humanize(item.source.value),
        "type": _humanize(item.item_type.value),
        "cluster": item.cluster_label or f"Cluster {item.cluster_id}",
        "collections": item.collections,
        "sender": item.sender,
        "when": item.timestamp.strftime("%d %b %Y %H:%M") if item.timestamp else "",
    }


def _render_page(
    plot_html: str,
    timeline_html: str,
    summaries: dict[int, dict],
    collections: dict[str, dict],
    conversations: dict[str, dict],
    total_items: int,
    default_tab: str,
) -> str:
    rows = sorted(summaries.values(), key=lambda c: -c["count"])
    sidebar_data = [
        {
            "id": c["id"],
            "label": c["label"],
            "count": c["count"],
            "cx": c["cx"],
            "cy": c["cy"],
            "cz": c["cz"],
            "x0": c["x_min"],
            "x1": c["x_max"],
            "y0": c["y_min"],
            "y1": c["y_max"],
            "z0": c["z_min"],
            "z1": c["z_max"],
        }
        for c in rows
    ]
    sidebar_rows = "\n".join(
        f'<li class="cg-row" data-idx="{i}"><span class="cg-row-label">{html.escape(c["label"])}</span>'
        f'<span class="cg-row-count">{c["count"]}</span></li>'
        for i, c in enumerate(rows)
    )

    collection_rows_data = sorted(collections.values(), key=lambda c: -c["count"])
    collection_data = [
        {"name": c["name"], "count": c["count"], "x": c["x"], "y": c["y"], "z": c["z"]}
        for c in collection_rows_data
    ]
    collection_rows = "\n".join(
        f'<li class="cg-row" data-idx="{i}"><span class="cg-row-label">{html.escape(c["name"])}</span>'
        f'<span class="cg-row-count">{c["count"]}</span></li>'
        for i, c in enumerate(collection_rows_data)
    )

    conversation_rows_data = sorted(conversations.values(), key=lambda c: c["last"], reverse=True)
    conversation_data = [
        {
            "id": c["thread_id"],
            "label": c["label"],
            "count": c["count"],
            "idx": i,
            "first": c["first"].isoformat(),
            "last": c["last"].isoformat(),
        }
        for i, c in enumerate(conversation_rows_data)
    ]
    conversation_rows = "\n".join(
        f'<li class="cg-row" data-idx="{i}"><span class="cg-row-label">{html.escape(c["label"])}</span>'
        f'<span class="cg-row-count">{c["count"]}</span></li>'
        for i, c in enumerate(conversation_rows_data)
    )

    def tab_class(name: str) -> str:
        return "cg-tab cg-tab-active" if name == default_tab else "cg-tab"

    def list_class(name: str) -> str:
        return "cg-list" if name == default_tab else "cg-list cg-tab-hidden"

    map_hidden = "" if default_tab == "clusters" else " cg-tab-hidden"
    timeline_hidden = "" if default_tab == "conversations" else " cg-tab-hidden"

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>Knowledge Cartography</title>
<style>
  :root {{
    color-scheme: dark;
    --page: {_PAGE};
    --surface: {_SURFACE};
    --text-primary: {_TEXT_PRIMARY};
    --text-secondary: {_TEXT_SECONDARY};
    --text-muted: {_TEXT_MUTED};
    --accent: {_ACCENT};
    --accent-glow: rgba(57, 135, 229, 0.45);
    --hairline: {_HAIRLINE};
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; height: 100%; background: var(--page); }}
  body {{
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    color: var(--text-primary);
    display: flex;
    flex-direction: column;
    position: relative;
  }}
  body::before {{
    content: "";
    position: fixed;
    inset: 0;
    pointer-events: none;
    background: radial-gradient(ellipse at 50% 40%, rgba(57, 135, 229, 0.06), transparent 60%);
    z-index: 0;
  }}
  @keyframes cg-pulse {{
    0%, 100% {{ text-shadow: 0 0 10px var(--accent-glow); }}
    50% {{ text-shadow: 0 0 20px var(--accent-glow), 0 0 34px var(--accent-glow); }}
  }}
  header {{
    padding: 14px 20px;
    border-bottom: 1px solid var(--hairline);
    display: flex;
    align-items: baseline;
    gap: 16px;
    flex-shrink: 0;
    position: relative;
    z-index: 1;
  }}
  header h1 {{
    font-size: 15px;
    font-weight: 600;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    margin: 0;
    color: var(--accent);
    animation: cg-pulse 4s ease-in-out infinite;
  }}
  header .cg-sub {{
    font-size: 12px;
    color: var(--text-muted);
  }}
  header .cg-stats {{
    margin-left: auto;
    display: flex;
    gap: 18px;
    font-size: 11px;
    letter-spacing: 0.04em;
    color: var(--text-secondary);
  }}
  header .cg-stats b {{
    color: var(--text-primary);
    font-variant-numeric: tabular-nums;
    font-weight: 600;
  }}
  main {{
    flex: 1;
    display: flex;
    min-height: 0;
    position: relative;
    z-index: 1;
  }}
  .cg-viewport {{
    position: relative;
    flex: 1;
    min-width: 0;
  }}
  #cg-plot {{
    width: 100%;
    height: 100%;
  }}
  .cg-corner {{
    position: absolute;
    width: 22px;
    height: 22px;
    border: 2px solid var(--accent);
    opacity: 0.55;
    pointer-events: none;
    z-index: 2;
    filter: drop-shadow(0 0 4px var(--accent-glow));
  }}
  .cg-corner-tl {{ top: 10px; left: 10px; border-right: none; border-bottom: none; }}
  .cg-corner-tr {{ top: 10px; right: 10px; border-left: none; border-bottom: none; }}
  .cg-corner-bl {{ bottom: 10px; left: 10px; border-right: none; border-top: none; }}
  .cg-corner-br {{ bottom: 10px; right: 10px; border-left: none; border-top: none; }}
  aside {{
    width: 300px;
    flex-shrink: 0;
    border-left: 1px solid var(--hairline);
    display: flex;
    flex-direction: column;
    background: var(--surface);
  }}
  .cg-tabs {{
    display: flex;
    gap: 2px;
    padding: 12px 14px 8px;
  }}
  .cg-tab {{
    flex: 1;
    padding: 6px 0;
    font-size: 11px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    text-align: center;
    color: var(--text-muted);
    background: var(--page);
    border: 1px solid var(--hairline);
    cursor: pointer;
    transition: color 0.12s ease, border-color 0.12s ease;
  }}
  .cg-tab:first-child {{ border-radius: 4px 0 0 4px; }}
  .cg-tab:last-child {{ border-radius: 0 4px 4px 0; border-left: none; }}
  .cg-tab.cg-tab-active {{
    color: var(--accent);
    border-color: var(--accent);
  }}
  #cg-search {{
    margin: 0 14px 10px;
    padding: 8px 10px;
    background: var(--page);
    border: 1px solid var(--hairline);
    border-radius: 4px;
    color: var(--text-primary);
    font-size: 13px;
    transition: border-color 0.15s ease, box-shadow 0.15s ease;
  }}
  #cg-search:focus {{
    outline: none;
    border-color: var(--accent);
    box-shadow: 0 0 0 1px var(--accent), 0 0 10px var(--accent-glow);
  }}
  .cg-list {{
    list-style: none;
    margin: 0;
    padding: 0 0 14px;
    overflow-y: auto;
    flex: 1;
  }}
  .cg-list.cg-tab-hidden {{ display: none; }}
  .cg-row {{
    padding: 7px 14px;
    font-size: 12.5px;
    color: var(--text-secondary);
    cursor: pointer;
    display: flex;
    justify-content: space-between;
    gap: 8px;
    border-left: 2px solid transparent;
    transition: background 0.12s ease, border-color 0.12s ease, color 0.12s ease;
  }}
  .cg-row:hover, .cg-row.cg-active {{
    background: rgba(57, 135, 229, 0.1);
    border-left-color: var(--accent);
    color: var(--text-primary);
  }}
  .cg-row-label {{
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
  .cg-row-count {{
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
    flex-shrink: 0;
  }}
  .cg-row.cg-hidden {{ display: none; }}
  #cg-search-count {{
    display: block;
    margin: -4px 14px 8px;
    font-size: 11px;
    color: var(--text-muted);
    min-height: 14px;
  }}
  .cg-inspector {{
    position: fixed;
    right: 316px;
    bottom: 16px;
    width: 420px;
    max-width: calc(100vw - 340px);
    max-height: 45vh;
    display: flex;
    flex-direction: column;
    background: rgba(13, 13, 13, 0.96);
    border: 1px solid var(--hairline);
    border-left: 2px solid var(--accent);
    border-radius: 6px;
    box-shadow: 0 4px 24px rgba(0, 0, 0, 0.55);
    opacity: 0;
    transform: translateY(12px);
    pointer-events: none;
    transition: opacity 0.18s ease, transform 0.18s ease;
    z-index: 4;
  }}
  .cg-inspector.cg-inspector-visible {{
    opacity: 1;
    transform: translateY(0);
    pointer-events: auto;
  }}
  .cg-inspector-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 8px 10px 14px;
    border-bottom: 1px solid var(--hairline);
    flex-shrink: 0;
  }}
  .cg-inspector-meta {{
    font-size: 11px;
    letter-spacing: 0.03em;
    color: var(--text-muted);
  }}
  .cg-inspector-close {{
    background: none;
    border: none;
    color: var(--text-muted);
    font-size: 16px;
    line-height: 1;
    cursor: pointer;
    padding: 4px 8px;
  }}
  .cg-inspector-close:hover {{ color: var(--text-primary); }}
  .cg-inspector-body {{
    padding: 12px 14px 14px;
    overflow-y: auto;
    font-size: 13px;
    line-height: 1.55;
    color: var(--text-primary);
    white-space: pre-wrap;
  }}
  .cg-inspector-collections {{
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    padding: 0 14px 10px;
  }}
  .cg-chip {{
    font-size: 11px;
    padding: 3px 9px;
    border-radius: 12px;
    border: 1px solid var(--accent);
    color: var(--accent);
    cursor: pointer;
    transition: background 0.12s ease;
  }}
  .cg-chip:hover {{ background: rgba(57, 135, 229, 0.15); }}
  .cg-inspector-footer {{
    padding: 10px 14px;
    border-top: 1px solid var(--hairline);
    flex-shrink: 0;
  }}
  .cg-inspector-link {{
    color: var(--accent);
    font-size: 12.5px;
    text-decoration: none;
  }}
  .cg-inspector-link:hover {{ text-decoration: underline; }}
  .cg-timeline-viewport {{
    position: relative;
    flex: 1;
    min-width: 0;
  }}
  .cg-timeline-viewport.cg-tab-hidden, .cg-viewport.cg-tab-hidden {{ display: none; }}
  #cg-timeline-plot {{
    width: 100%;
    height: 100%;
  }}
</style>
</head>
<body>
<header>
  <h1>Knowledge Cartography</h1>
  <span class="cg-sub">
    drag to orbit, scroll to zoom — click a conversation to focus it, click a point to open it
  </span>
  <span class="cg-stats">
    <span><b>{total_items:,}</b> items</span>
    <span><b>{len(conversation_data)}</b> conversations</span>
    <span><b>{len(rows)}</b> clusters</span>
  </span>
</header>
<main>
  <div class="cg-viewport{map_hidden}">
    {plot_html}
    <div class="cg-corner cg-corner-tl"></div>
    <div class="cg-corner cg-corner-tr"></div>
    <div class="cg-corner cg-corner-bl"></div>
    <div class="cg-corner cg-corner-br"></div>
  </div>
  <div class="cg-timeline-viewport{timeline_hidden}">
    {timeline_html}
  </div>
  <aside>
    <div class="cg-tabs">
      <div class="{tab_class("conversations")}" data-tab="conversations">Conversations</div>
      <div class="{tab_class("clusters")}" data-tab="clusters">Clusters</div>
      <div class="{tab_class("collections")}" data-tab="collections">Collections</div>
    </div>
    <input id="cg-search" type="text" placeholder="Search everything..." autocomplete="off" />
    <span id="cg-search-count"></span>
    <ul id="cg-list-conversations" class="{list_class("conversations")}">
      {conversation_rows}
    </ul>
    <ul id="cg-list-clusters" class="{list_class("clusters")}">
      {sidebar_rows}
    </ul>
    <ul id="cg-list-collections" class="{list_class("collections")}">
      {collection_rows}
    </ul>
  </aside>
</main>
<div id="cg-inspector" class="cg-inspector">
  <div class="cg-inspector-header">
    <span id="cg-inspector-meta" class="cg-inspector-meta"></span>
    <button id="cg-inspector-close" class="cg-inspector-close" aria-label="Close">&times;</button>
  </div>
  <div id="cg-inspector-body" class="cg-inspector-body"></div>
  <div id="cg-inspector-collections" class="cg-inspector-collections"></div>
  <div id="cg-inspector-footer" class="cg-inspector-footer"></div>
</div>
<script>
  const CG_CLUSTERS = {json.dumps(sidebar_data)};
  const CG_COLLECTIONS = {json.dumps(collection_data)};
  const CG_CONVERSATIONS = {json.dumps(conversation_data)};
  const CG_DEFAULT_TAB = {json.dumps(default_tab)};
  const CG_NON_ITEM_TRACES = new Set(["cluster-anchors", "collection-highlight", "constellation-edges"]);
  const plotDiv = document.getElementById("cg-plot");
  const timelineDiv = document.getElementById("cg-timeline-plot");
  const searchInput = document.getElementById("cg-search");
  const searchCount = document.getElementById("cg-search-count");
  const clusterRows = document.querySelectorAll("#cg-list-clusters .cg-row");
  const collectionRows = document.querySelectorAll("#cg-list-collections .cg-row");
  const conversationRows = document.querySelectorAll("#cg-list-conversations .cg-row");
  const clustersById = new Map(CG_CLUSTERS.map((c) => [c.id, c]));
  const collectionsByName = new Map(CG_COLLECTIONS.map((c, i) => [c.name, i]));

  // The map (3D, hundreds of thousands of points) and the timeline (same
  // scale) are each expensive to build — eagerly building both at once was
  // enough to crash the tab outright on a real dataset. Whichever isn't the
  // default tab ships as an inert <script type="text/plotly-deferred"> (see
  // _defer_script in viz.py) that only gets executed — and only then is its
  // one-time setup (search index, click handler) built — the first time its
  // tab is actually opened.
  let mapReady = false;
  let timelineReady = false;
  let highlightTraceIndex = -1;
  const searchIndex = [];

  function activateDeferredScript(scriptId) {{
    const el = document.getElementById(scriptId);
    if (!el) return; // already eager-loaded — nothing to activate
    const script = document.createElement("script");
    script.textContent = el.textContent;
    el.replaceWith(script);
  }}

  function ensureMapReady() {{
    if (mapReady) return;
    mapReady = true;
    activateDeferredScript("cg-plot-script");
    highlightTraceIndex = plotDiv.data.findIndex((t) => t.name === "collection-highlight");
    // Full-text search index: built once from data already loaded for hover/click,
    // no extra payload — every point's full text already ships in its customdata.
    plotDiv.data.forEach((trace) => {{
      if (CG_NON_ITEM_TRACES.has(trace.name)) return;
      (trace.customdata || []).forEach((detail, i) => {{
        if (detail && typeof detail === "object" && detail.text) {{
          searchIndex.push(
            {{ x: trace.x[i], y: trace.y[i], z: trace.z[i], text: detail.text.toLowerCase() }}
          );
        }}
      }});
    }});
    plotDiv.on("plotly_click", (eventdata) => {{
      const points = eventdata.points || [];
      const anchorPoint = points.find((p) => p.data.name === "cluster-anchors");
      if (anchorPoint) {{
        const cluster = clustersById.get(anchorPoint.customdata);
        if (cluster) zoomToCluster(cluster);
        return;
      }}
      // The highlight ring can sit on top of the real point at the same coordinates —
      // scan all overlapping points for one with real detail rather than trusting points[0].
      const detailPoint = points.find(
        (p) => p.customdata && typeof p.customdata === "object" && p.customdata.text !== undefined
      );
      if (detailPoint) openInspector(detailPoint.customdata);
    }});
    // Plotly's own embedded init script runs the moment the parser reaches it (or,
    // for a lazily-activated one, the moment activateDeferredScript runs it above) —
    // possibly before the sidebar/flex layout has settled, so a 3D scene can size
    // itself off a stale (e.g. pre-flex, full-window) container width. Re-measuring
    // here, after that's guaranteed to have happened, catches the real size.
    Plotly.Plots.resize(plotDiv);
  }}

  function ensureTimelineReady() {{
    if (timelineReady) return;
    timelineReady = true;
    activateDeferredScript("cg-timeline-plot-script");
    timelineDiv.on("plotly_click", (eventdata) => {{
      const detailPoint = (eventdata.points || []).find(
        (p) => p.customdata && typeof p.customdata === "object" && p.customdata.text !== undefined
      );
      if (detailPoint) openInspector(detailPoint.customdata);
    }});
    Plotly.Plots.resize(timelineDiv);
  }}

  function zoomToCluster(c) {{
    ensureMapReady();
    const padX = Math.max((c.x1 - c.x0) * 0.4, 0.5);
    const padY = Math.max((c.y1 - c.y0) * 0.4, 0.5);
    const padZ = Math.max((c.z1 - c.z0) * 0.4, 0.5);
    Plotly.relayout(plotDiv, {{
      "scene.xaxis.range": [c.x0 - padX, c.x1 + padX],
      "scene.yaxis.range": [c.y0 - padY, c.y1 + padY],
      "scene.zaxis.range": [c.z0 - padZ, c.z1 + padZ],
    }});
  }}

  // Categorical y-axis: Plotly assigns integer positions 0,1,2... in the
  // order given by categoryarray at layout time, matching CG_CONVERSATIONS'
  // idx — isolate one row by range, and zoom x to that thread's own span
  // (with a floor so a single-message thread still gets a visible window).
  function zoomToConversation(c) {{
    ensureTimelineReady();
    const first = new Date(c.first).getTime();
    const last = new Date(c.last).getTime();
    const padMs = Math.max((last - first) * 0.15, 1000 * 60 * 60 * 24);
    Plotly.relayout(timelineDiv, {{
      "yaxis.range": [c.idx - 0.6, c.idx + 0.6],
      "xaxis.range": [new Date(first - padMs), new Date(last + padMs)],
    }});
  }}

  // --- highlight: shared by collections and full-text search (one active at a time) ---
  // Neither is spatially coherent the way an HDBSCAN cluster is, so matches are
  // highlighted in place across the whole map rather than zoomed to.
  function clearHighlight() {{
    ensureMapReady();
    Plotly.restyle(plotDiv, {{ x: [[]], y: [[]], z: [[]] }}, [highlightTraceIndex]);
  }}
  function setHighlight(xs, ys, zs) {{
    ensureMapReady();
    Plotly.restyle(plotDiv, {{ x: [xs], y: [ys], z: [zs] }}, [highlightTraceIndex]);
  }}

  let activeCollectionIdx = null;
  function deselectCollection() {{
    activeCollectionIdx = null;
    collectionRows.forEach((r) => r.classList.remove("cg-active"));
  }}
  function selectCollection(idx, row) {{
    const isToggleOff = activeCollectionIdx === idx;
    collectionRows.forEach((r) => r.classList.remove("cg-active"));
    if (isToggleOff) {{
      activeCollectionIdx = null;
      clearHighlight();
      return;
    }}
    searchInput.value = "";
    searchCount.textContent = "";
    activeCollectionIdx = idx;
    row.classList.add("cg-active");
    const c = CG_COLLECTIONS[idx];
    setHighlight(c.x, c.y, c.z);
  }}

  // --- tabs: switch between browsing conversations, clusters, and collections ---
  const tabs = document.querySelectorAll(".cg-tab");
  const lists = {{
    conversations: document.getElementById("cg-list-conversations"),
    clusters: document.getElementById("cg-list-clusters"),
    collections: document.getElementById("cg-list-collections"),
  }};
  const mapViewport = document.querySelector(".cg-viewport");
  const timelineViewport = document.querySelector(".cg-timeline-viewport");
  function activateTab(tabName) {{
    tabs.forEach((t) => t.classList.toggle("cg-tab-active", t.dataset.tab === tabName));
    Object.entries(lists).forEach(([name, list]) => {{
      list.classList.toggle("cg-tab-hidden", name !== tabName);
    }});
    const showTimeline = tabName === "conversations";
    timelineViewport.classList.toggle("cg-tab-hidden", !showTimeline);
    mapViewport.classList.toggle("cg-tab-hidden", showTimeline);
    if (showTimeline) {{ ensureTimelineReady(); }} else {{ ensureMapReady(); }}
    Plotly.Plots.resize(showTimeline ? timelineDiv : plotDiv);
  }}
  tabs.forEach((tab) => {{
    tab.addEventListener("click", () => {{
      activateTab(tab.dataset.tab);
      searchInput.value = "";
      searchInput.dispatchEvent(new Event("input"));
    }});
  }});

  function runSearch(query) {{
    const q = query.trim().toLowerCase();
    if (q.length < 2) {{
      searchCount.textContent = "";
      if (activeCollectionIdx === null) clearHighlight();
      return;
    }}
    ensureMapReady();
    deselectCollection();
    const matches = searchIndex.filter((entry) => entry.text.includes(q));
    const n = matches.length;
    searchCount.textContent = `${{n.toLocaleString()}} item${{n === 1 ? "" : "s"}} match`;
    setHighlight(matches.map((m) => m.x), matches.map((m) => m.y), matches.map((m) => m.z));
  }}

  // --- sidebar: search (scoped to whichever tab is active) + click-to-zoom + content search ---
  let searchDebounce;
  searchInput.addEventListener("input", () => {{
    const q = searchInput.value.trim().toLowerCase();
    clusterRows.forEach((row) => {{
      const label = CG_CLUSTERS[Number(row.dataset.idx)].label.toLowerCase();
      row.classList.toggle("cg-hidden", q.length > 0 && !label.includes(q));
    }});
    collectionRows.forEach((row) => {{
      const name = CG_COLLECTIONS[Number(row.dataset.idx)].name.toLowerCase();
      row.classList.toggle("cg-hidden", q.length > 0 && !name.includes(q));
    }});
    conversationRows.forEach((row) => {{
      const label = CG_CONVERSATIONS[Number(row.dataset.idx)].label.toLowerCase();
      row.classList.toggle("cg-hidden", q.length > 0 && !label.includes(q));
    }});
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(() => runSearch(searchInput.value), 200);
  }});

  clusterRows.forEach((row) => {{
    row.addEventListener("click", () => {{
      clusterRows.forEach((r) => r.classList.remove("cg-active"));
      row.classList.add("cg-active");
      zoomToCluster(CG_CLUSTERS[Number(row.dataset.idx)]);
    }});
  }});

  collectionRows.forEach((row) => {{
    row.addEventListener("click", () => selectCollection(Number(row.dataset.idx), row));
  }});

  conversationRows.forEach((row) => {{
    row.addEventListener("click", () => {{
      conversationRows.forEach((r) => r.classList.remove("cg-active"));
      row.classList.add("cg-active");
      zoomToConversation(CG_CONVERSATIONS[Number(row.dataset.idx)]);
    }});
  }});

  // --- inspector: persistent panel on point click (replaces the old auto-hiding toast —
  // the point is to actually read a recipe, not glimpse it for 5 seconds) ---
  const inspector = document.getElementById("cg-inspector");
  const inspectorMeta = document.getElementById("cg-inspector-meta");
  const inspectorBody = document.getElementById("cg-inspector-body");
  const inspectorCollections = document.getElementById("cg-inspector-collections");
  const inspectorFooter = document.getElementById("cg-inspector-footer");

  function openInspector(detail) {{
    inspectorMeta.textContent = detail.sender
      ? `${{detail.sender}} · ${{detail.when}}`
      : `${{detail.cluster}} · ${{detail.source}} (${{detail.type}})`;
    inspectorBody.textContent = detail.text || "(no text)";
    inspectorCollections.innerHTML = "";
    (detail.collections || []).forEach((name) => {{
      const chip = document.createElement("span");
      chip.className = "cg-chip";
      chip.textContent = name;
      chip.addEventListener("click", () => {{
        const idx = collectionsByName.get(name);
        if (idx === undefined) return;
        activateTab("collections");
        selectCollection(idx, collectionRows[idx]);
      }});
      inspectorCollections.appendChild(chip);
    }});
    inspectorFooter.innerHTML = "";
    if (detail.url) {{
      const link = document.createElement("a");
      link.href = detail.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.className = "cg-inspector-link";
      link.textContent = "Open original →";
      inspectorFooter.appendChild(link);
    }}
    inspector.classList.add("cg-inspector-visible");
  }}

  document.getElementById("cg-inspector-close").addEventListener("click", () => {{
    inspector.classList.remove("cg-inspector-visible");
  }});

  // Whichever tab is shown first already has its (non-deferred) Plotly figure
  // built by the time this script runs — this just runs its one-time setup
  // (search index, click handler). The other tab's figure is built lazily,
  // the first time activateTab() switches to it.
  if (CG_DEFAULT_TAB === "clusters") {{ ensureMapReady(); }} else {{ ensureTimelineReady(); }}
</script>
</body>
</html>
"""
