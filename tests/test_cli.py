from unittest.mock import MagicMock

from click.testing import CliRunner

import cartography.cli as cli_module
from cartography.cli import cli
from cartography.embed import get_collection


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "ingest" in result.output
    assert "cluster" in result.output
    assert "stats" in result.output
    assert "export" in result.output
    assert "search" in result.output


def test_export_command_writes_json_and_markdown(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_module.settings, "chroma_dir", tmp_path / "chroma")
    monkeypatch.setattr(cli_module.settings, "output_dir", tmp_path / "output")
    collection = get_collection(cli_module.settings)
    n = 12  # UMAP needs enough points to build a manifold graph — 2 points degenerates.
    collection.add(
        ids=[f"id{i}" for i in range(n)],
        embeddings=[[float(i), float(i % 3)] for i in range(n)],
        documents=[f"doc {i}" for i in range(n)],
        metadatas=[{"source": "bookmark", "item_type": "bookmark"} for _ in range(n)],
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["export", "--recompute"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "output" / "knowledge_graph.json").exists()
    assert (tmp_path / "output" / "notes" / "index.md").exists()


def test_search_command_prints_results(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_module.settings, "chroma_dir", tmp_path / "chroma")
    monkeypatch.setattr(cli_module.settings, "output_dir", tmp_path / "output")
    collection = get_collection(cli_module.settings)
    collection.add(
        ids=["a"],
        embeddings=[[0.0, 0.0]],
        documents=["Pasta recipe"],
        metadatas=[{"source": "bookmark", "title": "Pasta"}],
    )
    fake_embedder = MagicMock()
    fake_embedder.embed.side_effect = lambda texts: [[0.0, 0.0] for _ in texts]
    monkeypatch.setattr(cli_module, "get_embedder", lambda settings: fake_embedder)

    runner = CliRunner()
    result = runner.invoke(cli, ["search", "pasta"])

    assert result.exit_code == 0, result.output
    assert "Pasta" in result.output
