"""
ApexAgent 悬浮球 + 弹出面板
双击运行即可在桌面显示可拖拽的悬浮球，单击弹出磨砂面板。
"""

import sys
import os
import re
import json
import queue
import platform
from datetime import datetime
from PyQt6.QtWidgets import (
    QApplication, QWidget, QMenu, QVBoxLayout, QHBoxLayout,
    QLabel, QTextEdit, QPushButton, QSlider, QDialog,
    QScrollArea, QFrame, QSizePolicy, QGraphicsOpacityEffect,
    QComboBox, QLineEdit, QTabWidget,
)
from PyQt6.QtCore import (
    Qt, QPoint, QPointF, QPropertyAnimation, QEasingCurve,
    pyqtProperty, QRectF, QRect, QTimer, pyqtSignal
)
from PyQt6.QtGui import (
    QPainter, QBrush, QColor, QLinearGradient, QRadialGradient,
    QFont, QPen, QAction, QPainterPath, QPixmap, QIcon, QCursor
)
from .storage import ConfigManager, ConversationStore, AIConfig
from .agent import ApexAgent


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
                # 延迟 emit 避免 pyqt6 keyPressEvent 内直接 emit 的 GC 崩溃
                QTimer.singleShot(0, self.send_signal.emit)
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


_THINKING_TAG_RE = re.compile(
    r'[<＜](/?)(speaking|action|thinking|step|plan|OpenExe|ReadFile|ReadFolder|'
    r'SearchFile|RemoveFile|ReadRunning|KillProcess|GetSystemInfo|GetDiskUsage|'
    r'GetProcessList|GetNetworkStatus|GetActiveWindow|CloseWindow|ResizeWindow|'
    r'FocusWindow|Screenshot|MouseMove|MouseClick|ClipboardRead|ClipboardWrite|'
    r'RegistryRead|RegistryWrite|ServiceControl|Shutdown|NameChange|DiffJsonFile|'
    r'RunCommand|GetDesktopFiles|while|for|If)\b[^>＞]*[>＞]',
    re.IGNORECASE,
)

# 命令小框 → QLabel 用 HTML span 渲染
_CMD_PILL_HTML = (
    '<span style="'
    'background-color:#2d3440;'
    'color:#88c0d0;'
    'border:1.5px solid #5e81ac;'
    'border-radius:4px;'
    'padding:1px 6px;'
    'font-size:10px;'
    'font-weight:bold;'
    '">'
    '⚡命令'
    '</span>'
)


def _filter_thinking_tags(text: str) -> str:
    """把思考中的XML标签替换为⚡命令小框（分段escape，防HTML破坏）"""
    # 先用标记位替换 XML 标签
    text = _THINKING_TAG_RE.sub('\x00CMD\x00', text)
    # 分段转义：以 pill 为界拆分，每段单独转义 < >
    parts = text.split('\x00CMD\x00')
    escaped_parts = [p.replace('<', '&lt;').replace('>', '&gt;') for p in parts]
    # 用HTML pill拼接，外层包span强制RichText渲染
    return '<span>' + _CMD_PILL_HTML.join(escaped_parts) + '</span>'


class CollapsibleThinkingBubble(QWidget):
    """可折叠的思考气泡 —— 小三角 + 微光骨架屏 + 流光边框"""

    def __init__(self, text="", collapsed=False, parent=None):
        super().__init__(parent)
        self._collapsed = collapsed
        self._full_text = text
        self._raw_buf = ""          # 累积原始文本用于全局重过滤
        self._glow_active = False
        self._shimmer_offset = 0.0
        self._setup_ui()
        self._start_shimmer()

    def _setup_ui(self):
        self.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(4)

        self._toggle_btn = QPushButton()
        self._toggle_btn.setFixedSize(20, 20)
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.setStyleSheet("""
            QPushButton {
                background: rgba(120, 140, 200, 70);
                color: #a0b0d8;
                border: none;
                border-radius: 10px;
                font-size: 10px;
                font-weight: bold;
                padding: 0;
            }
            QPushButton:hover {
                background: rgba(140, 160, 220, 120);
                color: #d0d8f8;
            }
        """)
        self._update_toggle_icon()
        self._toggle_btn.clicked.connect(self._toggle)
        header_row.addWidget(self._toggle_btn)

        self._hint_label = QLabel("思考过程")
        self._hint_label.setFont(QFont("Microsoft YaHei", 9, QFont.Weight.Bold))
        self._hint_label.setStyleSheet("color: #9098c0; background: transparent; border: none;")
        header_row.addWidget(self._hint_label)
        header_row.addStretch()
        layout.addLayout(header_row)

        self._content = QLabel(self._full_text)
        self._content.setWordWrap(True)
        self._content.setFont(QFont("Microsoft YaHei", 9))
        self._content.setTextFormat(Qt.TextFormat.RichText)
        self._content.setStyleSheet("""
            QLabel {
                background-color: rgba(60, 60, 80, 100);
                color: #9098b8;
                border-radius: 10px;
                padding: 6px 10px;
                margin: 1px 0;
                border-left: 3px solid rgba(120, 140, 200, 80);
            }
        """)
        self._content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._content.setVisible(not self._collapsed)
        layout.addWidget(self._content)

    def _start_shimmer(self):
        self._shimmer_timer = QTimer(self)
        self._shimmer_timer.timeout.connect(self._tick_shimmer)
        self._shimmer_timer.start(35)

    def _tick_shimmer(self):
        self._shimmer_offset = (self._shimmer_offset + 0.05) % 2.0
        self.update()

    def _stop_shimmer(self):
        if hasattr(self, "_shimmer_timer") and self._shimmer_timer.isActive():
            self._shimmer_timer.stop()

    def _update_toggle_icon(self):
        self._toggle_btn.setText("▶" if self._collapsed else "▼")

    def setGlowActive(self, active):
        self._glow_active = active
        self.update()

    def _toggle(self):
        self._collapsed = not self._collapsed
        self._update_toggle_icon()
        self._content.setVisible(not self._collapsed)
        self.updateGeometry()

    def setText(self, text):
        self._full_text = text
        self._content.setText(text)

    def text(self):
        return self._full_text

    def isCollapsed(self):
        return self._collapsed

    def setCollapsed(self, collapsed):
        self._collapsed = collapsed
        self._update_toggle_icon()
        self._content.setVisible(not self._collapsed)

    def appendRaw(self, raw_chunk: str):
        """逐 token 累积原始文本，每次全局重过滤后替换整个卡片内容"""
        self._raw_buf += raw_chunk
        filtered = _filter_thinking_tags(self._raw_buf)
        self._full_text = filtered
        self._content.setText(filtered)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())

        if self._collapsed:
            # 折叠态：微光骨架屏 — 移动渐变条模拟流式加载
            shimmer_rect = QRectF(rect.x(), rect.y() + 24, rect.width(), 8)
            shimmer_grad = QLinearGradient(
                shimmer_rect.left() + (self._shimmer_offset - 1) * shimmer_rect.width(),
                0,
                shimmer_rect.left() + self._shimmer_offset * shimmer_rect.width(),
                0
            )
            shimmer_grad.setColorAt(0.0, QColor(45, 55, 85, 140))
            shimmer_grad.setColorAt(0.5, QColor(90, 110, 170, 160))
            shimmer_grad.setColorAt(1.0, QColor(45, 55, 85, 140))
            path = QPainterPath()
            path.addRoundedRect(shimmer_rect, 4, 4)
            painter.fillPath(path, QBrush(shimmer_grad))
        elif self._glow_active:
            # 展开态 + 流式激活：微光扫描效果
            glow_grad = QLinearGradient(
                rect.left() + (self._shimmer_offset - 1) * rect.width() * 0.6,
                0,
                rect.left() + self._shimmer_offset * rect.width() * 0.6,
                0
            )
            glow_grad.setColorAt(0.0, QColor(80, 110, 200, 0))
            glow_grad.setColorAt(0.5, QColor(120, 160, 240, 35))
            glow_grad.setColorAt(1.0, QColor(80, 110, 200, 0))
            path = QPainterPath()
            path.addRoundedRect(rect, 10, 10)
            painter.fillPath(path, QBrush(glow_grad))

        # 流光边框（展开+流式时）
        if not self._collapsed and self._glow_active:
            border_alpha = int(60 + 30 * abs(1 - self._shimmer_offset))
            pen = QPen(QColor(120, 160, 255, border_alpha), 1.8)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)

        painter.end()


# ============================================================
#  流光边框气泡包装器（用于 speaking 气泡流式动画）
# ============================================================
class GlowBorderBubble(QWidget):
    """带流光边框的包装器 — 包裹 speaking QLabel 显示流式动画"""

    def __init__(self, child, parent=None):
        super().__init__(parent)
        self._child = child
        self._glow_active = False
        self._shimmer_offset = 0.0
        self.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.addWidget(self._child)
        self._start_glow()

    def _start_glow(self):
        self._glow_timer = QTimer(self)
        self._glow_timer.timeout.connect(self._tick_glow)
        self._glow_timer.start(35)

    def _tick_glow(self):
        self._shimmer_offset = (self._shimmer_offset + 0.05) % 2.0
        if self._glow_active:
            self.update()

    def setGlowActive(self, active):
        self._glow_active = active
        self.update()

    def setText(self, text):
        self._child.setText(text)

    def text(self):
        return self._child.text()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._glow_active:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        alpha = int(50 + 30 * abs(1 - self._shimmer_offset))
        pen = QPen(QColor(140, 180, 255, alpha), 1.5)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 14, 14)
        painter.end()


# ============================================================
#  Action 卡片 — 微光骨架屏 + 中文命令名
# ============================================================
# 命令名中英文映射
_CMD_ZH_MAP = {
    "ReadFile": "读取文件", "ReadFolder": "读取文件夹", "SearchFile": "搜索文件",
    "RemoveFile": "删除文件", "OpenExe": "打开程序", "ReadRunning": "查看进程",
    "KillProcess": "终止进程", "GetSystemInfo": "系统信息", "GetDiskUsage": "磁盘使用",
    "GetProcessList": "进程列表", "GetNetworkStatus": "网络状态",
    "GetActiveWindow": "活动窗口", "CloseWindow": "关闭窗口", "ResizeWindow": "调整窗口",
    "FocusWindow": "窗口置顶", "ClipboardRead": "读取剪贴板", "ClipboardWrite": "写入剪贴板",
    "Screenshot": "截图", "MouseMove": "鼠标移动", "MouseClick": "鼠标点击",
    "RegistryRead": "读取注册表", "RegistryWrite": "写入注册表",
    "ServiceControl": "服务控制", "RunCommand": "运行命令", "Shutdown": "关机",
    "NameChange": "重命名", "DiffJsonFile": "JSON修改", "while": "循环",
    "for": "遍历", "If": "条件套件",
}


def _parse_action_card_info(action_xml: str) -> list:
    """从 action XML 中提取命令名列表，返回 [(英, 中), ...]"""
    cmds = []
    if not action_xml or not action_xml.strip():
        return cmds
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(f"<root>{action_xml}</root>")
        for el in root:
            tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
            cmds.append((tag, _CMD_ZH_MAP.get(tag, tag)))
    except ET.ParseError:
        pass
    return cmds


class ActionCard(QFrame):
    """单个 action 卡片：流式微光骨架屏 → 命令列表 → 执行结果"""

    CARD_WIDTH = 370

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = "streaming"  # streaming | ready | done
        self._commands = []        # [(en, zh), ...]
        self._results = []         # [(en, success, output), ...]
        self._shimmer_offset = 0.0
        self._setup_ui()
        self._start_shimmer()

    def _setup_ui(self):
        self.setFixedWidth(self.CARD_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        self.setStyleSheet("background: transparent; border: none;")

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(8, 6, 8, 6)
        self._layout.setSpacing(4)

        # 状态标签
        self._status_label = QLabel("⚡ 接收指令中...")
        self._status_label.setFont(QFont("Microsoft YaHei", 9))
        self._status_label.setStyleSheet("color: #60b0ff; background: transparent;")
        self._layout.addWidget(self._status_label)

        # 命令列表容器
        self._cmd_container = QWidget()
        self._cmd_container.setStyleSheet("background: transparent;")
        self._cmd_layout = QVBoxLayout(self._cmd_container)
        self._cmd_layout.setContentsMargins(0, 0, 0, 0)
        self._cmd_layout.setSpacing(2)
        self._layout.addWidget(self._cmd_container)

        # 结果容器
        self._result_container = QWidget()
        self._result_container.setStyleSheet("background: transparent;")
        self._result_layout = QVBoxLayout(self._result_container)
        self._result_layout.setContentsMargins(0, 0, 0, 0)
        self._result_layout.setSpacing(2)
        self._result_container.hide()
        self._layout.addWidget(self._result_container)

    def _start_shimmer(self):
        self._shimmer_timer = QTimer(self)
        self._shimmer_timer.timeout.connect(self._tick_shimmer)
        self._shimmer_timer.start(30)

    def _tick_shimmer(self):
        self._shimmer_offset = (self._shimmer_offset + 0.04) % 2.0
        self.update()

    def _stop_shimmer(self):
        if hasattr(self, "_shimmer_timer") and self._shimmer_timer.isActive():
            self._shimmer_timer.stop()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())

        # 背景
        if self._state == "streaming":
            # 微光骨架屏：移动的渐变光束
            bg_grad = QLinearGradient(
                rect.left() + (self._shimmer_offset - 1) * rect.width(),
                0,
                rect.left() + self._shimmer_offset * rect.width(),
                0
            )
            bg_grad.setColorAt(0.0, QColor(45, 55, 90, 200))
            bg_grad.setColorAt(0.5, QColor(80, 100, 160, 210))
            bg_grad.setColorAt(1.0, QColor(45, 55, 90, 200))
        elif self._state == "ready":
            bg_grad = QLinearGradient(0, 0, 0, self.height())
            bg_grad.setColorAt(0.0, QColor(40, 50, 80, 200))
            bg_grad.setColorAt(1.0, QColor(35, 45, 70, 200))
        else:
            bg_grad = QLinearGradient(0, 0, 0, self.height())
            bg_grad.setColorAt(0.0, QColor(38, 48, 75, 200))
            bg_grad.setColorAt(1.0, QColor(32, 42, 65, 200))

        path = QPainterPath()
        path.addRoundedRect(rect, 10, 10)
        painter.fillPath(path, QBrush(bg_grad))

        # 边框光圈 — streaming 时更亮
        if self._state == "streaming":
            alpha = int(70 + 30 * abs(1 - self._shimmer_offset))
            pen = QPen(QColor(100, 160, 255, alpha), 2.0)
        else:
            pen = QPen(QColor(80, 120, 200, 50), 1.2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)

        painter.end()

    def set_commands(self, commands: list):
        """设置解析好的命令列表 [(en, zh), ...]"""
        self._stop_shimmer()
        self._state = "ready"
        self._commands = commands
        self._status_label.setText("📋 等待执行...")
        self._status_label.setStyleSheet("color: #f0c060; background: transparent;")
        self._rebuild_cmd_list()

    def _rebuild_cmd_list(self):
        for i in reversed(range(self._cmd_layout.count())):
            w = self._cmd_layout.itemAt(i).widget()
            if w:
                w.deleteLater()
        for en, zh in self._commands:
            lbl = QLabel(f"  {zh}")
            lbl.setFont(QFont("Microsoft YaHei", 10))
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lbl.setStyleSheet("color: #c8d0e8; background: rgba(255,255,255,8); "
                              "border-radius: 6px; padding: 4px 8px;")
            self._cmd_layout.addWidget(lbl)

    def set_results(self, results_data: dict):
        """设置执行结果 results_data = executor.execute() 返回值"""
        self._state = "done"
        self._stop_shimmer()
        success = results_data.get("success", False)
        results_list = results_data.get("results", [])

        self._status_label.setText("✅ 执行完成" if success else "❌ 执行失败")
        self._status_label.setStyleSheet(
            "color: #40c080; background: transparent;" if success
            else "color: #f06060; background: transparent;"
        )

        self._result_container.show()
        for i in reversed(range(self._result_layout.count())):
            w = self._result_layout.itemAt(i).widget()
            if w:
                w.deleteLater()

        if results_list:
            for r in results_list:
                if isinstance(r, dict):
                    text = r.get("output", str(r))
                else:
                    text = str(r)
                if len(text) > 120:
                    text = text[:120] + "..."
                lbl = QLabel(text)
                lbl.setFont(QFont("Microsoft YaHei", 9))
                lbl.setWordWrap(True)
                lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                lbl.setStyleSheet("color: #90a0c0; background: transparent; padding: 2px 6px;")
                self._result_layout.addWidget(lbl)
        else:
            err = results_data.get("error", "")
            if err:
                lbl = QLabel(str(err)[:200])
                lbl.setFont(QFont("Microsoft YaHei", 9))
                lbl.setWordWrap(True)
                lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                lbl.setStyleSheet("color: #f08080; background: transparent; padding: 2px 6px;")
                self._result_layout.addWidget(lbl)


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

    # 跨线程安全：queue + timer 轮询，逐 chunk 渲染
    _STREAM_POLL_MS = 20

    def __init__(self):
        super().__init__()
        self._drag_pos = QPoint()
        self._dragging = False
        self._conv_id = ConfigManager.get_current_conversation() or ""
        self._agent = ApexAgent()
        self._is_processing = False
        # 步骤卡片系统（时间线顺序块，替代固定3块）
        self._step_cards = []         # [StepCard, ...]
        self._active_card = None      # 当前流式卡片
        self._action_buffer = ""      # action XML 缓冲
        # 流式队列 + 轮询定时器
        self._chunk_queue = queue.Queue()
        self._poll_timer = QTimer()
        self._poll_timer.timeout.connect(self._drain_queue)
        # 单向打字机：单一缓冲 + 单一卡片目标
        self._tw_buffer = ""
        self._tw_card = None
        self._tw_timer = QTimer()
        self._tw_timer.timeout.connect(self._typewriter_tick)
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

        # ---- 规划提示（闭环多轮调用时闪现） ----
        self._planning_label = QLabel("正在规划下一步...")
        self._planning_label.setFont(QFont("Microsoft YaHei", 9))
        self._planning_label.setStyleSheet("""
            QLabel {
                color: #8ea0c8;
                background: transparent;
                padding: 2px 8px;
            }
        """)
        self._planning_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._planning_label.setWordWrap(True)
        self._planning_label.hide()
        self._planning_shimmer = 0.0
        self._planning_timer = QTimer()
        self._planning_timer.timeout.connect(self._planning_shimmer_tick)

        self._msg_layout.addWidget(self._planning_label)
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

        self._stop_btn = QPushButton("■")
        self._stop_btn.setFixedSize(36, 54)
        self._stop_btn.setFont(QFont("Microsoft YaHei", 11))
        self._stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stop_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(240, 80, 80, 140);
                color: #ffffff;
                border: none;
                border-radius: 10px;
            }
            QPushButton:hover {
                background-color: rgba(255, 100, 100, 200);
            }
            QPushButton:pressed {
                background-color: rgba(200, 50, 50, 180);
            }
        """)
        self._stop_btn.clicked.connect(self._on_stop)
        self._stop_btn.hide()
        input_row.addWidget(self._stop_btn)

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
        if self._is_processing:
            return
        self._is_processing = True
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
        if len(conv["messages"]) == 1:
            ConversationStore.update_title(self._conv_id, text)
        ConversationStore.save(conv)

        self._add_message(text, is_user=True)

        # 自动附加系统信息
        now = datetime.now()
        sys_info = f"[系统信息] 时间: {now.strftime('%Y-%m-%d %H:%M')}, 操作系统: {platform.system()} {platform.release()}, 架构: {platform.machine()}, 主机名: {platform.node()}"
        user_msg_with_ctx = f"{sys_info}\n\n{text}"

        # 显示停止按钮
        self._stop_btn.show()

        # 构建对话上下文（历史消息含时间戳）
        context_messages = []
        for msg in conv.get("messages", []):
            if msg["role"] in ("user", "assistant"):
                ctx_msg = {"role": msg["role"], "content": msg["content"]}
                if msg["role"] == "user":
                    ts = msg.get("timestamp", "")
                    time_str = ""
                    if ts:
                        try:
                            dt = datetime.fromisoformat(ts)
                            time_str = f", 时间: {dt.strftime('%Y-%m-%d %H:%M')}"
                        except:
                            pass
                    ctx_msg["content"] = (
                        f"[系统信息] 操作系统: {platform.system()} {platform.release()}, "
                        f"架构: {platform.machine()}, 主机名: {platform.node()}{time_str}\n\n"
                        f"{msg['content']}"
                    )
                context_messages.append(ctx_msg)

        # 重置队列状态（线程安全）
        self._hide_planning()
        self._chunk_queue = queue.Queue()
        self._tw_buffer = ""
        self._tw_card = None
        self._active_card = None
        self._step_cards = []
        self._action_buffer = ""

        # 立即可见：创建思考占位卡片（微光骨架屏）
        placeholder = self._create_step_card("thinking")
        placeholder.setCollapsed(True)
        self._active_card = placeholder
        self._tw_card = placeholder

        # 重置 Agent 停止标志
        self._agent.reset_stop()

        # 启动轮询定时器
        self._poll_timer.start(self._STREAM_POLL_MS)

        # 异步 Agent — 子线程把 chunk 推入 queue
        self._agent.run_async(
            user_message=user_msg_with_ctx,
            conversation_messages=context_messages,
            on_chunk=self._push_chunk,
            on_complete=self._push_complete,
        )

    def _on_stop(self):
        """停止当前 Agent 推理"""
        if not self._is_processing:
            return
        self._agent.stop()
        self._hide_planning()
        self._poll_timer.stop()
        self._chunk_queue = queue.Queue()
        self._typewriter_flush()
        # 闭合 action 卡片
        action_buf = self._action_buffer.strip()
        exec_result = None
        if action_buf and self._active_card and isinstance(self._active_card, ActionCard):
            cmds = _parse_action_card_info(action_buf)
            if cmds:
                self._active_card.set_commands(cmds)
                exec_result = {"success": False, "results": [{"output": "⏹ 用户手动停止"}], "error": "用户手动停止"}
            else:
                self._msg_layout.removeWidget(self._active_card)
                self._active_card.deleteLater()
                self._active_card = None
        self._action_buffer = ""
        self._on_agent_complete({
            "thinking": "",
            "speaking": "",
            "success": False,
            "error": "用户手动停止",
            "messages": [],
            "action": action_buf,
            "exec_result": [exec_result] if exec_result else [],
        })

    def _push_chunk(self, chunk_type: str, text: str):
        """子线程回调：把 chunk 推入队列"""
        self._chunk_queue.put(("chunk", chunk_type, text))

    def _push_complete(self, result: dict):
        """子线程回调：把完成信号推入队列"""
        self._chunk_queue.put(("done", result))

    def _drain_queue(self):
        """主线程定时器：从队列取 chunk，分发到步骤卡片"""
        try:
            item = self._chunk_queue.get_nowait()
            kind = item[0]
            if kind == "chunk":
                _, chunk_type, text = item
                if chunk_type == "block_start":
                    self._hide_planning()
                    self._on_block_start(text)
                elif chunk_type == "thinking":
                    self._on_step_chunk("thinking", text)
                elif chunk_type == "speaking":
                    self._on_step_chunk("speaking", text)
                elif chunk_type == "action":
                    self._on_action_chunk(text)
                elif chunk_type == "planning":
                    self._show_planning()
                self._scroll_to_bottom()
            elif kind == "done":
                self._on_drain_done(item[1])
                return
            self._scroll_to_bottom()
        except queue.Empty:
            pass

    def _on_block_start(self, block_type: str):
        """新步骤块开始 — flush 旧块，创建新步骤卡片"""
        self._typewriter_flush()
        # 闭合当前 action 卡片
        if isinstance(self._active_card, ActionCard) and self._action_buffer.strip():
            cmds = _parse_action_card_info(self._action_buffer.strip())
            if cmds:
                self._active_card.set_commands(cmds)
        self._active_card = None
        self._action_buffer = ""

    def _on_step_chunk(self, chunk_type: str, text: str):
        """thinking/speaking 流式块 → 并入打字机"""
        if self._active_card is None or not isinstance(self._active_card, (CollapsibleThinkingBubble, GlowBorderBubble)):
            self._active_card = self._create_step_card(chunk_type)
        if not self._tw_timer.isActive():
            self._tw_timer.start(25)
        self._tw_card = self._active_card
        if isinstance(self._active_card, CollapsibleThinkingBubble):
            cur = self._active_card.text()
            if cur.startswith("⏳"):
                self._active_card.setText("")
            # 不过滤 — 逐 token 时原始文本累积，在 typewriter tick 中全局重过滤
        elif isinstance(self._active_card, GlowBorderBubble):
            cur = self._active_card.text()
            if cur.startswith("⏳"):
                self._active_card.setText("")
        self._tw_buffer += text

    def _on_action_chunk(self, text: str):
        """action 流式块 → 创建/更新 ActionCard"""
        self._typewriter_flush()
        self._action_buffer += text
        if not isinstance(self._active_card, ActionCard):
            self._active_card = ActionCard()
            self._active_card.setMaximumWidth(int(self.PANEL_WIDTH * 0.88))
            self._msg_layout.addWidget(self._active_card)
            self._msg_layout.addStretch()
            self._step_cards.append(self._active_card)

    def _create_step_card(self, step_type: str):
        """创建步骤卡片：thinking→CollapsibleThinkingBubble, speaking→GlowBorderBubble"""
        if step_type == "thinking":
            card = CollapsibleThinkingBubble("⏳ 思考中...", collapsed=True)
            card.setGlowActive(True)
            card.setMaximumWidth(int(self.PANEL_WIDTH * 0.88))
        else:
            lbl = QLabel("⏳ 等待回复...")
            lbl.setWordWrap(True)
            lbl.setMaximumWidth(int(self.PANEL_WIDTH * 0.80))
            lbl.setFont(QFont("Microsoft YaHei", 12))
            lbl.setStyleSheet("""
                QLabel {
                    background-color: rgba(255, 255, 255, 14);
                    color: #f0f0f8;
                    border-radius: 12px;
                    padding: 10px 14px;
                    margin: 2px 0;
                }
            """)
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            card = GlowBorderBubble(lbl)
            card.setGlowActive(True)
            card.setMaximumWidth(int(self.PANEL_WIDTH * 0.85))

        self._add_step_to_layout(card)
        self._step_cards.append(card)
        return card

    def _add_step_to_layout(self, widget):
        """把步骤卡片 widget 加入消息布局"""
        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")
        wrapper_layout = QHBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.addWidget(widget)
        wrapper_layout.addStretch()

        if self._msg_layout.count() > 0:
            last = self._msg_layout.itemAt(self._msg_layout.count() - 1)
            if last.spacerItem():
                self._msg_layout.removeItem(last)

        self._msg_layout.addWidget(wrapper)
        self._msg_layout.addStretch()

    def _typewriter_tick(self):
        """打字机：每 25ms 从缓冲取 2 字符渲染到当前卡片"""
        if self._tw_buffer:
            chunk = self._tw_buffer[:2]
            self._tw_buffer = self._tw_buffer[2:]
            if self._tw_card:
                if isinstance(self._tw_card, CollapsibleThinkingBubble):
                    self._tw_card.appendRaw(chunk)
                else:
                    self._tw_card.setText(self._tw_card.text() + chunk)
                self._tw_card.repaint()
            self._scroll_to_bottom()
        else:
            self._tw_timer.stop()

    def _typewriter_flush(self):
        """立即展现所有剩余打字机缓冲字符"""
        self._tw_timer.stop()
        if self._tw_buffer and self._tw_card:
            if isinstance(self._tw_card, CollapsibleThinkingBubble):
                self._tw_card.appendRaw(self._tw_buffer)
            else:
                self._tw_card.setText(self._tw_card.text() + self._tw_buffer)
            self._tw_card.repaint()
        self._tw_buffer = ""
        self._tw_card = None

    def _on_drain_done(self, result: dict):
        """Agent 完成：停止轮询，闭合卡片，刷新打字机"""
        self._poll_timer.stop()
        self._chunk_queue = queue.Queue()
        self._hide_planning()
        self._typewriter_flush()
        # 闭合当前激活的 action 卡片（如果有缓冲内容）
        action_content = self._action_buffer.strip()
        if action_content and isinstance(self._active_card, ActionCard):
            cmds = _parse_action_card_info(action_content)
            if cmds:
                self._active_card.set_commands(cmds)
            elif not self._active_card._commands:
                self._active_card.hide()
                self._active_card.deleteLater()
                if self._active_card in self._step_cards:
                    self._step_cards.remove(self._active_card)
        elif isinstance(self._active_card, ActionCard):
            # action卡片已无缓冲 → 如果还没有命令内容，就删除空壳
            if not getattr(self._active_card, '_commands', None):
                self._active_card.hide()
                self._active_card.deleteLater()
                if self._active_card in self._step_cards:
                    self._step_cards.remove(self._active_card)
        self._action_buffer = ""
        self._active_card = None
        self._on_agent_complete(result)

    def _on_agent_complete(self, result: dict):
        """Agent 推理完成回调 — 关闭流光，填充结果，保存对话"""
        self._is_processing = False

        # 关闭所有步骤卡片的流光
        for card in self._step_cards:
            if hasattr(card, "setGlowActive"):
                card.setGlowActive(False)

        self._stop_btn.hide()

        thinking = result.get("thinking", "")
        speaking = result.get("speaking", "")
        error = result.get("error", "")

        # 清理无内容的占位卡片（所有类型）
        to_remove = []
        for card in list(self._step_cards):
            if isinstance(card, CollapsibleThinkingBubble):
                t = card.text()
                if not t or t.startswith("⏳"):
                    to_remove.append(card)
            elif isinstance(card, GlowBorderBubble):
                t = card.text()
                if not t or t.startswith("⏳"):
                    to_remove.append(card)
            elif isinstance(card, ActionCard):
                if not card._commands:
                    to_remove.append(card)
        for card in to_remove:
            if card in self._step_cards:
                self._step_cards.remove(card)
            card.hide()
            card.deleteLater()

        # 错误提示 — 在最后一个 speaking 卡片上追加
        if error:
            for card in reversed(self._step_cards):
                if isinstance(card, GlowBorderBubble):
                    cur = card.text()
                    card.setText(cur + f"\n\n❌ 出错了: {error}")
                    break

        # 填充 action 卡片执行结果
        exec_results = result.get("exec_result", [])
        if isinstance(exec_results, list):
            action_cards = [c for c in self._step_cards if isinstance(c, ActionCard)]
            for i, ac in enumerate(action_cards):
                if i < len(exec_results) and exec_results[i]:
                    ac.set_results(exec_results[i])
        elif isinstance(exec_results, dict) and exec_results:
            for c in self._step_cards:
                if isinstance(c, ActionCard):
                    c.set_results(exec_results)
                    break

        # 保存到对话
        final_text = speaking.strip() if speaking else (error or "任务已完成。")
        thinking_text = ""
        for card in self._step_cards:
            if isinstance(card, CollapsibleThinkingBubble):
                t = card.text()
                if t and not t.startswith("⏳"):
                    thinking_text += t + "\n"

        # 收集所有 action 块用于保存
        action_blocks = []
        for blk in result.get("blocks", []):
            if blk.get("type") == "action" and blk.get("content", "").strip():
                action_blocks.append(blk["content"].strip())

        conv = ConversationStore.load(self._conv_id)
        if conv:
            conv["messages"].append({
                "role": "assistant",
                "content": final_text,
                "thinking": thinking_text.strip() if thinking_text else thinking.strip() if thinking else "",
                "action": result.get("action", ""),
                "action_blocks": action_blocks,
                "timestamp": datetime.now().isoformat()
            })
            agent_messages = result.get("messages", [])
            conv["_agent_context"] = agent_messages
            ConversationStore.save(conv)

        # 清空引用但保留卡片
        self._active_card = None
        self._tw_buffer = ""
        self._tw_card = None
        self._action_buffer = ""

    def _show_planning(self):
        """显示'正在规划下一步'微光提示"""
        try:
            self._planning_label.show()
            self._planning_shimmer = 0.0
            if not self._planning_timer.isActive():
                self._planning_timer.start(40)
        except RuntimeError:
            pass

    def _hide_planning(self):
        """隐藏规划提示"""
        try:
            self._planning_timer.stop()
            self._planning_label.hide()
        except RuntimeError:
            pass

    def _planning_shimmer_tick(self):
        """微光动画：文字亮度波动"""
        try:
            self._planning_shimmer = (self._planning_shimmer + 0.04) % 1.0
            import math
            alpha = 0.4 + 0.6 * math.sin(self._planning_shimmer * math.pi * 2)
            alpha = max(0.25, alpha)
            r, g, b = 142, 160, 200
            self._planning_label.setStyleSheet(
                f"QLabel {{ color: rgba({r},{g},{b},{int(alpha*255)}); background: transparent; padding: 2px 8px; }}")
        except RuntimeError:
            self._planning_timer.stop()

    def _scroll_to_bottom(self):
        sb = self._scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

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
                    role = msg.get("role", "user")
                    content = msg.get("content", "")
                    thinking = msg.get("thinking", "")
                    action_xml = msg.get("action", "")

                    if role == "user":
                        self._add_message(content, is_user=True)
                    else:
                        # 先重建 thinking（可折叠）
                        if thinking:
                            self._restore_thinking_bubble(thinking)
                        # 再重建 speaking
                        if content:
                            self._add_message(content, is_user=False)
                        # 重建 action 卡片：优先用 action_blocks (多块)
                        action_blocks = msg.get("action_blocks", [])
                        if action_blocks:
                            for ab in action_blocks:
                                self._restore_action_card(ab)
                        elif action_xml:
                            self._restore_action_card(action_xml)

    def _restore_thinking_bubble(self, thinking_text):
        """从历史对话中恢复思考气泡（默认折叠状态）"""
        if not thinking_text.strip():
            return
        bubble = CollapsibleThinkingBubble(thinking_text, collapsed=True)
        bubble.setMaximumWidth(int(self.PANEL_WIDTH * 0.88))

        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")
        wrapper_layout = QHBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.addWidget(bubble)
        wrapper_layout.addStretch()

        if self._msg_layout.count() > 0:
            last = self._msg_layout.itemAt(self._msg_layout.count() - 1)
            if last.spacerItem():
                self._msg_layout.removeItem(last)

        self._msg_layout.addWidget(wrapper)
        self._msg_layout.addStretch()

    def _restore_action_card(self, action_xml):
        """从历史对话中恢复 action 卡片（已完成状态）"""
        if not action_xml.strip():
            return
        cmds = _parse_action_card_info(action_xml)
        if not cmds:
            return
        card = ActionCard()
        card.set_commands(cmds)
        exec_result = {"success": True, "results": [{"output": "历史记录 — 执行结果已归档"}], "error": None}
        card.set_results(exec_result)
        self._msg_layout.addWidget(card)
        self._msg_layout.addStretch()

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
                role = msg.get("role", "user")
                content = msg.get("content", "")
                thinking = msg.get("thinking", "")
                action_xml = msg.get("action", "")

                if role == "user":
                    self._add_message(content, is_user=True)
                else:
                    if thinking:
                        self._restore_thinking_bubble(thinking)
                    if content:
                        self._add_message(content, is_user=False)
                    action_blocks = msg.get("action_blocks", [])
                    if action_blocks:
                        for ab in action_blocks:
                            self._restore_action_card(ab)
                    elif action_xml:
                        self._restore_action_card(action_xml)

    def _clear_messages(self):
        while self._msg_layout.count():
            item = self._msg_layout.takeAt(0)
            if item.widget() and item.widget() is not self._hint_label:
                item.widget().deleteLater()
        self._msg_layout.addWidget(self._hint_label)
        self._msg_layout.addStretch()
        self._hint_label.show()
        self._hint_opacity.setOpacity(1.0)

    def _add_message(self, text, is_user, is_loading=False):
        bubble = QLabel(text)
        bubble.setWordWrap(True)
        bubble.setFont(QFont("Microsoft YaHei", 10))
        bubble.setMaximumWidth(int(self.PANEL_WIDTH * 0.7))
        bubble.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        bubble.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

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
        elif is_loading:
            bubble.setStyleSheet("""
                QLabel {
                    background-color: rgba(255, 200, 60, 30);
                    color: #f0c060;
                    border-radius: 12px;
                    padding: 8px 12px;
                    margin: 0;
                    font-style: italic;
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

        return bubble

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
    """设置对话框 — 面板透明度 + AI 配置"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(340, 420)
        self._init_ui()

    def _init_ui(self):
        self.setStyleSheet("QDialog { background: transparent; }")

        container = QWidget(self)
        container.setGeometry(0, 0, 340, 420)
        container.setStyleSheet("""
            QWidget {
                background-color: rgba(22, 25, 42, 230);
                border: 1px solid rgba(255, 255, 255, 30);
                border-radius: 14px;
            }
        """)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(6)

        # ---- 标题 ----
        title = QLabel("设置")
        title.setFont(QFont("Microsoft YaHei", 13, QFont.Weight.Bold))
        title.setStyleSheet("color: #ffffff; background: transparent; border: none;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # ---- Tab ----
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet("""
            QTabWidget::pane {
                border: none;
                background: transparent;
            }
            QTabBar::tab {
                color: #8088b0;
                background: transparent;
                padding: 4px 16px;
                font-size: 11px;
                font-family: "Microsoft YaHei";
                border: none;
                border-bottom: 2px solid transparent;
            }
            QTabBar::tab:selected {
                color: #a0c8ff;
                border-bottom: 2px solid #6098f0;
            }
        """)

        self._panel_tab = self._build_panel_tab()
        self._ai_tab = self._build_ai_tab()
        self._tabs.addTab(self._panel_tab, "面板")
        self._tabs.addTab(self._ai_tab, "AI")
        layout.addWidget(self._tabs)

        # ---- 底部按钮 ----
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        close_btn = QPushButton("确定")
        close_btn.setFixedHeight(30)
        close_btn.setFont(QFont("Microsoft YaHei", 10))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(self._btn_style())
        close_btn.clicked.connect(self._save_and_close)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    def _build_panel_tab(self):
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        l = QVBoxLayout(w)
        l.setContentsMargins(4, 8, 4, 4)
        l.setSpacing(10)

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
        l.addLayout(value_layout)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(20, 90)
        self._slider.setValue(int((1.0 - PanelWindow._panel_opacity) * 100))
        self._slider.setStyleSheet(self._slider_style())
        self._slider.valueChanged.connect(self._on_slider_changed)
        l.addWidget(self._slider)
        l.addStretch()
        return w

    def _build_ai_tab(self):
        ai = AIConfig.load_all()
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        l = QVBoxLayout(w)
        l.setContentsMargins(4, 8, 4, 4)
        l.setSpacing(6)

        # 提供商选择
        prov_layout = QHBoxLayout()
        prov_label = QLabel("AI 提供商")
        prov_label.setFont(QFont("Microsoft YaHei", 10))
        prov_label.setStyleSheet("color: #c0c8e0; background: transparent; border: none;")
        prov_layout.addWidget(prov_label)
        prov_layout.addStretch()
        self._provider_combo = QComboBox()
        self._provider_combo.addItems(["ollama", "openai"])
        self._provider_combo.setCurrentText(ai.get("provider", "ollama"))
        self._provider_combo.setStyleSheet(self._combo_style())
        self._provider_combo.currentTextChanged.connect(self._on_provider_changed)
        prov_layout.addWidget(self._provider_combo)
        l.addLayout(prov_layout)

        # Ollama 配置组
        self._ollama_group = QWidget()
        self._ollama_group.setStyleSheet("background: transparent;")
        og = QVBoxLayout(self._ollama_group)
        og.setContentsMargins(0, 2, 0, 2)
        og.setSpacing(5)
        og.addLayout(self._labeled_input("Ollama URL", "ollama_url", ai.get("ollama_url", "")))
        self._ollama_model_input = self._labeled_input_widget("模型名称", "ollama_model", ai.get("ollama_model", ""))
        og.addLayout(self._ollama_model_input)
        l.addWidget(self._ollama_group)

        # OpenAI 配置组
        self._openai_group = QWidget()
        self._openai_group.setStyleSheet("background: transparent;")
        og2 = QVBoxLayout(self._openai_group)
        og2.setContentsMargins(0, 2, 0, 2)
        og2.setSpacing(5)
        og2.addLayout(self._labeled_input("API URL", "openai_url", ai.get("openai_url", "")))
        og2.addLayout(self._labeled_input("API Key", "openai_key", ai.get("openai_key", ""), echo=True))
        og2.addLayout(self._labeled_input("模型名称", "openai_model", ai.get("openai_model", "")))
        l.addWidget(self._openai_group)

        # 初始显示/隐藏
        self._on_provider_changed(ai.get("provider", "ollama"))

        l.addStretch()
        return w

    def _labeled_input(self, label_text, field_name, default_val, echo=False):
        row = QHBoxLayout()
        row.setSpacing(6)
        lbl = QLabel(label_text)
        lbl.setFixedWidth(60)
        lbl.setFont(QFont("Microsoft YaHei", 9))
        lbl.setStyleSheet("color: #9098b0; background: transparent; border: none;")
        row.addWidget(lbl)
        edit = QLineEdit(default_val)
        edit.setFont(QFont("Microsoft YaHei", 9))
        edit.setStyleSheet(self._input_style())
        if echo:
            edit.setEchoMode(QLineEdit.EchoMode.Password)
        setattr(self, f"_field_{field_name}", edit)
        row.addWidget(edit, 1)
        return row

    def _labeled_input_widget(self, label_text, field_name, default_val):
        row = QHBoxLayout()
        row.setSpacing(6)
        lbl = QLabel(label_text)
        lbl.setFixedWidth(60)
        lbl.setFont(QFont("Microsoft YaHei", 9))
        lbl.setStyleSheet("color: #9098b0; background: transparent; border: none;")
        row.addWidget(lbl)
        edit = QLineEdit(default_val)
        edit.setFont(QFont("Microsoft YaHei", 9))
        edit.setStyleSheet(self._input_style())
        setattr(self, f"_field_{field_name}", edit)
        row.addWidget(edit, 1)
        return row

    def _on_provider_changed(self, provider):
        if provider == "ollama":
            self._ollama_group.show()
            self._openai_group.hide()
        else:
            self._ollama_group.hide()
            self._openai_group.show()

    def _save_and_close(self):
        PanelWindow._panel_opacity = 1.0 - self._slider.value() / 100.0
        ConfigManager.set_opacity(PanelWindow._panel_opacity)

        ai_data = {
            "provider": self._provider_combo.currentText(),
            "ollama_url": getattr(self, "_field_ollama_url", QLineEdit()).text().strip(),
            "ollama_model": getattr(self, "_field_ollama_model", QLineEdit()).text().strip(),
            "openai_url": getattr(self, "_field_openai_url", QLineEdit()).text().strip(),
            "openai_key": getattr(self, "_field_openai_key", QLineEdit()).text().strip(),
            "openai_model": getattr(self, "_field_openai_model", QLineEdit()).text().strip(),
        }
        AIConfig.save_all(ai_data)

        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, PanelWindow):
                widget.update()
        self.accept()

    def _on_slider_changed(self, val):
        self._value_label.setText(f"{val}%")

    # ------- 样式 --------
    def _btn_style(self):
        return """
            QPushButton {
                background-color: rgba(70, 130, 240, 160);
                color: #ffffff;
                border: none;
                border-radius: 10px;
            }
            QPushButton:hover { background-color: rgba(90, 150, 255, 200); }
            QPushButton:pressed { background-color: rgba(50, 100, 200, 180); }
        """

    def _input_style(self):
        return """
            QLineEdit {
                background-color: rgba(255, 255, 255, 12);
                color: #e0e0e0;
                border: 1px solid rgba(255, 255, 255, 20);
                border-radius: 6px;
                padding: 3px 6px;
            }
            QLineEdit:focus {
                border: 1px solid rgba(120, 160, 255, 80);
            }
        """

    def _combo_style(self):
        return """
            QComboBox {
                background-color: rgba(255, 255, 255, 12);
                color: #e0e0e0;
                border: 1px solid rgba(255, 255, 255, 20);
                border-radius: 6px;
                padding: 2px 6px;
                min-width: 80px;
                font-size: 10px;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background-color: #1a1d2e;
                color: #e0e0e0;
                selection-background-color: #3a4f80;
                border: 1px solid #3a3f55;
            }
        """

    def _slider_style(self):
        return """
            QSlider { background: transparent; border: none; }
            QSlider::groove:horizontal {
                height: 6px; background: rgba(255, 255, 255, 15); border-radius: 3px;
            }
            QSlider::handle:horizontal {
                width: 18px; height: 18px; margin: -6px 0;
                background: qradialgradient(cx:0.5, cy:0.5, radius:0.5,
                    fx:0.3, fy:0.3, stop:0 #a0c8ff, stop:0.7 #4078d0, stop:1 #1a3a70);
                border-radius: 9px;
            }
            QSlider::handle:horizontal:hover {
                background: qradialgradient(cx:0.5, cy:0.5, radius:0.5,
                    fx:0.3, fy:0.3, stop:0 #c0e0ff, stop:0.7 #6098f0, stop:1 #2a4a90);
            }
            QSlider::sub-page:horizontal {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(70, 130, 240, 180), stop:1 rgba(100, 160, 255, 150));
                border-radius: 3px;
            }
        """


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