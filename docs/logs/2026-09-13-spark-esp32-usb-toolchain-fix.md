# 2026-09-13 · spark 物理USB接ESP32全链路打通

## 背景
用户要求：OTA 用于"发布已验证版本"，物理USB（WSL或spark）留给"开发调试看实时日志"场景。
本次任务是在 spark 上把物理USB烧录+串口监视功能实操跑通。

代码仓：`archer@spark-c9a7:~/workspace/git_space/esp32_wifi`（feature/aec-fullduplex 分支，git bundle 从 WSL 转运）
工具链：`archer@spark-c9a7:~/workspace/git_space/environment/esp32`（esp-idf + .espressif，均需 aarch64 原生安装，不能从 WSL x86_64 直接拷贝）

## 两个坑

### 1. IDF_TOOLS_PATH 未设置，export.sh 默认找 `~/.espressif`
`.espressif`（python venv、xtensa 工具链）按要求装在了 `~/workspace/git_space/environment/esp32/.espressif`，
不是默认位置 `~/.espressif`。直接 `source esp-idf/export.sh` 会报：
```
ERROR: ESP-IDF Python virtual environment ".../.espressif/python_env/idf5.5_py3.12_env/bin/python" not found.
```
**修复**：source 之前必须先 `export IDF_TOOLS_PATH=~/workspace/git_space/environment/esp32/.espressif`。

### 2. `rm -rf build sdkconfig` 把 target 也删没了，默认回落到 esp32（不是 esp32s3）
排查另一个编译错误时做过 `rm -rf build sdkconfig` 清缓存重建，但 sdkconfig 里的
`CONFIG_IDF_TARGET` 只有跑过 `idf.py set-target esp32s3` 才会写入——sdkconfig.defaults
本身不含 target。删掉 sdkconfig 后下次 build 默认目标回落成 `esp32`（原始款，不支持TDM）。

**现象**：编译报一堆看起来毫不相关的错误——
`box_audio_codec.c`: `.mclk`/`.ws`/`.dout`/`.invert_flags` "field name not in record"，
`afe_pipeline.c`: `AEC_NLP_LEVEL_AGGR` undeclared、`afe_config_t` 无 `aec_nlp_level` 成员。
两边源码、esp-sr 组件hash（`.component_hash`）、i2s_tdm.h 文件本身逐字节比对全部相同，
表象上完全无法解释。

**根因**：`driver/i2s_tdm.h` 整个文件体被 `#if SOC_I2S_SUPPORTS_TDM` 包裹，这个宏只有
esp32s3/s3等新款芯片的 `soc_caps.h` 里定义为1，esp32（原始款）没有这个宏。target 一旦
回落到 esp32，TDM相关类型/函数全部消失，`i2s_tdm_config_t tdm_cfg = {...}` 里的
`.gpio_cfg = {...}` 就被编译器当成对一个不存在类型的初始化，级联报出"字段不存在于
record"这种误导性很强的错误。afe_pipeline.c 同理是 esp-sr 组件按 target 选的头文件
（`esp32s3` vs 其他）里没有 `aec_nlp_level` 字段。

**排查教训**：遇到"两边源码和依赖hash完全一致，但编译报错不一致"时，第一时间该查
**当前生效的 IDF_TARGET / sdkconfig**（`grep CONFIG_IDF_TARGET sdkconfig`），而不是死磕
逐字节比对头文件内容——头文件内容相同不代表它是否被`#if`整体禁用，且 target 这种
"隐式状态"（删 sdkconfig 就会丢）比源码差异更容易被忽略。

**修复**：`idf.py set-target esp32s3` 后正常 `idf.py build` 通过。

## 验证结果（2026-09-13 11:xx）
- `idf.py build` 成功生成 `esp32_wangyutang.bin`（22% flash剩余空间）
- `idf.py -p /dev/ttyACM0 flash` 烧录成功（约12秒写入固件分区）
- `idf.py -p /dev/ttyACM0 monitor` 确认设备正常启动：8MB PSRAM识别正常、
  分区表加载正常、ESP-IDF v5.5.5 二级引导正常

## 结论
spark 上物理USB接ESP32的开发调试链路（build+flash+monitor）已完全打通并验证生效。
archer 已在 dialout 组，/dev/ttyACM0 权限正常，无需额外 sudo。
