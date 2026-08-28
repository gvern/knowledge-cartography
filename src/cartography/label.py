from __future__ import annotations

import logging
import math
import re
from collections import Counter, defaultdict

import anthropic

from .config import Settings
from .schema import ClusteredItem, SourcePlatform

logger = logging.getLogger(__name__)

_PROMPT = """You are labeling a cluster of personal knowledge items (saved posts, watched \
videos, search queries, bookmarks) that a semantic clustering algorithm grouped together \
because they are topically similar.

Here are up to {n} representative items from the cluster:

{samples}

Reply with ONLY a short (2-5 word) descriptive label for this cluster's overarching topic. \
No punctuation, no explanation."""


def label_clusters(
    items: list[ClusteredItem], settings: Settings, samples_per_cluster: int = 12
) -> list[ClusteredItem]:
    by_cluster: dict[int, list[ClusteredItem]] = defaultdict(list)
    for item in items:
        by_cluster[item.cluster_id].append(item)

    # Always computed, never a network call — the fallback for clusters the
    # API path can't or won't label (no key, no cloud-safe samples, a failed
    # call), not just an afterthought for the all-Messenger case.
    local_labels = _local_keyword_labels(by_cluster)

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None
    if client is None:
        logger.warning("No Anthropic API key configured; using local keyword labels for every cluster")

    labels: dict[int, str] = {-1: "Unclustered"}
    for cluster_id, cluster_items in by_cluster.items():
        if cluster_id == -1:
            continue
        fallback = local_labels.get(cluster_id, f"Cluster {cluster_id}")
        labels[cluster_id] = (
            _label_one(client, settings, cluster_id, cluster_items, samples_per_cluster, fallback)
            if client is not None
            else fallback
        )

    for item in items:
        item.cluster_label = labels.get(item.cluster_id, f"Cluster {item.cluster_id}")

    return items


def _label_one(
    client: anthropic.Anthropic,
    settings: Settings,
    cluster_id: int,
    cluster_items: list[ClusteredItem],
    samples_per_cluster: int,
    fallback: str,
) -> str:
    # Messenger content never leaves the machine — not even a sample of it,
    # even when it shares a cluster with non-sensitive items. A cluster made
    # up entirely of Messenger items ends up with no safe samples below, and
    # falls back to a local keyword label rather than calling the API at all.
    safe_items = [item for item in cluster_items if item.source != SourcePlatform.MESSENGER]
    sample_texts = [item.text for item in safe_items[:samples_per_cluster] if item.text]
    if not sample_texts:
        return fallback

    samples = "\n".join(f"- {text[:200]}" for text in sample_texts)
    prompt = _PROMPT.format(n=len(sample_texts), samples=samples)
    try:
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=30,
            thinking={"type": "disabled"},
            messages=[{"role": "user", "content": prompt}],
        )
        text_block = next(
            (block for block in response.content if isinstance(block, anthropic.types.TextBlock)), None
        )
        if text_block is None:
            block_types = [type(block).__name__ for block in response.content]
            raise TypeError(f"No text block in response content: {block_types}")
        label = text_block.text.strip()
    except Exception:
        logger.exception("Failed to label cluster %d", cluster_id)
        return fallback

    logger.info("Cluster %d (%d items): %s", cluster_id, len(cluster_items), label)
    return label


# French (the dominant language in this project's real data) + English function
# words and chat filler, so keyword extraction below surfaces topical words
# rather than "salut merci bien" on every single cluster.
_STOPWORDS = frozenset(
    """
    le la les l un une des de du au aux et ou où ni mais donc or car ce cet cette ces
    je tu il elle on nous vous ils elles me te se moi toi lui eux y en
    mon ma mes ton ta tes son sa ses notre nos votre vos leur leurs
    qui que quoi dont pas plus moins très trop bien mal oui non si comme
    avec sans pour dans sur sous entre vers chez par
    est sont suis es été être avoir ai as a avons avez ont
    va vas vais allons allez vont fait faire faites
    tout tous toute toutes rien aucun aucune chaque autre autres
    alors donc puis ensuite enfin voila voilà quand comment pourquoi
    ça cela ceci ici là bas haut
    salut coucou bonjour bonsoir merci svp stp ok okay lol mdr ptdr haha hihi
    encore toujours jamais déjà aussi peu beaucoup quelque quelques certain certains
    chose truc fois dire dit sais sait veux veut peux peut vais vas faut falloir
    the a an and or but if of to in on for with at by from as is are was were be
    been being have has had do does did will would can could should shall may might
    this that these those it its i you he she we they them his her their our your my
    yes no not just so then than very really okay hey hi thanks about today let when
    what who which there here get got going know think want like one also into out
    up down over after before because while
    """.split()
)
_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _local_keyword_labels(by_cluster: dict[int, list[ClusteredItem]], top_n: int = 3) -> dict[int, str]:
    """Cheap, dependency-free TF-IDF over clusters-as-documents: term frequency
    within a cluster, weighted down by how many other clusters also use that
    term, so generic words lose to ones that actually distinguish this
    cluster's topic. No model, no network — safe for Messenger content."""
    cluster_terms: dict[int, Counter[str]] = {}
    doc_freq: Counter[str] = Counter()

    for cluster_id, cluster_items in by_cluster.items():
        if cluster_id == -1:
            continue
        counts: Counter[str] = Counter()
        for item in cluster_items:
            for token in _TOKEN_RE.findall(item.text.lower()):
                if len(token) < 3 or token in _STOPWORDS:
                    continue
                counts[token] += 1
        cluster_terms[cluster_id] = counts
        doc_freq.update(counts.keys())

    n_clusters = len(cluster_terms) or 1
    labels: dict[int, str] = {}
    for cluster_id, counts in cluster_terms.items():
        if not counts:
            labels[cluster_id] = f"Cluster {cluster_id}"
            continue

        def score(term: str, count: int) -> float:
            idf = math.log((n_clusters + 1) / (doc_freq[term] + 1)) + 1
            return count * idf

        top_terms = sorted(counts, key=lambda t: score(t, counts[t]), reverse=True)[:top_n]
        labels[cluster_id] = " / ".join(t.capitalize() for t in top_terms)

    return labels
