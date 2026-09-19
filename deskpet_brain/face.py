# SPDX-License-Identifier: Apache-2.0
"""
桌宠的表情窗口：接收brain发来的表情名字，画出对应的脸

brain通过UDP把表情名字（比如"happy"）丢过来，这个窗口只管画
选UDP是因为它"发了就不管"，就是说即便这个窗口没在跑，brain也不会报错或卡住
"""

import random
import socket

import pygame

LISTEN_ADDR = ('127.0.0.1', 8766)  # 监听brain发表情名字的地址，和brain.py里的一致
WINDOW_SIZE = (240, 240)
BG_COLOR = (24, 24, 32)       # 深色背景，模仿熄灭的屏幕
FACE_COLOR = (110, 230, 190)  # 荧光绿，模仿LED灯

EYE_X = (75, 165)  # 左右两只眼的x坐标
EYE_Y = 95
MOUTH_Y = 165


def draw_face(screen, expression, eyes_closed):
    """按表情名字画一张脸，eyes_closed为真时把眼睛画成闭着的线（用于眨眼）"""
    screen.fill(BG_COLOR)

    # 眼睛
    if eyes_closed or expression == 'sleepy':
        # 闭眼：两条横线
        for x in EYE_X:
            pygame.draw.line(screen, FACE_COLOR, (x - 15, EYE_Y), (x + 15, EYE_Y), 5)
    elif expression == 'happy':
        # 开心：两道向上的弯弧，像 ^ ^
        for x in EYE_X:
            rect = pygame.Rect(x - 20, EYE_Y - 20, 40, 40)
            pygame.draw.arc(screen, FACE_COLOR, rect, 0.3, 3.14 - 0.3, 6)
    elif expression == 'surprised':
        # 惊讶：两个空心大圆
        for x in EYE_X:
            pygame.draw.circle(screen, FACE_COLOR, (x, EYE_Y), 22, 5)
    else:
        # 其他表情都是圆眼睛，伤心时画得小一点、低一点
        radius = 14 if expression == 'sad' else 18
        y = EYE_Y + 6 if expression == 'sad' else EYE_Y
        for x in EYE_X:
            pygame.draw.circle(screen, FACE_COLOR, (x, y), radius)
            # 瞳孔高光，让眼睛看起来有光泽
            pygame.draw.circle(screen, (255, 255, 255), (x - 6, y - 6), 5)

    # 生气时额外画两道倒八字眉毛
    if expression == 'angry':
        pygame.draw.line(screen, FACE_COLOR, (55, 55), (92, 72), 6)
        pygame.draw.line(screen, FACE_COLOR, (185, 55), (148, 72), 6)

    # 嘴巴
    if expression == 'happy':
        # 微笑：向下的弯弧
        pygame.draw.arc(screen, FACE_COLOR, pygame.Rect(95, 130, 50, 50), 3.14 + 0.4, 6.28 - 0.4, 5)
    elif expression == 'sad':
        # 难过：向上的弯弧（嘴角下垂）
        pygame.draw.arc(screen, FACE_COLOR, pygame.Rect(95, 165, 50, 50), 0.4, 3.14 - 0.4, 5)
    elif expression == 'surprised':
        # 惊讶：张成O形的嘴
        pygame.draw.circle(screen, FACE_COLOR, (120, MOUTH_Y), 12, 5)
    elif expression == 'sleepy':
        # 困了：一小段短短的嘴
        pygame.draw.line(screen, FACE_COLOR, (110, MOUTH_Y), (130, MOUTH_Y), 5)
    else:
        # neutral和angry：一条平直的嘴
        pygame.draw.line(screen, FACE_COLOR, (95, MOUTH_Y), (145, MOUTH_Y), 5)

    # 窗口底部用小字显示当前表情名，方便对照调试
    font = pygame.font.Font(None, 22)
    label = font.render(expression, True, (90, 90, 110))
    screen.blit(label, label.get_rect(center=(120, 220)))


def main():
    pygame.init()
    screen = pygame.display.set_mode(WINDOW_SIZE)
    pygame.display.set_caption('桌宠表情')
    clock = pygame.time.Clock()

    # UDP套接字设成不阻塞：每帧看一眼有没有新表情，没有就画当前的
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(LISTEN_ADDR)
    sock.setblocking(False)

    expression = 'neutral'
    next_blink = 2.0   # 距下次眨眼的时刻（秒）
    blink_until = 0.0  # 眨眼结束的时刻

    running = True
    while running:
        # 处理窗口事件（比如点关闭按钮）
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

        # 把积压的消息全读完，只留最新一个表情
        while True:
            try:
                data, _ = sock.recvfrom(64)
                expression = data.decode()
            except BlockingIOError:
                break

        # 眨眼逻辑：每隔2.5到4.5秒随机眨一次，一次0.12秒
        now = pygame.time.get_ticks() / 1000
        if now >= next_blink:
            blink_until = now + 0.12
            next_blink = now + random.uniform(2.5, 4.5)

        draw_face(screen, expression, eyes_closed=now < blink_until)
        pygame.display.flip()
        clock.tick(30)  # 每秒30帧，够了

    sock.close()
    pygame.quit()


if __name__ == '__main__':
    main()
