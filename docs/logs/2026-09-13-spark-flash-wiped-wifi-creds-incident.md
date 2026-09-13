# 2026-09-13 · spark 物理USB烧录误清空ESP32 WiFi凭据事故

## 事故经过

在验证 spark 物理USB接ESP32的build+flash+monitor链路时（见同日
`2026-09-13-spark-esp32-usb-toolchain-fix.md`），排查编译错误过程中执行过
`rm -rf build sdkconfig` 清缓存重建。`idf.py set-target esp32s3` 恢复了target，
但没意识到 `CONFIG_DEVICE_WIFI_SSID`/`CONFIG_DEVICE_WIFI_PASSWORD`（及 SSID2/
PASSWORD2）这几个字段也是 sdkconfig 里的普通配置项，删掉sdkconfig后重新生成的
是**空值**（spark上没有这几个字段的真实值来源，只有 wsl 的 sdkconfig 里有）。

随后为了验证"物理USB flash生效"执行了 `idf.py -p /dev/ttyACM0 flash`，把这份
**WiFi凭据为空**的固件刷到了真机 `esp32-s3-walle` 上。设备当场掉线：

```
W (1336) wifi:Password length is zero, but authmode threshold is 3, ...
I (1347) wangyutang_app: Wi-Fi trying SSID[0/0]=""
```

之后再监视，20秒窗口内没有任何 got-ip / NVS远程凭据回退 / MQTT连接的日志——设备
处于离线状态。

## 发现方式

排查内存泄漏问题前，例行检查 spark 的 sdkconfig 里 WiFi 凭据字段长度
（`awk -F= '{print length($2)}'`，不打印明文），发现 SSID 长度为 2（即空字符串
`""`），而 wsl 上同字段长度为 10。对比确认。

## 修复

1. 从 wsl 的 sdkconfig 里取出 4 个凭据字段（`SSID`/`PASSWORD`/`SSID2`/
   `PASSWORD2`）的完整 KEY=VALUE 行，整个过程不在本地终端输出明文（写入
   scratchpad临时文件、scp到spark、spark上用脚本替换后立即删除临时文件）。
2. 替换 spark sdkconfig 里对应的 4 行。
3. `idf.py build` + `idf.py -p /dev/ttyACM0 flash` 重新烧录。
4. `idf.py monitor` 确认：`Wi-Fi got IP=192.168.1.15` → `device_hub: registered OK`
   → `mqtt_control: connected broker=ws://110.40.154.41/mqtt` → 心跳正常。

## 教训

**这是 `docs/AEC_INTEGRATION_LOG.md` 里"编译仍需 WSL"那条教训的物理USB版本**：
原教训针对的是"在别处从干净仓库编译会产出空密码固件，OTA 上去设备永久失联"，
这次不是OTA、是物理USB flash，但根因和后果模式完全一样——**任何一台新装的编译
环境（不管是干净仓库还是删了sdkconfig重建），在真正往真机烧录前，必须先确认
WiFi凭据字段跟"金本位"来源（wsl的sdkconfig）一致，不能只凭"build成功"就判断
可以烧录**。

物理USB相比OTA的唯一优势是：即使刷坏了WiFi配置，设备物理在手边、串口还在，
可以立刻重新flash修复，不会像OTA刷坏那样"永久失联"。但这不代表可以不检查——
本次事故里设备中间确实经历了一段真实的离线期，如果是在无法物理接触设备的场景
（比如设备已经部署在树莓派机器人本体上），这个操作会造成真正的永久失联。

## 后续动作建议

spark 的 sdkconfig 里 WiFi 凭据这类"env-var 式"配置，理论上应该像 `.gitignore`
里已经排除明文凭据那样，有一个专门的"同步脚本"而不是靠人工每次记得对比长度。
暂未实现，留作后续改进项。
