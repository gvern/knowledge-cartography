from __future__ import annotations

import logging
from pathlib import Path

import plotly.graph_objects as go

from .config import Settings
from .schema import ClusteredItem

logger = logging.getLogger(__name__)


def build_map(items: list[ClusteredItem], settings: Settings, output_name: str = "knowledge_map.html") -> Path:
    settings.ensure_dirs()

    fig = go.Figure()
    for cluster_id in sorted({item.cluster_id for item in items}):
        cluster_items = [item for item in items if item.cluster_id == cluster_id]
        label = cluster_items[0].cluster_label or (
            "Unclustered" if cluster_id == -1 else f"Cluster {cluster_id}"
        )
        fig.add_trace(
            go.Scattergl(
                x=[item.x for item in cluster_items],
                y=[item.y for item in cluster_items],
                mode="markers",
                name=f"{label} ({len(cluster_items)})",
                marker=dict(
                    size=4 if cluster_id == -1 else 6,
                    opacity=0.3 if cluster_id == -1 else 0.85,
                ),
                text=[_hover_text(item) for item in cluster_items],
                hoverinfo="text",
            )
        )

    fig.update_layout(
        title="Knowledge Cartography",
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        legend=dict(itemsizing="constant"),
        template="plotly_dark",
        width=1400,
        height=900,
    )

    output_path = settings.output_dir / output_name
    fig.write_html(output_path, include_plotlyjs="cdn")
    logger.info("Wrote knowledge map to %s", output_path)
    return output_path


def _hover_text(item: ClusteredItem) -> str:
    lines = [f"<b>{item.title or '(untitled)'}</b>", f"source: {item.source.value} / {item.item_type.value}"]
    if item.url:
        lines.append(item.url)
    return "<br>".join(lines)
