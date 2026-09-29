// SPDX-License-Identifier: Apache-2.0
/*
 * NonoDeskPet下位机固件（ESP32-S3 N16R8）
 *
 * 职责：连上局域网WiFi，开一个WebSocket服务，接收上位机发来的动作指令并执行。
 *
 * 协议与 relay_server 完全一致（上位机零改动即可在仿真/真机之间切换）：
 *     C→S {"type": "goal", "command": "move_forward", "value": 0.2}   下一个动作指令
 *     C→S {"type": "cancel"}                                          取消当前动作
 *     S→C {"type": "accepted"} / {"type": "rejected", "message": ...} 受理 / 拒绝（附原因）
 *     S→C {"type": "feedback", "progress": 0.42}                      执行进度（0~1）
 *     S→C {"type": "result", "success": true, "message": "..."}       执行结束，汇报结果
 *     S→C {"type": "error", "message": "..."}                         消息本身有问题
 */

#include <Arduino.h>
#include <ArduinoJson.h>
#include <WebSocketsServer.h>
#include <WiFi.h>

#include "secrets.h"  // WiFi账号密码，见secrets.h.example

constexpr uint16_t WS_PORT = 8765;  // 和relay_server同一个端口号

WebSocketsServer wsServer(WS_PORT);

// 动作执行器状态（是假执行）
bool executing = false;            // 是否正在执行动作
unsigned long execStartMs = 0;     // 动作开始的时刻
unsigned long execDurationMs = 0;  // 动作预计耗时
int lastSentPercent = -1;          // 上次汇报的进度（百分比），避免重复发

// 把JSON消息发给所有连着的客户端
void broadcast(JsonDocument & doc)
{
  char buf[256];
  size_t len = serializeJson(doc, buf, sizeof(buf));
  wsServer.broadcastTXT(reinterpret_cast<uint8_t *>(buf), len);
}

void sendSimple(const char * type)
{
  JsonDocument doc;
  doc["type"] = type;
  broadcast(doc);
}

void sendWithMessage(const char * type, const char * message)
{
  JsonDocument doc;
  doc["type"] = type;
  doc["message"] = message;
  broadcast(doc);
}

// result消息比其他的多一个success字段，单独一个函数
void sendResult(bool success, const char * message)
{
  JsonDocument doc;
  doc["type"] = "result";
  doc["success"] = success;
  doc["message"] = message;
  broadcast(doc);
}

// 受理并启动假执行计时
void startGoal(const char * command, float value)
{
  executing = true;
  execStartMs = millis();
  // 把指令数值折算成耗时，0.2秒起步，最多5秒，纯模拟
  execDurationMs = constrain(static_cast<unsigned long>(value * 2000), 500, 5000);
  lastSentPercent = -1;
  sendSimple("accepted");
  Serial.printf("[executor] 开始(假)执行: %s %.2f，耗时 %lu ms\n", command, value, execDurationMs);
}

// 每个loop周期调用，推进假执行、发进度、到点报结果
void updateExecutor()
{
  if (!executing) return;

  float progress = static_cast<float>(millis() - execStartMs) / execDurationMs;
  if (progress >= 1.0f) {
    executing = false;
    sendResult(true, "执行完成（模拟）");
    Serial.println("[executor] 完成");
    return;
  }

  // 进度每涨10%汇报一次
  int percent = static_cast<int>(progress * 100);
  if (percent / 10 != lastSentPercent / 10) {
    lastSentPercent = percent;
    JsonDocument doc;
    doc["type"] = "feedback";
    doc["progress"] = progress;
    broadcast(doc);
  }
}

// 处理一条客户端消息
void handleMessage(const char * raw)
{
  JsonDocument doc;
  if (deserializeJson(doc, raw)) {
    sendWithMessage("error", "非法JSON");
    return;
  }

  const char * type = doc["type"];
  if (type == nullptr) {
    sendWithMessage("error", "缺少type字段");
    return;
  }

  if (strcmp(type, "goal") == 0) {
    if (executing) {
      sendWithMessage("rejected", "正忙，有goal在执行");
      return;
    }
    const char * command = doc["command"];
    if (command == nullptr || !doc["value"].is<float>()) {
      sendWithMessage("error", "goal需要 str command + 数值 value");
      return;
    }
    startGoal(command, doc["value"]);
  } else if (strcmp(type, "cancel") == 0) {
    if (!executing) {
      sendWithMessage("error", "当前没有执行中的goal");
      return;
    }
    executing = false;
    sendResult(false, "已取消");
    Serial.println("[executor] 被取消");
  } else {
    sendWithMessage("error", "未知消息类型");
  }
}

// WebSocket事件回调
void onWsEvent(uint8_t clientNum, WStype_t eventType, uint8_t * payload, size_t length)
{
  switch (eventType) {
    case WStype_CONNECTED:
      Serial.printf("[ws] 客户端 #%u 接入\n", clientNum);
      break;
    case WStype_DISCONNECTED:
      Serial.printf("[ws] 客户端 #%u 断开\n", clientNum);
      break;
    case WStype_TEXT:
      Serial.printf("[ws] 收到: %.*s\n", static_cast<int>(length), payload);
      payload[length] = '\0';  // 手动补结束符，当C字符串用
      handleMessage(reinterpret_cast<char *>(payload));
      break;
    default:
      break;
  }
}

void setup()
{
  Serial.begin(115200);
  delay(500);

  Serial.printf("正在连接WiFi: %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print('.');
  }
  Serial.printf("\nWiFi已连接，IP: %s\n", WiFi.localIP().toString().c_str());

  wsServer.begin();
  wsServer.onEvent(onWsEvent);
  Serial.printf("WebSocket服务已启动: ws://%s:%u\n", WiFi.localIP().toString().c_str(), WS_PORT);
}

void loop()
{
  wsServer.loop();   // 处理WebSocket收发
  updateExecutor();  // 推进假执行

  // WiFi断了就重连
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[wifi] 连接断开，尝试重连...");
    WiFi.reconnect();
    delay(1000);
  }
}
