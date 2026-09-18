# SPDX-License-Identifier: Apache-2.0
"""
把用户说的话变成小车的动作，也可以进行简单的聊天

流程：用户打字输入 → 发给本地的llama-server → 收到结构化JSON → 分发：
reply（说的话）先打印，expression（表情）先打印，action（动作）发给relay_server执行
"""

import asyncio
import json
import urllib.request

import websockets

LLAMA_URL = 'http://127.0.0.1:8080/v1/chat/completions'  # llama-server的OpenAI兼容接口
RELAY_URL = 'ws://localhost:8765'  # relay_server的WebSocket地址

SYSTEM_PROMPT = """
你是一个桌面宠物机器人。根据用户的话，输出JSON：\
reply是你说的话（简短可爱），expression是你的表情，action是动作指令（用户没让你动就为null）。
动作指令command只能是move_forward/move_backward/turn_left/turn_right，\
value对移动指令是米（0.05到1.0），对转向指令是弧度（0.26到3.14）。
参考：一小步约0.2米，转个身约1.57弧度。
"""

# JSON Schema，定义输出契约，llama-server会把它编译成文法，强制模型只能输出这个结构
RESPONSE_SCHEMA = {
    'type': 'object',
    'properties': {
        'reply': {'type': 'string'},
        'expression': {
            'type': 'string',
            'enum': ['neutral', 'happy', 'sad', 'angry', 'surprised', 'sleepy'],
        },
        'action': {
            'type': ['object', 'null'],
            'properties': {
                'command': {
                    'type': 'string',
                    'enum': ['move_forward', 'move_backward', 'turn_left', 'turn_right'],
                },
                'value': {'type': 'number'},
            },
            'required': ['command', 'value'],
            'additionalProperties': False,
        },
    },
    'required': ['reply', 'expression', 'action'],
    'additionalProperties': False,
}

# JSON Schema不管数值范围，此处用于约束动作指令的范围
VALUE_RANGE = {
    'move_forward': (0.05, 1.0),
    'move_backward': (0.05, 1.0),
    'turn_left': (0.26, 3.14),
    'turn_right': (0.26, 3.14),
}


def ask_llm(user_text):
    """把用户的话发给llama-server，返回解析好的JSON字典"""
    # 要发送给llama-server的请求内容的字典
    payload = {
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': user_text},
        ],
        'response_format': {
            'type': 'json_schema',
            'json_schema': {
                'name': 'deskpet_reply',
                'strict': True,
                'schema': RESPONSE_SCHEMA,
            },
        },
        'max_tokens': 128,
    }

    req = urllib.request.Request(
        # 告诉llama-server要寄到哪个地址
        LLAMA_URL,
        # 将payload字典变成JSON字符串再编码成bytes（因为HTTP传输的是字节流）
        data=json.dumps(payload).encode(),
        # 请求头，告诉llama-server发的是JSON
        headers={'Content-Type': 'application/json'},
    )

    # 发送请求并读取响应
    # resp是HTTPResponse对象
    # content是llama-server返回的JSON字符串
    # with...as...会在结束时自动调用resp.close()，断开与llama-server的连接
    with urllib.request.urlopen(req) as resp:
        content = json.load(resp)['choices'][0]['message']['content']

    # 将JSON字符串解析成Python字典并返回
    return json.loads(content)


async def send_action(action):
    """把动作指令转发给relay_server，它是和小车执行层对接的唯一出口"""
    command = action['command']  # 从LLM的输出中取出动作指令
    lo, hi = VALUE_RANGE[command]  # 从VALUE_RANGE中取出动作指令的合法范围
    value = min(max(action['value'], lo), hi)  # 超出范围就夹回边界

    # 建立WebSocket连接
    # async with...as...会在结束时自动调用ws.close()断开与relay_server的连接
    async with websockets.connect(RELAY_URL) as ws:
        # 把指令打包成JSON字符串发给relay_server
        await ws.send(json.dumps({'type': 'goal', 'command': command, 'value': value}))

        # 等待relay_server响应要在连接断开之前，所以接收循环也在async with内部
        try:
            async with asyncio.timeout(10):
                # 根据type执行不同的逻辑，如果是result就break退出循环
                # async for ... in异步等待WebSocket的消息，raw是收到的原始字符串
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg['type'] in ('accepted', 'rejected', 'result'):
                        print(f'  [relay] {msg}')
                    if msg['type'] == 'result':
                        break
        except TimeoutError:
            print('  [relay] 等待relay_server响应超过10秒，请检查是否连接正常')


async def main():
    print('桌宠大脑已启动，直接打字聊天（Ctrl+C/Ctrl+D退出）')

    while True:
        try:
            # 异步调用input，因为input是阻塞的，所以放到线程池里跑
            user_text = await asyncio.to_thread(input, '\n你: ')
        except EOFError:  # Ctrl+D触发EOFError，也算退出
            break

        # 无视空白输入
        if not user_text.strip():
            continue

        # 与上面input同理，异步调用ask_llm
        answer = await asyncio.to_thread(ask_llm, user_text)

        print(f'桌宠: {answer["reply"]}  [表情: {answer["expression"]}]')

        # 如果有动作指令，就发给relay_server执行
        # 因其本身是异步的，所以直接await
        if answer['action']:
            print(f'  [动作] {answer["action"]}')
            try:
                await send_action(answer['action'])
            except OSError as e:
                print(f'  [relay] 连接失败（relay_server没在跑？）: {e}')


if __name__ == '__main__':
    asyncio.run(main())  # 带异步逻辑，所以用它启动
