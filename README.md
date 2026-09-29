# NonoDeskPet

LLM 驱动的桌面宠物机器人：对它说话，它会回答、变表情、出声、满桌子跑。

上位机（PC / 本地大模型）当大脑，ESP32-S3 小车当本体，中间用一套自定义WebSocket JSON 协议解耦——同一套上位机程序，既能驱动Gazebo仿真小车，也能驱动真实硬件，切换只需改一个地址。

> 仿真全链路 + ESP32通信协议已实测通过
> 配套安卓 App（Qt/QML）：[NonoDeskPet-Qt](https://github.com/badlichees/NonoDeskPet-Qt)

## 结构

| 目录 | 说明 |
|---|---|
| `deskpet_interfaces/` | ROS2接口定义（Primitive.action动作原语） |
| `deskpet_control/` | 动作执行节点（primitive_server，开环，发/cmd_vel） |
| `deskpet_relay/` | WebSocket ↔ ROS Action中继节点 |
| `deskpet_brain/` | 桌宠大脑（LLM调用、表情、语音），跑在宿主机 |
| `firmware/` | ESP32-S3下位机固件（PlatformIO） |

## 通信协议

上位机与执行端之间是同一套 WebSocket JSON 协议（端口 8765）：

```
C→S  {"type": "goal", "command": "move_forward", "value": 0.2}
C→S  {"type": "cancel"}
S→C  {"type": "accepted"} / {"type": "rejected", "message": "..."}
S→C  {"type": "feedback", "progress": 0.42}
S→C  {"type": "result", "success": true, "message": "..."}
S→C  {"type": "error", "message": "..."}
```

command 4选1——`move_forward` / `move_backward` / `turn_left` / `turn_right`；移动单位米（0.05–1.0），转向单位弧度（0.26–3.14）。

LLM输出由llama-server的`json_schema`文法约束为三字段结构：`reply`（说的话）、`expression`（六种表情之一）、`action`（动作或 null）。

## 软件环境与仿真测试

- 宿主机：Linux + Docker；宿主机侧跑大脑/表情/语音
- 容器：`osrf/ros:jazzy-desktop-full`（Ubuntu 24.04 + ROS 2 Jazzy + Gazebo Harmonic），host 网络模式
- LLM：llama.cpp + Qwen2.5-7B-Instruct（Q4_K_M），Vulkan加速

容器内（三条，各一个终端）：

```bash
ros2 launch deskpet_bringup diff_drive.launch.py rviz:=false
ros2 run deskpet_control primitive_server --ros-args -p use_sim_time:=true
ros2 run deskpet_relay relay_server
```

宿主机（三个，各一个终端）：

```bash
llama-server --model qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf \
    --device Vulkan1 --n-gpu-layers 99 --port 8080

cd deskpet_brain && .venv/bin/python face.py     # 表情窗
cd deskpet_brain && .venv/bin/python brain.py    # 服务模式，Qt App连8767
```

之后在Qt App或 `brain.py --cli` 终端里聊天；说"往前走"，Gazebo里的小车会动。

## ESP32 固件

```bash
cd firmware
cp src/secrets.h.example src/secrets.h   # 填入WiFi信息
pio run -t upload                        # PlatformIO烧录
```

固件连上WiFi后在8765端口起WebSocket服务，协议与relay_server完全一致；当前执行器为模拟执行（汇报进度但不驱动电机），电机驱动待硬件接线后接入。

## 自用硬件概要（下位机）

- 主控：ESP32-S3 N16R8（16MB Flash + 8MB PSRAM）
- 运动：N20减速电机（100RPM、带AB相编码器）×2 + 43mm 轮 + TB6612，极速约0.22 m/s
- 感知：MPU6050（转向闭环）、VL53L0X（避障）、TCRT5000×3（防跌落）
- 交互：1.28" GC9A01圆屏（表情）、MAX98357A + 喇叭（语音）、WS2812、INMP441（预留）
- 电源：18650×2串联（7.4V母线）→ 急停开关；Mini360降压5V供逻辑；板载LDO出3.3V供传感器

### 引脚分配

| GPIO | 用途 | GPIO | 用途 |
|---|---|---|---|
| 1/2 | 功放BCLK / LRC | 17/46/3 | 防跌落前左/前右/尾部 |
| 4/5/6 | TB6612 PWMA/AIN1/AIN2 | 18/21 | 左编码器A/B |
| 7/15/16 | TB6612 PWMB/BIN1/BIN2 | 38/39 | 右编码器A/B |
| 8/9 | I2C SDA/SCL（MPU6050 + VL53L0X共线） | 40/41/42 | 麦克风 SCK/WS/SD（预留） |
| 12/13/11/10/14 | 屏幕SCK/MOSI/DC/CS/RST | 45 | WS2812数据 |
| 43/44 | 调试串口（保留） | 47 | 功放DIN |

禁区：GPIO0、19/20（USB）、26–32（Flash）、33–37（Octal PSRAM）。

## License

Apache-2.0
