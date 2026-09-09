# Plurapack documentation website

This folder is a self-contained, text-only [MkDocs](https://www.mkdocs.org/) project. It deliberately contains no logos, screenshots, fonts, or other binary assets; those can be added later under `docs/assets/`.

## Preview locally

```bash
python -m pip install mkdocs
cd documentation
mkdocs serve
```

Open the local URL printed by MkDocs. To produce a static website, run:

```bash
cd documentation
mkdocs build --strict
```

The generated `site/` directory is intentionally ignored by the repository and can be deployed to any static host. Edit navigation in `mkdocs.yml`; documentation pages live under `docs/`.
