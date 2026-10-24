"""C++ 扩展构建：quant._core（回测热路径）。

无编译器环境时 `pip install` 会失败——业务代码通过 quant.core_native 探测，
构建失败不影响 Python 回退路径；此时可用 `QUANT_SKIP_NATIVE=1` 跳过扩展编译。
"""

import os
from glob import glob

from setuptools import setup

if os.environ.get("QUANT_SKIP_NATIVE") == "1" or not glob("cpp/*.cpp"):
    ext_modules = []
else:
    from pybind11.setup_helpers import Pybind11Extension, build_ext

    ext_modules = [
        Pybind11Extension(
            "quant._core",
            sorted(glob("cpp/*.cpp")),
            cxx_std=17,
            extra_compile_args=["-O2"],
        ),
    ]

setup(ext_modules=ext_modules, cmdclass={"build_ext": build_ext})
