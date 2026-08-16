from __future__ import annotations

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QKeySequenceEdit,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .config import AUTHOR, HOTKEYS
from .preferences import (
    KARAOKE_MODES,
    LYRICS_OFFSET_DEFAULT,
    LYRICS_OFFSET_LIMIT,
    load_hotkey_sequences,
    normalize_lyrics_offset,
    sequence_to_native,
)


class SettingsDialog(QDialog):
    def __init__(
        self,
        settings: QSettings,
        font_names: list[str],
        *,
        first_run: bool = False,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.first_run = first_run
        self.reset_layout_requested = False
        self.setWindowTitle("Первый запуск" if first_run else "Настройки оверлея")
        self.resize(720, 590)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet(self._style())

        root = QVBoxLayout(self)
        title = QLabel("Быстрая настройка · 3 шага" if first_run else "Настройки оверлея")
        title.setObjectName("title")
        subtitle = QLabel(
            "Выберите внешний вид, караоке и удобные сочетания. Всё можно изменить позже из трея."
            if first_run
            else f"Автор {AUTHOR} · изменения применяются после сохранения"
        )
        subtitle.setObjectName("muted")
        root.addWidget(title)
        root.addWidget(subtitle)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._general_tab(font_names), "1 · Интерфейс")
        self.tabs.addTab(self._karaoke_tab(), "2 · Караоке")
        self.tabs.addTab(self._hotkeys_tab(), "3 · Клавиши")
        root.addWidget(self.tabs, 1)

        controls = QHBoxLayout()
        reset_layout = QPushButton("Сбросить позиции и масштаб")
        reset_layout.clicked.connect(self._request_layout_reset)
        controls.addWidget(reset_layout)
        controls.addStretch()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(
            "Завершить" if first_run else "Сохранить"
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Позже" if first_run else "Отмена")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        controls.addWidget(buttons)
        root.addLayout(controls)

    def _general_tab(self, font_names: list[str]) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setSpacing(16)
        self.font_combo = QComboBox()
        self.font_combo.addItems(font_names)
        current_font = str(self.settings.value("font_preset", "Sour Gummy"))
        self.font_combo.setCurrentText(current_font)
        self.smart_visibility = QCheckBox("Показывать карточку только при полезных событиях")
        self.smart_visibility.setChecked(
            str(self.settings.value("smart_visibility", "true")).lower() == "true"
        )
        self.game_mode = QCheckBox("Не отвлекать автоматическими карточками в полноэкранной игре")
        self.game_mode.setChecked(str(self.settings.value("game_mode", "true")).lower() == "true")
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(45, 100)
        self.opacity.setValue(int(self.settings.value("panel_opacity", 82)))
        form.addRow("Шрифт", self.font_combo)
        form.addRow("Прозрачность", self.opacity)
        form.addRow("Умное появление", self.smart_visibility)
        form.addRow("Игровой режим", self.game_mode)
        return page

    def _karaoke_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setSpacing(16)
        self.karaoke_mode = QComboBox()
        for value, label in KARAOKE_MODES:
            self.karaoke_mode.addItem(label, value)
        saved_mode = str(self.settings.value("karaoke_mode", "full"))
        self.karaoke_mode.setCurrentIndex(max(0, self.karaoke_mode.findData(saved_mode)))
        self.karaoke_fps = QComboBox()
        for value in (30, 60, 90):
            self.karaoke_fps.addItem(f"{value} FPS", value)
        saved_fps = int(self.settings.value("karaoke_fps", 60))
        self.karaoke_fps.setCurrentIndex(max(0, self.karaoke_fps.findData(saved_fps)))
        self.auto_karaoke = QCheckBox("Восстанавливать режим караоке после запуска")
        self.auto_karaoke.setChecked(
            str(self.settings.value("auto_karaoke", "false")).lower() == "true"
        )
        offset_row = QHBoxLayout()
        self.lyrics_offset = QDoubleSpinBox()
        self.lyrics_offset.setRange(-LYRICS_OFFSET_LIMIT, LYRICS_OFFSET_LIMIT)
        self.lyrics_offset.setSingleStep(0.10)
        self.lyrics_offset.setDecimals(2)
        self.lyrics_offset.setSuffix(" с")
        self.lyrics_offset.setValue(
            normalize_lyrics_offset(
                self.settings.value("lyrics_offset", LYRICS_OFFSET_DEFAULT)
            )
        )
        reset_offset = QPushButton("Сбросить")
        reset_offset.clicked.connect(
            lambda: self.lyrics_offset.setValue(LYRICS_OFFSET_DEFAULT)
        )
        offset_row.addWidget(self.lyrics_offset, 1)
        offset_row.addWidget(reset_offset)
        source = QLabel(
            "Приоритет: Яндекс Музыка → синхронный резерв → обычный текст. "
            "Результат сохраняется локально."
        )
        source.setWordWrap(True)
        source.setObjectName("muted")
        form.addRow("Отображение", self.karaoke_mode)
        form.addRow("Плавность", self.karaoke_fps)
        form.addRow("Синхронизация", offset_row)
        form.addRow("Автозапуск", self.auto_karaoke)
        form.addRow("Источник текста", source)
        return page

    def _hotkeys_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        note = QLabel("Нажмите поле и задайте новое сочетание. Одинаковые комбинации не сохраняются.")
        note.setObjectName("muted")
        layout.addWidget(note)
        self.hotkey_table = QTableWidget(len(HOTKEYS), 2)
        self.hotkey_table.setHorizontalHeaderLabels(("Действие", "Сочетание"))
        self.hotkey_table.verticalHeader().hide()
        self.hotkey_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.hotkey_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.hotkey_table.setColumnWidth(1, 170)
        self.hotkey_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        saved = load_hotkey_sequences(self.settings)
        self.hotkey_editors: dict[str, QKeySequenceEdit] = {}
        for row, (canonical, _, description) in enumerate(HOTKEYS):
            self.hotkey_table.setItem(row, 0, QTableWidgetItem(description))
            editor = QKeySequenceEdit(QKeySequence(saved[canonical]))
            editor.setMaximumSequenceLength(1)
            self.hotkey_table.setCellWidget(row, 1, editor)
            self.hotkey_editors[canonical] = editor
        layout.addWidget(self.hotkey_table)
        return page

    def _request_layout_reset(self) -> None:
        self.reset_layout_requested = True

    def _save(self) -> None:
        sequences: dict[str, str] = {}
        occupied: dict[tuple[int, int], str] = {}
        try:
            for canonical, editor in self.hotkey_editors.items():
                sequence = editor.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
                native = sequence_to_native(sequence)
                if native in occupied:
                    raise ValueError(f"Конфликт с командой «{occupied[native]}»")
                occupied[native] = next(
                    item[2] for item in HOTKEYS if item[0] == canonical
                )
                sequences[canonical] = sequence
        except ValueError as error:
            QMessageBox.warning(self, "Не удалось сохранить", str(error))
            return

        self.settings.setValue("font_preset", self.font_combo.currentText())
        self.settings.setValue("smart_visibility", self.smart_visibility.isChecked())
        self.settings.setValue("game_mode", self.game_mode.isChecked())
        self.settings.setValue("panel_opacity", self.opacity.value())
        self.settings.setValue("karaoke_mode", self.karaoke_mode.currentData())
        self.settings.setValue("karaoke_fps", self.karaoke_fps.currentData())
        self.settings.setValue(
            "lyrics_offset", normalize_lyrics_offset(self.lyrics_offset.value())
        )
        self.settings.setValue("auto_karaoke", self.auto_karaoke.isChecked())
        for canonical, sequence in sequences.items():
            self.settings.setValue(f"hotkeys/{canonical}", sequence)
        if self.first_run:
            self.settings.setValue("first_run_complete", True)
        self.accept()

    @staticmethod
    def _style() -> str:
        return """
        QDialog { background: #080a10; color: #f4f6ff; }
        QLabel#title { font-size: 24px; font-weight: 700; }
        QLabel#muted { color: rgba(185,192,216,210); }
        QTabWidget::pane { border: 0; background: #0e1119; border-radius: 18px; }
        QTabBar::tab { background: #11151f; padding: 10px 16px; margin-right: 4px; border-radius: 10px; }
        QTabBar::tab:selected { background: #28220b; color: #ffdc54; }
        QComboBox, QDoubleSpinBox, QKeySequenceEdit, QTableWidget {
            background: #151a25; border: 0; border-radius: 9px; padding: 7px;
        }
        QHeaderView::section { background: #11151f; color: #ffdc54; border: 0; padding: 8px; }
        QPushButton {
            background: #ffda47; color: #111318; border: 0; border-radius: 9px;
            padding: 9px 14px; font-weight: 700;
        }
        QPushButton:hover { background: #ffe477; }
        QCheckBox { spacing: 9px; }
        """


class FirstRunWizard(SettingsDialog):
    def __init__(self, settings: QSettings, font_names: list[str]) -> None:
        super().__init__(settings, font_names, first_run=True)
