import numpy as np

from abc import ABC, abstractmethod

from xebikart.parts.joystick import KeynoteAction


class Driver:

    def __init__(self, config):
        self.config = config

    def run(
            self,
            mode,
            user_angle, user_throttle,  # from controller
            pilot_angle, pilot_throttle,  # from ML model
            x, y, z, angle,  # from lidar
            dx, dy, dz, tx, ty, tz  # from imu
    ):
        if mode == 'user':
            return user_angle, user_throttle, True, self.config.IMU_ENABLED, self.config.LIDAR_ENABLED
        else:
            return pilot_angle, pilot_throttle, False, self.config.IMU_ENABLED, self.config.LIDAR_ENABLED


class Mode:
    """
    Based on mode, returns user or ai outputs (steering and throttle)
    """
    def run(self, mode, user_steering, user_throttle, ai_steering, ai_throttle):
        if mode == 'user':
            return user_steering, user_throttle
        elif mode == 'local_angle':
            return ai_steering, user_throttle
        elif mode == 'local':
            return ai_steering, ai_throttle
        else:
            return 0., 0.


class KeynoteDriverV1:

    def __init__(self, exit_threshold=1., brightness_threshold=500000):
        self.mode_map = {
            Mode.USER_MODE: lambda: UserModeV1(exit_threshold=exit_threshold, brightness_threshold= brightness_threshold),
            Mode.AI_MODE: lambda: AIModeV1(exit_threshold=exit_threshold, brightness_threshold= brightness_threshold),
            Mode.EMERGENCY_STOP_MODE: lambda: EmergencyStopMode(),
            Mode.SAFE_MODE: lambda: SafeMode(),
            Mode.RETURN_MODE: lambda: ReturnMode()
        }
        self.current_mode = None
        self.current_mode_str = None
        self.set_mode(Mode.USER_MODE)

    def set_mode(self, mode):
        if mode in self.mode_map:
            self.current_mode = self.mode_map[mode]()
            self.current_mode_str = mode
        else:
            print(mode, "doesn't exist.")

    def run(self, user_steering, user_throttle, user_actions, ai_steering, detect_box, exit_buffer, brightness_buffer):
        steering, throttle = self.current_mode.run(user_steering, user_throttle, user_actions,
                                                   ai_steering, detect_box, exit_buffer, brightness_buffer)
        if self.current_mode.next_mode is not None:
            self.set_mode(self.current_mode.next_mode)
        return steering, throttle, self.current_mode_str


class KeynoteDriverV2:
    def __init__(self):
        self.mode_map = {
            Mode.USER_MODE: lambda: UserModeV2(),
            Mode.AI_MODE: lambda: AIModeV2(),
            Mode.EMERGENCY_STOP_MODE: lambda: EmergencyStopMode(),
            Mode.SAFE_MODE: lambda: SafeMode()
        }
        self.current_mode = None
        self.current_mode_str = None
        self.set_mode(Mode.USER_MODE)

    def set_mode(self, mode):
        if mode in self.mode_map:
            self.current_mode = self.mode_map[mode]()
            self.current_mode_str = mode
        else:
            print(mode, "doesn't exist.")

    def run(self, user_steering, user_throttle, user_actions, ai_steering, detect_box, exit_buffer, brightness_buffer):
        steering, throttle = self.current_mode.run(user_steering, user_throttle, user_actions,
                                                   ai_steering, detect_box, exit_buffer, brightness_buffer)
        if self.current_mode.next_mode is not None:
            self.set_mode(self.current_mode.next_mode)
        return steering, throttle, self.current_mode_str


class Mode(ABC):
    SAFE_MODE = "safe_mode"
    USER_MODE = "user_mode"
    AI_MODE = "ai_mode"
    AI_STEERING_MODE = "ai_steering_mode"
    EMERGENCY_STOP_MODE = "emergency_stop_mode"
    RETURN_MODE = "return_mode"
    TAKEOVER_MODE = "takeover_mode"

    def __init__(self):
        self.js_actions_fn = {}
        self.next_mode = None
        print("INFO: Changing mode: {}".format(self.__class__.__name__))

    def do_js_actions(self, actions):
        for action in actions:
            if action in self.js_actions_fn:
                self.js_actions_fn[action]()
            else:
                print("WARN: {} action does not exist.".format(action))
                print("WARN: {}".format(str(self.js_actions_fn.keys())))

    def set_next_mode(self, next_mode):
        self.next_mode = next_mode

    def fn_set_next_mode(self, next_mode):
        return lambda: self.set_next_mode(next_mode)

    @abstractmethod
    def run(self, user_steering, user_throttle, user_actions, ai_steering, detect_box, exit_buffer, brightness_buffer):
        raise NotImplemented


class ModeV1(Mode):
    def __init__(self, toggle_mode, exit_threshold, brightness_threshold):
        super(ModeV1, self).__init__()
        self.js_actions_fn = {
            KeynoteAction.TRIGGER_EMERGENCY_STOP: self.fn_set_next_mode(Mode.EMERGENCY_STOP_MODE),
            KeynoteAction.MODE_TOGGLE: self.fn_set_next_mode(toggle_mode)
        }
        self.exit_threshold = exit_threshold
        self.brightness_threshold = brightness_threshold

    def check_ai_buffers(self, detect_box, exit_buffer, brightness_buffer):
        if np.sum(exit_buffer) > self.exit_threshold or np.sum(brightness_buffer) < self.brightness_threshold:
            self.set_next_mode(Mode.EMERGENCY_STOP_MODE)

        x = detect_box[1]
        y = detect_box[2]
        # max_y
        if 50 < y <= 120 and 0 <= x < 100:
            self.set_next_mode(Mode.EMERGENCY_STOP_MODE)

    def run(self, user_steering, user_throttle, user_actions, ai_steering, detect_box, exit_buffer, brightness_buffer):
        self.do_js_actions(user_actions)
        self.check_ai_buffers(detect_box, exit_buffer, brightness_buffer)
        return self.get_steering_throttle( user_steering, user_throttle, user_actions, ai_steering)

    @abstractmethod
    def get_steering_throttle(self, user_steering, user_throttle, user_actions, ai_steering):
        raise NotImplementedError


class UserModeV1(ModeV1):
    def __init__(self, *args, **kwargs):
        super(UserModeV1, self).__init__(toggle_mode=Mode.AI_MODE, *args, **kwargs)

    def get_steering_throttle(self, user_steering, user_throttle, user_actions, ai_steering):
        return user_steering, user_throttle


class AISteeringModeV1(ModeV1):
    def __init__(self, *args, **kwargs):
        super(AISteeringModeV1, self).__init__(toggle_mode=Mode.AI_MODE, *args, **kwargs)

    def get_steering_throttle(self, user_steering, user_throttle, user_actions, ai_steering):
        return ai_steering, user_throttle


class AIModeV1(ModeV1):
    def __init__(self, *args, **kwargs):
        super(AIModeV1, self).__init__(toggle_mode=Mode.USER_MODE, *args, **kwargs)
        self.js_actions_fn[KeynoteAction.INCREASE_THROTTLE] = self.fn_throttle(0.01)
        self.js_actions_fn[KeynoteAction.DECREASE_THROTTLE] = self.fn_throttle(-0.01)
        self.const_throttle = 0.22

    def fn_throttle(self, to_add):
        def _set_throttle():
            self.const_throttle += to_add
        return _set_throttle

    def get_steering_throttle(self, user_steering, user_throttle, user_actions, ai_steering):
        return ai_steering, self.const_throttle


class ReturnMode(Mode):
    def __init__(self):
        super(ReturnMode, self).__init__()
        self.js_actions_fn = {
            KeynoteAction.TRIGGER_EMERGENCY_STOP: self.fn_set_next_mode(Mode.EMERGENCY_STOP_MODE)
        }
        self.const_throttle = 0.18
        self.const_steering = -0.10

    def check_ai_buffers(self, exit_buffer):
        if np.sum(exit_buffer) < 0.1:
            self.set_next_mode(Mode.AI_MODE)

    def run(self, user_steering, user_throttle, user_actions, ai_steering, detect_box, exit_buffer, brightness_buffer):
        self.do_js_actions(user_actions)
        self.check_ai_buffers(exit_buffer)
        return self.const_steering, self.const_throttle


class SafeMode(Mode):
    def __init__(self):
        super(SafeMode, self).__init__()
        self.js_actions_fn = {
            KeynoteAction.TRIGGER_EXIT_SAFE_MODE: self.fn_set_next_mode(Mode.USER_MODE),
            KeynoteAction.TRIGGER_RETURN_MODE: self.fn_set_next_mode(Mode.RETURN_MODE)
        }

    def run(self, user_steering, user_throttle, user_actions, ai_steering, detect_box, exit_buffer, brightness_buffer):
        self.do_js_actions(user_actions)
        return user_steering, user_throttle


class EmergencyStopMode(Mode):
    def __init__(self):
        super(EmergencyStopMode, self).__init__()
        # Emergency stop sequences
        self.es_sequence = [-0.4, 0.01, -0.4, 0., 0.,
                            0., 0., 0., 0., 0., 0., 0.]

    def is_in_loop(self):
        return len(self.es_sequence) > 0

    def roll_emergency_stop(self):
        throttle = self.es_sequence.pop(0)
        return throttle

    def run(self, user_steering, user_throttle, user_actions, ai_steering, detect_box, exit_buffer, brightness_buffer):
        if self.is_in_loop():
            return 0., self.roll_emergency_stop()
        else:
            self.set_next_mode(Mode.SAFE_MODE)
            return 0., 0.


class TakeoverMode(Mode):
    def __init__(self):
        super(TakeoverMode, self).__init__()
        self.const_throttle = 0.20
        self.sequence = ([-1.] * 15) + ([0.5] * 15)

    def run(self, user_steering, user_throttle, user_actions, ai_steering, detect_box, exit_buffer, brightness_buffer):
        if len(self.sequence) > 0:
            steering = self.sequence.pop(0)
            return steering, self.const_throttle
        else:
            self.set_next_mode(Mode.AI_MODE)
            return 0., self.const_throttle
