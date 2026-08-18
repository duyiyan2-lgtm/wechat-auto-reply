# 微信 PC 离线留言自动回复

人离开电脑时，自动给新来的私聊回一句「现在不在，稍后回复」。

这不是微信官方插件。个人微信没有开放自动回复接口，本脚本通过 **官方 PC 客户端界面自动化**（`pyweixin` / `pywinauto`）模拟你本人点开会话、输入并发送。不注入微信进程，不走非官方协议。

## 环境

| 项 | 本机现状 |
| --- | --- |
| 系统 | Windows 10 / 11 |
| Python | 3.11+（已检测到 `D:\duyiyan\Python311\python.exe`） |
| 微信 | **4.x（Weixin.exe）**，本机为 `4.1.12.26` |
| 不要用 | 旧版 3.9 `WeChat.exe`（本脚本按 4.x 写） |

## 使用

微信 4.x 默认不把界面控件暴露给系统，脚本第一次会连不上。按这个顺序做一次即可：

```powershell
# 1）启动系统讲述人（会出声，按 CapsLock+M 可静音）
D:\duyiyan\Python311\python.exe reply.py --prepare

# 2）托盘右键微信 → 退出，再重新打开并登录（必须重启微信）

# 3）确认控件已露出
D:\duyiyan\Python311\python.exe reply.py --check
```

之后日常：

1. 打开并登录 **电脑版微信 4.x**，窗口保持可见。
2. 按需要改 `config.yaml` 里的留言正文。
3. 双击 `start.bat`，或在本目录执行：

```powershell
D:\duyiyan\Python311\python.exe reply.py
```

常用参数：

```powershell
# 只检查能否连上微信
D:\duyiyan\Python311\python.exe reply.py --check

# 演练：扫描未读，不真正发送
D:\duyiyan\Python311\python.exe reply.py --dry-run

# 只扫一轮
D:\duyiyan\Python311\python.exe reply.py --once
```

按 `Ctrl+C` 停止。

首次使用建议先 `--check`，再用 `--dry-run` 看会匹配到谁，确认无误后再正式跑。

## 配置说明（`config.yaml`）

- `message`：离线留言正文。
- `cooldown_minutes`：同一人多少分钟内只回一次（默认 60，避免对方连发你连回）。
- `poll_interval`：扫描间隔秒数。
- `duration`：`2h` / `30min` / `0`（一直开到手动停）。
- `reply_groups`：是否回群。离线留言建议保持 `false`。群设为免打扰后脚本也不会扫到。
- `allowlist`：非空则只回这些人（备注名必须和会话列表完全一致）。
- `blocklist`：永不回复。已默认排除文件传输助手、微信团队等。
- `active_hours`：只在某时段工作，例如 `09:00`–`22:00`。

回复记录在 `data/replied.json`，日志在 `logs/`。

## 注意

- **可能违反微信用户协议，有封号风险。** 只给自己账号做离线留言，不要群发、营销、骚扰。
- 脚本运行时会占用鼠标和键盘，不要同时操作电脑。
- 锁屏、熄屏、微信最小化到托盘时，界面自动化会失效。
- 检测群聊需要点开该会话，点开后会变成已读。不想动群：把群设为免打扰，或保持 `reply_groups: false`。
- 免打扰会话默认不会被扫描。
- 微信 4.x 必须先让系统讲述人跑过、再重启微信，控件树才会出现。`--prepare` 只做这一件事。
- 微信大版本更新后控件可能变，脚本可能失效，那时需要再适配。

## 依赖

```powershell
D:\duyiyan\Python311\python.exe -m pip install -r requirements.txt
```
