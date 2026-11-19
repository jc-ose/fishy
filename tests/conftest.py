"""Shared fixtures for the tasks tests."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings

from fishy.document import ProjectDocument


@pytest.fixture
def doc(tmp_path, qapp):
    d = ProjectDocument.create(tmp_path / "d.fishy", "D", "P", display_name="T")
    yield d
    d.close()


@pytest.fixture
def doc_with_cause(doc):
    return doc, doc.add_bone(doc.tree.roots()[0], "c")


@pytest.fixture
def tmp_settings(tmp_path):
    return QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
