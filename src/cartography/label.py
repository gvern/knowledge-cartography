from __future__ import annotations

import logging
import math
import re
import unicodedata
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

_MESSAGE_PROMPT = """You are labeling a cluster of private chat messages that a semantic \
clustering algorithm grouped together because they are topically similar. This analysis is \
entirely local — nothing here leaves this machine.

Here are up to {n} representative messages from the cluster:

{samples}

Reply with ONLY a short (2-5 word) descriptive label for what this cluster of messages is \
about — the topic or theme, not a quote, not a person's name. No punctuation, no explanation."""


def label_clusters(
    items: list[ClusteredItem], settings: Settings, samples_per_cluster: int = 12
) -> list[ClusteredItem]:
    by_cluster: dict[int, list[ClusteredItem]] = defaultdict(list)
    for item in items:
        by_cluster[item.cluster_id].append(item)

    # Always computed, never a network call — the final fallback for clusters
    # neither model path can or will label (no key, no model pulled, a failed
    # call), not just an afterthought for the all-Messenger case.
    local_labels = _local_keyword_labels(by_cluster)

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None
    if client is None:
        logger.warning("No Anthropic API key configured; falling back to local labeling for every cluster")

    # Second tier, ahead of raw keywords: a local Ollama chat model, the only
    # path allowed to see actual Messenger text (same reasoning as embed.py's
    # Messenger routing — Ollama never leaves the machine). Used for clusters
    # Claude can't see any safe samples for, and for every cluster when no
    # Anthropic key is configured at all.
    local_llm = (
        LocalLabeler(settings.ollama_chat_model, settings.ollama_host) if settings.ollama_chat_model else None
    )

    labels: dict[int, str] = {-1: "Unclustered"}
    for cluster_id, cluster_items in by_cluster.items():
        if cluster_id == -1:
            continue
        fallback = local_labels.get(cluster_id, f"Cluster {cluster_id}")
        if client is not None:
            labels[cluster_id] = _label_one(
                client, settings, cluster_id, cluster_items, samples_per_cluster, fallback, local_llm
            )
        elif local_llm is not None:
            labels[cluster_id] = local_llm.label(cluster_id, cluster_items, samples_per_cluster, fallback)
        else:
            labels[cluster_id] = fallback

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
    local_llm: LocalLabeler | None,
) -> str:
    # Messenger content never leaves the machine — not even a sample of it,
    # even when it shares a cluster with non-sensitive items. A cluster made
    # up entirely of Messenger items ends up with no safe samples below; try
    # the local model over the full (Messenger-inclusive) cluster instead of
    # calling the API at all, falling back to keywords if that's unavailable too.
    safe_items = [item for item in cluster_items if item.source != SourcePlatform.MESSENGER]
    sample_texts = [item.text for item in safe_items[:samples_per_cluster] if item.text]
    if not sample_texts:
        if local_llm is not None:
            return local_llm.label(cluster_id, cluster_items, samples_per_cluster, fallback)
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


class LocalLabeler:
    """Labels a cluster with a local Ollama chat model — better than raw
    keyword frequency for chat-style text (slang, filler, short messages),
    and, unlike the Anthropic path, safe to point at actual Messenger text
    since Ollama never leaves the machine. Falls back to the TF-IDF keyword
    label on any failure: model not pulled, Ollama not running, empty/odd
    response — this is a nice-to-have upgrade, never a hard requirement."""

    def __init__(self, model: str, host: str):
        self.model = model
        self.host = host

    def label(
        self, cluster_id: int, items: list[ClusteredItem], samples_per_cluster: int, fallback: str
    ) -> str:
        sample_texts = [item.text for item in items[:samples_per_cluster] if item.text]
        if not sample_texts:
            return fallback

        samples = "\n".join(f"- {text[:200]}" for text in sample_texts)
        prompt = _MESSAGE_PROMPT.format(n=len(sample_texts), samples=samples)
        try:
            import ollama

            response = ollama.Client(host=self.host).chat(
                model=self.model, messages=[{"role": "user", "content": prompt}]
            )
            label = response["message"]["content"].strip().strip("\"'“”")
        except Exception:
            logger.exception("Local LLM labeling failed for cluster %d (model %s)", cluster_id, self.model)
            return fallback

        # An 8B local model doesn't follow "2-5 words, no explanation" nearly
        # as reliably as Claude — a multi-line ramble or a content-policy
        # refusal ("I cannot provide a label that describes...") is a real,
        # observed failure mode, not hypothetical. A genuine short label is
        # never multi-line and comfortably under this length; treat anything
        # else as a failed attempt rather than shipping a sentence as a "label".
        if not label or "\n" in label or len(label) > 60:
            logger.warning(
                "Local LLM gave an unusable label for cluster %d, using fallback: %r", cluster_id, label
            )
            return fallback
        logger.info("Cluster %d (%d items, local LLM): %s", cluster_id, len(items), label)
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
    salut coucou bonjour bonsoir merci svp stp ok okay lol mdr ptdr haha hihi ahah ah
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
_REPEAT_RE = re.compile(r"(.)\1+")


def _is_stopword(token: str) -> bool:
    # Catches chat elongation ("mdrrrr", "ouiiii") by collapsing repeated
    # letters down to one before checking — cheaper than listing every variant.
    return token in _STOPWORDS or _REPEAT_RE.sub(r"\1", token) in _STOPWORDS


def _fold(term: str) -> str:
    """Accent- and crude-plural-insensitive key, used only to dedupe near-identical
    terms within one label ("même"/"meme"/"mêmes" shouldn't fill all 3 slots)."""
    folded = "".join(c for c in unicodedata.normalize("NFKD", term) if not unicodedata.combining(c))
    return folded[:-1] if len(folded) > 4 and folded.endswith("s") else folded


def keyword_tags(items: list[ClusteredItem], top_n: int = 5) -> list[str]:
    """Cheap, dependency-free top keywords for one cluster or collection —
    plain term frequency using the same tokenizer/stopword/dedup rules as the
    fallback label below, minus its cross-cluster TF-IDF weighting (there's no
    "other clusters" to weight against for a single item list). Used to give
    clusters a few grounding keywords alongside their short label wherever
    they're surfaced (the map sidebar, the JSON export, the MCP tools) — no
    model, no network, safe for Messenger content."""
    counts: Counter[str] = Counter()
    for item in items:
        for token in _TOKEN_RE.findall(item.text.lower()):
            if len(token) < 3 or _is_stopword(token):
                continue
            counts[token] += 1

    tags: list[str] = []
    seen_folded: set[str] = set()
    for term, _ in counts.most_common():
        key = _fold(term)
        if key in seen_folded:
            continue
        seen_folded.add(key)
        tags.append(term.capitalize())
        if len(tags) == top_n:
            break
    return tags


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
                if len(token) < 3 or _is_stopword(token):
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

        ranked = sorted(counts, key=lambda t: score(t, counts[t]), reverse=True)
        top_terms: list[str] = []
        seen_folded: set[str] = set()
        for term in ranked:
            key = _fold(term)
            if key in seen_folded:
                continue
            seen_folded.add(key)
            top_terms.append(term)
            if len(top_terms) == top_n:
                break
        labels[cluster_id] = " / ".join(t.capitalize() for t in top_terms)

    return labels
