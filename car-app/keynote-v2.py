#!/usr/bin/env python3

"""
Usage:
    keynote.py --steering-model=<steering_model_path> --exit-model=<exit_model_path> [--color=<color>] [--throttle=<throttle>]

Options:
    -h --help                    Show this screen.
    --steering-model=<path>      Path to h5 model (steering only) (.h5)
    --exit-model=<path>          Path to tflite model (exit) (.tflite)
    --color=<color>              Color to detect in picture [default: 187,133,101]
    --throttle=<throttle>        Fix throttle [default: 0.2]
"""

import logging
from docopt import docopt

import donkeycar as dk

from xebikart.parts import (add_throttle, add_steering, add_pi_camera, add_logger,
                            add_mqtt_image_base64_publisher, add_mqtt_metadata_publisher, add_mqtt_remote_mode_subscriber,
                            add_imu_sensor, add_lidar_sensor)
from xebikart.parts.keras import OneOutputModel
from xebikart.parts.tflite import AsyncBufferedAction
from xebikart.parts.image import ImageTransformation, ExtractColorAreaInBox
from xebikart.parts.joystick import KeynoteJoystick
from xebikart.parts.buffers import Rolling
from xebikart.parts.driver import KeynoteDriverV2

import xebikart.images.transformer as image_transformer

import tensorflow as tf

tf.compat.v1.enable_eager_execution()


def drive(cfg, args):
    vehicle = dk.vehicle.Vehicle()

    # Connect pi camera
    print("Loading pi camera...")
    add_pi_camera(vehicle, cfg, 'cam/image_array')

    print("Loading joystick...")
    joystick = KeynoteJoystick(
        throttle_scale=cfg.JOYSTICK_MAX_THROTTLE,
        steering_scale=cfg.JOYSTICK_STEERING_SCALE
    )
    vehicle.add(joystick, outputs=['js/steering', 'js/throttle', 'js/actions'], threaded=True)

    # Detect color boxes
    print("Loading color boxes detector...")
    color_to_detect = [int(v) for v in args["--color"].split(",")]
    add_color_box_detector(vehicle, color_to_detect, 'cam/image_array', 'detect/box')

    # Steering model
    print("Loading steering model...")
    steering_model_path = args["--model"]
    add_steering_model(vehicle, steering_model_path, 'cam/image_array', 'detect/box', 'ai/steering')

    # Keynote driver
    print("Loading keynote driver...")
    throttle = args["--throttle"]
    driver = KeynoteDriverV2()
    vehicle.add(driver,
                inputs=['js/steering', 'js/throttle', 'js/actions',
                        'ai/steering', 'detect/box', 'exit/buffer', 'brightness/buffer'],
                outputs=['pilot/steering', 'pilot/throttle', 'pilot/mode'])

    add_steering(vehicle, cfg, 'pilot/steering')
    add_throttle(vehicle, cfg, 'pilot/throttle')

    # Add sensor
    #print("Add IMU")
    #add_imu_sensor(vehicle, cfg,
    #               car_dx="car/dx", car_dy="car/dy", car_dz="car/dz",
    #               car_tx="car/tx", car_ty="car/ty", car_tz="car/tz")

    #print("Add LIDAR")
    #add_lidar_sensor(vehicle, cfg,
    #                 car_x="car/x", car_y="car/y", car_z="car/z", car_angle="car/angle")

    print("Log to rabbitmq")
    add_mqtt_image_base64_publisher(vehicle, cfg, 'cam/image_array')
    add_mqtt_metadata_publisher(vehicle, cfg,
                                steering="pilot/steering", throttle="pilot/throttle", mode="pilot/mode",
                                car_x="car/x", car_y="car/y", car_z="car/z", car_angle="car/angle",
                                car_dx="car/dx", car_dy="car/dy", car_dz="car/dz",
                                car_tx="car/tx", car_ty="car/ty", car_tz="car/tz")
    add_mqtt_remote_mode_subscriber(vehicle, cfg, 'mqtt/mode')

    #add_logger(vehicle, 'detect/_sum', 'detect/_sum')
    #add_logger(vehicle, 'mqtt/mode', 'mqtt/mode')

    print("Starting vehicle...")
    vehicle.start(
        rate_hz=cfg.DRIVE_LOOP_HZ,
        max_loop_count=cfg.MAX_LOOPS
    )

def add_color_box_detector(vehicle, color_to_detect, camera_input, detect_model_output):
    # Get color box from image
    detection_model = ExtractColorAreaInBox(color_to_detect=color_to_detect, epsilon=30, nb_pixel_min=10)
    vehicle.add(detection_model, inputs=[camera_input], outputs=[detect_model_output])


def add_steering_model(vehicle, steering_path, camera_input, box_detection_input, steering_model_output):
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