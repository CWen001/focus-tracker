# 安装与撤销

这份说明不是已安装报告。先确认本机使用者同意以下权限；示例中的 `--apply` 或 `--allow-observation` 不是自动执行许可。

## 一次需要确认的范围

- Windows 10/11，现有 Python 3.13；一个普通 Chrome 配置文件、一个登录会话。先用独立测试配置，不加载日常标签。
- 手动加载本项目 `chrome-extension/`。MV3 权限为 `tabs`、`nativeMessaging`，内容脚本匹配全部 HTTP/HTTPS 页面；虽然只读取媒体状态，这仍是广泛页面访问权限，必须明确同意。无无痕、本地文件、历史、cookie、调试器或同步权限。
- 记录加载后显示的真实扩展 ID。仅授权该 `chrome-extension://<ID>/`，不使用通配来源。
- 注册表仅此键：`HKEY_CURRENT_USER\Software\Google\Chrome\NativeMessagingHosts\com.local.distraction_tracker`。默认字符串值指向安装目录中的 `manifest.json`。不需要管理员权限。
- 建议桥接目录为 `%LOCALAPPDATA%\DistractionTrackerHost`，包含 `manifest.json`、`host.cmd`、`receipt.json`；命令明确选择目录，没有默认隐式安装。启动器引用现有 Python 与本项目 `tools/native_host.py`，移动/删除项目会使桥接失效。
- 受控采集数据库建议为 `%LOCALAPPDATA%\DistractionTracker\activity.sqlite3`；与源码、OneDrive 和其他同步位置分开。先确认该目录没有被用户另行同步；路径检测不是所有同步软件的证明。
- 数据是应用文件名、域名、YouTube 标题/视频 ID、时间和覆盖状态。不保存截图、正文、按键内容或完整 URL。不开放网络监听。

## 授权后才执行

以下为 PowerShell 示例；将 `EXTENSION_ID` 替换为 Chrome 显示的 32 字符真实 ID。它是占位符，不能直接执行成功。先审核输出，再批准 `--apply`。

```powershell
python -B -m tools.install_host install --host-dir "$env:LOCALAPPDATA\DistractionTrackerHost" --extension-id EXTENSION_ID
# 上行只预览；核对准确路径、allowed_origins、注册表键后：
python -B -m tools.install_host install --host-dir "$env:LOCALAPPDATA\DistractionTrackerHost" --extension-id EXTENSION_ID --apply
python -B -m activity_log doctor
```

`doctor` 发现配置后仍会报告 `HOST_UNVERIFIED`；它不会假装加载与连线已经通过。现有不同注册项或被修改的安装文件会被拒绝覆盖。相同安装可以重复执行；失败时先检查现场，不盲目覆盖或反复重试。

**真实采集单独确认测试时段与页面后**，启动独立采集器，再打开/重载测试扩展以连接：

```powershell
python -B -m activity_log run --data-dir "$env:LOCALAPPDATA\DistractionTracker" --allow-observation
# 在第二个终端只读查看：
python -B -m activity_log status --data-dir "$env:LOCALAPPDATA\DistractionTracker"
python -B -m tools.check_saved_fields --data-dir "$env:LOCALAPPDATA\DistractionTracker"
```

Ctrl+C 停止采集，成功时只保存最后一次实际观察，不将退出尾段补成活动。关闭终端可能丢失最近未提交的尾段；`status` 的时间戳代表保存事实，不证明进程仍健康。

## 必须一起验证的项目

1. 扩展 → Native Messaging → 本机管道 → SQLite → status 真实闭环；Chrome 退出后桌面采集仍运行，重连不沿用旧页。
2. 至少 30 次受控应用/标签切换，记录实际误差；多窗口和不同 Chrome 配置不得错配。`.cmd` 从 Chrome 启动、无控制台闪窗仍未验证。
3. 用户配合锁屏/休眠及恢复；不要由安装工具主动改变系统状态。验证启动时会话状态查询，失败应记未知而非假定解锁。
4. 普通视频、暂停、缓冲、后台视频、跨域嵌入、画中画和桌面播放器。当前跨域帧、特殊渲染器和桌面播放器不能证明播放状态，必须保留未知；不能据此宣称所有播放器的计时规则均已验证。
5. 独立网络观测，区分页面流量与扩展/采集器流量；目前只有代码/合成检查，不是无外传实机验收。
6. 最后才进行获批的 8 小时真实运行。用 Windows 性能监视器等现有工具记录采集器、桥和相关 Chrome 进程的 CPU、私有内存及库增长。虚拟 8 小时时间线测试不替代这项，亦不为测量安装新依赖。

## 撤销

先 Ctrl+C 停止采集，并在测试配置中禁用/移除扩展。然后预览并审核卸载：

```powershell
python -B -m tools.install_host uninstall --host-dir "$env:LOCALAPPDATA\DistractionTrackerHost"
python -B -m tools.install_host uninstall --host-dir "$env:LOCALAPPDATA\DistractionTrackerHost" --apply
```

卸载核对 receipt 和文件哈希，只删除上述注册项及三个原样安装文件；保留其他文件与活动数据库。被用户修改的文件或指向别处的注册项会阻止自动卸载，先人工核对。数据库不自动删除、备份或迁移；若要删除受控测试历史，另行确认准确路径。无自启动、服务或计划任务需要撤销。
