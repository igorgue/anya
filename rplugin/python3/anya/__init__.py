try:
    from .plugin import AnyaPlugin, VERSION

    __version__ = VERSION
    __all__ = ["AnyaPlugin", "__version__"]
except ImportError:
    # Running outside Neovim (e.g. agent execute() in a project venv).
    # pynvim and other plugin deps may not be available — that's fine,
    # callers only need anya.libs.
    AnyaPlugin = None  # type: ignore[assignment,misc]
    __version__ = "0.0.0"
    __all__ = ["__version__"]
