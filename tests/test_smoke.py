import re

import payments_lakehouse


def test_package_imports_and_has_semantic_version():
    assert re.fullmatch(r"\d+\.\d+\.\d+", payments_lakehouse.__version__)
