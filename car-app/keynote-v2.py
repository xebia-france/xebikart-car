#!/usr/bin/env python3

"""
Usage:
    keynote-v2.py [--steering-model=<steering_model_path>] [--exit-model=<exit_model_path>] [--throttle=<throttle>]

Options:
    -h --help                    Show this screen.
    --steering-model=<path>      Path to h5 model (steering only) (.h5)
    --exit-model=<path>          Path to tflite model (exit) (.tflite)
    --throttle=<throttle>        Fix throttle [default: 0.2]
"""

import os
import logging
from docopt import docopt

import donkeycar as dk

from xebikart.parts import (add_throttle, add_steering, add_pi_camera, add_logger,
                            add_mqtt_image_base64_publisher, add_mqtt_metadata_publisher,
                            add_mqtt_remote_mode_subscriber, add_brightness_detector)
from xebikart.parts.tflite import AsyncBufferedAction
from xebikart.parts.image import ImageTransformation
from xebikart.parts.joystick import Joystick
from xebikart.parts.keras import OneOutputModel
from xebikart.parts.lidar import LidarScan, LidarDistancesVector, LidarPosition

import xebikart.images.transformer as image_transformer

import tensorflow as tf

import numpy as np

tf.compat.v1.enable_eager_execution()


def drive(cfg, args):
    vehicle = dk.vehicle.Vehicle()

    # Connect pi camera
    print("Loading pi camera...")
    add_pi_camera(vehicle, cfg, 'cam/image_array')

    print("Loading joystick...")
    joystick = Joystick(
        throttle_scale=cfg.JOYSTICK_MAX_THROTTLE,
        steering_scale=cfg.JOYSTICK_STEERING_SCALE
    )
    vehicle.add(joystick, outputs=['js/steering', 'js/throttle', 'js/actions'], threaded=True)

    # Add lidar scan
    print("Loading Lidar scan...")
    lidar_scan = LidarScan()
    lidar_distances_vector = LidarDistancesVector()
    lidar_position = LidarPosition()
    vehicle.add(lidar_scan, outputs=['lidar/scan'], threaded=True)
    vehicle.add(lidar_distances_vector, inputs=['lidar/scan'], outputs=['lidar/distances'])
    vehicle.add(lidar_position, inputs=['lidar/scan'], outputs=['lidar/position', 'lidar/borders'], threaded=True)

    # Steering model
    print("Loading steering model...")
    steering_model_path = args["--steering-model"]
    steering_model_path = steering_model_path if steering_model_path is not None else os.path.expandvars(
        "$HOME/models/steering_v2.h5")
    add_steering_model(vehicle, steering_model_path, 'cam/image_array', 'ai/steering')

    # Exit model
    print("Loading exit model...")
    exit_model_path = args["--exit-model"]
    exit_model_path = exit_model_path if exit_model_path is not None else os.path.expandvars("$HOME/models/exit.tflite")
    add_exit_model(vehicle, exit_model_path, 'cam/image_array', 'exit/buffer')

    # Brightness
    print("Loading brightness detector...")
    brightness_buffer_size = 10
    add_brightness_detector(vehicle, brightness_buffer_size, 'cam/image_array', 'brightness/buffer')

    # RabbitMQ
    print("Log to rabbitmq")
    add_mqtt_image_base64_publisher(vehicle, cfg, cfg.RABBITMQ_VIDEO_TOPIC, cfg.CAR_ID, 'cam/image_array')
    add_mqtt_metadata_publisher(vehicle, cfg, cfg.RABBITMQ_TOPIC, cfg.CAR_ID,
                                steering="pilot/steering", throttle="pilot/throttle", mode="pilot/mode")
    add_mqtt_remote_mode_subscriber(vehicle, cfg, cfg.RABBITMQ_MODES_TOPIC, cfg.CAR_ID, 'mqtt/mode')

    # Keynote driver
    print("Loading keynote driver...")
    throttle = float(args["--throttle"])
    driver = KeynoteDriverV2(default_throttle=throttle, max_throttle=cfg.JOYSTICK_MAX_THROTTLE,
                             exit_threshold=1., brightness_threshold=50000 * brightness_buffer_size)
    vehicle.add(driver,
                inputs=['js/steering', 'js/throttle', 'js/actions', 'mqtt/mode', 'ai/steering', 'lidar/distances', 'exit/buffer', 'brightness/buffer'],
                outputs=['pilot/steering', 'pilot/throttle', 'pilot/mode'])

    add_steering(vehicle, cfg, 'pilot/steering')
    add_throttle(vehicle, cfg, 'pilot/throttle')

    #add_logger(vehicle, 'pilot/throttle', 'pilot/throttle')
    #add_logger(vehicle, 'lidar/distances', 'lidar/distances')

    print("Starting vehicle...")
    vehicle.start(
        rate_hz=cfg.DRIVE_LOOP_HZ,
        max_loop_count=cfg.MAX_LOOPS
    )


class KeynoteDriverV2:
    def __init__(self, default_throttle, max_throttle, exit_threshold, brightness_threshold):
        self.default_throttle = default_throttle
        self.current_throttle = self.default_throttle
        self.exit_threshold = exit_threshold
        self.brightness_threshold = brightness_threshold
        self.emergency_sequence = [-max_throttle, 0.01] + ([-max_throttle] * 5) + ([0.] * 20)
        self.current_emergency_sequence = []
        self.safe_mode = True
        self.user_mode = False

    def is_emergency_mode(self):
        return len(self.current_emergency_sequence) > 0

    def initiate_emergency_mode(self):
        self.safe_mode = True
        self.user_mode = False
        self.current_throttle = self.default_throttle
        self.current_emergency_sequence = self.emergency_sequence.copy()

    def has_obstacle(self, measures, start_range, end_range, distance, size):
        measures = [m < distance for m in measures[start_range:end_range]]
        max_size = 0
        current_size = 0
        while len(measures) > 0:
            m = measures.pop()
            if m:
                current_size += 1
                if current_size > max_size:
                    max_size = current_size
            else:
                current_size = 0
        return size <= max_size

    def run(self, user_steering, user_throttle, user_buttons, mq_action, ai_steering, lidar_distances, exit_buffer, brightness_buffer):
        if self.is_emergency_mode():
            return 0., self.current_emergency_sequence.pop(0), "emergency_stop"
        elif self.safe_mode:
            if Joystick.SQUARE in user_buttons:
                self.safe_mode = False
                self.user_mode = True
            elif mq_action == "ai":
                self.safe_mode = False
                self.user_mode = False
            return user_steering, user_throttle, "safe_mode"
        elif not self.user_mode:
            if (Joystick.CROSS in user_buttons
                    or mq_action == "stop"
                    or np.sum(exit_buffer) > self.exit_threshold
                    or np.sum(brightness_buffer) < self.brightness_threshold
                    or self.has_obstacle(lidar_distances, 135, 225, 600, 3)):
                self.initiate_emergency_mode()
            if Joystick.R1 in user_buttons or mq_action == "faster":
                self.current_throttle += 0.01
            if Joystick.L1 in user_buttons or mq_action == "slower":
                self.current_throttle -= 0.01
            return ai_steering, self.current_throttle, "ai_v1_mode"
        elif self.user_mode:
            if Joystick.SELECT in user_buttons or mq_action == "ai":
                self.user_mode = False
            if (Joystick.CROSS in user_buttons
                    or mq_action == "stop"
                    or np.sum(exit_buffer) > 0.5
                    or np.sum(brightness_buffer) < self.brightness_threshold):
                self.initiate_emergency_mode()
            return user_steering, user_throttle, "user"


def add_exit_model(vehicle, exit_model_path, camera_input, exit_model_output):
    image_transformation = ImageTransformation([
        image_transformer.normalize,
        image_transformer.generate_crop_fn(0, 40, 160, 80),
        tf.image.rgb_to_grayscale
    ])
    vehicle.add(image_transformation, inputs=[camera_input], outputs=['exit/_image'])
    # Predict on transformed image
    exit_model = AsyncBufferedAction(model_path=exit_model_path, buffer_size=4, rate_hz=20.)
    vehicle.add(exit_model, inputs=['exit/_image'], outputs=[exit_model_output], threaded=True)


def add_steering_model(vehicle, steering_path, camera_input, steering_model_output):
    image_transformation = ImageTransformation([
        image_transformer.normalize,
        image_transformer.generate_crop_fn(0, 40, 160, 80),
        image_transformer.edges
    ])
    vehicle.add(image_transformation, inputs=[camera_input], outputs=['ai/_image'])
    # Predict on transformed image
    steering_model = OneOutputModel()
    steering_model.load(steering_path)
    vehicle.add(steering_model, inputs=['ai/_image'], outputs=[steering_model_output])


if __name__ == '__main__':
    args = docopt(__doc__)
    cfg = dk.load_config()
    logging.basicConfig(level=cfg.LOG_LEVEL, format=cfg.LOG_FORMAT, handlers=[logging.StreamHandler()])
    drive(cfg, args)
