from __future__ import annotations

import tomllib
from pathlib import Path

from .schema import Experiment, Manifest
from .storage import read_json


def load_config(path: Path) -> tuple[Experiment, Manifest]:
    path = path.resolve()
    config = Experiment.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
    config.manifest = (path.parent / config.manifest).resolve()
    config.output = (path.parent / config.output).resolve()
    manifest = Manifest.model_validate(read_json(config.manifest))
    for document in manifest.documents:
        document.pdf = (config.manifest.parent / document.pdf).resolve()
        if not document.pdf.is_file():
            raise ValueError(f"Missing PDF for document {document.id}: {document.pdf}")
    return config, manifest
