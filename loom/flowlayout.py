"""
Loom — a layout whose children wrap onto a new row instead of overflowing.

Qt ships no flow layout, only the classic example this is a small version of.
It earns its place here for one reason: **the launcher must never scroll
sideways**, and a row of buttons in a QHBoxLayout makes that impossible to
promise. Qt reports a button's whole text width as its minimum, so a row of
six of them reports the sum, and the window can never be narrower than that
however small the player drags it. Measured before this existed: the
launcher's scrolling column had a minimum width of 1108px against a window
minimum of 560, and the single worst row was the overlay controls at 1078 -
838px of buttons plus a 240px status label.

A flow layout's minimum is its WIDEST SINGLE CHILD, because everything else
can move down a line. That is the property that makes "no horizontal
scrollbar" something the launcher can actually keep.

heightForWidth is the whole trick and the reason this cannot be a QHBoxLayout
with wrapping bolted on: the layout has to be able to answer "how tall are you
if you are this wide?" before anything is placed, or the scroll area above it
cannot work out whether it needs a vertical bar.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QLayout, QSizePolicy, QWidget


class FlowLayout(QLayout):
    """Lay children left to right, wrapping to a new row when out of width."""

    def __init__(self, parent=None, margin=0, spacing=6):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)

    # ---- the QLayout contract -------------------------------------------

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        # Nothing: a wrapping row wants its natural height, and claiming to
        # expand would have it fight the widgets above and below it for
        # leftover space in the launcher's column.
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._lay_out(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._lay_out(rect, apply=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        """The widest single child, not the sum of them.

        This is the whole point of the class. Everything but the widest item
        can always move to another row, so that item is the only real floor.
        """
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(),
                            margins.top() + margins.bottom())

    # ---- internals -------------------------------------------------------

    def _lay_out(self, rect, apply):
        """Place the children in rect, or just measure. Returns the height."""
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(),
                             -margins.right(), -margins.bottom())
        x, y, row_height = area.x(), area.y(), 0

        for item in self._items:
            hint = item.sizeHint()
            gap = self._gap(item)
            next_x = x + hint.width() + gap
            if next_x - gap > area.right() and row_height > 0:
                # Does not fit on this row, and this row is not empty - so
                # start another. The row_height guard is what stops an item
                # wider than the whole layout looping forever on its own line.
                x = area.x()
                y = y + row_height + gap
                next_x = x + hint.width() + gap
                row_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            row_height = max(row_height, hint.height())

        return y + row_height - rect.y() + margins.bottom()

    def _gap(self, item):
        """The spacing to use after this item.

        spacing() of -1 means "ask the style", which is what a layout built
        without an explicit spacing gets. Falling back to the widget's own
        style keeps a wrapped row looking like every other row in the window.
        """
        gap = self.spacing()
        if gap >= 0:
            return gap
        widget = item.widget()
        if widget is None:
            return 6
        return widget.style().layoutSpacing(
            QSizePolicy.ControlType.PushButton,
            QSizePolicy.ControlType.PushButton,
            Qt.Orientation.Horizontal)


def flow_row(widgets):
    """A row of widgets that wraps onto another line instead of overflowing.

    Returned as a WIDGET rather than a bare layout on purpose: a nested
    layout's heightForWidth is not reliably consulted by the box layout above
    it, and getting that wrong means the row is given one line's height and
    draws its wrapped rows on top of whatever follows. A widget with the
    height-for-width size policy set is asked properly.

    Lives here rather than in the launcher because the build preview needs it
    too, and the preview cannot import the launcher - the launcher builds the
    preview, so the dependency only runs one way.
    """
    host = QWidget()
    flow = FlowLayout(host)
    for widget in widgets:
        flow.addWidget(widget)
    policy = host.sizePolicy()
    policy.setHeightForWidth(True)
    host.setSizePolicy(policy)
    return host
