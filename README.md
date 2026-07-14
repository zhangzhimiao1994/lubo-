# Lubo 直播录制

Lubo 是一个面向抖音直播的跨平台录制应用。它可以循环检查多个直播间，在主播开播后自动开始录制，并在下播或用户停止值守时安全结束录制。

项目目前处于 Alpha 阶段，优先完成抖音录制链路和三端发布：

- Windows 桌面应用
- Linux 桌面应用
- Android 手机本机录制应用

## 当前能力

- 多直播间循环值守
- 限流并发检查，避免大量直播间同时请求
- 开播自动录制、下播自动停止
- 原画、蓝光、超清、高清、标清、流畅画质选择
- TS、MKV、FLV、MP4、MP3、M4A 输出配置
- 分段录制和录制完成后转 MP4
- 可选代理和抖音 Cookie
- Windows/Linux 图形界面
- Android 前台服务持续值守
- Android 常驻通知一键停止录制
- 发布包自动清除 Cookie、账号信息、保存路径和默认直播间

## 使用方式

### Windows 与 Linux

1. 启动 `DouyinLiveRecorder`。
2. 输入抖音直播间地址，可选填写备注。
3. 添加一个或多个直播间。
4. 点击“开始值守”。
5. 软件检测到开播后自动录制，默认文件保存在应用数据目录下的 `downloads`。

Windows 发布目录中的 EXE 必须和 `_internal` 目录放在一起。FFmpeg 已包含在 Windows/Linux 打包产物中，无需用户单独配置。

### Android 手机录制

1. 安装 APK 后允许通知权限。
2. 输入抖音直播间地址并添加目标。
3. 点击 `Start monitoring` 启动前台值守服务。
4. 返回桌面或锁屏后，服务继续检查直播状态并录制。
5. 在应用内点击 `Stop`，或使用常驻通知中的停止操作结束值守。

Android 录制文件保存在应用私有目录的 `recordings` 中。当前手机端直接保存抖音 FLV 流，不在手机上转码；仅提供 HLS 的直播流会被拒绝，避免把播放列表误保存为视频。

## 直播间配置

图形界面会维护本地 `URL_config.ini`。也可以手动编辑，每行一个直播间：

```ini
原画,https://live.douyin.com/123456,主播备注
高清,https://live.douyin.com/654321
#流畅,https://live.douyin.com/111111,暂停值守
```

- 第一列是画质，可省略。
- 第二列是直播间地址。
- 第三列是可选备注。
- 行首添加 `#` 会保留配置但停止值守。
- 重复地址只保留一条。

录制格式、循环间隔、并发数、代理、分段时间和抖音 Cookie 位于本地 `config.ini`。Cookie 只应配置在自己的设备上，不要提交到 Git。

## 从源码运行

要求 Python 3.10 至 3.13。桌面端还需要系统可用的 FFmpeg。

```bash
python -m venv .venv
```

Windows：

```powershell
.venv\Scripts\python -m pip install -r requirements-gui.txt
.venv\Scripts\python -m douyinliverecorder.apps.desktop.main
```

Linux：

```bash
.venv/bin/python -m pip install -r requirements-gui.txt
.venv/bin/python -m douyinliverecorder.apps.desktop.main
```

## 构建发布包

### Windows

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

输出目录：`dist/DouyinLiveRecorder/`

### Linux

先安装 FFmpeg 和 Kivy 所需系统库，然后执行：

```bash
bash scripts/build_linux.sh
```

输出目录：`dist/DouyinLiveRecorder/`

### Android

Android 必须在 Linux 环境使用 Buildozer/python-for-android 构建：

```bash
bash scripts/build_android.sh
```

输出文件：`dist/android/DouyinLiveRecorder-android-debug.apk`

推送 `v*` 标签会触发 GitHub Actions：

- `Build Desktop Apps` 生成 Windows 和 Linux artifact
- `Build Android APK` 生成 Android debug APK artifact

更完整的系统依赖和平台说明见 [docs/cross-platform-apps.md](docs/cross-platform-apps.md)。

## 架构

```text
douyinliverecorder/
  core/                 配置、模型、事件、URL 存储和调度器
  platforms/            平台适配器与注册表
  recorders/            FFmpeg 和手机直连流录制器
  apps/desktop/         Windows/Linux 图形应用
  apps/android/         Android 界面、前台服务和状态管理
android/                Buildozer、Manifest 和 Java 服务扩展
scripts/                三端构建与发布配置清理脚本
tests/                  核心、平台、录制器、应用和打包测试
```

调度器与界面解耦。平台适配器只负责解析直播状态和流地址；录制器负责进程或网络流生命周期；Windows、Linux 和 Android 复用同一套配置、目标模型和调度语义。

## 测试

```bash
python -m pytest -q
```

测试覆盖多房间调度、并发限制、优雅/强制停止、抖音适配、桌面控制器、Android 前台服务、打包配置清理和构建脚本契约。

## 隐私与发布安全

- Release 构建不会携带本地直播间列表或 Cookie。
- 源代码测试会阻止固定 Cookie 被重新提交。
- Android 录制默认保存在应用私有目录。
- 需要登录态时，请只在本机配置自己的抖音 Cookie。

请遵守直播平台规则、著作权要求和所在地法律，只录制你有权保存的内容。

## 开发状态

当前新架构只承诺抖音链路。其他平台需要通过 `PlatformAdapter` 接口逐个迁移和验证后再声明支持，不在 README 中沿用旧项目的平台列表。

许可证见 [LICENSE](LICENSE)。
