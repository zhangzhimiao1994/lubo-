from kivy.tools.packaging.pyinstaller_hooks import (
    add_dep_paths,
    datas,
    excludedimports,
    get_factory_modules,
    kivy_modules,
)


add_dep_paths()

# The stock hook discovers every Kivy provider in isolated subprocesses. On
# headless Windows runners some unused providers can block indefinitely.
_desktop_providers = [
    "kivy.core.window.window_sdl2",
    "kivy.core.text.text_sdl2",
    "kivy.core.text.text_pil",
    "kivy.core.image.img_sdl2",
    "kivy.core.image.img_pil",
    "kivy.core.image.img_tex",
    "kivy.core.image.img_dds",
    "kivy.core.clipboard.clipboard_winctypes",
    "kivy.core.clipboard.clipboard_xclip",
    "kivy.core.clipboard.clipboard_xsel",
    "kivy.core.clipboard.clipboard_dbusklipper",
    "kivy.core.clipboard.clipboard_gtk3",
    "kivy.core.clipboard.clipboard_sdl2",
    "kivy.core.clipboard.clipboard_dummy",
]

hiddenimports = sorted(set(get_factory_modules() + kivy_modules + _desktop_providers))

