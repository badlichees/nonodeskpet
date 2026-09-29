# SPDX-License-Identifier: Apache-2.0
"""
语义动作原语Action服务器

目前为开环执行，按“时间 = 距离/速度”持续发布cmd_vel，到点或取消时发零速，取消goal即急停
"""

import math

import rclpy
from deskpet_interfaces.action import Primitive
from geometry_msgs.msg import Twist
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from rclpy.signals import SignalHandlerOptions

_VALID_COMMANDS = ('move_forward', 'move_backward', 'turn_left', 'turn_right')


class PrimitiveServer(Node):
    """把语义动作原语翻译成cmd_vel的开环执行器"""

    def __init__(self):
        super().__init__('primitive_server')

        self.declare_parameter('linear_speed', 0.2)
        self.declare_parameter('angular_speed', 1.0)
        self.declare_parameter('max_distance', 1.0)
        self.declare_parameter('max_angle', math.pi)
        self.declare_parameter('cmd_vel_topic', '/diff_drive/cmd_vel')
        self.declare_parameter('publish_rate', 20.0)

        cmd_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self.cmd_pub = self.create_publisher(
            Twist, self.get_parameter('cmd_vel_topic').value, cmd_qos
        )

        # 执行是阻塞循环，取消/新目标必须能并行进来，需要创建一个回调(Reentrant)组
        self.action_server = ActionServer(
            self,  # node实例
            Primitive,  # action类型
            'primitive',  # action名
            goal_callback=self._on_goal,  # 接收goal时的回调
            cancel_callback=self._on_cancel,  # 接收cancel时的回调
            execute_callback=self._on_execute,  # 执行goal时的回调
            callback_group=ReentrantCallbackGroup(),  # 允许并行执行的回调组
        )

    def _on_goal(self, goal):
        if goal.command not in _VALID_COMMANDS:
            self.get_logger().warn(f'未知指令: {goal.command}')
            return GoalResponse.REJECT
        if goal.value <= 0.0:
            self.get_logger().warn(f'非法数值: {goal.value}')
            return GoalResponse.REJECT
        return GoalResponse.ACCEPT

    def _on_cancel(self, _goal_handle):
        return CancelResponse.ACCEPT

    def _publish_stop(self):
        self.cmd_pub.publish(Twist())

    def _on_execute(self, goal_handle):
        """
        接收一个移动指令，用开环定时控制的方式让机器人底盘运动一段距离或角度

        goal_handle是ServerGoalHandle的实例对象，可以用来管理某个具体goal的状态
        """
        # 获取目标和参数
        request = goal_handle.request
        linear_speed = self.get_parameter('linear_speed').value
        angular_speed = self.get_parameter('angular_speed').value

        # 根据命令类型计算运动参数
        twist = Twist()
        if request.command in ('move_forward', 'move_backward'):
            max_value = self.get_parameter('max_distance').value
            value = min(request.value, max_value)
            duration = value / linear_speed  # 计算运动持续时间
            twist.linear.x = linear_speed if request.command == 'move_forward' else -linear_speed
        else:
            max_value = self.get_parameter('max_angle').value
            value = min(request.value, max_value)
            duration = value / angular_speed  # 计算运动持续时间
            twist.angular.z = angular_speed if request.command == 'turn_left' else -angular_speed

        # 初始化结果、反馈和定时器
        result = Primitive.Result()
        feedback = Primitive.Feedback()
        rate = self.create_rate(self.get_parameter('publish_rate').value)
        start = self.get_clock().now()

        # 主循环，检查ROS2状态、时间、取消请求，在接收到取消请求之前持续发布cmd_vel
        while rclpy.ok():
            elapsed = (self.get_clock().now() - start).nanoseconds / 1e9
            if elapsed >= duration:
                self._publish_stop()
                goal_handle.succeed()
                result.success = True
                result.message = f'{request.command}({value}) 完成'
                return result
            if goal_handle.is_cancel_requested:
                self._publish_stop()
                goal_handle.canceled()
                result.success = False
                result.message = '已取消'
                return result
            self.cmd_pub.publish(twist)
            feedback.progress = min(elapsed / duration, 1.0)
            goal_handle.publish_feedback(feedback)
            # 休眠到下一次循环，保证发布频率
            # 会阻塞循环但因为是ReentrantCallbackGroup所以不会阻塞取消请求的回调
            rate.sleep()

        # 节点被关闭时就跳出循环，最后再发一次零速，确保机器人停下
        self._publish_stop()
        result.success = False
        result.message = '节点关闭'
        return result


def main(args=None):
    # 初始化rclpy但不会接管ctrl+c信号
    # 一般情况下rclpy.init()会注册SIGINT信号处理器，捕获ctrl+c信号后会调用rclpy.shutdown()
    # 但这里我们希望在退出前还能发一次零速
    # 所以这里选择不注册信号处理器，自己在finally里调用shutdown()
    rclpy.init(args=args, signal_handler_options=SignalHandlerOptions.NO)
    # ReentrantCallbackGroup允许在执行循环中接收取消请求但首先需要一个多线程的executor
    # 所以这里使用MultiThreadedExecutor
    node = PrimitiveServer()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    # 启动执行循环，自己检测ctrl+c信号
    # 收到后会跳出循环，最后在finally里发一次零速再shutdown
    # executor.spin()内部是不带超时的等待，等的时候Ctrl+C传不进来
    # spin_once设0.5秒超时，每圈回到Python检查一次信号，Ctrl+C最多延迟0.5秒生效
    try:
        while rclpy.ok():
            executor.spin_once(timeout_sec=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        node._publish_stop()
        node.destroy_node()
        rclpy.shutdown()
