import os

import kivy
from PyInstaller.utils.hooks import collect_submodules
from kivy.tools.packaging.pyinstaller_hooks import add_dep_paths


add_dep_paths()

# Kivy omits these official hook variables in KIVY_DOC mode, so define the
# small stable set here while keeping provider initialization disabled.
datas = [
    (
        kivy.kivy_data_dir,
        os.path.join("kivy_install", os.path.basename(kivy.kivy_data_dir)),
    ),
    (
        kivy.kivy_modules_dir,
        os.path.join("kivy_install", os.path.basename(kivy.kivy_modules_dir)),
    ),
]
excludedimports = ["tkinter", "_tkinter", "twisted"]
kivy_modules = [
    "xml.etree.cElementTree",
    "kivy.core.gl",
    "kivy.weakmethod",
    "kivy.core.window.window_info",
] + collect_submodules("kivy.graphics")

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

hiddenimports = sorted(set(kivy_modules + _desktop_providers))
