import tensorflow as tf
import random


def read_image(path):
    tf_content = tf.io.read_file(path)
    return tf.image.decode_jpeg(tf_content, channels=3)


def normalize(tf_image):
    return tf.image.convert_image_dtype(tf_image, dtype=tf.float32)


def data_augmentation(tf_image):
    """
    - Brightness : Adjust the brightness of images by a random factor.
    - Saturation : Adjust the saturation of images by a random factor (must be RGB images)
    - Contrast : Adjust the contrast of images by a random factor.
    - Jpeg quality : Randomly changes jpeg encoding quality for inducing jpeg noise

    :param tf_image: Tensor of shape [80, 160, 3]
    :return: Tensor of shape [80, 160, 3]
    """
    # Random Brightness
    tf_image = tf.image.random_brightness(tf_image, max_delta=random.uniform(0, 1))
    # Random Saturation
    tf_image = tf.image.random_saturation(tf_image, lower=0.5, upper=1.5)
    # Random Contrast
    tf_image = tf.image.random_contrast(tf_image, lower=0.5, upper=1.5)
    return tf_image


def edges(tf_image):
    """
    - Convert rgb images to grayscale
    - Expand dimension to [1, height, width, 1]
    - Apply sobel filter
    - Squeeze dimension to [height, width, 2]
    - Select image gradient up to 0.3
    - Binarize images by setting elements to 0 or 1

    :param tf_image: Tensor of shape [80, 160, 3]
    :return: Tensor of shape [80, 160, 2]
    """
    tf_image = tf.image.rgb_to_grayscale(tf_image)
    tf_image = tf.expand_dims(tf_image, axis=0)
    tf_image = tf.image.sobel_edges(tf_image)
    tf_image = tf.squeeze(tf_image)
    return tf.where(tf_image > 0.3, tf.ones_like(tf_image), tf.zeros_like(tf_image))


def generate_crop_fn(left_margin=0, height_margin=40, width=160, height=80):
    """
    Create a crop function

    :param left_margin: Vertical coordinate of the top-left corner of the result in the input.
    :param height_margin: Horizontal coordinate of the top-left corner of the result in the input.
    :param width: Height of the result.
    :param height: Width of the result.
    :return:
    """
    def _crop(tf_image):
        return tf.image.crop_to_bounding_box(tf_image, height_margin, left_margin, height, width)
    return _crop


def generate_vae_fn(vae):
    """
    Create a function to encode image with Variational Auto Encoder model

    :param vae: tf.keras.Model
    :return:
    """
    vae_encoder = vae.get_layer('encoder')

    def _transform(tf_image):
        return tf.squeeze(vae_encoder.predict(tf.expand_dims(tf_image, 0))[2])
    return _transform
