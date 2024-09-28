#!/usr/bin/env python
import time
import unittest
from json import dumps, loads
from threading import Thread

import rclpy
from example_interfaces.action._fibonacci import Fibonacci_FeedbackMessage
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rosbridge_library.capabilities.action_feedback import ActionFeedback
from rosbridge_library.capabilities.action_result import ActionResult
from rosbridge_library.capabilities.advertise_action import AdvertiseAction
from rosbridge_library.capabilities.send_action_goal import SendActionGoal
from rosbridge_library.capabilities.unadvertise_action import UnadvertiseAction
from rosbridge_library.internal.exceptions import (
    InvalidArgumentException,
    MissingArgumentException,
)
from rosbridge_library.protocol import Protocol


class TestActionCapabilities(unittest.TestCase):
    def setUp(self):
        rclpy.init()
        self.executor = SingleThreadedExecutor()
        self.node = Node("test_action_capabilities")
        self.executor.add_node(self.node)

        self.node.declare_parameter("call_services_in_new_thread", False)
        self.node.declare_parameter("send_action_goals_in_new_thread", False)

        self.proto = Protocol(self._testMethodName, self.node)
        # change the log function so we can verify errors are logged
        self.proto.log = self.mock_log
        # change the send callback so we can access the rosbridge messages
        # being sent
        self.proto.send = self.local_send_cb
        self.advertise = AdvertiseAction(self.proto)
        self.unadvertise = UnadvertiseAction(self.proto)
        self.result = ActionResult(self.proto)
        self.send_goal = SendActionGoal(self.proto)
        self.feedback = ActionFeedback(self.proto)
        self.received_messages = []
        self.log_entries = []

        self.exec_thread = Thread(target=self.executor.spin)
        self.exec_thread.start()

    def tearDown(self):
        self.executor.remove_node(self.node)
        self.executor.shutdown()
        rclpy.shutdown()

    def local_send_cb(self, msg):
        print(f"GOT MESSAGE:\n{msg}")
        self.received_messages.append(msg)

    def feedback_subscriber_cb(self, msg):
        self.latest_feedback = msg

    def mock_log(self, loglevel, message, _=None):
        self.log_entries.append((loglevel, message))

    def test_advertise_missing_arguments(self):
        advertise_msg = loads(dumps({"op": "advertise_action"}))
        self.assertRaises(MissingArgumentException, self.advertise.advertise_action, advertise_msg)

    def test_advertise_invalid_arguments(self):
        advertise_msg = loads(dumps({"op": "advertise_action", "type": 42, "action": None}))
        self.assertRaises(InvalidArgumentException, self.advertise.advertise_action, advertise_msg)

    def test_result_missing_arguments(self):
        result_msg = loads(dumps({"op": "action_result"}))
        self.assertRaises(MissingArgumentException, self.result.action_result, result_msg)

        # this message has the optional fields, with correct types, but not the
        # required ones
        result_msg = loads(dumps({"op": "action_result", "id": "dummy_action", "values": "none"}))
        self.assertRaises(MissingArgumentException, self.result.action_result, result_msg)

    def test_result_invalid_arguments(self):
        result_msg = loads(dumps({"op": "action_result", "action": 5, "result": "error"}))
        self.assertRaises(InvalidArgumentException, self.result.action_result, result_msg)

    def test_advertise_action(self):
        action_path = "/fibonacci_action_1"
        advertise_msg = loads(
            dumps(
                {
                    "op": "advertise_action",
                    "type": "example_interfaces/Fibonacci",
                    "action": action_path,
                }
            )
        )
        self.advertise.advertise_action(advertise_msg)

    def test_execute_advertised_action(self):
        # Advertise the action
        action_path = "/fibonacci_action_2"
        advertise_msg = loads(
            dumps(
                {
                    "op": "advertise_action",
                    "type": "example_interfaces/Fibonacci",
                    "action": action_path,
                }
            )
        )
        self.advertise.advertise_action(advertise_msg)
        time.sleep(0.1)

        # Send a goal to the advertised action using rosbridge
        self.received_messages = []
        goal_msg = loads(
            dumps(
                {
                    "op": "send_action_goal",
                    "id": "foo2",
                    "action": action_path,
                    "action_type": "example_interfaces/Fibonacci",
                    "args": {"order": 5},
                }
            )
        )
        Thread(target=self.send_goal.send_action_goal, args=(goal_msg,)).start()

        loop_iterations = 0
        while len(self.received_messages) < 1:
            time.sleep(0.5)
            loop_iterations += 1
            if loop_iterations > 3:
                self.fail("Timed out waiting for action goal message.")

        self.assertEqual(len(self.received_messages), 1)
        msg = self.received_messages[0]
        self.assertIsNotNone(msg)
        self.assertTrue("op" in msg)
        self.assertEqual(msg["op"], "send_action_goal")
        self.assertTrue("id" in msg)

        # Send feedback message
        self.latest_feedback = None
        sub_qos_profile = QoSProfile(
            depth=10,
            durability=DurabilityPolicy.VOLATILE,
            reliability=ReliabilityPolicy.RELIABLE,
        )
        self.subscription = self.node.create_subscription(
            Fibonacci_FeedbackMessage,
            f"{action_path}/_action/feedback",
            self.feedback_subscriber_cb,
            sub_qos_profile,
        )
        time.sleep(0.1)
        feedback_msg = loads(
            dumps(
                {
                    "op": "action_feedback",
                    "action": action_path,
                    "id": msg["id"],
                    "values": {"sequence": [0, 1, 1]},
                }
            )
        )
        self.feedback.action_feedback(feedback_msg)
        loop_iterations = 0
        while self.latest_feedback is None:
            time.sleep(0.5)
            loop_iterations += 1
            if loop_iterations > 3:
                self.fail("Timed out waiting for action feedback message.")

        self.assertIsNotNone(self.latest_feedback)
        self.assertEqual(list(self.latest_feedback.feedback.sequence), [0, 1, 1])

        # Now send the result
        result_msg = loads(
            dumps(
                {
                    "op": "action_result",
                    "action": action_path,
                    "id": msg["id"],
                    "values": {"sequence": [0, 1, 1, 2, 3, 5]},
                    "result": True,
                }
            )
        )
        self.received_messages = []
        self.result.action_result(result_msg)

        loop_iterations = 0
        while len(self.received_messages) < 1:
            time.sleep(0.5)
            loop_iterations += 1
            if loop_iterations > 3:
                self.fail("Timed out waiting for action result message.")

        self.assertEqual(len(self.received_messages), 1)
        msg = self.received_messages[0]
        self.assertIsNotNone(msg)
        self.assertEqual(msg["op"], "action_result")
        self.assertEqual(msg["values"]["result"]["sequence"], [0, 1, 1, 2, 3, 5])

    def test_cancel_advertised_action(self):
        # Advertise the action
        action_path = "/fibonacci_action_3"
        advertise_msg = loads(
            dumps(
                {
                    "op": "advertise_action",
                    "type": "example_interfaces/Fibonacci",
                    "action": action_path,
                }
            )
        )
        self.advertise.advertise_action(advertise_msg)
        time.sleep(0.1)

        # Send a goal to the advertised action using rosbridge
        self.received_messages = []
        goal_msg = loads(
            dumps(
                {
                    "op": "send_action_goal",
                    "id": "foo3",
                    "action": action_path,
                    "action_type": "example_interfaces/Fibonacci",
                    "args": {"order": 5},
                }
            )
        )
        Thread(target=self.send_goal.send_action_goal, args=(goal_msg,)).start()

        loop_iterations = 0
        while len(self.received_messages) < 1:
            time.sleep(0.5)
            loop_iterations += 1
            if loop_iterations > 3:
                self.fail("Timed out waiting for action goal message.")

        self.assertEqual(len(self.received_messages), 1)
        msg = self.received_messages[0]
        self.assertIsNotNone(msg)
        self.assertTrue("op" in msg)
        self.assertEqual(msg["op"], "send_action_goal")
        self.assertTrue("id" in msg)

        # Now cancel the goal
        cancel_msg = loads(
            dumps(
                {
                    "op": "cancel_action_goal",
                    "action": action_path,
                    "id": "foo3",
                }
            )
        )
        self.received_messages = []
        self.send_goal.cancel_action_goal(cancel_msg)

        loop_iterations = 0
        while len(self.received_messages) < 2:
            time.sleep(0.5)
            loop_iterations += 1
            if loop_iterations > 3:
                self.fail("Timed out waiting for action result message.")

        self.assertEqual(len(self.received_messages), 2)
        got_cancel_message = False
        got_result_message = False
        for msg in self.received_messages:
            if msg["op"] == "cancel_action_goal":
                got_cancel_message = True
            elif msg["op"] == "action_result":
                got_result_message = True
                self.assertEqual(msg["values"]["status"], 6)  # Aborted
                self.assertTrue(msg["values"]["result"]["sequence"] == [])

        self.assertTrue(got_cancel_message)
        self.assertTrue(got_result_message)

    @unittest.skip("Currently raises an exception not catchable by unittest, need to fix this")
    def test_unadvertise_action(self):
        # Advertise the action
        action_path = "/fibonacci_action_4"
        advertise_msg = loads(
            dumps(
                {
                    "op": "advertise_action",
                    "type": "example_interfaces/Fibonacci",
                    "action": action_path,
                }
            )
        )
        self.received_messages = []
        self.advertise.advertise_action(advertise_msg)
        time.sleep(0.1)

        # Send a goal to the advertised action using rosbridge
        self.received_messages = None
        goal_msg = loads(
            dumps(
                {
                    "op": "send_action_goal",
                    "id": "foo4",
                    "action": action_path,
                    "action_type": "example_interfaces/Fibonacci",
                    "args": {"order": 5},
                }
            )
        )
        Thread(target=self.send_goal.send_action_goal, args=(goal_msg,)).start()

        loop_iterations = 0
        while len(self.received_messages) < 1:
            time.sleep(0.5)
            loop_iterations += 1
            if loop_iterations > 3:
                self.fail("Timed out waiting for action goal message.")

        self.assertEqual(len(self.received_messages), 1)
        msg = self.received_messages[0]
        self.assertTrue("op" in msg)
        self.assertEqual(msg["op"], "send_action_goal")
        self.assertTrue("id" in msg)

        # Now unadvertise the action
        # TODO: This raises an exception, likely because of the following rclpy issue:
        # https://github.com/ros2/rclpy/issues/1098
        unadvertise_msg = loads(dumps({"op": "unadvertise_action", "action": action_path}))
        self.received_messages = []
        self.unadvertise.unadvertise_action(unadvertise_msg)

        loop_iterations = 0
        while len(self.received_messages) < 1:
            rclpy.spin_once(self.node, timeout_sec=0.1)
            time.sleep(0.5)
            loop_iterations += 1
            if loop_iterations > 3:
                self.fail("Timed out waiting for unadvertise action message.")


if __name__ == "__main__":
    unittest.main()
