# Spark 外网访问与 noVNC 虚拟桌面方案

> 最后更新：2026-07-23
>
> 目标：让 `spark-c9a7` 在局域网内可通过浏览器远程操作，并通过韩国出口访问 Google、ChatGPT 等外网站点。当前方案已可用，但浏览器层仍建议后续迁移到 Chromium。

## 设备与网络

| 项目 | 值 |
|------|----|
| Spark 主机 | `spark-c9a7` |
| Spark LAN IP | `192.168.1.98` |
| Spark Tailscale IP | `100.97.66.46` |
| Spark 用户 | `archer` |
| Spark 密码 | 本地运维已知 |
| 韩国出口机器 | `VM-0-5-ubuntu` |
| 韩国出口 Tailscale IP | `100.116.142.44` |
| 韩国出口公网 IP | `43.155.169.6` |
| noVNC URL | `http://192.168.1.98:6080/vnc.html` |
| VNC 密码 | 本地运维已知 |

## 当前采用的方案

当前分成两层：

1. 网络出口：Spark 使用 Tailscale exit node，经 `VM-0-5-ubuntu` 出口访问外网。
2. 远程桌面：Spark 提供 noVNC，后端接一个稳定的虚拟 X 桌面 `:99`。

访问链路：

```text
Windows 浏览器
  -> http://192.168.1.98:6080/vnc.html
  -> Spark websockify/noVNC :6080
  -> x11vnc :5900
  -> Xvfb :99 + openbox/tint2 + browser
  -> Tailscale exit node 100.116.142.44
  -> 韩国公网出口 43.155.169.6
```

选择虚拟桌面的原因：Spark 的物理显示器状态不稳定，曾出现所有 HDMI/USB-C 输出均为 `disconnected`，GDM 只提供黑屏或 `640x480`，导致 noVNC 看起来正常连接但画面不可用。虚拟桌面不依赖物理显示器，适合远程查资料和临时网页操作。

## 设计取舍

### 为什么用虚拟桌面，而不是 Spark 真实浏览器

真实浏览器依赖 Spark 当前物理 GNOME 会话、GDM 登录状态和显示器输出。实测过程中出现过：

```text
GNOME 出现“系统出错无法恢复”
GDM 重启后物理显示输出全部 disconnected
noVNC 只能看到 640x480 黑屏
x11vnc 绑定真实 :0/:1 会随登录、注销、显示器状态切换而断开
```

因此，真实桌面适合本地有人接显示器时操作，不适合长期无人值守远程入口。当前改用 `Xvfb :99` 虚拟桌面，是为了让远程入口具备这些性质：

- 不依赖物理显示器或 HDMI dummy plug。
- 不依赖 GDM 是否停在登录页。
- 不依赖 GNOME Shell 是否稳定。
- noVNC 后端固定连接 `:99`，不随 `:0`、`:1` 切换。
- 机器重启后可由 systemd 自动恢复。

### 为什么后续要改成非 Snap Chromium

当前虚拟桌面里临时使用 `epiphany-browser`。它能打开普通网页和 Google 首页，但 WebKit/Epiphany 对 Google、ChatGPT、Cloudflare 这类高风控站点的兼容性和指纹表现不如主流 Chromium。

后续建议使用非 Snap Chromium，原因：

1. 更接近真实桌面浏览器环境，对 Google、ChatGPT、Cloudflare 兼容性更好。
2. 避免 Snap cgroup 限制。Snap Firefox 在虚拟 systemd 桌面中曾报错：

```text
/system.slice/spark-virtual-desktop.service is not a snap cgroup for tag snap.firefox.firefox
```

3. 便于持久化 profile。Chromium 可固定 `--user-data-dir`，保存 Cookie、登录态、语言、缓存和站点权限。
4. 便于调试和自动化。Chromium 支持 DevTools Protocol，后续可以用 Playwright 或脚本做截图、页面状态检测和登录流程验证。
5. 启动参数可控，适合无 GPU 虚拟桌面：

```bash
chromium \
  --user-data-dir=/home/archer/.config/chromium-spark-vnc \
  --disable-gpu \
  --disable-dev-shm-usage \
  --no-first-run \
  --no-default-browser-check \
  https://www.google.com
```

注意：非 Snap Chromium 只能降低浏览器兼容性问题，不能保证绕过 Google/OpenAI 的 IP 风控。若仍触发 `/sorry/index` 或 `403`，下一步应从出口 IP 质量、持久登录 profile、Cookie、语言/时区一致性排查。

## 按顺序部署

本节记录从空白 Spark 远程桌面能力到可访问 `http://192.168.1.98:6080/vnc.html` 的顺序。实际执行前需确认 Spark 和韩国出口机器都已加入同一个 Tailscale tailnet。

### 1. 配置韩国出口机器为 Exit Node

在 `VM-0-5-ubuntu` 上启用转发并声明 exit node：

```bash
sudo mkdir -p /etc/sysctl.d
printf '%s\n' \
  'net.ipv4.ip_forward = 1' \
  'net.ipv6.conf.all.forwarding = 1' |
  sudo tee /etc/sysctl.d/99-tailscale.conf
sudo sysctl -p /etc/sysctl.d/99-tailscale.conf
sudo tailscale set --advertise-exit-node
```

然后在 Tailscale Admin Console 中批准该机器作为 exit node：

```text
Machines -> VM-0-5-ubuntu -> Edit route settings -> Use as exit node
```

### 2. 在 Spark 上启用 Exit Node

在 `spark-c9a7` 上选择韩国出口：

```bash
sudo tailscale set --exit-node=100.116.142.44 --exit-node-allow-lan-access
```

验证：

```bash
tailscale exit-node list
curl --noproxy "*" -sS https://ipinfo.io/json
```

期望：

```text
100.116.142.44 ... selected
"ip": "43.155.169.6"
"country": "KR"
```

### 3. 安装 noVNC 与虚拟桌面依赖

在 Spark 上安装：

```bash
sudo apt-get update
sudo apt-get install -y \
  novnc websockify x11vnc xvfb openbox tint2 xterm dbus-x11 scrot epiphany-browser
```

其中：

- `novnc` / `websockify` 提供浏览器访问入口 `:6080`。
- `x11vnc` 将 X 桌面暴露为 VNC `:5900`。
- `xvfb` 提供不依赖物理显示器的虚拟 X 桌面。
- `openbox` / `tint2` / `xterm` 提供最小可用桌面。
- `dbus-x11` 提供浏览器所需 DBus 会话工具。
- `epiphany-browser` 是当前临时浏览器；后续替换为非 Snap Chromium。

### 4. 设置 VNC 密码

```bash
mkdir -p ~/.vnc
x11vnc -storepasswd ~/.vnc/passwd
```

此密码是访问 noVNC 后连接 VNC 桌面时输入的密码，不应写入仓库。

### 5. 创建虚拟桌面启动脚本

`/usr/local/bin/spark-virtual-desktop-start`：

```sh
#!/bin/sh
set -eu
export DISPLAY=:99
export HOME=/home/archer
export USER=archer
export XDG_RUNTIME_DIR=/tmp/spark-runtime-archer
mkdir -p "$HOME/.cache" "$HOME/.config/openbox" "$XDG_RUNTIME_DIR/at-spi"
chmod 700 "$XDG_RUNTIME_DIR"
rm -f /tmp/.X99-lock
Xvfb :99 -screen 0 1280x800x24 -nolisten tcp >"$HOME/.cache/xvfb-99.log" 2>&1 &
sleep 2
exec dbus-run-session -- /bin/sh -lc '
  export DISPLAY=:99 HOME=/home/archer USER=archer XDG_RUNTIME_DIR=/tmp/spark-runtime-archer
  mkdir -p "$XDG_RUNTIME_DIR/at-spi" "$HOME/.cache"
  chmod 700 "$XDG_RUNTIME_DIR"
  openbox >/home/archer/.cache/openbox-99.log 2>&1 &
  tint2 >/home/archer/.cache/tint2-99.log 2>&1 &
  xterm -geometry 100x30+40+60 -title Spark-Virtual-Desktop -e "echo Spark virtual desktop is running.; echo Public IP:; curl --noproxy \"*\" -sS https://ipinfo.io/ip || true; echo; echo Browser: epiphany-browser; bash" >/home/archer/.cache/xterm-99.log 2>&1 &
  sleep 3
  epiphany-browser https://www.google.com >/home/archer/.cache/epiphany-99.log 2>&1 &
  wait
'
```

安装：

```bash
sudo install -m 0755 /tmp/spark-virtual-desktop-start /usr/local/bin/spark-virtual-desktop-start
```

### 6. 创建 systemd 服务

`/etc/systemd/system/spark-virtual-desktop.service`：

```ini
[Unit]
Description=Spark virtual desktop on Xvfb :99
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=archer
Environment=DISPLAY=:99
Environment=HOME=/home/archer
Environment=USER=archer
Environment=XDG_RUNTIME_DIR=/tmp/spark-runtime-archer
ExecStart=/usr/local/bin/spark-virtual-desktop-start
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/x11vnc-spark.service`：

```ini
[Unit]
Description=x11vnc for Spark virtual desktop
After=spark-virtual-desktop.service
Wants=spark-virtual-desktop.service

[Service]
Type=simple
ExecStartPre=/bin/sleep 4
ExecStart=/usr/bin/x11vnc -display :99 -rfbauth /home/archer/.vnc/passwd -rfbport 5900 -forever -shared -noshm -noxdamage -repeat -o /home/archer/.cache/x11vnc.log
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/novnc-spark.service`：

```ini
[Unit]
Description=noVNC web access for Spark desktop
After=network-online.target x11vnc-spark.service
Wants=network-online.target x11vnc-spark.service

[Service]
Type=simple
User=archer
ExecStart=/usr/bin/websockify --web=/usr/share/novnc 0.0.0.0:6080 localhost:5900
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

启用并启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable spark-virtual-desktop.service x11vnc-spark.service novnc-spark.service
sudo systemctl restart spark-virtual-desktop.service x11vnc-spark.service novnc-spark.service
```

### 7. 验证 noVNC URL

Spark 本机验证：

```bash
curl -I http://127.0.0.1:6080/vnc.html
ss -ltnp | grep -E ":(5900|6080)"
DISPLAY=:99 scrot /tmp/spark-virtual-desktop.png
```

Windows 浏览器访问：

```text
http://192.168.1.98:6080/vnc.html
```

浏览器打开后点击 Connect，输入本地运维保存的 VNC 密码。正常画面应包括：

```text
黑色虚拟桌面背景
底部 tint2 面板
左侧 xterm 终端
浏览器窗口
```

## Tailscale Exit Node 配置

韩国出口机器已执行：

```bash
sudo sysctl -w net.ipv4.ip_forward=1
sudo sysctl -w net.ipv6.conf.all.forwarding=1
sudo tailscale set --advertise-exit-node
```

并在 Tailscale 管理后台批准为 exit node。

Spark 已执行：

```bash
sudo tailscale set --exit-node=100.116.142.44 --exit-node-allow-lan-access
```

`--exit-node-allow-lan-access` 必须保留，否则 Spark 使用 exit node 后可能影响 Windows 从局域网访问 `192.168.1.98:6080`。

验证：

```bash
ssh archer@spark-c9a7 'tailscale exit-node list'
ssh archer@spark-c9a7 'curl --noproxy "*" -sS https://ipinfo.io/json'
```

期望看到：

```text
100.116.142.44 ... selected
"ip": "43.155.169.6"
"city": "Seoul"
"country": "KR"
```

## noVNC 与虚拟桌面服务

Spark 当前关键服务：

```text
spark-virtual-desktop.service
x11vnc-spark.service
novnc-spark.service
```

职责：

| 服务 | 作用 |
|------|------|
| `spark-virtual-desktop.service` | 启动 `Xvfb :99`、`openbox`、`tint2`、终端和浏览器 |
| `x11vnc-spark.service` | 将虚拟桌面 `:99` 暴露为 VNC `:5900` |
| `novnc-spark.service` | 将 VNC `:5900` 转为 WebSocket/Web 页面 `:6080` |

检查状态：

```bash
ssh archer@spark-c9a7 'systemctl --no-pager --full status spark-virtual-desktop.service x11vnc-spark.service novnc-spark.service'
ssh archer@spark-c9a7 'ss -ltnp | grep -E ":(5900|6080)"'
```

期望：

```text
spark-virtual-desktop.service active (running)
x11vnc-spark.service active (running)
novnc-spark.service active (running)
0.0.0.0:5900 LISTEN
0.0.0.0:6080 LISTEN
```

重启：

```bash
ssh archer@spark-c9a7 'sudo systemctl restart spark-virtual-desktop.service x11vnc-spark.service novnc-spark.service'
```

## 已安装的关键包

```text
tailscale
novnc
websockify
x11vnc
xvfb
openbox
tint2
xterm
dbus-x11
epiphany-browser
```

当前虚拟桌面使用 `epiphany-browser`。这是临时选择，因为 Ubuntu 24.04 的 `firefox` 与 `chromium-browser` 默认是 Snap 包，在 systemd 启动的虚拟桌面中容易遇到 cgroup/runtime 限制。

## 验证记录

网络层验证结果：

```text
Spark public IP: 43.155.169.6
city: Seoul
country: KR
```

命令行访问验证：

```text
https://www.google.com       HTTP 200，约 2-3 秒
https://accounts.google.com  HTTP 200，但可能 15-20 秒
https://chatgpt.com          HTTP 403，约 1-2 秒
https://openai.com           HTTP 403，约 2-3 秒
```

解释：

- Google 首页能打开，说明网络出口可用。
- Google 登录和搜索可能触发 `/sorry/index` 风控页。
- ChatGPT/OpenAI 返回 `403` 说明到站点网络可达，但云出口 IP、浏览器指纹或 WebKit 环境被风控拦截。

## 中途问题与处理

### 1. Firefox 代理有时能用、有时很慢

早期方案是 Firefox 手工配置 SOCKS：

```text
Firefox -> 127.0.0.1:1081 -> SSH reverse SOCKS -> 韩国出口
```

问题：

- 只影响 Firefox，不是全机出口。
- Google 页面多连接时总耗时可到 8-10 秒。
- ChatGPT 也可能 7 秒以上。

处理：

- 改为 Tailscale exit node，全机流量走韩国出口。
- Firefox 手工 SOCKS 配置已取消，避免双层代理。

### 2. Tailscale exit node 声明后 Spark 看不到

现象：

```text
no exit nodes found
node 100.116.142.44 is not advertising an exit node
```

原因：

- 出口机器已执行 `--advertise-exit-node`，但 Tailscale 管理后台尚未批准。

处理：

- 在 Tailscale Admin Console 的机器路由设置中勾选 `Use as exit node`。

### 3. noVNC 页面能打开，但桌面连接失败

现象：

```text
Failed to connect to localhost:5900: [Errno 111] Connection refused
```

原因：

- `websockify/noVNC :6080` 还在，但后端 `x11vnc :5900` 掉了。

处理：

- 将 `x11vnc` 和 noVNC 改为 systemd 服务自动拉起。

### 4. GNOME 出现“系统出错无法恢复”

现象：

```text
糟糕！出现了错误。
系统出错并无法恢复，请尝试注销并重新登录。
```

尝试过：

```bash
DISPLAY=:1 XAUTHORITY=/run/user/1000/gdm/Xauthority gnome-shell --replace
loginctl terminate-session 2
sudo systemctl restart gdm3
```

结果：

- `gnome-shell --replace` 未恢复。
- 重启 GDM 后进入登录界面，但物理显示器输出不稳定。

最终处理：

- 不再依赖物理 GNOME 桌面。
- 改用 `Xvfb :99 + openbox + tint2` 虚拟桌面。

### 5. GDM 登录界面黑屏

现象：

```bash
xrandr
# HDMI-0 disconnected
# USB-C-* disconnected
# current 640x480
```

原因：

- Spark 没检测到物理显示器，GDM 登录界面无法提供正常远程画面。

处理：

- noVNC 后端切到虚拟桌面 `:99`。

### 6. Epiphany/WebKit 页面进程崩溃

现象：

```text
Failed to mkdir for dbus proxy (/run/user/1000/webkitgtk): 权限不够
网页进程已崩溃
```

原因：

- systemd 服务中使用真实 `/run/user/1000` 时权限和会话环境不完整。

处理：

- 虚拟桌面使用独立 runtime 目录：

```text
XDG_RUNTIME_DIR=/tmp/spark-runtime-archer
```

并通过 `dbus-run-session` 启动桌面和浏览器。

### 7. Snap Firefox 无法在虚拟桌面中启动

现象：

```text
/system.slice/spark-virtual-desktop.service is not a snap cgroup for tag snap.firefox.firefox
```

原因：

- Snap 应用对 cgroup/session 有要求，不适合当前 systemd 虚拟桌面启动方式。

处理：

- 临时使用非 Snap 的 `epiphany-browser`。
- 后续计划迁移到非 Snap Chromium。

## 常用排障命令

检查出口：

```bash
ssh archer@spark-c9a7 'curl --noproxy "*" -sS https://ipinfo.io/json'
```

检查外网站点：

```bash
ssh archer@spark-c9a7 'for url in https://www.google.com https://accounts.google.com https://chatgpt.com; do echo $url; curl --noproxy "*" -sS -L --connect-timeout 10 --max-time 30 -o /dev/null -w "code=%{http_code} total=%{time_total} final=%{url_effective}\n" "$url"; done'
```

检查 noVNC：

```bash
ssh archer@spark-c9a7 'curl -I http://127.0.0.1:6080/vnc.html'
```

截图虚拟桌面：

```bash
ssh archer@spark-c9a7 'DISPLAY=:99 scrot /tmp/spark-virtual-desktop.png'
scp archer@spark-c9a7:/tmp/spark-virtual-desktop.png /tmp/
```

查看日志：

```bash
ssh archer@spark-c9a7 'journalctl -u spark-virtual-desktop.service -n 100 --no-pager'
ssh archer@spark-c9a7 'journalctl -u x11vnc-spark.service -n 100 --no-pager'
ssh archer@spark-c9a7 'journalctl -u novnc-spark.service -n 100 --no-pager'
ssh archer@spark-c9a7 'tail -100 ~/.cache/epiphany-99.log'
```

## 当前局限

1. Epiphany/WebKit 能打开普通网页和 Google 首页，但不适合作为长期主浏览器。
2. Google 搜索、Google 登录、ChatGPT 可能触发风控，不等同于网络不可达。
3. 云服务器出口 IP `43.155.169.6` 可能天然更容易被 Google/OpenAI 风控。
4. 虚拟桌面不适合看视频或高帧率图形操作；它适合运维、查资料、低频网页操作。

## 后续：使用 Chromium 进一步实现

建议后续目标是提供一个非 Snap Chromium 浏览器，运行在 `Xvfb :99` 虚拟桌面中。

方向：

1. 优先寻找适合 Ubuntu 24.04 arm64 的非 Snap Chromium 包源，避免 Snap cgroup 限制。
2. 若系统包不可用，评估 Playwright Chromium 或第三方 deb 源，但必须确认 arm64 支持和安全来源。
3. Chromium 启动参数建议：

```bash
chromium \
  --user-data-dir=/home/archer/.config/chromium-spark-vnc \
  --disable-gpu \
  --disable-dev-shm-usage \
  --no-first-run \
  --no-default-browser-check \
  https://www.google.com
```

4. 若仍触发 Google/OpenAI 风控，需要从出口 IP 质量、浏览器持久 profile、Cookie、字体/语言/时区一致性继续优化，而不是继续调整 Tailscale。

验收标准：

- noVNC 打开后可见 Chromium 窗口。
- `ipinfo.io/json` 显示韩国出口。
- `google.com` 可正常搜索。
- `accounts.google.com` 可进入登录页。
- `chatgpt.com` 至少能进入 Cloudflare/登录流程，若 403 需记录响应与截图。
