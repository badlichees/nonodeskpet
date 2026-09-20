# SPDX-License-Identifier: Apache-2.0
"""
桌宠大脑：把用户说的话变成小车的动作，也可以进行简单的聊天

两种运行方式：
    默认（服务模式）：开WebSocket端口等Qt客户端连接，手机/电脑都能连
    --cli（终端模式）：直接在终端里打字聊天，用于不依赖Qt的快速调试

内部流程：收到一句话 → 发给本地的llama-server → 收到结构化JSON → 分发：
reply（说的话）回报给用户并语音播报，expression（表情）丢给face.py窗口，
action（动作）发给relay_server执行，执行结果也回报给用户

服务模式收发JSON格式（type字段表示消息种类）：
    Qt → brain  {"type": "chat", "text": "往前走两步"}                 用户说的一句话
    brain → Qt  {"type": "reply", "text": "好呀", "expression": "happy"}  回复和表情
    brain → Qt  {"type": "action", "command": "...", "value": 0.4}     LLM决定了要执行的动作
    brain → Qt  {"type": "accepted"} / {"type": "rejected", ...}       relay受理/拒绝（转发）
    brain → Qt  {"type": "result", "success": true, "message": "..."}  动作执行结果（转发）
    brain → Qt  {"type": "error", "message": "..."}                    出错了（附原因）
"""

import asyncio
import json
import os
import socket
import sys
import tempfile

import aiohttp
import edge_tts
import websockets

LLAMA_URL = 'http://127.0.0.1:8080/v1/chat/completions'  # llama-server的OpenAI兼容接口
RELAY_URL = 'ws://localhost:8765'  # relay_server的WebSocket地址
FACE_ADDR = ('127.0.0.1', 8766)  # 表情窗口的UDP地址，和face.py里的一致
LISTEN_PORT = 8767  # 服务模式监听的端口，Qt客户端连这个
VOICE = 'zh-CN-XiaoxiaoNeural'  # edge-tts的发音人，用edge-tts --list-voices可以看所有

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

# 给表情窗口发消息用的UDP套接字
_face_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# 语音播报互斥锁（服务模式下来自不同客户端的回复可能同时想说话，排队一个个播）
_speak_lock = asyncio.Lock()


async def ask_llm(user_text):
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

    # 用aiohttp发异步POST请求：异步等待期间Ctrl+C能立刻打断
    # timeout是给整个请求的超时上限，防止llama-server卡住时一直等
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(LLAMA_URL, json=payload) as resp:
            data = await resp.json()  # resp是响应对象，data是它返回的JSON解析成的字典

    # 从OpenAI兼容格式里取出模型输出的JSON字符串，解析成字典返回
    return json.loads(data['choices'][0]['message']['content'])


async def speak(text):
    """edge-tts在线生成语音（要联网），保存成临时mp3后用mpv播放"""
    # 先在磁盘上占一个临时文件的位置（delete=False是因为mpv要自己打开这个文件）
    with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
        path = f.name
    try:
        # 把文本发给微软的语音服务，返回的音频存进临时文件
        await edge_tts.Communicate(text, VOICE).save(path)
        # 启动mpv播放，--really-quiet让它不在终端打印信息，await等它播完再继续
        proc = await asyncio.create_subprocess_exec('mpv', '--really-quiet', path)
        try:
            await proc.wait()
        except asyncio.CancelledError:
            # 程序被打断时，把还在播的mpv一起带走，不然退出后声音还在放
            proc.kill()
            raise
    finally:
        # 不管播放成功还是失败，临时文件都删掉
        os.unlink(path)


async def send_action(action, report):
    """把动作指令转发给relay_server执行，它是和小车执行层对接的唯一出口"""
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
                # async for ... in异步等待WebSocket的消息，raw是收到的原始字符串
                # relay的受理/拒绝/结果消息原样转给客户端，是result就break退出循环
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg['type'] in ('accepted', 'rejected', 'result'):
                        await report(msg)
                    if msg['type'] == 'result':
                        return
        except TimeoutError:
            await report({'type': 'error', 'message': '等待relay_server响应超过10秒'})


async def handle_chat(user_text, report):
    """
    处理一句用户输入：问LLM → 回复 → 换表情 → 语音播报 → 执行动作

    report是一个异步函数，负责把过程消息送回给用户
    （CLI模式下它打印到终端，服务模式下它发给Qt客户端）
    """
    # llama-server没启动（OSError）或返回异常（aiohttp.ClientError）时告诉用户，继续服务
    try:
        answer = await ask_llm(user_text)
    except (OSError, aiohttp.ClientError) as e:
        await report({'type': 'error', 'message': f'请求llama-server失败（它没在跑？）: {e}'})
        return

    await report({
        'type': 'reply',
        'text': answer['reply'],
        'expression': answer['expression'],
    })

    # 把表情名字丢给表情窗口换脸
    _face_sock.sendto(answer['expression'].encode(), FACE_ADDR)

    # 语音播报失败（比如断网、没装mpv）不影响后面的动作执行，所以单独包一层try
    # 只看edge-tts自己的异常（服务返回错误等）和OSError（断网、找不到mpv程序等）
    try:
        async with _speak_lock:
            await speak(answer['reply'])
    except (edge_tts.exceptions.EdgeTTSException, OSError) as e:
        await report({'type': 'error', 'message': f'语音播报失败: {e}'})

    # 如果有动作指令，就发给relay_server执行
    if answer['action']:
        await report({'type': 'action', **answer['action']})
        try:
            await send_action(answer['action'], report)
        except OSError as e:
            await report({'type': 'error', 'message': f'连接relay_server失败（它没在跑？）: {e}'})


async def serve():
    """开WebSocket端口，每个连上的客户端独立处理"""
    async def ws_handler(ws):
        """一个客户端连接的处理逻辑，每收到一条消息处理一次"""
        print(f'客户端接入: {ws.remote_address}')
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send(json.dumps({'type': 'error', 'message': '非法JSON'}))
                continue

            if msg.get('type') == 'chat' and isinstance(msg.get('text'), str):
                # report把消息发回这个客户端
                # ensure_ascii=False让中文原样发出不转义
                async def report(payload):
                    await ws.send(json.dumps(payload, ensure_ascii=False))
                await handle_chat(msg['text'], report)
            else:
                await ws.send(json.dumps({'type': 'error', 'message': '需要 {"type": "chat", "text": "..."}'}))

    # 0.0.0.0表示接受任何网卡进来的连接（手机通过局域网也能连上）
    async with websockets.serve(ws_handler, '0.0.0.0', LISTEN_PORT):
        print(f'桌宠大脑服务已启动: ws://0.0.0.0:{LISTEN_PORT}（Ctrl+C退出）')
        await asyncio.Future()  # 永久挂起，服务一直开着，直到Ctrl+C打断


async def main_cli():
    """终端模式：直接打字聊天"""
    print('桌宠大脑（终端模式）已启动，直接打字聊天（Ctrl+C/Ctrl+D退出）')

    # 把标准输入接进事件循环，这样等待输入本身就是异步的，Ctrl+C能立刻打断
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    # 终端模式下的report，reply打印成对话格式，其他消息原样打印
    async def report(msg):
        if msg['type'] == 'reply':
            print(f'桌宠: {msg["text"]}  [表情: {msg["expression"]}]')
        else:
            print(f'  [{msg["type"]}] {msg}')

    while True:
        print('\n你: ', end='', flush=True)
        # 异步等一行输入；Ctrl+D会读到EOF，readline返回空bytes，也算退出
        line = await reader.readline()
        if not line:
            break

        user_text = line.decode().strip()
        # 无视空白输入
        if not user_text:
            continue

        await handle_chat(user_text, report)


if __name__ == '__main__':
    try:
        if '--cli' in sys.argv:
            asyncio.run(main_cli())  # 终端模式
        else:
            asyncio.run(serve())  # 服务模式（默认）
    except KeyboardInterrupt:
        # Ctrl+C退到这里，安静退出，不打印一堆报错
        print('\n再见')
