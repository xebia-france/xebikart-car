#!/usr/bin/env python3

"""
Usage:
    steeringpilot.py --model=<model_path> [--throttle=<throttle>]

Options:
    -h --help               Show this screen.
    --model=<path>          Path to h5 model (steering only) (.h5)
    --throttle=<throttle>   Fix throttle [default: 0.2]
"""

import logging
from docopt import docopt

import donkeycar as dk

from xebikart.parts import add_controller, add_throttle, add_steering, add_pi_camera, add_logger
from xebikart.parts.image import ImageTransformation
from xebikart.parts.keras import OneOutputModel

import xebikart.images.transformer as image_transformer

import tensorflow as tf

tf.compat.v1.enable_eager_execution()


def drive(cfg, args):
    vehicle = dk.vehicle.Vehicle()

    # Connect pi camera
    add_pi_camera(vehicle, cfg, 'cam/image_array')

    # Keep it for emergency stop (cross)
    add_controller(vehicle, cfg, 'user/steering', 'user/throttle', 'user/mode', 'recording')

    # Model
    model_path = args["--model"]
    add_image_transformation(vehicle, 'cam/image_array', 'transformed/image_array')
    add_detection_model(vehicle, model_path, 'transformed/image_array', 'ai/output')

    add_steering(vehicle, cfg, 'user/steering')
    add_throttle(vehicle, cfg, 'user/throttle')

    add_logger(vehicle, 'ai/output', 'ai/output')

    print("Starting vehicle...")

    vehicle.start(
        rate_hz=cfg.DRIVE_LOOP_HZ,
        max_loop_count=cfg.MAX_LOOPS
    )


def add_image_transformation(vehicle, camera_input, transformation_output):
    image_transformation = ImageTransformation([
        image_transformer.normalize,
        image_transformer.generate_crop_fn(0, 80, 160, 40),
        tf.image.rgb_to_grayscale
    ])
    vehicle.add(
        image_transformation,
        inputs=[
            camera_input
        ],
        outputs=[
            transformation_output
        ]
    )


def add_detection_model(vehicle, detection_path, image_input, ai_output):
    detection_model = OneOutputModel()
    detection_model.load(detection_path)
    vehicle.add(
        detection_model,
        inputs=[
            image_input
        ],
        outputs=[
            ai_output
        ]
    )


if __name__ == '__main__':
    args = docopt(__doc__)
    cfg = dk.load_config()
    logging.basicConfig(level=cfg.LOG_LEVEL, format=cfg.LOG_FORMAT, handlers=[logging.StreamHandler()])
    drive(cfg, args)