"""
ApexAgent 悬浮球 + 弹出面板
双击运行即可在桌面显示可拖拽的悬浮球，单击弹出磨砂面板。
"""

import sys
import os
from datetime import datetime
from PyQt6.QtWidgets import (
    QApplication, QWidget, QMenu, QVBoxLayout, QHBoxLayout,
    QLabel, QTextEdit, QPushButton, QSlider, QDialog,
    QScrollArea, QFrame, QSizePolicy, QGraphicsOpacityEffect
)
from PyQt6.QtCore import (
    Qt, QPoint, QPointF, QPropertyAnimation, QEasingCurve,
    pyqtProperty, QRectF, QRect, QTimer, pyqtSignal
)
from PyQt6.QtGui import (
    QPainter, QBrush, QColor, QLinearGradient, QRadialGradient,
    QFont, QPen, QAction, QPainterPath, QPixmap, QIcon, QCursor
)
from .storage import ConfigManager, ConversationStore


ASSET_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "asset")


# ============================================================
#  悬浮球
# ============================================================
class FloatingBall(QWidget):
    """桌面悬浮球 - 可拖拽、圆润、带渐变光效"""

    BALL_SIZE = 48
    MARGIN = 10

    def __init__(self):
        super().__init__()
        self._drag_pos = QPoint()
        self._press_pos = QPoint()
        self._hovered = False
        self._scale = 1.0
        self._panel = None

        self._ball_pixmap = None
        self._load_image()

        self._init_ui()
        self._init_animations()

    def _load_image(self):
        img_path = os.path.join(ASSET_DIR, "qiu.png")
        if os.path.exists(img_path):
            pix = QPixmap(img_path)
            if not pix.isNull():
                self._ball_pixmap = pix
        if self._ball_pixmap is None:
            print("[ApexAgent] 未找到 asset/qiu.png，使用默认图标")

    def _init_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        total = self.BALL_SIZE + self.MARGIN * 2
        self.setFixedSize(total, total)

        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.right() - self.width() - 40
        y = screen.bottom() - self.height() - 120
        self.move(x, y)

        self._context_menu = QMenu(self)
        self._context_menu.setStyleSheet(self._menu_style())
        action_show = QAction("打开面板", self)
        action_show.triggered.connect(self._toggle_panel)
        action_hide = QAction("隐藏悬浮球", self)
        action_hide.triggered.connect(self.hide)
        action_exit = QAction("退出", self)
        action_exit.triggered.connect(QApplication.quit)

        self._context_menu.addAction(action_show)
        self._context_menu.addSeparator()
        action_settings = QAction("设置", self)
        action_settings.triggered.connect(self._open_settings)
        self._context_menu.addAction(action_settings)
        action_history = QAction("历史对话", self)
        action_history.triggered.connect(self._open_history)
        self._context_menu.addAction(action_history)
        self._context_menu.addAction(action_hide)
        self._context_menu.addAction(action_exit)

    def _init_animations(self):
        self._enter_anim = QPropertyAnimation(self, b"ball_scale")
        self._enter_anim.setDuration(180)
        self._enter_anim.setStartValue(1.0)
        self._enter_anim.setEndValue(1.15)
        self._enter_anim.setEasingCurve(QEasingCurve.Type.OutBack)

        self._leave_anim = QPropertyAnimation(self, b"ball_scale")
        self._leave_anim.setDuration(220)
        self._leave_anim.setStartValue(1.15)
        self._leave_anim.setEndValue(1.0)
        self._leave_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    def _get_ball_scale(self):
        return self._scale

    def _set_ball_scale(self, val):
        self._scale = val
        self.update()

    ball_scale = pyqtProperty(float, _get_ball_scale, _set_ball_scale)

    # ----- events -----
    def enterEvent(self, event):
        self._hovered = True
        self._leave_anim.stop()
        self._enter_anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._enter_anim.stop()
        self._leave_anim.start()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._press_pos = event.globalPosition().toPoint()
        elif event.button() == Qt.MouseButton.RightButton:
            self._context_menu.exec(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self._press_pos
            if delta.manhattanLength() < 5:
                self._toggle_panel()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        center = QPointF(self.rect().center())
        outer_r = int(self.BALL_SIZE / 2 * self._scale)

        # ---- 外层光晕 ----
        glow = QRadialGradient(center, outer_r + 6)
        glow.setColorAt(0.0, QColor(100, 180, 255, 50))
        glow.setColorAt(0.7, QColor(80, 120, 255, 12))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(QBrush(glow))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center, outer_r + 6, outer_r + 6)

        if self._ball_pixmap and not self._ball_pixmap.isNull():
            # ---- 裁剪圆形，绘制图片 ----
            clip_path = QPainterPath()
            clip_path.addEllipse(center, outer_r, outer_r)
            painter.setClipPath(clip_path)

            img_size = int(outer_r * 2)
            target_rect = QRectF(
                center.x() - outer_r,
                center.y() - outer_r,
                img_size, img_size
            )
            painter.drawPixmap(target_rect.toRect(), self._ball_pixmap)
            painter.setClipping(False)

            # ---- 圆形边框 ----
            border_pen = QPen(QColor(140, 180, 255, 60), 1.5)
            painter.setPen(border_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(center, outer_r, outer_r)
        else:
            # ---- 默认渐变球（未找到图片时） ----
            body_grad = QRadialGradient(
                center.x() - outer_r * 0.25,
                center.y() - outer_r * 0.35,
                outer_r * 1.3
            )
            body_grad.setColorAt(0.0, QColor(180, 210, 255))
            body_grad.setColorAt(0.35, QColor(70, 130, 240))
            body_grad.setColorAt(0.7, QColor(35, 70, 180))
            body_grad.setColorAt(1.0, QColor(18, 35, 100))
            painter.setBrush(QBrush(body_grad))

            border_pen = QPen(QColor(140, 180, 255, 90), 1.5)
            painter.setPen(border_pen)
            painter.drawEllipse(center, outer_r, outer_r)

            inner_highlight = QRadialGradient(
                center.x() - outer_r * 0.3,
                center.y() - outer_r * 0.45,
                outer_r * 0.55
            )
            inner_highlight.setColorAt(0.0, QColor(255, 255, 255, 80))
            inner_highlight.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setBrush(QBrush(inner_highlight))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(center, outer_r, outer_r)

        painter.end()

    # ----- panel -----
    def _toggle_panel(self):
        if self._panel and self._panel.isVisible():
            self._panel.hide()
            return

        if self._panel is None:
            self._panel = PanelWindow()
        self._position_panel()
        self._panel.show()

    def _position_panel(self):
        saved = ConfigManager.get_panel_position()
        if saved:
            sx, sy = saved
            screen = QApplication.primaryScreen().availableGeometry()
            panel_w = self._panel.width()
            panel_h = self._panel.height()
            sx = max(screen.left(), min(sx, screen.right() - panel_w))
            sy = max(screen.top(), min(sy, screen.bottom() - panel_h))
            self._panel.move(sx, sy)
            return

        ball_center = self.geometry().center()
        panel_w = self._panel.width()
        panel_h = self._panel.height()
        screen = QApplication.primaryScreen().availableGeometry()

        x = ball_center.x() - panel_w - 16
        if x < screen.left():
            x = ball_center.x() + 16

        y = ball_center.y() - panel_h // 2
        if y < screen.top():
            y = screen.top() + 8
        if y + panel_h > screen.bottom():
            y = screen.bottom() - panel_h - 8

        self._panel.move(x, y)

    def _open_settings(self):
        dlg = SettingsDialog(self)
        dlg.exec()

    def _open_history(self):
        if not self._panel:
            self._panel = PanelWindow()
        self._panel._open_history()
        self._panel.show()

    @staticmethod
    def _menu_style():
        return """
            QMenu {
                background-color: #1e2130;
                color: #e0e0e0;
                border: 1px solid #3a3f55;
                border-radius: 10px;
                padding: 6px 4px;
                font-size: 13px;
                font-family: "Microsoft YaHei";
            }
            QMenu::item {
                padding: 8px 28px;
                border-radius: 6px;
            }
            QMenu::item:selected {
                background-color: #3a4f80;
            }
            QMenu::separator {
                height: 1px;
                background: #3a3f55;
                margin: 4px 12px;
            }
        """


# ============================================================
#  聊天输入框（Enter发送，Ctrl+Enter换行）
# ============================================================
class ChatInput(QTextEdit):
    """自定义输入框：Enter发送，Ctrl+Enter换行"""

    send_signal = pyqtSignal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.insertPlainText("\n")
            else:
                self.send_signal.emit()
        else:
            super().keyPressEvent(event)


# ============================================================
#  历史对话列表弹窗
# ============================================================
class HistoryDialog(QDialog):
    """历史对话选择对话框"""

    conversation_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(310, 390)
        self._init_ui()
        self._load_list()

    def _init_ui(self):
        self.setStyleSheet("QDialog { background: transparent; }")

        container = QWidget(self)
        container.setGeometry(0, 0, 310, 390)
        container.setStyleSheet("""
            QWidget {
                background-color: rgba(20, 23, 38, 240);
                border: 1px solid rgba(255, 255, 255, 25);
                border-radius: 14px;
            }
        """)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title = QLabel("历史对话")
        title.setFont(QFont("Microsoft YaHei", 13, QFont.Weight.Bold))
        title.setStyleSheet("color: #fff; background: transparent; border: none;")
        title_row.addWidget(title)
        title_row.addStretch()

        new_btn = QPushButton("+ 新对话")
        new_btn.setFixedHeight(26)
        new_btn.setFont(QFont("Microsoft YaHei", 9))
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(70, 130, 240, 120);
                color: #c0d8ff;
                border: none;
                border-radius: 8px;
                padding: 0 10px;
            }
            QPushButton:hover {
                background-color: rgba(90, 150, 255, 180);
            }
        """)
        new_btn.clicked.connect(self._new_conversation)
        title_row.addWidget(new_btn)
        layout.addLayout(title_row)

        self._list = QScrollArea()
        self._list.setWidgetResizable(True)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setFrameShape(QFrame.Shape.NoFrame)
        self._list.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                width: 5px;
                background: transparent;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 18);
                border-radius: 2px;
                min-height: 20px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
        """)

        self._list_container = QWidget()
        self._list_container.setStyleSheet("background: transparent; border: none;")
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(5)
        self._list_layout.addStretch()
        self._list.setWidget(self._list_container)
        layout.addWidget(self._list, 1)

        close_btn = QPushButton("关闭")
        close_btn.setFixedHeight(30)
        close_btn.setFont(QFont("Microsoft YaHei", 10))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 10);
                color: #c0c8e0;
                border: none;
                border-radius: 10px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 20);
            }
        """)
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)

    def _load_list(self):
        for i in reversed(range(self._list_layout.count())):
            item = self._list_layout.itemAt(i)
            if item.widget():
                item.widget().deleteLater()
            elif item.spacerItem():
                self._list_layout.removeItem(item)

        conversations = ConversationStore.list_all()
        for conv in conversations:
            card = self._build_card(conv)
            layout_idx = self._list_layout.count()
            if layout_idx > 0 and self._list_layout.itemAt(layout_idx - 1).spacerItem():
                self._list_layout.insertWidget(layout_idx - 1, card)
            else:
                self._list_layout.addWidget(card)

    def _build_card(self, conv):
        card = QPushButton()
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.setFixedHeight(52)
        conv_id = conv["id"]
        title = conv.get("title", "未命名对话")
        try:
            dt = conv.get("updated_at", "")[:16].replace("T", " ")
        except Exception:
            dt = ""

        msg_count = len(conv.get("messages", []))
        card.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(255, 255, 255, 6);
                border: 1px solid rgba(255, 255, 255, 10);
                border-radius: 10px;
                text-align: left;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 14);
                border: 1px solid rgba(120, 160, 255, 60);
            }}
        """)

        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(10, 4, 10, 4)
        card_layout.setSpacing(6)

        text_col = QVBoxLayout()
        title_label = QLabel(title)
        title_label.setFont(QFont("Microsoft YaHei", 10))
        title_label.setStyleSheet("color: #e0e4f0; background: transparent; border: none;")

        sub_label = QLabel(f"{dt}  ·  {msg_count} 条消息")
        sub_label.setFont(QFont("Microsoft YaHei", 8))
        sub_label.setStyleSheet("color: #7078a0; background: transparent; border: none;")

        text_col.addWidget(title_label)
        text_col.addWidget(sub_label)
        card_layout.addLayout(text_col, 1)

        del_btn = QPushButton("×")
        del_btn.setFixedSize(22, 22)
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #666;
                border: none;
                border-radius: 11px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: rgba(255, 80, 80, 120);
                color: #fff;
            }
        """)
        del_btn.clicked.connect(lambda _, cid=conv_id: self._delete_conversation(cid))
        card_layout.addWidget(del_btn)

        card.clicked.connect(lambda _, cid=conv_id: self._select(cid))
        return card

    def _select(self, conv_id):
        self.conversation_selected.emit(conv_id)
        self.accept()

    def _delete_conversation(self, conv_id):
        ConversationStore.delete(conv_id)
        current = ConfigManager.get_current_conversation()
        if current == conv_id:
            ConfigManager.set_current_conversation("")
        self._load_list()

    def _new_conversation(self):
        conv = ConversationStore.create()
        ConfigManager.set_current_conversation(conv["id"])
        self.conversation_selected.emit(conv["id"])
        self.accept()


# ============================================================
#  弹出面板 (11cm × 6cm，45% 磨砂玻璃，可拖动)
# ============================================================
class PanelWindow(QWidget):
    """悬浮球弹出面板 — 磨砂玻璃质感，可拖动，聊天界面"""

    PANEL_WIDTH = 416
    PANEL_HEIGHT = 360
    RADIUS = 16
    DRAG_BAR_HEIGHT = 38

    _panel_opacity = ConfigManager.get_opacity()

    def __init__(self):
        super().__init__()
        self._drag_pos = QPoint()
        self._dragging = False
        self._conv_id = ConfigManager.get_current_conversation() or ""
        self._init_ui()
        self._build_content()
        self._restore_conversation()

    def _init_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(self.PANEL_WIDTH, self.PANEL_HEIGHT)

    def _build_content(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 6, 16, 12)
        layout.setSpacing(6)

        # ---- 可拖动的标题栏 ----
        self._title_bar = QWidget()
        self._title_bar.setFixedHeight(self.DRAG_BAR_HEIGHT)
        self._title_bar.setCursor(Qt.CursorShape.OpenHandCursor)

        title_layout = QHBoxLayout(self._title_bar)
        title_layout.setContentsMargins(4, 4, 4, 2)
        title_layout.setSpacing(0)

        title = QLabel("ApexAgent")
        title.setFont(QFont("Microsoft YaHei", 13, QFont.Weight.Bold))
        title.setStyleSheet("color: #ffffff; background: transparent;")
        title_layout.addWidget(title)

        title_layout.addStretch()

        # ---- 历史对话按钮 ----
        self._history_btn = QPushButton()
        self._history_btn.setFixedSize(26, 26)
        self._history_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        hist_icon_path = os.path.join(ASSET_DIR, "history.png")
        if os.path.exists(hist_icon_path):
            self._history_btn.setIcon(QIcon(hist_icon_path))
            self._history_btn.setIconSize(self._history_btn.size())
        else:
            self._history_btn.setText("📋")
        self._history_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #888;
                border: none;
                border-radius: 13px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 30);
            }
        """)
        self._history_btn.clicked.connect(self._open_history)
        title_layout.addWidget(self._history_btn)

        close_btn = QPushButton("×")
        close_btn.setFixedSize(24, 24)
        close_btn.setFont(QFont("Microsoft YaHei", 11))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #888;
                border: none;
                border-radius: 12px;
            }
            QPushButton:hover {
                background-color: rgba(255, 80, 80, 150);
                color: #fff;
            }
        """)
        close_btn.clicked.connect(self.hide)
        title_layout.addWidget(close_btn)

        layout.addWidget(self._title_bar)

        # ---- 消息滚动区 ----
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                width: 5px;
                background: transparent;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 20);
                border-radius: 2px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255, 255, 255, 35);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
        """)

        self._msg_container = QWidget()
        self._msg_container.setStyleSheet("background: transparent;")
        self._msg_layout = QVBoxLayout(self._msg_container)
        self._msg_layout.setContentsMargins(4, 4, 4, 4)
        self._msg_layout.setSpacing(8)

        # ---- 欢迎提示（首次显示，发送后渐隐消失） ----
        self._hint_label = QLabel("我能感知你的电脑，告诉我你要做什么？")
        self._hint_label.setFont(QFont("Microsoft YaHei", 10))
        self._hint_label.setStyleSheet("color: #7a82a0; background: transparent;")
        self._hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint_label.setWordWrap(True)
        self._hint_opacity = QGraphicsOpacityEffect(self._hint_label)
        self._hint_opacity.setOpacity(1.0)
        self._hint_label.setGraphicsEffect(self._hint_opacity)

        self._msg_layout.addWidget(self._hint_label)
        self._msg_layout.addStretch()

        self._scroll.setWidget(self._msg_container)
        layout.addWidget(self._scroll, 1)

        # ---- 输入区 ----
        input_row = QHBoxLayout()
        input_row.setSpacing(8)

        self._input = ChatInput()
        self._input.setPlaceholderText("输入指令，Enter发送，Ctrl+Enter换行")
        self._input.setFixedHeight(54)
        self._input.setFont(QFont("Microsoft YaHei", 10))
        self._input.setStyleSheet("""
            QTextEdit {
                background-color: rgba(255, 255, 255, 18);
                color: #e0e0e0;
                border: 1px solid rgba(255, 255, 255, 30);
                border-radius: 10px;
                padding: 6px 10px;
            }
            QTextEdit:focus {
                border: 1px solid rgba(120, 160, 255, 100);
            }
        """)
        self._input.send_signal.connect(self._on_send)
        input_row.addWidget(self._input, 1)

        send_btn = QPushButton("发送")
        send_btn.setFixedSize(46, 54)
        send_btn.setFont(QFont("Microsoft YaHei", 10))
        send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        send_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(70, 130, 240, 160);
                color: #ffffff;
                border: none;
                border-radius: 10px;
            }
            QPushButton:hover {
                background-color: rgba(90, 150, 255, 200);
            }
            QPushButton:pressed {
                background-color: rgba(50, 100, 200, 180);
            }
        """)
        send_btn.clicked.connect(self._on_send)
        input_row.addWidget(send_btn)

        layout.addLayout(input_row)

    # ----- 拖动逻辑 -----
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            local_y = event.position().toPoint().y()
            if local_y <= self.DRAG_BAR_HEIGHT + 10:
                self._dragging = True
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                self._title_bar.setCursor(Qt.CursorShape.ClosedHandCursor)
            else:
                self._dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._title_bar.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(event)

    def focusOutEvent(self, event):
        self._save_current_conversation()
        self.hide()
        super().focusOutEvent(event)

    def showEvent(self, event):
        self.activateWindow()
        self._input.setFocus()
        super().showEvent(event)

    # ----- 绘制磨砂背景 -----
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(self.rect())
        a = self._panel_opacity
        ba = int(255 * a)
        hi = int(22 * (a / 0.45))
        bo = int(35 * (a / 0.45))

        path = QPainterPath()
        path.addRoundedRect(rect, self.RADIUS, self.RADIUS)

        bg_grad = QLinearGradient(0, 0, 0, self.height())
        bg_grad.setColorAt(0.0, QColor(22, 25, 42, int(ba * 1.02)))
        bg_grad.setColorAt(0.5, QColor(18, 22, 36, ba))
        bg_grad.setColorAt(1.0, QColor(14, 18, 30, int(ba * 0.98)))
        painter.fillPath(path, QBrush(bg_grad))

        highlight_path = QPainterPath()
        highlight_rect = QRectF(2, 2, self.width() - 4, self.height() // 2 - 2)
        highlight_path.addRoundedRect(highlight_rect, self.RADIUS - 2, self.RADIUS - 2)
        highlight_grad = QLinearGradient(0, 0, 0, self.height() // 2)
        highlight_grad.setColorAt(0.0, QColor(255, 255, 255, hi))
        highlight_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillPath(highlight_path, QBrush(highlight_grad))

        border_pen = QPen(QColor(255, 255, 255, bo), 1.2)
        painter.setPen(border_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(
            rect.adjusted(0.5, 0.5, -0.5, -0.5),
            self.RADIUS, self.RADIUS
        )

        painter.end()

    # ----- 发送消息 -----
    def _on_send(self):
        text = self._input.toPlainText().strip()
        if not text:
            return
        self._input.clear()

        if self._hint_label.isVisible():
            self._fade_out_hint()

        self._ensure_conversation()
        conv = ConversationStore.load(self._conv_id)
        conv["messages"].append({
            "role": "user",
            "content": text,
            "timestamp": datetime.now().isoformat()
        })
        # 用第一条消息做标题
        if len(conv["messages"]) == 1:
            ConversationStore.update_title(self._conv_id, text)
        ConversationStore.save(conv)

        self._add_message(text, is_user=True)

        # AI 固定回复
        ai_text = "回答"
        conv["messages"].append({
            "role": "assistant",
            "content": ai_text,
            "timestamp": datetime.now().isoformat()
        })
        ConversationStore.save(conv)

        self._add_message(ai_text, is_user=False)

    def _ensure_conversation(self):
        if not self._conv_id:
            conv = ConversationStore.create()
            self._conv_id = conv["id"]
            ConfigManager.set_current_conversation(self._conv_id)

    def _save_current_conversation(self):
        if self._conv_id:
            ConfigManager.set_current_conversation(self._conv_id)
        pos = self.pos()
        ConfigManager.set_panel_position(pos.x(), pos.y())

    def _restore_conversation(self):
        if self._conv_id:
            conv = ConversationStore.load(self._conv_id)
            if conv and conv.get("messages"):
                self._hint_label.hide()
                for msg in conv["messages"]:
                    self._add_message(
                        msg["content"],
                        is_user=(msg["role"] == "user")
                    )

    def _open_history(self):
        self._save_current_conversation()
        dlg = HistoryDialog(self)
        pos = self.pos() + QPoint(40, 50)
        dlg.move(pos)
        dlg.conversation_selected.connect(self._on_history_selected)
        dlg.exec()

    def _on_history_selected(self, conv_id):
        self._conv_id = conv_id
        ConfigManager.set_current_conversation(conv_id)
        self._clear_messages()
        conv = ConversationStore.load(conv_id)
        if conv and conv.get("messages"):
            self._hint_label.hide()
            for msg in conv["messages"]:
                self._add_message(
                    msg["content"],
                    is_user=(msg["role"] == "user")
                )

    def _clear_messages(self):
        while self._msg_layout.count():
            item = self._msg_layout.takeAt(0)
            if item.widget() and item.widget() is not self._hint_label:
                item.widget().deleteLater()
        self._msg_layout.addWidget(self._hint_label)
        self._msg_layout.addStretch()
        self._hint_label.show()
        self._hint_opacity.setOpacity(1.0)

    def _add_message(self, text, is_user):
        bubble = QLabel(text)
        bubble.setWordWrap(True)
        bubble.setFont(QFont("Microsoft YaHei", 10))
        bubble.setMaximumWidth(int(self.PANEL_WIDTH * 0.7))
        bubble.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

        if is_user:
            bubble.setStyleSheet("""
                QLabel {
                    background-color: #3a6fd8;
                    color: #ffffff;
                    border-radius: 12px;
                    padding: 8px 12px;
                    margin: 0;
                }
            """)
        else:
            bubble.setStyleSheet("""
                QLabel {
                    background-color: rgba(255, 255, 255, 10);
                    color: #d0d4e8;
                    border-radius: 12px;
                    padding: 8px 12px;
                    margin: 0;
                }
            """)

        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")
        wrapper_layout = QHBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)

        if is_user:
            wrapper_layout.addStretch()
            wrapper_layout.addWidget(bubble)
        else:
            wrapper_layout.addWidget(bubble)
            wrapper_layout.addStretch()

        # 移除 stretch 再插入新消息
        if self._msg_layout.count() > 0:
            last = self._msg_layout.itemAt(self._msg_layout.count() - 1)
            if last.spacerItem():
                self._msg_layout.removeItem(last)

        self._msg_layout.addWidget(wrapper)
        self._msg_layout.addStretch()

        # 滚动到底部
        QTimer.singleShot(50, self._scroll_to_bottom)

    def _scroll_to_bottom(self):
        sb = self._scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _fade_out_hint(self):
        self._hint_anim = QPropertyAnimation(self._hint_opacity, b"opacity")
        self._hint_anim.setDuration(400)
        self._hint_anim.setStartValue(1.0)
        self._hint_anim.setEndValue(0.0)
        self._hint_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hint_anim.finished.connect(lambda: self._hint_label.hide())
        self._hint_anim.start()


# ============================================================
#  设置弹窗
# ============================================================
class SettingsDialog(QDialog):
    """设置对话框 — 面板透明度调节"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(280, 160)
        self._init_ui()

    def _init_ui(self):
        self.setStyleSheet("""
            QDialog {
                background: transparent;
            }
        """)

        container = QWidget(self)
        container.setGeometry(0, 0, 280, 160)
        container.setStyleSheet("""
            QWidget {
                background-color: rgba(22, 25, 42, 230);
                border: 1px solid rgba(255, 255, 255, 30);
                border-radius: 14px;
            }
        """)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 14, 20, 14)
        layout.setSpacing(10)

        # ---- 标题 ----
        title = QLabel("设置")
        title.setFont(QFont("Microsoft YaHei", 13, QFont.Weight.Bold))
        title.setStyleSheet("color: #ffffff; background: transparent; border: none;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # ---- 透明度标签 + 数值 ----
        value_layout = QHBoxLayout()
        value_layout.setSpacing(8)

        label = QLabel("面板透明度")
        label.setFont(QFont("Microsoft YaHei", 10))
        label.setStyleSheet("color: #c0c8e0; background: transparent; border: none;")

        self._value_label = QLabel(f"{int((1.0 - PanelWindow._panel_opacity) * 100)}%")
        self._value_label.setFont(QFont("Microsoft YaHei", 10, QFont.Weight.Bold))
        self._value_label.setStyleSheet("color: #90b0ff; background: transparent; border: none;")
        self._value_label.setAlignment(Qt.AlignmentFlag.AlignRight)

        value_layout.addWidget(label)
        value_layout.addStretch()
        value_layout.addWidget(self._value_label)
        layout.addLayout(value_layout)

        # ---- 滑块 ----
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(20, 90)
        self._slider.setValue(int((1.0 - PanelWindow._panel_opacity) * 100))
        self._slider.setStyleSheet("""
            QSlider {
                background: transparent;
                border: none;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: rgba(255, 255, 255, 15);
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                width: 18px;
                height: 18px;
                margin: -6px 0;
                background: qradialgradient(
                    cx:0.5, cy:0.5, radius:0.5,
                    fx:0.3, fy:0.3,
                    stop:0 #a0c8ff,
                    stop:0.7 #4078d0,
                    stop:1 #1a3a70
                );
                border-radius: 9px;
            }
            QSlider::handle:horizontal:hover {
                background: qradialgradient(
                    cx:0.5, cy:0.5, radius:0.5,
                    fx:0.3, fy:0.3,
                    stop:0 #c0e0ff,
                    stop:0.7 #6098f0,
                    stop:1 #2a4a90
                );
            }
            QSlider::sub-page:horizontal {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(70, 130, 240, 180),
                    stop:1 rgba(100, 160, 255, 150)
                );
                border-radius: 3px;
            }
        """)
        self._slider.valueChanged.connect(self._on_slider_changed)
        layout.addWidget(self._slider)

        # ---- 关闭按钮 ----
        close_btn = QPushButton("确定")
        close_btn.setFixedHeight(30)
        close_btn.setFont(QFont("Microsoft YaHei", 10))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(70, 130, 240, 160);
                color: #ffffff;
                border: none;
                border-radius: 10px;
            }
            QPushButton:hover {
                background-color: rgba(90, 150, 255, 200);
            }
            QPushButton:pressed {
                background-color: rgba(50, 100, 200, 180);
            }
        """)
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def _on_slider_changed(self, val):
        PanelWindow._panel_opacity = 1.0 - val / 100.0
        ConfigManager.set_opacity(PanelWindow._panel_opacity)
        self._value_label.setText(f"{val}%")
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, PanelWindow):
                widget.update()


# ============================================================
#  入口
# ============================================================
def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    ball = FloatingBall()
    ball.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()