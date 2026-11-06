"""林草种子检测软件：图像浏览、标注叠加与已标注种子统计。"""

import sys
from pathlib import Path

from PySide6.QtCore import QIODevice, QSaveFile, Qt, Signal
from PySide6.QtGui import (
    QColor, QFont, QIcon, QImage, QImageReader, QKeySequence, QPainter, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from annotation_loader import CLASS_COLORS, CLASS_NAMES, load_annotations
from dicom_loader import load_dicom


class ImageView(QGraphicsView):
    scale_changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fit_mode = True
        self._fit_scale = 1.0
        self._annotation_items = []

        scene = QGraphicsScene(self)
        self.setScene(scene)
        self._image_item = QGraphicsPixmapItem()
        scene.addItem(self._image_item)
        self._empty_message = scene.addText("点击“打开图片”选择一张图片")
        self._empty_message.setDefaultTextColor(QColor("#d4d7dd"))

        self.setBackgroundBrush(QColor("#242831"))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        # 固定隐藏滚动条，避免适应窗口时反复触发尺寸变化；仍可拖动平移。
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    @property
    def has_image(self):
        return not self._image_item.pixmap().isNull()

    def set_image(self, pixmap, reset_view=True):
        if reset_view:
            self.clear_annotations()
        self._image_item.setPixmap(pixmap)
        self._empty_message.hide()
        self.scene().setSceneRect(self._image_item.boundingRect())
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        if reset_view:
            self.fit_to_window()

    def clear_annotations(self):
        for item in self._annotation_items:
            item.setParentItem(None)
            self.scene().removeItem(item)
        self._annotation_items.clear()

    def set_annotations(self, annotations):
        self.clear_annotations()
        font = QFont()
        font.setPointSize(11)
        for annotation in annotations:
            color = QColor(CLASS_COLORS[annotation.label])
            rectangle = QGraphicsRectItem(
                0, 0, annotation.width, annotation.height, self._image_item
            )
            rectangle.setPos(annotation.x, annotation.y)
            pen = QPen(color, 2)
            pen.setCosmetic(True)
            rectangle.setPen(pen)
            rectangle.setZValue(10)
            rectangle.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            rectangle.setToolTip(
                f"{annotation.number:02d} {annotation.label}\n"
                f"位置：({annotation.x:g}, {annotation.y:g})\n"
                f"框尺寸：{annotation.width:g} × {annotation.height:g} 像素"
            )

            # 标签保持屏幕字号，框的坐标始终跟随原图一起缩放和平移。
            badge = QGraphicsRectItem(rectangle)
            badge.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            badge.setBrush(color)
            badge.setPen(QPen(Qt.PenStyle.NoPen))
            badge.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            text = QGraphicsSimpleTextItem(
                f"{annotation.number:02d} {annotation.label.removesuffix('种子')}", badge
            )
            text.setFont(font)
            text.setBrush(QColor("white"))
            text.setPos(4, 2)
            text.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            badge.setRect(0, 0, text.boundingRect().width() + 8, text.boundingRect().height() + 4)
            self._annotation_items.append(rectangle)

    def set_annotations_visible(self, visible):
        for item in self._annotation_items:
            item.setVisible(visible)

    def fit_to_window(self):
        if not self.has_image:
            return
        self._fit_mode = True
        self.resetTransform()
        self.fitInView(
            self._image_item.boundingRect(), Qt.AspectRatioMode.KeepAspectRatio
        )
        # 小图片不自动放大，避免打开时就出现明显的像素模糊。
        if self.transform().m11() > 1.0:
            self.resetTransform()
        self.centerOn(self._image_item)
        self._fit_scale = self.transform().m11()
        self.scale_changed.emit(self._fit_scale)

    def actual_size(self):
        if not self.has_image:
            return
        center = self.mapToScene(self.viewport().rect().center())
        self._fit_mode = False
        self.resetTransform()
        self.centerOn(center)
        self.scale_changed.emit(1.0)

    def zoom_by(self, factor, position=None):
        if not self.has_image:
            return
        current = self.transform().m11()
        target = max(min(self._fit_scale, 0.05), min(20.0, current * factor))
        if abs(target - current) < 1e-10:
            return

        self._fit_mode = False
        if position is None:
            position = self.viewport().rect().center()
        before = self.mapToScene(position)
        self.scale(target / current, target / current)
        after = self.mapToScene(position)
        center = self.mapToScene(self.viewport().rect().center())
        self.centerOn(center + before - after)
        self.scale_changed.emit(self.transform().m11())

    def wheelEvent(self, event):
        if not self.has_image:
            event.ignore()
            return
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if not delta:
            event.ignore()
            return
        delta = max(-600, min(600, delta))
        self.zoom_by(1.2 ** (delta / 120), event.position().toPoint())
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit_mode:
            self.fit_to_window()


class SeedViewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("林草种子检测软件 · 标注查看")
        self._dicom_image = None
        self._current_path = None
        self._current_image_size = None
        self._annotation_set = None
        self._last_annotation_directory = None
        self._last_directory = Path(__file__).parent / "data" / "raw"
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        toolbar = QHBoxLayout()
        self.open_button = QPushButton("打开图片")
        self.open_button.setShortcut(QKeySequence(QKeySequence.StandardKey.Open))
        self.open_button.clicked.connect(self.open_image)
        toolbar.addWidget(self.open_button)
        self.display_combo = QComboBox()
        self.display_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.display_combo.addItem("灰度显示")
        self.display_combo.setEnabled(False)
        self.display_combo.currentIndexChanged.connect(self._change_dicom_display)
        toolbar.addWidget(self.display_combo)
        self.export_button = QPushButton("导出高清PNG")
        self.export_button.setToolTip(
            "导出原始尺寸的标注图片：优先使用文件灰度，无有效文件窗口时使用自动对比度。"
        )
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_annotation_png)
        toolbar.addWidget(self.export_button)
        toolbar.addStretch()

        self.zoom_out_button = QPushButton("缩小")
        self.zoom_label = QLabel("—")
        self.zoom_label.setMinimumWidth(72)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_in_button = QPushButton("放大")
        self.actual_size_button = QPushButton("100%")
        self.fit_button = QPushButton("适应窗口")
        toolbar.addWidget(self.zoom_out_button)
        toolbar.addWidget(self.zoom_label)
        toolbar.addWidget(self.zoom_in_button)
        toolbar.addWidget(self.actual_size_button)
        toolbar.addWidget(self.fit_button)
        layout.addLayout(toolbar)

        self.details_label = QLabel("请选择 DICOM 原图（.dcm）或 PNG、JPG、BMP 图片。")
        self.details_label.setWordWrap(True)
        layout.addWidget(self.details_label)

        annotation_bar = QHBoxLayout()
        self.import_annotations_button = QPushButton("导入JSON标注")
        self.import_annotations_button.setEnabled(False)
        self.import_annotations_button.clicked.connect(self.import_annotations)
        annotation_bar.addWidget(self.import_annotations_button)
        self.clear_annotations_button = QPushButton("清除显示的标注")
        self.clear_annotations_button.setEnabled(False)
        self.clear_annotations_button.clicked.connect(self.clear_annotations)
        annotation_bar.addWidget(self.clear_annotations_button)
        self.show_annotations_checkbox = QCheckBox("显示标注")
        self.show_annotations_checkbox.setChecked(True)
        self.show_annotations_checkbox.setEnabled(False)
        annotation_bar.addWidget(self.show_annotations_checkbox)
        annotation_bar.addStretch()
        layout.addLayout(annotation_bar)

        self.annotation_status_label = QLabel("尚未导入标注。")
        self.annotation_status_label.setWordWrap(True)
        layout.addWidget(self.annotation_status_label)
        self.demo_notice = QLabel("演示标注：仅用于功能测试，类别不代表真实种子判定。")
        self.demo_notice.setWordWrap(True)
        self.demo_notice.setStyleSheet(
            "color: #92400e; background-color: #fffbeb; padding: 8px; border-radius: 4px;"
        )
        self.demo_notice.hide()
        layout.addWidget(self.demo_notice)

        body = QHBoxLayout()
        self.image_view = ImageView()
        body.addWidget(self.image_view, 1)
        self.statistics_panel = QGroupBox("已标注种子统计")
        self.statistics_panel.setMinimumWidth(260)
        self.statistics_panel.setMaximumWidth(300)
        statistics_layout = QVBoxLayout(self.statistics_panel)
        self.count_table = QTableWidget(len(CLASS_NAMES), 2)
        self.count_table.setHorizontalHeaderLabels(["类别", "数量"])
        self.count_table.verticalHeader().hide()
        self.count_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.count_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.count_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.count_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.count_table.setMaximumHeight(210)
        self.count_table.setMinimumHeight(190)
        self._count_items = {}
        for row, label in enumerate(CLASS_NAMES):
            item = QTableWidgetItem(label)
            swatch = QPixmap(12, 12)
            swatch.fill(QColor(CLASS_COLORS[label]))
            item.setIcon(QIcon(swatch))
            self.count_table.setItem(row, 0, item)
            count = QTableWidgetItem("—")
            count.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.count_table.setItem(row, 1, count)
            self._count_items[label] = count
        statistics_layout.addWidget(self.count_table)
        self.total_label = QLabel("已标注数量：—")
        self.fullness_label = QLabel("已标注饱满占比：—")
        self.total_label.setWordWrap(True)
        self.fullness_label.setWordWrap(True)
        statistics_layout.addWidget(self.total_label)
        statistics_layout.addWidget(self.fullness_label)
        formula = QLabel("（饱满 + 半饱满）÷ 已标注总数")
        formula.setWordWrap(True)
        statistics_layout.addWidget(formula)
        scope_note = QLabel("统计仅针对导入的标注。是否覆盖整张图，需由标注人员确认。")
        scope_note.setWordWrap(True)
        statistics_layout.addWidget(scope_note)
        statistics_layout.addStretch()
        body.addWidget(self.statistics_panel)
        layout.addLayout(body, 1)
        self.show_annotations_checkbox.toggled.connect(self.image_view.set_annotations_visible)
        self.image_view.scale_changed.connect(
            lambda scale: self.zoom_label.setText(f"{scale * 100:.1f}%")
        )
        self.zoom_out_button.clicked.connect(lambda: self.image_view.zoom_by(1 / 1.2))
        self.zoom_in_button.clicked.connect(lambda: self.image_view.zoom_by(1.2))
        self.actual_size_button.clicked.connect(self.image_view.actual_size)
        self.fit_button.clicked.connect(self.image_view.fit_to_window)
        self._view_buttons = (
            self.zoom_out_button,
            self.zoom_in_button,
            self.actual_size_button,
            self.fit_button,
        )
        for button in self._view_buttons:
            button.setEnabled(False)

        layout.addWidget(QLabel("滚轮或触控板上下滑动缩放，按住鼠标左键拖动图片。"))
        layout.addWidget(QLabel("当前支持图片与导入标注查看，尚未接入AI检测。"))
        self.setCentralWidget(content)
        self.resize(1100, 800)

    def open_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 DICOM 或图片",
            str(self._last_directory),
            "支持的图像 (*.dcm *.dicom *.png *.jpg *.jpeg *.bmp);;"
            "DICOM 原图 (*.dcm *.dicom);;普通图片 (*.png *.jpg *.jpeg *.bmp)",
        )
        if not path:
            return

        dicom_image = None
        try:
            if Path(path).suffix.lower() in (".dcm", ".dicom"):
                dicom_image = load_dicom(path)
                pixels = dicom_image.default_pixels
                if pixels is None:
                    pixels = dicom_image.auto_pixels
                image = self._grayscale_image(pixels)
            else:
                reader = QImageReader(path)
                reader.setAutoTransform(True)
                image = reader.read()
                if image.isNull():
                    raise ValueError(reader.errorString())
        except Exception as error:
            QMessageBox.warning(self, "无法打开图像", str(error))
            return

        self._dicom_image = dicom_image
        self._current_path = Path(path)
        self._current_image_size = (image.width(), image.height())
        self._last_directory = self._current_path.parent
        self.export_button.setEnabled(dicom_image is not None)
        self.import_annotations_button.setEnabled(True)
        self.image_view.set_image(QPixmap.fromImage(image))
        self.clear_annotations()
        for button in self._view_buttons:
            button.setEnabled(True)
        self.display_combo.blockSignals(True)
        self.display_combo.clear()
        if dicom_image is not None:
            if dicom_image.default_pixels is not None:
                self.display_combo.addItem("文件灰度", "default")
            self.display_combo.addItem("自动对比度", "auto")
        else:
            self.display_combo.addItem("普通图片")
        self.display_combo.setEnabled(dicom_image is not None)
        self.display_combo.blockSignals(False)
        if dicom_image is not None:
            self._update_dicom_details()
        else:
            self.details_label.setText(
                f"当前图片：{self._current_path.name}\n"
                f"图像尺寸：{image.width()} × {image.height()} 像素"
            )
        self.details_label.setToolTip(path)

    def import_annotations(self):
        if self._current_path is None:
            return
        directory = self._last_annotation_directory or self._current_path.parent
        path, _ = QFileDialog.getOpenFileName(
            self, "选择当前图片的Labelme标注", str(directory), "Labelme 标注 (*.json)"
        )
        if not path:
            return
        try:
            annotation_set = load_annotations(
                path, self._current_path, self._current_image_size
            )
        except ValueError as error:
            QMessageBox.warning(self, "无法导入标注", str(error))
            return

        self.image_view.set_annotations(annotation_set.annotations)
        self._annotation_set = annotation_set
        self._last_annotation_directory = Path(path).parent
        self.clear_annotations_button.setEnabled(True)
        self.show_annotations_checkbox.setEnabled(bool(annotation_set.annotations))
        self.image_view.set_annotations_visible(self.show_annotations_checkbox.isChecked())
        self.annotation_status_label.setText(
            f"{'演示标注' if annotation_set.demo_only else '已导入标注'}：{Path(path).name}"
        )
        self.annotation_status_label.setToolTip(str(Path(path)))
        self.demo_notice.setVisible(annotation_set.demo_only)
        self.statistics_panel.setTitle(
            "演示标注统计" if annotation_set.demo_only else "已标注种子统计"
        )
        for label, count in annotation_set.counts.items():
            self._count_items[label].setText(str(count))
        self.total_label.setText(
            f"{'演示框数' if annotation_set.demo_only else '已标注数量'}："
            f"{len(annotation_set.annotations)}"
        )
        rate = annotation_set.fullness_rate
        rate_text = "—" if rate is None else f"{rate:.1f}%"
        self.fullness_label.setText(
            f"{'演示饱满占比' if annotation_set.demo_only else '已标注饱满占比'}：{rate_text}"
        )

    def clear_annotations(self):
        self._annotation_set = None
        self.image_view.clear_annotations()
        self.clear_annotations_button.setEnabled(False)
        self.show_annotations_checkbox.setEnabled(False)
        self.show_annotations_checkbox.setChecked(True)
        self.annotation_status_label.setText("尚未导入标注。")
        self.annotation_status_label.setToolTip("")
        self.demo_notice.hide()
        self.statistics_panel.setTitle("已标注种子统计")
        for item in self._count_items.values():
            item.setText("—")
        self.total_label.setText("已标注数量：—")
        self.fullness_label.setText("已标注饱满占比：—")

    def export_annotation_png(self):
        if self._dicom_image is None:
            return

        output_directory = Path(__file__).parent / "data" / "label_images"
        try:
            output_directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            QMessageBox.warning(self, "无法创建导出文件夹", str(error))
            return

        dialog = QFileDialog(self, "导出高清PNG", str(output_directory))
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setNameFilter("PNG 图片 (*.png)")
        dialog.setDefaultSuffix("png")
        dialog.selectFile(f"{self._current_path.stem}.png")
        if dialog.exec() != QFileDialog.DialogCode.Accepted:
            return
        selected = dialog.selectedFiles()
        if not selected:
            return
        destination = Path(selected[0])
        if destination.suffix.lower() != ".png":
            QMessageBox.warning(self, "文件名不正确", "请使用 .png 作为文件扩展名。")
            return
        if destination.resolve() == self._current_path.resolve():
            QMessageBox.warning(self, "无法导出", "请选择新的PNG文件，保留原始DICOM。")
            return

        data = self._dicom_image
        # 标注图片固定使用同一规则，与当前查看模式、缩放或平移无关。
        if data.default_pixels is not None:
            pixels = data.default_pixels
            rule = f"文件灰度：{data.window_description}"
        else:
            pixels = data.auto_pixels
            rule = "自动对比度：有效像素的0.5%—99.5%分位；分位相等时使用完整范围"
        image = self._grayscale_image(pixels)
        image.setText("SourceFile", self._current_path.name)
        image.setText("DisplayRule", rule)
        image.setText("PhotometricInterpretation", data.photometric)
        image.setText("PresentationInverted", str(data.presentation_inverse).lower())
        image.setText("SourceBitsStored", str(data.bits_stored))
        output = QSaveFile(str(destination))
        try:
            if not output.open(QIODevice.OpenModeFlag.WriteOnly):
                raise OSError(output.errorString())
            if not image.save(output, "PNG"):
                output.cancelWriting()
                raise OSError("PNG写入失败，请检查目标文件夹的可用空间和权限。")
            if not output.commit():
                raise OSError(output.errorString())
        except Exception as error:
            output.cancelWriting()
            QMessageBox.warning(self, "导出失败", str(error))
            return

        QMessageBox.information(
            self,
            "导出完成",
            f"已导出：{destination}\n"
            f"尺寸：{image.width()} × {image.height()} 像素\n"
            f"灰度规则：{rule}\n"
            "请保留图片编号，用于Labelme标注。",
        )

    @staticmethod
    def _grayscale_image(pixels):
        height, width = pixels.shape
        # copy 让 Qt 拥有自己的像素，避免 numpy 临时数组释放后出现花屏。
        return QImage(
            pixels.data, width, height, pixels.strides[0], QImage.Format.Format_Grayscale8
        ).copy()

    def _change_dicom_display(self):
        if self._dicom_image is None:
            return
        data = self._dicom_image
        pixels = (
            data.default_pixels
            if self.display_combo.currentData() == "default"
            else data.auto_pixels
        )
        self.image_view.set_image(
            QPixmap.fromImage(self._grayscale_image(pixels)), reset_view=False
        )
        self._update_dicom_details()

    def _update_dicom_details(self):
        data = self._dicom_image
        height, width = data.raw_pixels.shape
        mode = (
            data.window_description
            if self.display_combo.currentData() == "default"
            else "自动对比度（0.5%—99.5%）"
        )
        text = (
            f"当前图片：{self._current_path.name}\n"
            f"图像尺寸：{width} × {height} 像素 · 单帧 · "
            f"存储 {data.bits_allocated} 位 / 有效 {data.bits_stored} 位\n"
            f"类型标记：{data.modality} · {data.photometric} · 显示：{mode}"
        )
        if data.note:
            text += f"\n{data.note}"
        self.details_label.setText(text)


def main():
    app = QApplication(sys.argv)
    window = SeedViewer()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
