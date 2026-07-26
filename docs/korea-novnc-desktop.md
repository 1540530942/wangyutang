# Korea noVNC 虚拟桌面（korea.wangyutang.com）部署手册

> 最后更新：2026-07-27
>
> 目标：在韩国出口机 `VM-0-5-ubuntu` 上直接运行一个虚拟桌面，通过公网域名
> `https://korea.wangyutang.com/` 以 HTTPS + 账号密码访问，浏览器天然从韩国 IP 出网。
>
> 与 [spark-exit-node-novnc-runbook.md](spark-exit-node-novnc-runbook.md) 的区别：
> 那套 noVNC 跑在 **Spark**（`192.168.1.98:6080`，仅局域网 / Tailscale）；本套直接跑在
> **VM-0-5 本机**，公网可达，**不依赖 Spark**。两套互相独立。

---

## 基本信息

| 项目 | 值 |
|------|----|
| 主机 | `VM-0-5-ubuntu`（本 workspace 主机，同时是 Tailscale 韩国 exit node） |
| 公网 IP | `43.155.169.6`（=`korea.wangyutang.com` 的 A 记录） |
| Tailscale IP | `100.116.142.44` |
| 系统 | Ubuntu 24.04 LTS |
| 规格 | 2 vCPU / 3.6 GB RAM / 1.9 GB swap / 59 GB 磁盘 |
| 访问入口 | `https://korea.wangyutang.com/vnc.html`（或 `vnc_lite.html`） |
| 鉴权 | Caddy HTTP Basic Auth：账号 `archer`，密码见服务器（**默认弱口令，应尽快更换**） |
| 出口 | 桌面浏览器天然从 `43.155.169.6`（KR）出网 |

---

## 架构

```text
浏览器（任意联网设备）
  -> https://korea.wangyutang.com/vnc.html   (公网 443)
  -> Caddy：自动 Let's Encrypt 证书 + HTTP Basic Auth
  -> 127.0.0.1:6080   websockify / noVNC（--web=/usr/share/novnc）
  -> 127.0.0.1:5900   x11vnc（-nopw -localhost）
  -> Xvfb :99（1440x900x24）+ openbox + tint2 + google-chrome-stable
  -> 从 43.155.169.6（韩国）出网
```

安全分层：`5900`、`6080` 均绑定 `127.0.0.1`，公网只能经 Caddy（TLS + Basic Auth）进入；
x11vnc 不设 VNC 密码（`-nopw`），仅靠 Caddy Basic Auth + localhost 绑定防护，做到单次登录。

---

## systemd 服务

四个 system 级 unit，均已 `enable` 开机自启，启动有依赖顺序：

| 服务 | 作用 |
|------|------|
| `korea-xvfb.service` | 启动虚拟显示 `Xvfb :99`（1440x900x24），不依赖物理显示器 |
| `korea-session.service` | `dbus-run-session -- openbox-session`，跑 openbox + tint2 + Chrome |
| `korea-x11vnc.service` | `x11vnc` 把 `:99` 桌面转成 VNC `:5900`（`-nopw -localhost`） |
| `korea-novnc.service` | `websockify --web=/usr/share/novnc 127.0.0.1:6080 localhost:5900` |

会话自启内容在 `/home/ubuntu/.config/openbox/autostart`：

```sh
tint2 &
(sleep 1; google-chrome-stable \
  --user-data-dir=/home/ubuntu/.config/chrome-korea \
  --disable-gpu --disable-dev-shm-usage \
  --no-first-run --no-default-browser-check --password-store=basic \
  --window-position=0,0 --window-size=1440,900 \
  https://www.google.com) &
```

常用管理：

```bash
# 状态
systemctl is-active korea-xvfb korea-session korea-x11vnc korea-novnc
# 重启整套（会重开桌面里的 Chrome）
sudo systemctl restart korea-xvfb korea-session korea-x11vnc korea-novnc
# 看会话/浏览器日志
journalctl -u korea-session -n 50 --no-pager
```

---

## Caddy（公网 HTTPS 入口）

`/etc/caddy/Caddyfile`：

```caddyfile
korea.wangyutang.com {
    basic_auth {
        archer <BCRYPT_HASH>
    }
    reverse_proxy 127.0.0.1:6080
}
```

- Caddy v2.11+ 指令为 `basic_auth`（旧版是 `basicauth`）。
- `reverse_proxy` 自动处理 WebSocket 升级（noVNC 的 `/websockify` 走 HTTP/1.1 Upgrade）。
- 证书由 Caddy 自动向 Let's Encrypt 申请续期，需要公网 80/443 入站可达。

### 修改访问密码

```bash
caddy hash-password --plaintext '新密码'          # 生成 bcrypt 哈希
sudoedit /etc/caddy/Caddyfile                     # 替换 archer 那行的哈希
sudo systemctl restart caddy
```

---

## 前置条件

1. **DNS**：`korea.wangyutang.com` A 记录指向 `43.155.169.6`。
2. **腾讯云安全组**：放行 **80、443** 入站（80 供 Let's Encrypt HTTP-01 签证书）。
   - 验证方法：本机临时 `sudo python3 -m http.server 80`，再从外部主机
     `curl -I http://43.155.169.6/`，返回 200 即放行（从本机连自己的公网 IP 会绕过安全组，测不准）。

---

## 从零部署顺序

```bash
# 1. 桌面 + noVNC 栈
sudo apt-get install -y xvfb x11vnc novnc websockify openbox tint2 \
  dbus-x11 x11-utils xterm fonts-liberation fonts-noto-cjk scrot

# 2. 浏览器：Google Chrome 官方 deb（非 snap）
curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
  | sudo gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" \
  | sudo tee /etc/apt/sources.list.d/google-chrome.list
sudo apt-get update && sudo apt-get install -y google-chrome-stable

# 3. Caddy 官方源
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update && sudo apt-get install -y caddy

# 4. 写 openbox autostart / 4 个 systemd unit / Caddyfile（见上文）
# 5. 启用并启动
sudo systemctl daemon-reload
sudo systemctl enable --now korea-xvfb korea-session korea-x11vnc korea-novnc
sudo systemctl restart caddy
```

---

## 排障

### noVNC 页面能打开，但连不上桌面 / 画面空白
```bash
systemctl is-active korea-xvfb korea-x11vnc korea-novnc
ss -ltn | grep -E ':5900|:6080'          # 应为 127.0.0.1 监听
journalctl -u korea-x11vnc -n 20 --no-pager
```

### 桌面能进，但网页打不开（浏览器崩溃）
本套曾用 epiphany（WebKit），在极简虚拟桌面里 `Web process crashed` 刷屏，已换成
Google Chrome。若 Chrome 也异常：
```bash
journalctl -u korea-session -n 30 --no-pager | grep -iE 'chrome|sandbox|crash'
```
- Chrome 官方 deb 自带 SUID 沙箱助手，Ubuntu 24.04 限制 userns 也能用，一般无需 `--no-sandbox`。
- 若沙箱确实起不来，autostart 里给 Chrome 加 `--no-sandbox` 兜底。

### curl 测 WebSocket 返回 404（假阴性）
`curl` 默认走 HTTP/2，`Upgrade: websocket` 头在 HTTP/2 里无效 → 被当普通 GET → 404。
真实浏览器的 WebSocket 走 HTTP/1.1，正常。要复现用 `curl --http1.1 ...`，期望 `101`。

### HTTPS 打不开 / 证书签发失败
确认安全组放行 80/443；`journalctl -u caddy -n 50 --no-pager` 看 ACME 报错。

---

## 已知限制

- 内存仅 3.6 GB，适合远程查资料 / 轻量网页操作，不宜多标签重载。
- Chrome profile 持久化在 `~/.config/chrome-korea`（保留登录态 / Cookie）。
- 默认 Basic Auth 口令为弱口令，务必更换；该桌面含 xterm，可执行命令并走韩国出口，泄露风险高。
