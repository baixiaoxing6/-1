# LocalScreenCam MVP

LocalScreenCam 是一个本地自用的 Windows 桌面软件 MVP。它可以采集屏幕、指定显示器、指定窗口、指定区域、本地视频文件或本地图片文件，并通过 `pyvirtualcam` 输出到系统中已有的虚拟摄像头后端。

> 重要说明：本 MVP 不从零编写 Windows 摄像头驱动，也不会安装新的内核级摄像头设备。它依赖 OBS Studio Virtual Camera 或 Unity Capture 等成熟虚拟摄像头后端。浏览器摄像头列表里通常显示的是 `OBS Virtual Camera`、`Unity Video Capture` 等后端设备名，不一定显示 `LocalScreenCam`。如果后续必须显示为 `LocalScreenCam` 这个设备名，需要开发/签名自定义 DirectShow 虚拟摄像头过滤器或驱动，复杂度会明显提高。

## 功能

- GUI 桌面界面，基于 PySide6。
- 来源类型：
  - 全屏
  - 指定显示器
  - 指定窗口
  - 指定区域
  - 本地视频文件，例如 MP4
  - 本地图片文件，例如 JPG/PNG
- 输出分辨率：1280x720、1920x1080。
- 输出帧率：15 FPS、30 FPS。
- 填充模式：
  - 适应：等比缩放并加黑边。
  - 裁剪填充：等比放大并居中裁剪。
- 水平镜像、垂直翻转。
- 开始/停止虚拟摄像头输出。
- 实时预览当前输出画面。
- 窗口置顶。
- 保存配置到 `config.json`，下次启动自动加载。
- 日志输出到界面和 `logs/` 目录。
- 后台线程采集和推流，不阻塞界面。

## 运行环境

- Windows 10/11，推荐 64 位。
- Python 3.11 或更高版本。
- 已安装 OBS Studio（带 OBS Virtual Camera）或 Unity Capture。
- Google Chrome 或 Chromium 类浏览器，例如使用 Chrome/Chromium 内核的 BitBrowser 配置环境。

## 安装依赖

在项目目录打开 PowerShell 或 CMD：

```bat
cd LocalScreenCam
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

如果 `pyvirtualcam` 打开摄像头失败，优先安装或重装 OBS Studio；仍失败时可安装 Unity Capture，然后重启 LocalScreenCam 和浏览器。

## 运行

```bat
cd LocalScreenCam
.venv\Scripts\activate
python main.py
```

默认配置：1280x720、30 FPS、全屏来源、适应模式、不开镜像。

## 使用步骤

1. 先安装 OBS Studio 或 Unity Capture。
2. 启动 `python main.py`。
3. 在 GUI 中选择来源类型：全屏、指定显示器、指定窗口、本地视频、本地图片等。
4. 选择分辨率和帧率。
5. 选择填充模式、镜像、翻转等画面选项。
6. 点击“开始虚拟摄像头”。
7. 打开 Chrome/BitBrowser 的网页摄像头权限页面，选择 `OBS Virtual Camera` 或 `Unity Video Capture`。
8. 不使用时点击“停止虚拟摄像头”。

## 打包成 EXE

在项目目录双击或运行：

```bat
build_exe.bat
```

构建完成后文件位于：

```text
dist\LocalScreenCam\LocalScreenCam.exe
```

请复制整个 `dist\LocalScreenCam` 文件夹，不要只复制 exe。目标机器仍然需要安装 OBS Studio Virtual Camera 或 Unity Capture。

## Chrome 中选择虚拟摄像头

1. 启动 LocalScreenCam，并点击“开始虚拟摄像头”。
2. 打开 Chrome。
3. 访问需要摄像头的网站。
4. 浏览器弹出权限时选择允许。
5. 在网页的视频设置或 Chrome 设置中选择摄像头：`OBS Virtual Camera` 或 `Unity Video Capture`。
6. 如果没有看到设备，关闭所有 Chrome 窗口后重新打开。

Chrome 设置路径：

```text
chrome://settings/content/camera
```

## BitBrowser/比特浏览器中选择虚拟摄像头

BitBrowser/比特浏览器属于指纹/多环境浏览器，通常提供 Chrome/Chromium 内核环境。理论上，使用 Chrome/Chromium 内核的浏览器环境会通过系统媒体设备列表读取摄像头设备，因此也应能看到 OBS/Unity Capture 这类系统虚拟摄像头。

排查时注意：BitBrowser 的每个浏览器环境可能有独立的指纹、WebRTC、权限和启动参数配置。请在对应环境里检查摄像头权限和 WebRTC/媒体设备相关设置。

## 常见问题

### 1. 浏览器看不到虚拟摄像头

按顺序排查：

1. 重启浏览器，确保所有 Chrome/BitBrowser 进程都已退出。
2. 重启 LocalScreenCam。
3. 检查 Windows 隐私设置里的摄像头权限：
   - Windows 11：设置 → 隐私和安全性 → 摄像头。
   - Windows 10：设置 → 隐私 → 摄像头。
   - 确保摄像头访问、桌面应用访问摄像头已开启。
4. 检查网页摄像头权限：
   - Chrome：`chrome://settings/content/camera`。
   - 删除被阻止的网站记录，重新进入网页并允许访问。
5. 检查 OBS Studio Virtual Camera 或 Unity Capture 是否已经安装。
6. 检查 32 位/64 位兼容问题：建议 Python、浏览器、OBS/Unity Capture 都使用 64 位。
7. 检查浏览器是否被启动参数限制了摄像头或 WebRTC，例如 fake media、禁用 WebRTC、禁用媒体设备枚举等参数。
8. 在 `https://webcamtests.com/` 或其他 browser camera test 页面测试。
9. 查看 LocalScreenCam 界面日志和 `logs/` 目录日志。

### 2. LocalScreenCam 提示没有可用虚拟摄像头后端

说明 `pyvirtualcam` 没能打开 OBS/Unity Capture 后端。处理方法：

1. 安装或重装 OBS Studio。
2. 或安装 Unity Capture。
3. 重启 LocalScreenCam。
4. 重启浏览器。
5. 确认没有其他程序独占虚拟摄像头。

### 3. 指定窗口黑屏或显示异常

MVP 使用屏幕区域方式采集窗口矩形，因此：

- 窗口被最小化时无法采集，会输出错误提示画面。
- 某些硬件加速窗口、受保护内容窗口、管理员权限窗口可能无法正常采集。
- 如果被采集窗口以管理员权限运行，LocalScreenCam 也可尝试以管理员权限运行。
- 如果窗口被其他窗口遮挡，屏幕区域采集可能采到遮挡内容。后续版本可考虑 Windows Graphics Capture API。

### 4. 本地视频打不开

- 优先使用 H.264 编码的 MP4。
- 如果路径包含特殊字符或中文，先复制到英文路径测试。
- 安装系统解码器或换用 OpenCV 能读取的格式。

### 5. 输出卡顿或 CPU 占用高

- 降低分辨率到 1280x720。
- 降低帧率到 15 FPS。
- 使用“指定区域”减少采集范围。
- 关闭不必要的预览窗口和高占用软件。

### 6. Chrome/BitBrowser 仍然显示旧画面或旧设备

- 停止 LocalScreenCam 输出。
- 关闭所有浏览器进程。
- 重新启动 LocalScreenCam 并开始输出。
- 再启动浏览器进入测试页面。

## 项目结构

```text
LocalScreenCam/
  main.py
  requirements.txt
  README.md
  build_exe.bat
  config.json
  assets/
  logs/
```

## 后续可升级方向

- 使用 Windows Graphics Capture API 替换窗口区域采集，提高窗口捕获稳定性。
- 添加区域拖拽选择器。
- 添加文字/水印/时间戳叠加。
- 添加摄像头设备选择、音频输入、录制功能。
- 添加 OBS WebSocket 联动。
- 开发自定义 DirectShow 虚拟摄像头，使设备名称显示为 `LocalScreenCam`。
