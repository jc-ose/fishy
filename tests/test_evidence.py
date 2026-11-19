"""Milestone 5: evidence log — markdown rendering, attachments, editor, cards, timeline."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QMimeData, QSettings, Qt, QUrl
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QDialog, QMessageBox

from fishy.app import MainWindow, create_sample_project
from fishy.markdown_render import attachment_ids, render, summary_line
from fishy.storage import Repository
from fishy.views.evidence import EntryEditor, ImageViewer, TimelineDialog, png_bytes
from fishy.views.markdown_view import MarkdownView


def red_png(w=900, h=300) -> bytes:
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(QColor("red"))
    return png_bytes(img)


# ----- markdown rendering (pure) ---------------------------------------------- #


def test_render_gfm_features():
    out = render("| a | b |\n|---|---|\n| 1 | 2 |\n\n~~old~~ **new**\n\n- [x] done\n- [ ] todo")
    assert '<table border="1"' in out and "<td>1</td>" in out
    assert "<s>old</s>" in out and "<strong>new</strong>" in out
    assert "☑ done" in out and "☐ todo" in out and "list-style-type: none" in out


def test_render_code_is_highlighted_and_shaded():
    out = render("```python\ndef f():\n    return 1\n```")
    assert 'bgcolor="#f6f8fa"' in out and "<pre>" in out
    assert 'style="color: #008000; font-weight: bold">def</span>' in out
    assert render("```nosuchlang\nx\n```").count("<pre>") == 1  # unknown language is fine


def test_render_is_safe():
    out = render("<script>alert(1)</script> [x](javascript:alert(1)) <b>raw</b>")
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert 'href="javascript' not in out
    assert "<b>" not in out


def test_render_images_and_bone_refs():
    out = render("![chart](attachment:7) ![web](https://example.com/a.png)")
    assert '<a href="attachment:7"><img src="attachment:7" alt="chart"' in out
    assert '<img src="https' not in out and "🖼 web" in out
    labels = {3: "1.2 Shift [handover]"}
    out = render("See [[bone:3]] and [[bone:4]].", labels.get)
    assert '<a href="fishy-bone:3">1.2 Shift [handover]</a>' in out
    assert "<s>deleted bone #4</s>" in out


def test_summary_and_attachment_ids():
    md = "# Heading **bold**\n\n![shot](attachment:5) and ![x](attachment:9)"
    assert summary_line(md) == "Heading bold"
    assert summary_line("![shot](attachment:5)") == "[image: shot]"
    assert summary_line("- [ ] check [[bone:2]]", {2: "1.1 A"}.get) == "check 1.1 A"
    assert summary_line("x" * 300, limit=20).endswith("…")
    assert attachment_ids(md) == [5, 9]


# ----- storage: attachments without an entry ---------------------------------- #


def test_unowned_attachments_are_adopted_or_discarded(tmp_path):
    repo = Repository.create(tmp_path / "a.fishy", "A")
    cause = repo.add_bone(repo.bones()[0].id, "c")
    a1 = repo.add_attachment(None, b"one", "image/png")
    a2 = repo.add_attachment(None, b"two", "image/png")
    entry = repo.add_log_entry(cause.id, f"![](attachment:{a1})")
    repo.assign_attachments([a1], entry.id)
    assert [a.id for a in repo.attachments(entry.id)] == [a1]
    assert repo.discard_unowned_attachments([a1, a2]) >= 1  # a1 is owned: kept
    assert repo.attachment_data(a1) == b"one"
    with pytest.raises(KeyError):
        repo.attachment_data(a2)
    stray = repo.add_attachment(None, b"crash mid-edit", "image/png")
    repo.close()
    repo = Repository.open(tmp_path / "a.fishy")  # opening cleans up strays
    with pytest.raises(KeyError):
        repo.attachment_data(stray)
    repo.close()


# ----- UI fixtures ------------------------------------------------------------ #


@pytest.fixture
def win(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    w = MainWindow(settings)
    qtbot.addWidget(w)
    w.show()
    w._set_doc(create_sample_project(tmp_path / "s.fishy"))
    return w


def bone(win, prefix):
    return next(b.id for b in win.doc.tree.bones.values() if b.title.startswith(prefix))


@pytest.fixture
def yes(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))


def fake_editor(monkeypatch, body: str, title: str | None = None, paste_image: bool = False,
                accept: bool = True):
    """Make EntryEditor.exec type into the editor (and optionally paste an image)."""

    def exec_(self):
        self.title.setText(title or "")
        self.source.setPlainText(body)
        if paste_image:
            mime = QMimeData()
            mime.setImageData(QImage.fromData(red_png()))
            self.source.insertFromMimeData(mime)
        if accept:
            self.accept()
            return QDialog.DialogCode.Accepted
        self.reject()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(EntryEditor, "exec", exec_)


# ----- adding, editing, undo -------------------------------------------------- #


def test_add_entry_with_pasted_image_then_undo_redo(win, monkeypatch):
    cause = bone(win, "Shift handover")
    win.select_bone(cause)
    log = win.details.evidence
    fake_editor(monkeypatch, "Handover sheet photo:", "Handover sheet", paste_image=True)
    entry_id = log.add_entry()
    entry = win.doc.repo.log_entry(entry_id)
    (att,) = attachment_ids(entry.body_md)
    assert win.doc.repo.attachment(att).log_entry_id == entry_id
    assert log.heading.text() == "Evidence log (1)"
    card = log.card_for(entry_id)
    assert card.title.text() == "Handover sheet" and card.body is not None

    win.doc.undo_stack.undo()  # removes the entry and its image
    with pytest.raises(KeyError):
        win.doc.repo.attachment_data(att)
    assert log.heading.text() == "Evidence log (0)"
    win.doc.undo_stack.redo()  # both come back
    assert win.doc.repo.attachment_data(att)
    assert win.doc.model.index_for(cause, 4).data() == 1  # Log column


def test_cancelled_editor_discards_its_images(win, monkeypatch):
    win.select_bone(bone(win, "Shift handover"))
    fake_editor(monkeypatch, "draft", paste_image=True, accept=False)
    before = win.doc.repo.conn.execute("SELECT count(*) FROM attachment").fetchone()[0]
    assert win.details.evidence.add_entry() is None
    after = win.doc.repo.conn.execute("SELECT count(*) FROM attachment").fetchone()[0]
    assert after == before


def test_edit_entry_and_empty_entries_are_refused(win, monkeypatch, qtbot):
    cause = bone(win, "Training record")
    win.select_bone(cause)
    entry = win.doc.repo.log_entries(cause)[0]
    fake_editor(monkeypatch, "Updated **findings**", "Training records (v2)")
    assert win.details.evidence.edit_entry(entry.id)
    assert win.doc.repo.log_entry(entry.id).title == "Training records (v2)"
    assert "edited" in win.details.evidence.card_for(entry.id).meta.text()
    win.doc.undo_stack.undo()
    assert win.doc.repo.log_entry(entry.id).title == "Training records"

    shown = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a: shown.append(a)))
    editor = EntryEditor(win.doc, cause, parent=win)
    qtbot.addWidget(editor)
    editor.accept()  # nothing written
    assert shown and editor.result() != QDialog.DialogCode.Accepted


def test_dropped_image_files_and_links(win, tmp_path, qtbot):
    cause = bone(win, "Shift handover")
    editor = EntryEditor(win.doc, cause, parent=win)
    qtbot.addWidget(editor)
    f = tmp_path / "photo.png"
    f.write_bytes(red_png(100, 50))
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(f))])
    assert editor.source.canInsertFromMimeData(mime)
    editor.source.insertFromMimeData(mime)
    assert "![photo](attachment:" in editor.body()
    assert len(editor.new_attachments) == 1
    editor._update_preview()
    assert "<img" in editor.preview.toHtml()
    editor.reject()


# ----- cards: order, move, collapse, delete ----------------------------------- #


def test_order_move_collapse_delete(win, monkeypatch, yes):
    cause = bone(win, "Shift handover")
    win.select_bone(cause)
    doc, log = win.doc, win.details.evidence
    first = doc.add_log_entry(cause, "first")
    second = doc.add_log_entry(cause, "second")
    doc.repo.conn.execute("UPDATE log_entry SET created_at = '2020-01-01T00:00:00.000Z' "
                          "WHERE id = ?", (second,))
    third = doc.add_log_entry(cause, "third", index=0)  # manual: put at top
    ids = [e.id for e in log.entries()]
    assert ids == [third, first, second]
    log.order.setCurrentIndex(log.order.findData("date"))
    assert [e.id for e in log.entries()] == [second, first, third]  # oldest first
    assert win.settings.value("evidence/order") == "date"
    log.order.setCurrentIndex(log.order.findData("manual"))

    log.move_entry(third, +1)
    assert [e.id for e in log.entries()] == [first, third, second]

    log._toggle(first, True)
    assert log.card_for(first).body is None and log.card_for(third).body is not None
    log._fold_all()
    assert all(log.card_for(i).body is None for i in (first, second, third))
    log._fold_all()
    assert all(log.card_for(i).body is not None for i in (first, second, third))

    log.delete_entry(second)
    assert [e.id for e in log.entries()] == [first, third]
    doc.undo_stack.undo()
    assert second in [e.id for e in log.entries()]


def test_bone_link_in_entry_selects_bone(win):
    cause = bone(win, "Training record")
    win.select_bone(cause)
    card = win.details.evidence.card_for(win.doc.repo.log_entries(cause)[0].id)
    target = bone(win, "SOP rev C")
    card.body.anchorClicked.emit(QUrl(f"fishy-bone:{target}"))
    assert win.tree.current_bone() == target


def test_bone_links_follow_renumbering(win):
    cause = bone(win, "Training record")
    sop = bone(win, "SOP rev C")
    win.doc.move_up(sop)  # 1.1.2 -> 1.1.1
    win.select_bone(cause)
    card = win.details.evidence.card_for(win.doc.repo.log_entries(cause)[0].id)
    assert "1.1.1 SOP rev C" in card.body.toPlainText()


def test_categories_have_no_evidence_log(win):
    win.select_bone(win.doc.tree.roots()[0])
    assert win.details.evidence.isHidden() and win.details.evidence.bone_id is None


# ----- images ----------------------------------------------------------------- #


def test_markdown_view_loads_and_scales_attachments(win, qtbot):
    att = win.doc.repo.add_attachment(None, red_png(1200, 400), "image/png", "big.png")
    view = MarkdownView()
    qtbot.addWidget(view)
    view.resize(500, 300)
    view.show()
    qtbot.waitExposed(view)
    view.load_attachment = win.doc.repo.attachment_data
    from PySide6.QtGui import QTextDocument
    img = view.loadResource(QTextDocument.ResourceType.ImageResource.value,
                            QUrl(f"attachment:{att}"))
    assert isinstance(img, QImage) and img.width() <= 500
    missing = view.loadResource(QTextDocument.ResourceType.ImageResource.value,
                                QUrl("attachment:99999"))
    assert isinstance(missing, QImage) and missing.width() == 160  # placeholder


def test_image_viewer(win, qtbot):
    hvac = bone(win, "HVAC")
    entry = win.doc.repo.log_entries(hvac)[0]
    (att,) = attachment_ids(entry.body_md)
    viewer = ImageViewer(win.doc, att, win)
    qtbot.addWidget(viewer)
    assert viewer.pixmap.width() == 420 and viewer.windowTitle() == "failures-by-shift.png"


# ----- timeline --------------------------------------------------------------- #


def test_timeline_lists_entries_and_opens_them(win, qtbot):
    win.show_timeline()
    tl: TimelineDialog = win.timeline
    assert tl.tree.topLevelItemCount() == 3
    assert tl.tree.topLevelItem(0).text(2) == "Calibration check"  # oldest first
    assert "3 entries across 3 causes" == tl.count.text()
    tl.newest_first.setChecked(True)
    assert tl.tree.topLevelItem(2).text(2) == "Calibration check"
    item = tl.tree.topLevelItem(2)
    tl._activated(item, 0)
    bone_id, entry_id = item.data(0, Qt.ItemDataRole.UserRole)
    assert win.tree.current_bone() == bone_id
    assert win.details.evidence.card_for(entry_id) is not None
    win.doc.add_log_entry(bone(win, "HVAC"), "new")  # timeline stays live
    assert tl.tree.topLevelItemCount() == 4
