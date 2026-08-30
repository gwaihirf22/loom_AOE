"""
Loom — tests for the wrapping row's geometry arithmetic.

FlowLayout hands every child exactly one box and there is no expansion pass
to correct a box that came in low, so the box arithmetic IS the contract.
The rule these pin: the box is the WIDGET's own sizeHint, floored at the
widget's minimums, and the widget is placed in exactly that box.

The bug that forced the rule cannot be reproduced here, so it is recorded
instead: Qt's macOS style gives widgets "layout item margins" - the
QWidgetItem reports a hint SMALLER than the widget's own (measured: 14
against 26) and item.setGeometry inflates the rect back, and the two
transforms do not round-trip for a stylesheet-styled button. Every button,
checkbox and label in every flow row drew with the bottom of its text cut
off, on macOS only. The offscreen platform's style carries no such margins,
which is exactly why a green suite here never saw it - the fix was verified
by screenshotting the real launcher on a Mac, per CLAUDE.md's rule that
anything about real rendering is run once without offscreen before it is
believed.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from PyQt6.QtCore import QRect, QSize
from PyQt6.QtWidgets import QApplication, QWidget

from loom.flowlayout import FlowLayout


def app():
    # Held in a global on purpose: PyQt destroys the C++ application when
    # its Python wrapper is collected, and widgets built after that abort.
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


class ShrunkenHint(QWidget):
    """A widget whose sizeHint has come in under its own minimum.

    Real widgets do this transiently when their style engine changes under
    them; this one does it deterministically so the floor is testable.
    """

    def sizeHint(self):
        return QSize(30, 10)


def laid_out_geometry():
    app()
    host = QWidget()
    layout = FlowLayout(host)
    child = ShrunkenHint()
    child.setMinimumSize(80, 24)
    layout.addWidget(child)
    layout.setGeometry(QRect(0, 0, 400, 100))
    return child.geometry()


def test_a_hint_below_the_minimum_is_floored_to_it():
    """The child may never be handed a box it is not allowed to be drawn
    in. sizeHint is a suggestion; minimumSize is a promise."""
    geometry = laid_out_geometry()
    assert geometry.width() == 80
    assert geometry.height() == 24


def test_a_sane_hint_is_used_as_is():
    """The floor is a no-op when hints are healthy - the fix must not
    inflate every row in the launcher to prove a point about one."""
    app()
    host = QWidget()
    layout = FlowLayout(host)
    child = ShrunkenHint()
    child.setMinimumSize(10, 5)
    layout.addWidget(child)
    layout.setGeometry(QRect(0, 0, 400, 100))
    assert child.geometry().size() == QSize(30, 10)
