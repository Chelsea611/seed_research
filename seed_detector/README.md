# 林草种子检测软件

当前版本支持 DICOM 与普通图片浏览、高清 PNG 导出，以及 Labelme 矩形标注的读取、显示和统计。AI 自动检测尚未接入。

## 运行

在终端执行：

```bash
"/Users/sheena/Desktop/山大research/chatGPT/seed_detector/.conda/bin/python" "/Users/sheena/Desktop/山大research/chatGPT/seed_detector/main.py"
```

## 测试标注导入

1. 点击“打开图片”，选择 `data/label_images/2022550132.png` 或 `data/raw/2022550132.dcm`。
2. 点击“导入JSON标注”，选择 `data/demo_annotations/2022550132.demo.json`。
3. 检查图片上是否显示五个带编号的彩色框，右侧每类数量是否为 1。
4. 检查界面是否持续标明“演示标注”，演示饱满占比是否为 40.0%。
5. 尝试缩放、拖动、隐藏标注或切换 DICOM 灰度模式，框应始终对应原图位置。
6. 点击“清除显示的标注”，只移除当前界面的标注，不删除 JSON 文件。

演示文件中的框和类别是功能测试数据，未经专家确认，不作为真实种子分类、训练标签或检测精度证据。

## 正式标注要求

- 使用原始尺寸的高清图片标注，一粒种子一个矩形框。
- 类别名称为：饱满种子、半饱满种子、虫蛀种子、空瘪种子、腐烂种子。
- JSON 中的图片文件名和尺寸必须与当前图片匹配。查看同名 DICOM 时，可以导入其同尺寸 PNG 的标注。
- 当前版本仅接受 Labelme 的 `rectangle` 矩形；其它形状、未知类别、零面积、越界、重复框及尺寸不符会明确报错，并保留原有显示。
- JSON 的 `flags.demo_only` 为 `true` 时，界面会显示演示标识。正式专家标注不需要这个演示标记。
- 所有统计均以已导入的标注为范围。标注是否覆盖整张图片，需要标注人员确认，程序不会把部分标注当作整批检测结果。

## 代码入口

- `main.py`：窗口、图片交互、导出和标注显示。
- `dicom_loader.py`：DICOM 读取与灰度转换。
- `annotation_loader.py`：标注校验、五类计数和已标注饱满占比。
- `lesson01_window.py`：学习练习，继续单独保留。
