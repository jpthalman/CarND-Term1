import os
import sys
import argparse
import numpy as np
import scipy
import cv2
import pandas as pd
from moviepy.editor import ImageSequenceClip

import keras.backend as K
from keras.models import model_from_json


# Force keras into testing mode so it doesn't complain
K.set_learning_phase(0)


class VisualizeActivations(object):
    def __init__(self,
                 model,
                 preprocessor,
                 rectifier,
                 epsilon=1e-7):
        """
        This class grabs the activations from a given layer of a ConvNet, averages the activations to form a single heatmap,
        resizes the heatmap to the same size as the original image, and overlays it over the original image to show which
        regions were most interesting to the model.

        :param model: The trained ConvNet model.
        :param preprocessor: The function used by the model to preprocess any images
        :param rectifier: A function to transform the heatmap back to the original image space that the model is looking
            at. Really only necessary if there is any cropping or warping involved in the preprocessor.
        :param epsilon: Numerical stability constant.
        """
        self.model = model
        self.preprocessor = preprocessor
        self.rectifier = rectifier
        self.epsilon = epsilon

        self._layer_dict = dict([(layer.name, layer) for layer in self.model.layers])
        self._gradients_function = None

    def heat_map(self,
                 layer_name,
                 img_path,
                 threshold=0.2,
                 draw_pred=True,
                 draw_ground_truth=False,
                 ground_truth=None,
                 line_len=50,
                 line_thk=2):
        """
        Creates a heatmap from the relevant activations of the given layer in a ConvNet, overlays it over the original
        image, and optionally draws the real and predicted steering angles onto the image.

        Note that if the steering angles are drawn onto the image, the predicted angle will be BLUE and the ground truth
        angle will be GREEN.

        :param layer_name: The name of the layer in the Keras ConvNet to visualize.
        :param img_path: Path to the image to analyze.
        :param threshold: Value in [0.0, 1.0). Will remove any activation below this threshold from the heatmap.
        :param draw_pred: Boolean. Whether or not to draw the predicted steering angle onto the image
        :param draw_ground_truth: Boolean. Whether or not to draw the ground truth angle onto the image.
        :param ground_truth: Pass the ground truth angle in here if you would like it to be drawn.
        :param line_len: Length (in pixels) of the steering angle lines to draw.
        :param line_thk: Thickness of the steering angle lines to draw.
        :return: Annotated image.
        """
        if os.path.exists(img_path):
            im = cv2.imread(img_path)
        else:
            raise ValueError('Image does not exist:\n%r' % img_path)

        processed = self.preprocessor(im)
        processed = np.expand_dims(processed, 0)

        w, h = im.shape[:2]

        if self._gradients_function is None:
            self._set_gradient_function(layer_name)

        conv_outputs, grads_val, angle = self._gradients_function([processed])
        conv_outputs, grads_val = conv_outputs[0, ...], grads_val[0, ...]

        class_weights = self._grad_cam_loss(grads_val, angle)

        # Create the class activation map
        cam = np.mean(class_weights*conv_outputs, axis=2)
        cam /= np.max(cam)

        # Transform activation map back into original image space
        cam = self.rectifier(cam)

        heatmap = cv2.applyColorMap(np.uint8(255 * cam), cv2.COLORMAP_JET)
        heatmap[np.where(cam < threshold)] = 0

        # Apply heatmap to original image
        rgb = cv2.cvtColor(im, cv2.COLOR_BGR2RGB)
        output = cv2.addWeighted(rgb, 1, heatmap, 0.4, 0)

        if draw_pred:
            xshift = int(line_len * np.cos(np.deg2rad(90 + 25*angle)))
            yshift = int(line_len * np.sin(np.deg2rad(90 + 25*angle)))
            output = cv2.line(output, (h//2, w), (h//2 - xshift, w - yshift), color=(0, 0, 255), thickness=line_thk)
        if draw_ground_truth:
            if ground_truth is None:
                raise ValueError('Ground truth steering angle cannot be None.')
            xshift = int(line_len * np.cos(np.deg2rad(90 + 25*ground_truth)))
            yshift = int(line_len * np.sin(np.deg2rad(90 + 25*ground_truth)))
            output = cv2.line(output, (h//2, w), (h//2 - xshift, w - yshift), color=(0, 255, 0), thickness=line_thk)
        return output

    def _set_gradient_function(self, layer_name):
        """
        Defines a function in Keras to grab the gradients from the defined layer for the heatmap.

        :param layer_name: The name of the layer to extract
        :return: None
        """
        pred_angle = K.sum(self.model.layers[-1].output)
        layer = self._layer_dict[layer_name]
        grads = K.gradients(pred_angle, layer.output)[0]

        self.gradients_function = K.function(
            [self.model.layers[0].input],
            [self.model.output, grads, pred_angle]
          )
        return

    def _grad_cam_loss(self, x, angle):
        """
        If the predicted angle is positive, amplify the positive gradients. If the predicted angle is negative, amplify
        the negative gradients.If the predicted angle is close to zero, amplify the gradients which are close to zero.

        :param x: Gradients
        :param angle: Predicted steering angle
        :return: Amplified gradients
        """
        if angle > 5.0 * scipy.pi / 180.0:
            return x
        elif angle < -5.0 * scipy.pi / 180.0:
            return -x
        else:
            x += self.epsilon
            return (1.0 / x) * np.sign(angle)


def load_data(path, file):
    """
    Opens driving_log.csv and returns center, left, right, and steering in a dictionary.

    :param path: Full file path to file
    :param file: The name of the file to load

    :return: Dictionary containing the camera file paths and steering angles.
    :rtype: Dictionary with keys = ['angles', 'center', 'left', 'right']
    """
    df = pd.read_csv(path + file, names=['CenterImage', 'LeftImage', 'RightImage', 'SteeringAngle',
                                         'Throttle', 'Break', 'Speed'])
    data = {
        'angles': df['SteeringAngle'].astype('float32').as_matrix(),
        'center': np.array([path + str(im).replace(' ', '').replace('\\', '/') for im in df['CenterImage'].as_matrix()]),
        'right': np.array([path + str(im).replace(' ', '').replace('\\', '/') for im in df['RightImage'].as_matrix()]),
        'left': np.array([path + str(im).replace(' ', '').replace('\\', '/') for im in df['LeftImage'].as_matrix()])
      }
    return data


def processor(im):
    im = im[50:135, :]
    im = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
    return cv2.resize(im, (64, 64))


def rectifier(im):
    resized = cv2.resize(im, (320, 85))
    top_padding = np.zeros([50, 320])
    bot_padding = np.zeros([25, 320])
    output = np.concatenate((top_padding, resized, bot_padding))
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--json', help='The filepath to the JSON file for the model.')
    parser.add_argument('--h5', help='The filepath to the H5 file for the model')
    parser.add_argument('--log', help='The filepath to driving_log.csv')
    parser.add_argument('--layer', help='Name of the Conv Layer of which to visualize the activations')
    parser.add_argument('--fps', default=15, help='FPS for output video')
    parser.add_argument('--num-frames', default=None,
                        help='Option to set a stopping point for the number of frames to process.')
    parser.add_argument('--dir', default=os.getcwd() + '/',
                        help='Optional filepath to set the current working directory')
    parser.add_argument('--draw-ground-truth', action='store_true',
                        help='Flag to draw the ground-truth steering angles as well as the predicted angles.')
    parser.add_argument('--layer-names', action='store_true',
                        help='Flag to print out the layer names of the model and stop execution')

    args = parser.parse_args()

    # Load the model from the args.
    with open(args.dir + args.json, 'r') as jfile:
        model = model_from_json(jfile.read())
    model.compile("adam", "mse")
    model.load_weights(args.h5)

    # Print layer names and exit, if requested.
    if args.layer_names:
        model.summary()
        sys.exit()

    activation = VisualizeActivations(model=model, preprocessor=processor, rectifier=rectifier)
    data = load_data(args.dir + 'Data/Center/', args.log)

    # Clip the number of frames, if requested.
    if args.num_frames is not None:
        data['center'] = data['center'][:args.num_frames]
        data['angles'] = data['angles'][:args.num_frames]

    # Load and process the frames
    frames = []
    for im_path, angle in zip(data['center'], data['angles']):
        frames.append(activation.heat_map(layer_name=args.layer,
                                          img_path=im_path,
                                          draw_ground_truth=args.draw_ground_truth,
                                          ground_truth=angle))

    # Convert the frames to a video and save
    video = ImageSequenceClip(frames, fps=args.fps)
    video.write_videofile('activation_heatmap.mp4')
