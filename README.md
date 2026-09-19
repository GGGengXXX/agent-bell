# Agent Bell

Agent Bell 会在命令或 Codex 执行完成时，在你的 Mac 上播放声音并弹出提醒。

## 安装

需要 macOS、Python 和 [uv](https://docs.astral.sh/uv/)。在项目目录执行：

```bash
uv tool install --editable .
```

安装后可以在任何目录使用 `abll`。

## 启动

首次使用：

```bash
abll init
```

启动后台服务：

```bash
abll start
```

查看状态或停止服务：

```bash
abll status
abll stop
```

## 查看消息队列

每一次命令或 Codex 完成后的响应都会保存在本地消息队列中。打开交互式收件箱：

```bash
abll queue
```

界面会自动刷新。使用方向键或 `j/k` 选择消息，回车查看详情，`r` 标记已读，`u` 标记未读，`a` 全部标记已读，`n` 跳到下一条未读消息，`q` 退出。脚本或不支持交互终端时，可以用 `abll queue --once` 输出当前队列；加上 `--unread` 只显示未读消息。

队列列表会显示来源、发送端 IP、事件类型、thread name 和内容；回车打开详情可以查看完整字段。

队列只保留最近 20 条消息，新增消息后会自动删除更早的记录。

Codex 通知会尝试根据 `thread-id` 显示 Codex 的 thread 名称；如果本机没有对应的 Codex 状态记录，则使用本轮首条输入作为名称。

## 给终端命令加提醒

在命令前加 `abll run --`：

```bash
abll run -- npm test
abll run -- python train.py
```

命令成功或失败后，Mac 会收到提醒；原命令的退出码会保留。

可以在命令前增加一个 note，方便在队列中区分相似任务：

```bash
abll run "训练模型 A" -- python train.py
```

## 接入 Codex

推荐直接运行一键配置命令：

```bash
abll codex setup
```

它会启动 Agent Bell，并在 `~/.codex/config.toml` 中配置 Codex 的原生 `notify`。
重复执行是幂等的；如果发现已有其他 `notify` 命令，会保留原配置并提示，避免覆盖已有通知集成。

只检查当前配置、不修改文件或启动服务：

```bash
abll codex setup --check
```

确认要替换已有 `notify` 时，可以使用 `--force`。原配置会备份为
`~/.codex/config.toml.agent-bell.bak`：

```bash
abll codex setup --force
```

完成后重启 Codex，使新的配置生效。

## 从远端 SSH 机器提醒本机

先在本机启动服务，再配置远端：

```bash
abll start
abll ssh configure --ssh-port 6269 root@114.111.28.42
```

`ssh configure` 会幂等地写入远端 token 和配置并启动反向隧道。网络或 SSH
暂时失败时会自动重试；配置进度保存在本机 state 目录，重新执行同一命令会从
上次完成的步骤继续。隧道断开后也会自动重连。

远端安装 Agent Bell 后运行：

```bash
abll run -- pytest -q
```

命令结束后，提醒会出现在本机。查看或断开隧道：

```bash
abll ssh status root@114.111.28.42
abll ssh disconnect root@114.111.28.42
```
