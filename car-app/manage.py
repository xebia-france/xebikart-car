#!/usr/bin/env python3

"""
Usage:
    manage.py [--model=<model>]

Options:
    -h --help        Show this screen.
    --tub TUBPATHS   List of paths to tubs. Comma separated. Use quotes to use wildcards. ie "~/tubs/*"
"""

import logging
from docopt import docopt

import donkeycar as dk
from donkeycar.parts.camera import PiCamera
from donkeycar.parts.actuator import PCA9685, PWMSteering, PWMThrottle
from donkeycar.parts.clock import Timestamp
from donkeypart_ps3_controller import PS3JoystickController

from xebikart.parts.lidar import LidarScan, LidarPosition
from xebikart.parts.mqtt import MQTTClient


def drive(cfg, model_path=None):
    vehicle = dk.vehicle.Vehicle()

    clock = Timestamp()
    vehicle.add(
        clock,
        outputs=[
            'timestamp'
        ]
    )

    camera = PiCamera(resolution=cfg.CAMERA_RESOLUTION)
    vehicle.add(
        camera,
        outputs=[
            'cam/image_array'
        ],
        threaded=True
    )

    controller = PS3JoystickController(
        throttle_scale=cfg.JOYSTICK_MAX_THROTTLE,
        steering_scale=cfg.JOYSTICK_STEERING_SCALE,
        auto_record_on_throttle=cfg.AUTO_RECORD_ON_THROTTLE
    )
    vehicle.add(
        controller,
        outputs=[
            'user/angle',
            'user/throttle',
            'user/mode',
            'recording'
        ],
        threaded=True
    )

    steering = PWMSteering(
        controller=PCA9685(cfg.STEERING_CHANNEL),
        left_pulse=cfg.STEERING_LEFT_PWM,
        right_pulse=cfg.STEERING_RIGHT_PWM
    )
    vehicle.add(
        steering,
        inputs=[
            'user/angle'
        ]
    )

    throttle = PWMThrottle(
        controller=PCA9685(cfg.THROTTLE_CHANNEL),
        max_pulse=cfg.THROTTLE_FORWARD_PWM,
        zero_pulse=cfg.THROTTLE_STOPPED_PWM,
        min_pulse=cfg.THROTTLE_REVERSE_PWM
    )
    vehicle.add(
        throttle,
        inputs=[
            'user/throttle'
        ]
    )

    lidar = LidarScan()
    vehicle.add(
        lidar,
        outputs=[
            'lidar/scan'
        ],
        threaded=True
    )

    position = LidarPosition()
    vehicle.add(
        position,
        inputs=[
            'lidar/scan'
        ],
        outputs=[
            'lidar/position',
            'lidar/borders'
        ],
        threaded=True
    )

    mqtt_client = MQTTClient(cfg)
    vehicle.add(
        mqtt_client,
        inputs=[
            'user/mode',
            'user/angle',
            'user/throttle',
            'lidar/position',
            'lidar/borders'
        ],
        outputs=[
            'remote/mode'
        ],
        threaded=True
    )

    vehicle.start(
        rate_hz=cfg.DRIVE_LOOP_HZ,
        max_loop_count=cfg.MAX_LOOPS
    )


if __name__ == '__main__':
    args = docopt(__doc__)
    cfg = dk.load_config()
    logging.basicConfig(level=cfg.LOG_LEVEL, format=cfg.LOG_FORMAT, handlers=[logging.StreamHandler()])
    drive(cfg, model_path=args['--model'])
