from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CARTOGRAPHY_", env_file=".env", extra="ignore")

    data_dir: Path = Field(default=Path("./data"))
    chroma_dir: Path = Field(default=Path("./output/chroma"))
    output_dir: Path = Field(default=Path("./output"))

    embedding_provider: Literal["ollama", "vertex"] = "ollama"
    ollama_model: str = "nomic-embed-text"
    ollama_host: str = "http://localhost:11434"
    vertex_project: str | None = None
    vertex_location: str = "us-central1"
    vertex_model: str = "text-embedding-004"

    anthropic_model: str = "claude-opus-5"
    anthropic_api_key: str | None = None
    # Local (never leaves the machine) cluster-labeling model — the fallback
    # ahead of raw keywords for clusters the Anthropic path can't or won't see
    # (Messenger-only ones), and for everything when no Anthropic key is set.
    # Empty string disables it (straight to keyword labels). Must already be
    # pulled (`ollama pull <model>`) — see CLAUDE.md.
    ollama_chat_model: str = "llama3.1:8b"

    umap_n_neighbors: int = 15
    umap_min_dist: float = 0.1
    umap_n_components: int = 3
    umap_random_state: int = 42

    hdbscan_min_cluster_size: int = 8
    hdbscan_min_samples: int = 3

    def ensure_dirs(self) -> None:
        self.chroma_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
