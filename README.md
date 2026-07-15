# Lubo 多平台直播录制

Lubo 是一个独立的多平台直播录制应用，共享同一套房间配置、平台解析、调度和录制核心，并提供 Windows、Linux 桌面界面与 Android 应用。它适合值守自己有权保存的直播内容，不承诺任一真实平台或房间始终可录。

平台页面、接口和风控策略会变化；部分直播间需要有效 Cookie，部分协议可能无法由当前录制端处理。遇到失败时请先确认平台状态、网络、Cookie 和实际返回的直播协议。

## 支持平台

- 抖音 / Douyin
- Bilibili Live / B站直播
- Huya / 虎牙
- Douyu / 斗鱼

各平台允许的域名、解析 backend、Cookie key 和 Android 协议限制见 [docs/platforms.md](docs/platforms.md)。支持列表表示项目提供对应适配器，不代表平台变更、地区限制、账号风控或特定房间协议下永远可用。

## Windows / Linux 桌面使用

1. 从 GitHub Releases 下载对应系统的压缩包并完整解压。
2. 启动发布目录中的 Lubo 可执行文件，保留可执行文件与其内部资源目录的相对位置。
3. 添加直播间 URL，可选设置画质与备注。
4. 启动值守。检测到直播后，Lubo 会按本地配置开始录制；下播或手动停止后结束任务。

桌面端使用 FFmpeg 处理可用的直播流。平台解析可能受网络、Cookie、直播间权限和上游解析引擎版本影响。

## Android 使用

1. 安装 APK，并按系统提示授予通知等运行所需权限。
2. 添加直播间 URL，启动前台值守服务。
3. 在应用内或常驻通知中停止值守。

Android 端仅直接写入可直接读取的 FLV/HTTP 流，本身不处理 HLS，也不在手机上转码。HLS-only 流会抛出明确错误且不创建文件，避免把 HLS 播放列表保存成视频。不同平台或房间可能只提供 Android 当前无法录制的协议，因此桌面可录不等于 Android 一定可录。

## URL_config.ini

`config/URL_config.ini` 每行保存一个目标，格式为 `画质,直播间 URL,可选备注`。行首 `#` 表示保留但暂停该目标。

```ini
原画,https://live.douyin.com/123456,抖音示例
高清,https://live.bilibili.com/12345,B站直播示例
原画,https://www.huya.com/123456,虎牙示例
原画,https://www.douyu.com/123456,斗鱼示例
#流畅,https://live.douyin.com/654321,暂不值守
```

真实 URL 应来自对应平台。不要把私人房间地址、访问参数或其他敏感目标提交到版本控制。

## 本地 config.ini

`config/config.ini` 包含四个 section：

- `[recorder]`：保存目录、输出格式、画质、分段与转换选项。
- `[monitor]`：检查间隔与最大并发数。
- `[proxy]`：代理开关和地址。
- `[cookies]`：四个平台各自的 Cookie。

Cookie key 精确为 `douyin`、`bilibili`、`huya`、`douyu`。Cookie、代理凭据、私人保存路径和直播目标只应留在本机，不要提交、粘贴到 issue 或发送给他人。Release 产物不携带开发者本地凭据，也不携带本地 URL 目标。

## 源码安装

需要 Python 3.10–3.13。桌面录制还要求系统能够使用 FFmpeg。

```bash
python -m venv .venv
python -m pip install -r requirements-gui.txt
python -m lubo.apps.desktop.main
```

在虚拟环境中运行时，可将上面的 `python` 替换为 Windows 的 `.venv\Scripts\python` 或 Linux 的 `.venv/bin/python`。

## 构建

Windows：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

Linux：

```bash
bash scripts/build_linux.sh
```

Android（Linux + Buildozer 环境）：

```bash
bash scripts/build_android.sh
```

Windows 和 Linux 桌面构建输出到 `dist/Lubo`，Android debug APK 输出到 `dist/android/Lubo-android-debug.apk`。

## GitHub Releases

发布版本从 [Lubo Releases](https://github.com/zhangzhimiao1994/lubo-/releases) 下载。选择与系统匹配的 Windows、Linux 或 Android 产物；桌面压缩包需要完整解压后运行，Android 产物当前为 debug APK。

当前项目仓库为 [zhangzhimiao1994/lubo-](https://github.com/zhangzhimiao1994/lubo-)。Release 中不包含维护者本机的 Cookie、代理凭据、保存路径或 `URL_config.ini` 目标。

## 架构

```text
lubo/
  core/           配置、目标模型、事件和调度
  platforms/      四个平台适配器与注册表
  resolvers/      平台 Web API 与 Streamlink 解析 backend
  recorders/      桌面 FFmpeg 与 Android 直接 HTTP 录制
  apps/desktop/   Windows/Linux Kivy 应用
  apps/android/   Android Kivy 应用与前台服务协调
android/          Buildozer 配置、清单与平台入口
scripts/          Windows、Linux、Android 构建脚本
tests/            核心、平台、录制器、应用和打包契约测试
```

平台适配器负责识别房间与调用解析 backend；调度器管理检查和录制生命周期；录制器只负责选定流的落盘。桌面与 Android 共享配置语义，但使用不同录制能力。

## 测试

```bash
python -m pytest tests/packaging/test_build_scripts.py -q
python -m pytest tests/packaging -q
python -m pytest -q
```

测试使用固定样例和替身验证行为，不证明真实平台当前在线或接口永久兼容。平台变更后应结合可公开访问且有权测试的房间重新验证。

## 法律与合规

Lubo 采用 MIT License，详见 [LICENSE](LICENSE)。历史许可声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

使用者必须遵守直播平台条款、著作权规则、隐私要求和所在地法律，只录制已获得授权或依法有权保存的内容。Cookie 代表账号权限，应按敏感凭据保护；请勿使用本项目绕过访问控制、付费限制或平台风控。
