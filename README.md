# VideoEye - 视频码流分析工具

[English Version](README_en.md)

VideoEye 是一款基于 Python 和 PyQt6 开发的专业视频码流分析工具，对标 Elecard StreamEye。旨在为视频工程师和编解码开发人员提供轻量、直观的 H.264/AVC 与 H.265/HEVC 码流可视化分析能力。

![VideoEye 主界面](ScreenShot.png)

## 主要特性

- **多标准支持**：深度支持 H.264/AVC 和 H.265/HEVC 视频流分析。
- **可视化图表**：
  - **帧分布图 (Frame Chart)**：直观展示 I/P/B 帧类型及其大小分布。
  - **NALU 查看器**：树状结构解析 NAL Unit 头信息及语法结构。
  - **十六进制视图 (Hex Viewer)**：支持与 NALU 联动的原始码流查看。
- **解码预览**：内置解码器，支持逐帧查看解码后的画面图像。
- **精准导航**：支持逐帧步进、关键帧跳转，视图之间完全同步。
- **现代界面**：基于 PyQt6 的深色主题设计，支持拖拽打开文件。

## 快速开始

### 环境依赖

请确保已安装 Python 3.8+。

```bash
pip install -r requirements.txt
```

### 运行

```bash
python main.py
# 或直接指定文件路径
python main.py path/to/video.mp4
```

## 快捷键

- **左右方向键**：上一帧 / 下一帧
- **Shift + 左右方向键**：上一关键帧 / 下一关键帧
- **Home / End**：第一帧 / 最后一帧
- **F**：适应窗口大小
- **1**：100% 原始比例
