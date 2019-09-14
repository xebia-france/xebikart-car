import numpy as np

from gym.core import Wrapper, ObservationWrapper
from gym.spaces import Box

import xebikart.images.transformer as images_transformer


class CropObservationWrapper(ObservationWrapper):
    def __init__(self, env, left_margin, top_margin, width, height):
        """
        Crop observation based on left_margin, top_margin, width and height

        :param env:
        :param left_margin:
        :param top_margin:
        :param width:
        :param height:
        """

        super(CropObservationWrapper, self).__init__(env)

        self.original_shape = self.env.observation_space.shape
        assert self.original_shape[:2] == (top_margin + height, left_margin + width)

        self.new_shape = (height, width) + self.original_shape[2:]
        self.left_margin = left_margin
        self.top_margin = top_margin
        self.width = width
        self.height = height

        self.observation_space = Box(low=0,
                                     high=255,
                                     dtype=self.env.observation_space.dtype,
                                     shape=self.new_shape)

    def observation(self, observation):
        # Crop Region of interest
        return observation[
               self.top_margin:(self.top_margin + self.height),
               self.left_margin:(self.left_margin + self.width)
        ]


class EdgingObservationWrapper(ObservationWrapper):
    def __init__(self, env):
        """
        Edging image with 3 channels using tensorflow sobel_edges.
        First, it reduces all channel in one using mean math operation.

        Then, it uses sobel_edges to returns a new images with 2 channels,
        one for vertical edges, and another one for horizontal edges

        :param env:
        """
        super(EdgingObservationWrapper, self).__init__(env)
        original_shape = self.env.observation_space.shape
        assert original_shape[2] == 3

        import tensorflow as tf
        self.sess = tf.Session()

        self.image_placeholder = tf.placeholder(tf.float32, shape=original_shape)
        tf_image = images_transformer.normalize(self.image_placeholder)
        self.tf_edging_image = images_transformer.edges(tf_image)

        self.observation_space = Box(low=0., high=1.,
                                     shape=original_shape[:-1] + (2, ),
                                     dtype=self.env.observation_space.dtype)

    def observation(self, observation):
        return self.sess.run(self.tf_edging_image, {self.image_placeholder: observation})


class VAEObservationWrapper(ObservationWrapper):
    def __init__(self, env, vae):
        """
        Apply VAE (Variational Auto Encoder) on observation.
        Based on old implementation of VAE (vae.model.ConvVAE)

        :param env:
        :param vae: vae.model.ConvVAE
        """
        super(VAEObservationWrapper, self).__init__(env)

        self.vae = vae

        original_shape = self.env.observation_space.shape
        vae_input_shape = self.vae.input_shape[1:]

        assert original_shape == vae_input_shape

        self.observation_space = Box(low=np.finfo(np.float32).min,
                                     high=np.finfo(np.float32).max,
                                     shape=(self.vae.z_size, ),
                                     dtype=np.float32)

    def observation(self, observation):
        observation = observation.astype(np.float32) / 255.
        observation = observation[None]
        return self.vae.encode(observation)


class ConvVariationalAutoEncoderObservationWrapper(ObservationWrapper):
    def __init__(self, env, vae, normalize=False):
        """
        Apply VAE (Variational Auto Encoder) on observation.
        Based on keras implementation

        :param env:
        :param vae: tensorflow.keras.model
        :param normalize: Normalize observation before encoding
        """
        super(ConvVariationalAutoEncoderObservationWrapper, self).__init__(env)

        # TODO: find a way to reuse xebipreprocessor.normalize
        self.normalize_ratio = 255. if normalize else 1.

        self.vae = vae
        self.vae_encoder = vae.get_layer('encoder')
        self.vae_encoder_z = self.vae_encoder.get_layer('z')
        self.vae_input_shape = self.vae_encoder.input_shape[1:]
        z_size = self.vae_encoder_z.output_shape[1]

        original_shape = self.env.observation_space.shape

        assert original_shape == self.vae_input_shape

        self.observation_space = Box(low=np.finfo(np.float32).min,
                                     high=np.finfo(np.float32).max,
                                     shape=(z_size, ),
                                     dtype=np.float32)

    def observation(self, observation):
        observation = np.reshape(observation, (1, ) + self.vae_input_shape) / self.normalize_ratio
        return self.vae_encoder.predict(observation)[2]


# TODO: find a way to rename max_steering_diff
# TODO: will it work ?
class HistoryBasedWrapper(Wrapper):
    def __init__(self, env, n_command_history, max_steering_diff, jerk_penalty_weight):
        """
        Add historical actions to observation and apply a penalty in case of "jerk move".
        A "jerk move" is consider each time two consecutive steering diff is higher than max_steering_diff.

        :param env:
        :param n_command_history:
        :param max_steering_diff: Max diff between two consecutive steering to consider a "jerk move"
        :param jerk_penalty_weight: Penalty to apply in case of "jerk move"
        """
        super(HistoryBasedWrapper, self).__init__(env)

        assert len(self.env.observation_space.shape) == 1, "observation_space cannot have a multidimensional shape"
        assert self.env.observation_space.dtype == np.float32
        assert n_command_history > 1, "n_command_history at least 1"

        original_shape = self.env.observation_space.shape[0]

        # Save last n commands (throttle + steering)
        self.n_commands = self.env.action_space.shape[0]
        self.n_command_history = n_command_history
        # shape (1, x) to keep same as observation
        self.command_history = np.zeros((self.n_commands * self.n_command_history))

        # max steering and penalty
        self.max_steering_diff = max_steering_diff
        self.jerk_penalty_weight = jerk_penalty_weight

        # z latent vector from the VAE (encoded input image)
        self.observation_space = Box(low=np.finfo(np.float32).min,
                                     high=np.finfo(np.float32).max,
                                     shape=(original_shape + self.n_commands * self.n_command_history,),
                                     dtype=self.env.observation_space.dtype)

    def reset(self, **kwargs):
        self.command_history = np.zeros((self.n_commands * self.n_command_history))
        observation = self.env.reset(**kwargs)
        return self.observation(observation)

    def step(self, action):
        action = self.action(action)
        observation, reward, done, info = self.env.step(action)
        return self.observation(observation), self.reward(reward), done, info

    def action(self, action):
        # Clip steering angle rate to enforce continuity
        prev_steering = self.command_history[-2]
        # Add an extra at clipping to penalty in case of bad decision
        # Take a look at reward function
        max_diff = self.max_steering_diff + 1e-5
        diff = np.clip(action[0] - prev_steering, -max_diff, max_diff)
        action[0] = prev_steering + diff

        # Update command history
        self.command_history = np.roll(self.command_history, shift=-self.n_commands)
        self.command_history[-self.n_commands:] = action
        return action

    def observation(self, observation):
        command_history_reshaped = np.reshape(self.command_history, (1, self.n_commands * self.n_command_history))
        return np.concatenate((observation, command_history_reshaped), axis=-1)

    def reward(self, reward):
        """
        Add a continuity penalty to limit jerk.
        :return: (float)
        """
        jerk_penalty = 0
        # Take only last command into account
        for i in range(1):
            steering = self.command_history[-2 * (i + 1)]
            prev_steering = self.command_history[-2 * (i + 2)]
            steering_diff = (prev_steering - steering)

            if abs(steering_diff) > self.max_steering_diff:
                error = abs(steering_diff) - self.max_steering_diff
                jerk_penalty += (1 + self.jerk_penalty_weight * error) ** 2
            else:
                jerk_penalty += 0

        # Cancel reward if the continuity constrain is violated
        if jerk_penalty > 0 and reward > 0:
            reward = 0
        return reward - jerk_penalty
