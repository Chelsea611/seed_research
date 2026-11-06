from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QFileDialog,
)
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt

app = QApplication([])

window = QWidget()
window.setWindowTitle("林草种子检测软件")
window.resize(1000, 700)
layout = QVBoxLayout(window)

message_label = QLabel("请导入一张种子图片")
open_button = QPushButton("打开图片")

layout.addWidget(open_button)
layout.addWidget(message_label)

image_label = QLabel("图片将显示在这里")
image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

layout.addWidget(image_label)
 
def on_open_clicked():
    file_path, _ = QFileDialog.getOpenFileName(
        window,
        "选择种子图片",
        "",
        "图片文件 (*.png *.jpg *.jpeg *.bmp)"
    )

    if not file_path:
        return

    picture = QPixmap(file_path)

    if picture.isNull():
        message_label.setText("图片读取失败，请检查文件")
        return

    preview = picture.scaled(
        800,
        500,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation
    )

    image_label.setPixmap(preview)
    message_label.setText(file_path)
    
open_button.clicked.connect(on_open_clicked)

window.show()

app.exec()