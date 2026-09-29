# SPDX-License-Identifier: Apache-2.0
"""
WebSocket中继节点

收发的JSON格式（type字段表示消息种类）：
    Qt → 本节点  {"type": "goal", "command": "move_forward", "value": 0.2}   下一个动作指令
    Qt → 本节点  {"type": "cancel"}                                          取消当前动作
    本节点 → Qt  {"type": "accepted"} / {"type": "rejected", "message": ...} 受理 / 拒绝（附原因）
    本节点 → Qt  {"type": "feedback", "progress": 0.42}                      执行进度（0~1）
    本节点 → Qt  {"type": "result", "success": true, "message": "..."}       执行结束，汇报结果
    本节点 → Qt  {"type": "error", "message": "..."}                         消息本身有问题
"""

import asyncio
import json
import threading

import rclpy
import websockets
from deskpet_interfaces.action import Primitive
from rclpy.action import ActionClient
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.signals import SignalHandlerOptions


class RelayServer(Node):
    """一边充当WebSocket服务器等Qt连接，一边充当action客户端向primitive_server发送内容"""

    def __init__(self):
        super().__init__('relay_server')

        # 声明三个启动时可用-p参数覆盖的配置项
        self.declare_parameter('host', '0.0.0.0')  # 0.0.0.0表示接受任何网卡进来的连接
        self.declare_parameter('port', 8765)  # WebSocket端口号
        self.declare_parameter('action_name', 'primitive')  # 要对接到哪个action

        # action客户端实例，负责把动作目标发给primitive_server，并接收它的进度汇报和结果
        # 语法：节点实例, 动作类型, action名称
        self.action_client = ActionClient(self, Primitive, self.get_parameter('action_name').value)

        self._goal_handle = None  # 当前动作的执行句柄（用于取消动作），None表示空闲
        self._goal_pending = False  # 针对“已发出Goal但未收到响应”的状态标记
        self._ws_clients = set()  # 当前连接上的所有Qt客户端的集合
        self._ws_loop = None  # WebSocket服务用的asyncio事件循环，用于单线程里高效处理任务调度
        self._ws_stop = None  # 通知事件循环该退出的开关

    def start_ws_server(self):
        self._ws_loop = asyncio.new_event_loop()
        self._ws_stop = self._ws_loop.create_future()  # future在这儿作为一个准备接受退出通知的开关
        thread = threading.Thread(target=self._ws_loop_run, daemon=True)  # 主程序退出时一起结束
        thread.start()
        return thread

    def _ws_loop_run(self):
        """线程的入口函数，负责把asyncio事件循环绑定到自身并驱动它运转，直到收到停止信号返回退出"""
        asyncio.set_event_loop(self._ws_loop)  # 把asyncio事件循环绑定到当前线程
        self._ws_loop.run_until_complete(self._ws_main())

    async def _ws_main(self):
        """
        启动WebSocket服务器并挂起等待退出信号，使事件循环持续运转以处理客户端连接

        作为协程对象，允许内部存在await暂停点，事件循环在此期间可以去处理其他任务
        """
        host = self.get_parameter('host').value
        port = self.get_parameter('port').value
        # 进入异步流程，启动WebSocket服务器，绑定端口并开始监听
        async with websockets.serve(self._on_client, host, port):
            self.get_logger().info(f'WebSocket 中继已启动: ws://{host}:{port}')
            await self._ws_stop  # 一直挂在这里，直到收到退出通知

    def shutdown_ws_server(self):
        """停掉WebSocket线程的事件循环"""
        if self._ws_loop is not None:
            # 主线程通过call_soon_threadsafe通知WebSocket线程停止事件循环，具体来说：
            # self._ws_stop是一个Future对象，设置它的结果会让await self._ws_stop返回，从而退出协程
            self._ws_loop.call_soon_threadsafe(self._ws_stop.set_result, None)

    async def _on_client(self, ws, path):
        """每一个新客户端连接后的处理逻辑"""
        self._ws_clients.add(ws)
        self.get_logger().info(f'客户端接入: {ws.remote_address}')
        try:
            async for raw in ws:  # 异步循环，每收到一条消息循环一次，客户端断开时循环自动结束
                self._handle_client_message(raw)
        finally:
            self._ws_clients.discard(ws)  # 无论正常断开还是异常断开，最后都把客户端从集合里移除

    def _handle_client_message(self, raw):
        """解析JSON，根据type字段分发给不同的处理函数"""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            self._broadcast({'type': 'error', 'message': '非法JSON'})
            return

        msg_type = msg.get('type')
        if msg_type == 'goal':
            self._on_goal_request(msg.get('command', ''), msg.get('value', 0.0))
        elif msg_type == 'cancel':
            self._on_cancel_request()
        else:
            self._broadcast({'type': 'error', 'message': f'未知消息类型: {msg_type}'})

    def _on_goal_request(self, command, value):
        """
        收到动作指令时的处理逻辑

        检查格式、检查状态、转发给primitive_server
        """
        # 同一时刻只处理一个动作，正在执行就拒掉新的请求
        if self._goal_pending or self._goal_handle is not None:
            self._broadcast({'type': 'rejected', 'message': '正忙，有goal在执行'})
            return
        # 指令格式不对（指令名不是字符串、数值不是数字）直接报错
        if not isinstance(command, str) or not isinstance(value, (int, float)):
            self._broadcast({'type': 'error', 'message': 'goal需要 str command + 数值 value'})
            return
        # primitive_server没在线时也拒绝
        if not self.action_client.server_is_ready():
            self._broadcast({'type': 'rejected', 'message': 'action server未连接'})
            return

        goal = Primitive.Goal()
        goal.command = command
        goal.value = float(value)
        self._goal_pending = True
        # send_goal_async：异步下单，服务器答复后由_on_goal_response接手，
        # 执行过程中的进度汇报走_on_feedback
        future = self.action_client.send_goal_async(goal, feedback_callback=self._on_feedback)
        future.add_done_callback(self._on_goal_response)

    def _on_cancel_request(self):
        """收到取消指令后把取消请求转达给primitive_server"""
        if self._goal_pending:
            self._broadcast({'type': 'error', 'message': 'goal请求处理中，暂不能取消'})
            return
        if self._goal_handle is None:
            self._broadcast({'type': 'error', 'message': '当前没有执行中的goal'})
            return
        self._goal_handle.cancel_goal_async()

    def _on_goal_response(self, future):
        """primitive_server受理指令后就盯着结果，成功就转发给客户端，拒绝了就转发给客户端"""
        self._goal_pending = False
        goal_handle = future.result()  # 取出GoalHandle对象
        if not goal_handle.accepted:
            self._broadcast({'type': 'rejected', 'message': 'action server拒绝'})
            return
        self._goal_handle = goal_handle
        self._broadcast({'type': 'accepted'})
        goal_handle.get_result_async().add_done_callback(self._on_result)

    def _on_feedback(self, feedback_msg):
        """执行进度汇报，原样转发给Qt客户端"""
        self._broadcast({'type': 'feedback', 'progress': feedback_msg.feedback.progress})

    def _on_result(self, future):
        """执行结束（无论是完成/失败/被取消）把结果转发给客户端，节点回到空闲状态"""
        result = future.result().result
        self._goal_handle = None
        self._broadcast({'type': 'result', 'success': result.success, 'message': result.message})

    def _broadcast(self, payload):
        """
        把消息推给所有连着的Qt客户端，可从任何线程调用

        ROS的回调跑在ROS线程，而WebSocket发送必须在asyncio线程里做
        call_soon_threadsafe负责把发送任务安全地交接过去
        """
        if self._ws_loop is None:
            return
        raw = json.dumps(payload)
        self._ws_loop.call_soon_threadsafe(self._send_all, raw)

    def _send_all(self, raw):
        # 发送途中可能有客户端断开导致集合变动，先copy一份再遍历
        for ws in self._ws_clients.copy():
            asyncio.ensure_future(ws.send(raw))


def main(args=None):
    # 不让rclpy接管Ctrl+C
    rclpy.init(args=args, signal_handler_options=SignalHandlerOptions.NO)
    node = RelayServer()
    thread = node.start_ws_server()
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    try:
        # rclpy.spin(node)内部是不带超时的死等，等待期间Python处理不了Ctrl+C
        # spin_once设0.5秒超时，每转一圈回到Python检查一次信号，Ctrl+C最多延迟0.5秒生效
        while rclpy.ok():
            executor.spin_once(timeout_sec=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        node.shutdown_ws_server()
        thread.join(timeout=2.0)  # 等待WebSocket线程退出，最多等2秒
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
