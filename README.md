# 专注记录器

Windows 本地前台活动记录器的开发基线。使用 Python 标准库、SQLite 和 Chrome MV3 扩展，没有 Python 第三方运行依赖。只实现活动记录模块，尚不是完整分神管理应用。

## 不采集活动的检查

需要 Windows 10/11、Python 3.13，以及用于 JavaScript 开发测试的 Node.js。Node 不是采集器运行依赖。在仓库根目录运行：

```text
python -B -m activity_log --help
python -B -m activity_log doctor
python -B -m activity_log status
python -B -m unittest discover -s tests -v
node tests/extension.test.mjs
node tests/playback.test.mjs
node tests/background.test.mjs
```

`doctor` 只读检查环境，不安装或读取活动。返回 1 表示环境缺失或尚不能确认；没有原生桥时是预期结果。`status` 只读现有数据库，缺库返回 3 且不创建文件。它输出带时间戳的保存事实，不证明采集进程当前健康。

自动测试使用合成观察、临时数据库及随机命名的 Windows 管道和互斥体，不读取实际前台或输入。用 Chrome 打开 `tests/browser/playback.html` 并点击 Run checks，可以检查合成视频的播放、暂停及不可访问帧；这不是已安装扩展的集成验收。

## 安装和运行

先阅读 [安装权限与撤销步骤](docs/install.md)。加载扩展、修改注册表和保存实际活动都应由使用者明确同意。安装工具默认只预览，`--apply` 才修改配置。采集器必须显式传入 `run --allow-observation`，不会自启动。

默认数据库位于 `%LOCALAPPDATA%\DistractionTracker\activity.sqlite3`，不能放在源码或同步目录。数据可能包含应用文件名、域名、YouTube 标题和视频 ID。设计上不保存截图、页面正文、按键内容或完整 URL；没有云同步功能。**本地数据库未加密，运行时无外传仍需独立整机验收。**

## 记录语义和限制

- 只记录实际前台。状态包括 activity、pending、idle、locked、suspended 和 unknown。
- 无输入且确认没有视频满 300 秒，回溯将这段候选时间改为空闲。前台视频播放时继续计时，播放状态未知的对应部分不能当作已确认空闲。
- 中断前未定稿的 pending 保留不确定性，重启不补写未观察的活动。
- 桌面播放器状态目前为 unknown；跨域嵌入、特殊播放器、画中画和多 Chrome 配置尚未完整验收。
- 锁屏/休眠、原生桥从 Chrome 启动、切换精度、真实长时资源开销仍待验证。已有虚拟 8 小时测试不等于真实 8 小时运行。
- 无 GUI、后台守护、自启动、用途归类、每日/每周统计或提醒。终端关闭会停止采集。
- 不保证磁盘损坏恢复，也不抵御同一登录会话内的恶意进程。

## 源码入口

`activity_log/` 实现 CLI、Windows 观察、时间线、SQLite 和本地管道。`chrome-extension/` 提供前台页面身份和媒体状态。`tools/` 包含受授权控制的安装、原生消息桥、探测与字段审计。`tests/` 是自动测试及本地浏览器夹具。

[验证范围](docs/verification.md) 区分可复跑检查和未完成的系统验收。本仓库不包含个人活动历史、私有任务记录或原始代理会话。
