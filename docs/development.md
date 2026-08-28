# Development

Cinema Collections targets Python 3.13. Create a virtual environment and
install the development and package dependencies with your preferred Python
package manager. With `uv`, for example:

```console
uv sync --all-groups
```

Run the same checks used by continuous integration from the repository root:

```console
pytest
ruff check .
ruff format --check .
pyright
```

The worker package lives at `cinema_collections_worker`; the Home Assistant
integration lives at `custom_components/cinema_collections`.

