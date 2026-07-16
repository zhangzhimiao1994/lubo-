from PyInstaller.utils.hooks import (
    collect_delvewheel_libs_directory,
    collect_dynamic_libs,
    collect_submodules,
)


hiddenimports = list(
    dict.fromkeys(
        ["fractions", "dataclasses", "uuid", *collect_submodules("av")]
    )
)
binaries = collect_dynamic_libs(
    "av",
    search_patterns=["*.dll", "*.dylib", "*.so", "*.so.*"],
)
datas, binaries = collect_delvewheel_libs_directory("av", binaries=binaries)
module_collection_mode = {"av": "pyz+py"}
