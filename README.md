# Photography Hub

Photography Hub is a desktop focus-stacking assistant for DSLR and mirrorless cameras.

## What it does

- Talks to `gphoto2` for capture and manual focus drive operations.
- Provides a Qt desktop UI for capture runs.
- Lets you pick a camera and output folder from the app.
- Supports single-shot capture and stacked capture runs.
- Stores sample captures and output artifacts under `sample_data/`.

## Quick start

```bash
.venv/bin/python -m pip install -e .
.venv/bin/python -m desktop_ui
```

## Project layout

- `camera_engine/` holds camera control, capture orchestration, and stack planning.
- `desktop_ui/` holds the Qt application and window code.
- `sample_data/` holds sample images and generated outputs.
