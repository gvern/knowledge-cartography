from __future__ import annotations

import html
import json
import logging
from pathlib import Path

import plotly.graph_objects as go

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
    fig.add_trace(_anchor_trace(summaries.values()))
    fig.add_trace(_collection_highlight_trace())

    collections = _collections_summary(items)

    fig.update_layout(
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        showlegend=True,
        legend=dict(
            itemsizing="constant",
            bgcolor="rgba(0,0,0,0)",
            font=dict(color=_TEXT_SECONDARY),
        ),
        paper_bgcolor=_PAGE,
        plot_bgcolor=_SURFACE,
        autosize=True,
        margin=dict(l=0, r=0, t=0, b=0),
        annotations=[_cluster_annotation(c) for c in top_clusters],
        hoverlabel=dict(bgcolor=_SURFACE, font=dict(color=_TEXT_PRIMARY)),
    )

    config = {
        "scrollZoom": True,
        "displayModeBar": True,
        "displaylogo": False,
        "responsive": True,
    }
    plot_html = fig.to_html(
        include_plotlyjs="cdn",
        config=config,
        full_html=False,
        default_width="100%",
        default_height="100%",
        div_id="cg-plot",
    )

    conversations = _conversation_summaries(items)
    timeline_fig = _build_timeline_figure(items, conversations)
    timeline_html = timeline_fig.to_html(
        include_plotlyjs=False,
        config=config,
        full_html=False,
        default_width="100%",
        default_height="100%",
        div_id="cg-timeline-plot",
    )

    page = _render_page(
        plot_html, timeline_html, summaries, collections, conversations, total_items=len(items)
    )
    output_path = settings.output_dir / output_name
    output_path.write_text(page, encoding="utf-8")
    logger.info("Wrote knowledge map to %s", output_path)
    return output_path


def _platform_trace(
    items: list[ClusteredItem], platform_name: str, color: str, is_noise: bool
) -> go.Scattergl:
    return go.Scattergl(
        x=[item.x for item in items],
        y=[item.y for item in items],
        mode="markers",
        name=f"{platform_name} (unclustered)" if is_noise else platform_name,
        showlegend=not is_noise,
        legendgroup=platform_name,
        marker=dict(
            size=4 if is_noise else 8,
            opacity=0.25 if is_noise else 0.8,
            color=color,
            line=dict(width=0.3, color=_SURFACE),
        ),
        text=[_hover_text(item) for item in items],
        hoverinfo="text",
        customdata=[_item_detail(item) for item in items],
    )


def _anchor_trace(cluster_values) -> go.Scatter:
    """One clickable beacon per cluster centroid — the on-map selection target.

    Plain SVG Scatter (not Scattergl): only ~hundreds of points, and SVG gives
    more reliable click hit-testing than WebGL for a deliberate selector UI.
    """
    clusters: list[dict] = list(cluster_values)
    return go.Scatter(
        x=[c["cx"] for c in clusters],
        y=[c["cy"] for c in clusters],
        mode="markers",
        name="cluster-anchors",
        showlegend=False,
        customdata=[c["id"] for c in clusters],
        text=[f"{c['label']} ({c['count']})" for c in clusters],
        hoverinfo="text",
        marker=dict(
            symbol="diamond",
            size=13,
            color=_ACCENT,
            opacity=0.85,
            line=dict(width=1, color=_TEXT_PRIMARY),
        ),
    )


def _collection_highlight_trace() -> go.Scattergl:
    """Empty at load; the JS side populates x/y via Plotly.restyle when a
    collection is selected. Collections are the user's own curation and aren't
    spatially coherent the way an HDBSCAN cluster is, so instead of zooming to
    a (possibly huge, scattered) bounding box, selected items are highlighted
    in place across the whole map.
    """
    return go.Scattergl(
        x=[],
        y=[],
        mode="markers",
        name="collection-highlight",
        showlegend=False,
        hoverinfo="skip",
        marker=dict(
            size=11,
            color="rgba(0,0,0,0)",
            line=dict(width=2, color=_TEXT_PRIMARY),
        ),
    )


def _collections_summary(items: list[ClusteredItem]) -> dict[str, dict]:
    summaries: dict[str, dict] = {}
    for item in items:
        for name in item.collections:
            summary = summaries.setdefault(name, {"name": name, "count": 0, "x": [], "y": []})
            summary["count"] += 1
            summary["x"].append(item.x)
            summary["y"].append(item.y)
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
                "x_sum": 0.0,
                "y_sum": 0.0,
            },
        )
        summary["count"] += 1
        summary["x_min"] = min(summary["x_min"], item.x)
        summary["x_max"] = max(summary["x_max"], item.x)
        summary["y_min"] = min(summary["y_min"], item.y)
        summary["y_max"] = max(summary["y_max"], item.y)
        summary["x_sum"] += item.x
        summary["y_sum"] += item.y

    for summary in summaries.values():
        summary["cx"] = summary["x_sum"] / summary["count"]
        summary["cy"] = summary["y_sum"] / summary["count"]
        del summary["x_sum"], summary["y_sum"]

    return summaries


def _conversation_summaries(items: list[ClusteredItem]) -> dict[str, dict]:
    """One entry per thread_id, ordered by nothing yet (caller sorts) — items
    with no thread (not a conversation-shaped source) or no timestamp (can't
    place on a time axis) are excluded."""
    summaries: dict[str, dict] = {}
    for item in items:
        if not item.thread_id or item.timestamp is None:
            continue
        summary = summaries.setdefault(
            item.thread_id,
            {
                "thread_id": item.thread_id,
                "label": item.thread or item.thread_id,
                "count": 0,
                "first": item.timestamp,
                "last": item.timestamp,
            },
        )
        summary["count"] += 1
        summary["first"] = min(summary["first"], item.timestamp)
        summary["last"] = max(summary["last"], item.timestamp)
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
) -> str:
    rows = sorted(summaries.values(), key=lambda c: -c["count"])
    sidebar_data = [
        {
            "id": c["id"],
            "label": c["label"],
            "count": c["count"],
            "cx": c["cx"],
            "cy": c["cy"],
            "x0": c["x_min"],
            "x1": c["x_max"],
            "y0": c["y_min"],
            "y1": c["y_max"],
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
        {"name": c["name"], "count": c["count"], "x": c["x"], "y": c["y"]} for c in collection_rows_data
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

    # No conversation data (no Messenger — or similar — items ingested): default
    # to the clusters tab rather than an empty timeline.
    default_tab = "conversations" if conversation_data else "clusters"

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
    click a conversation (timeline or list) to focus it — click a point to open it
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
  const CG_ACCENT = {json.dumps(_ACCENT)};
  const CG_MAX_LABELS = {_MAX_DIRECT_LABELS};
  const plotDiv = document.getElementById("cg-plot");
  const timelineDiv = document.getElementById("cg-timeline-plot");
  const searchInput = document.getElementById("cg-search");
  const searchCount = document.getElementById("cg-search-count");
  const clusterRows = document.querySelectorAll("#cg-list-clusters .cg-row");
  const collectionRows = document.querySelectorAll("#cg-list-collections .cg-row");
  const conversationRows = document.querySelectorAll("#cg-list-conversations .cg-row");
  const clustersById = new Map(CG_CLUSTERS.map((c) => [c.id, c]));
  const collectionsByName = new Map(CG_COLLECTIONS.map((c, i) => [c.name, i]));
  const highlightTraceIndex = plotDiv.data.findIndex((t) => t.name === "collection-highlight");

  function escapeHtml(text) {{
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }}

  function zoomToCluster(c) {{
    const padX = Math.max((c.x1 - c.x0) * 0.4, 0.5);
    const padY = Math.max((c.y1 - c.y0) * 0.4, 0.5);
    Plotly.relayout(plotDiv, {{
      "xaxis.range": [c.x0 - padX, c.x1 + padX],
      "yaxis.range": [c.y0 - padY, c.y1 + padY],
    }});
  }}

  // Categorical y-axis: Plotly assigns integer positions 0,1,2... in the
  // order given by categoryarray at layout time, matching CG_CONVERSATIONS'
  // idx — isolate one row by range, and zoom x to that thread's own span
  // (with a floor so a single-message thread still gets a visible window).
  function zoomToConversation(c) {{
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
    Plotly.restyle(plotDiv, {{ x: [[]], y: [[]] }}, [highlightTraceIndex]);
  }}
  function setHighlight(xs, ys) {{
    Plotly.restyle(plotDiv, {{ x: [xs], y: [ys] }}, [highlightTraceIndex]);
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
    setHighlight(c.x, c.y);
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
    Plotly.Plots.resize(showTimeline ? timelineDiv : plotDiv);
  }}
  tabs.forEach((tab) => {{
    tab.addEventListener("click", () => {{
      activateTab(tab.dataset.tab);
      searchInput.value = "";
      searchInput.dispatchEvent(new Event("input"));
    }});
  }});

  // --- full-text search index: built once from data already loaded for hover/click,
  // no extra payload — every point's full text already ships in its customdata. ---
  const searchIndex = [];
  plotDiv.data.forEach((trace) => {{
    if (trace.name === "cluster-anchors" || trace.name === "collection-highlight") return;
    (trace.customdata || []).forEach((detail, i) => {{
      if (detail && typeof detail === "object" && detail.text) {{
        searchIndex.push({{ x: trace.x[i], y: trace.y[i], text: detail.text.toLowerCase() }});
      }}
    }});
  }});

  function runSearch(query) {{
    const q = query.trim().toLowerCase();
    if (q.length < 2) {{
      searchCount.textContent = "";
      if (activeCollectionIdx === null) clearHighlight();
      return;
    }}
    deselectCollection();
    const matches = searchIndex.filter((entry) => entry.text.includes(q));
    const n = matches.length;
    searchCount.textContent = `${{n.toLocaleString()}} item${{n === 1 ? "" : "s"}} match`;
    setHighlight(matches.map((m) => m.x), matches.map((m) => m.y));
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

  timelineDiv.on("plotly_click", (eventdata) => {{
    const detailPoint = (eventdata.points || []).find(
      (p) => p.customdata && typeof p.customdata === "object" && p.customdata.text !== undefined
    );
    if (detailPoint) openInspector(detailPoint.customdata);
  }});

  // --- map: labels stay legible while zooming — recompute what's visible ---
  function buildAnnotation(c) {{
    return {{
      x: c.cx,
      y: c.cy,
      text: escapeHtml(c.label),
      showarrow: false,
      font: {{ color: CG_ACCENT, size: 11, family: "system-ui, -apple-system, sans-serif" }},
      bgcolor: "rgba(13,13,13,0.65)",
      bordercolor: "rgba(57,135,229,0.4)",
      borderwidth: 1,
      borderpad: 3,
      opacity: 0.9,
    }};
  }}

  let lastAnnotationKey = "";
  function updateVisibleLabels() {{
    const xr = plotDiv.layout && plotDiv.layout.xaxis && plotDiv.layout.xaxis.range;
    const yr = plotDiv.layout && plotDiv.layout.yaxis && plotDiv.layout.yaxis.range;
    let visible = CG_CLUSTERS;
    if (xr && yr) {{
      const [x0, x1] = xr;
      const [y0, y1] = yr;
      visible = CG_CLUSTERS.filter((c) => c.cx >= x0 && c.cx <= x1 && c.cy >= y0 && c.cy <= y1);
    }}
    visible = visible.slice().sort((a, b) => b.count - a.count).slice(0, CG_MAX_LABELS);
    const key = visible.map((c) => c.id).join(",");
    if (key === lastAnnotationKey) return;
    lastAnnotationKey = key;
    Plotly.relayout(plotDiv, {{ annotations: visible.map(buildAnnotation) }});
  }}

  let debounceTimer;
  plotDiv.on("plotly_relayout", (eventdata) => {{
    const isAxisChange = Object.keys(eventdata || {{}}).some(
      (k) => k.startsWith("xaxis.") || k.startsWith("yaxis.")
    );
    if (!isAxisChange) return;
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(updateVisibleLabels, 120);
  }});
</script>
</body>
</html>
"""
