"""
USAGE
-----
This pipeline is meant to visualize the activations of a single layer in a given Keras model
on the images generated by the Udacity Self-Driving Car simulator. Given the location of the
`driving_log.csv`, it will visualize the activations for each image and generate a MP4 file
so you can see what your model believes is significant in each image for predicting the
steering angle.

###########################################################################################
### Make sure to modify the `PREPROCESSOR` and `RECTIFIER` functions to suit your model ###
###########################################################################################

I think that the example below is pretty self explanatory. If you have any questions, feel
free to reach out to me at `jpthalman@gmail.com`.

(This will output a summary for the model, from which you can get the layer name)
python visualize.py --h5 model.h5
                    --layer-names

python visualize.py --h5 model.h5
                    --log Data/driving_log.csv  (The CSV is in the `Data` subdir)
                    --layer convolution2d_4     (Chosen from the above command)
                    --fps 10                    (defaults to 15)
                    --num-frames 1000           (defaults to all)
                    --dir /home/user/projects/  (defaults to the current working directory)
                    --draw-ground-truth

### IMPORTANT ###
When choosing the layer to visualize, it is recommended to choose the POST-ACTIVATION layer.
If your activation function is not included in your convolutional layer, choose the output
of your activation layer. If this is not ideal for some reason, give it a shot, but it might
come out weird (I haven't tested it).

ARGUMENTS
---------
`--h5`:
        The filepath to the H5 file for the model.
`--log`:
        The filepath to driving_log.csv
`--layer`:
        Name of the Conv Layer of which to visualize the activations
`--fps`:
        FPS for output video
`--num-frames`:
        Option to set a stopping point for the number of frames to process.
`--dir`:
        Optional filepath to set the current working directory
`--draw-ground-truth`:
        Flag to draw the ground-truth steering angles as well as the predicted angles.
`--layer-names`:
        Flag to print out the summary of the model and stop execution
"""


import os
import pdb
import json
import sys
import argparse
import numpy as np
import cv2
import pandas as pd
from moviepy.editor import ImageSequenceClip, VideoFileClip

import keras.backend as K
from keras.models import load_model


# Force keras into testing mode so it doesn't complain
K.set_learning_phase(0)


#######################################################################################
#                                 MODIFY THESE FUNCTIONS                              #
#######################################################################################

def processor(im):
    """
    Takes in a raw image and performs every action necessary to feed it into your model.
    """
    im = im[50:135, :]
    im = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
    return cv2.resize(im, (64, 64))


def rectifier(im):
    """
    Takes an image that was fed into your model and transforms it back into the original image space.
    Note that if any cropping was performed, you should transform the image back into the cropped
    space and pad the result with zeros to get back to the original shape.
    """
    resized = cv2.resize(im, (320, 85))
    top_padding = np.zeros([50, 320])
    bot_padding = np.zeros([25, 320])
    output = np.concatenate((top_padding, resized, bot_padding))
    return output

#######################################################################################


class VisualizeActivations(object):
    def __init__(self,
                 model,
                 preprocessor,
                 rectifier,
                 epsilon=1e-7):
        """
        This class grabs the activations from a given layer of a ConvNet, averages the relevant activations
        to form a single heatmap, resizes the heatmap to the same size as the original image, and
        overlays it over the original image to show which regions were most interesting to the model.

        ### NOTE ###
        This class expects the model to be created with KERAS.

        This class is attempting to replicate the methods outlined in the below blog post and paper, which
        go into much greater detail about the methodology:

            - Blog: https://jacobgil.github.io/deeplearning/vehicle-steering-angle-visualizations
            - Paper: https://arxiv.org/pdf/1610.02391v1.pdf

        :param model: The trained Keras ConvNet model.
        :param preprocessor: The function used by the model to preprocess any images
        :param rectifier: A function to transform the heatmap back to the original image space that
            the model is looking at. Really only significant if there is any cropping or warping
            involved in the preprocessor.
        :param epsilon: Numerical stability constant.
        """
        self.model = model
        self.preprocessor = preprocessor
        self.rectifier = rectifier
        self.epsilon = epsilon

        self._layer_dict = dict([(layer.name, layer) for layer in self.model.layers])
        self._gradients_function = None

    def from_video(self,
                   infile_path,
                   outfile_path,
                   layer_name,
                   max_frames,
                   threshold=0.1,
                   draw_pred=True,
                   draw_ground_truth=False,
                   ground_truth=None,
                   line_len=50,
                   line_thk=2):
        """
        Creates a heatmap from the relevant activations of the given layer in a ConvNet, overlays it over the each frame
        of the given video, and optionally draws the real and predicted steering angles onto the image.

        Note that if the steering angles are drawn onto the image, the predicted angle will be BLUE and the ground truth
        angle will be GREEN.

        :param infile_path: Path to the video file. Must be a MP4.
        :param outfile_path: Path to save the processed video to.
        :param layer_name: The name of the layer in the Keras ConvNet to visualize.
        :param max_frames: The maximum number of frames to process.
        :param threshold: Value in (0.0, 1.0). Will remove any activation below this threshold from the heatmap.
        :param draw_pred: Boolean. Whether or not to draw the predicted steering angle onto the image
        :param draw_ground_truth: Boolean. Whether or not to draw the ground truth angle onto the image.
        :param ground_truth: Pass the ground truth angle in here if you would like it to be drawn.
        :param line_len: Length (in pixels) of the steering angle lines to draw.
        :param line_thk: Thickness of the steering angle lines to draw.
        :return: Annotated video.
        """
        if threshold == 0:
            raise ValueError('Threshold must be above zero.')

        if max_frames is None:
            max_frames = np.inf

        original = VideoFileClip(infile_path)

        frames = []
        for i, frame in enumerate(original.iter_frames()):
            if i < max_frames:
                frames.append(self.heat_map(
                    layer_name=layer_name,
                    img_path=frame,
                    threshold=threshold,
                    draw_pred=draw_pred,
                    draw_ground_truth=draw_ground_truth,
                    ground_truth=ground_truth,
                    line_len=line_len,
                    line_thk=line_thk
                ))
            else: break

        processed = ImageSequenceClip(frames, fps=original.fps)
        processed.write_videofile(outfile_path)
        return

    def heat_map(self,
                 layer_name,
                 img_path,
                 threshold=0.1,
                 draw_pred=True,
                 draw_ground_truth=False,
                 ground_truth=None,
                 line_len=50,
                 line_thk=2,
                 adaptive_grads=False):
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
        if threshold == 0:
            raise ValueError('Threshold must be above zero.')

        if os.path.exists(img_path):
            im = cv2.imread(img_path)
        else:
            raise ValueError('Image does not exist:\n%r' % img_path)

        h, w = im.shape[:2]

        processed = self.preprocessor(im)
        # Add a `batch` dimension of 1, because there is one image.
        processed = np.expand_dims(processed, 0)

        # Defines a function in Keras to grab the gradients from the model layer for the heatmap.
        # This is an expensive process, so only do it for the first frame and then reuse for
        # the rest.
        if self._gradients_function is None:
            pred_angle = K.sum(self.model.layers[-1].output)
            layer = self._layer_dict[layer_name]
            grads = K.gradients(pred_angle, layer.output)[0]

            self._gradients_function = K.function(
                [self.model.layers[0].input],
                [self.model.output, grads, pred_angle])

        # Get the activations of the model at the requested layer, the gradients at the requested
        # layer, and the predicted angle of the network.
        conv_outputs, grads_val, angle = self._gradients_function([processed])
        conv_outputs, grads_val = conv_outputs[0, ...], grads_val[0, ...]

        # Amplify the gradients that are relevant to the predicted steering angle.
        if adaptive_grads:
            grads_val = self._grad_cam_loss(grads_val, angle, threshold)

        # Create the class activation map
        cam = np.mean(grads_val*conv_outputs, axis=2)
        # Normalize to [0, 1]
        cam = (cam - np.min(cam)) / (np.max(cam) - np.min(cam))

        # Transform activation map back into original image space
        cam = self.rectifier(cam)

        heatmap = cv2.applyColorMap(np.uint8(255 * cam), cv2.COLORMAP_JET)

        if adaptive_grads:
            heatmap[cam < np.median(cam[cam > 0])] = 0

        # Apply heatmap to original image
        output = cv2.addWeighted(im, 1, heatmap, 0.4, 0)
        output = cv2.cvtColor(output, cv2.COLOR_BGR2RGB)

        if draw_pred:
            xshift = int(line_len * np.sin(np.deg2rad(25*angle)))
            yshift = -int(line_len * np.cos(np.deg2rad(25*angle)))
            output = cv2.line(output, (w//2, h), (w//2 + xshift, h + yshift), color=(0, 0, 255), thickness=line_thk)
        if draw_ground_truth:
            if ground_truth is None:
                raise ValueError('Ground truth steering angle cannot be None.')
            xshift = int(line_len * np.sin(np.deg2rad(25*ground_truth)))
            yshift = -int(line_len * np.cos(np.deg2rad(25*ground_truth)))
            output = cv2.line(output, (w//2, h), (w//2 + xshift, h + yshift), color=(0, 255, 0), thickness=line_thk)
        return output

    def _grad_cam_loss(self, gradients, angle, threshold):
        """
        If the predicted angle is positive, amplify the positive gradients. If the predicted angle is negative, amplify
        the negative gradients. If the predicted angle is close to zero, amplify the gradients which are close to zero.

        :param gradients: Think about it
        :param angle: Predicted steering angle
        :param threshold: If the predicted angle is above this threshold, assume it was turning right
            and return the relevant gradients. Vice-versa for the left.
        :return: Amplified gradients
        """
        if angle > threshold:
            return gradients
        elif angle < -threshold:
            return -gradients
        else:
            # Add numerical stability constant
            gradients += self.epsilon
            # Use gradient values as inputs to N(0, sigma**2).
            # Has the effect of amplifying the values closer to zero.
            # Sigma is calculated s.t. N(threshold) / N(0) == threshold
            sigma = -(threshold**2) / np.log(threshold)
            return np.sign(angle) * np.exp(-gradients**2 / sigma) / np.sqrt(2*np.pi)


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


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--h5', help='The filepath to the H5 file for the model.')
    parser.add_argument('--log', default=None, help='The filepath to driving_log.csv')
    parser.add_argument('--video', default=None, help='The filepath to the driving video.')
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
    sys.stdout.write('Loading the model...\n')
    model = load_model(args.h5)

    # Print layer names and exit, if requested.
    if args.layer_names:
        model.summary()
        sys.exit()

    activation = VisualizeActivations(model=model, preprocessor=processor, rectifier=rectifier)

    sys.stdout.write('Loading the data...\n')

    if args.log is not None:
        data = load_data(args.dir + 'Data/Center/', args.log)

        # Clip the number of frames, if requested.
        if args.num_frames is not None:
            data['center'] = data['center'][:int(args.num_frames)]
            data['angles'] = data['angles'][:int(args.num_frames)]

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

    elif args.video is not None:
        activation.from_video(
            infile_path=args.video,
            outfile_path='activation_heatmap.mp4',
            layer_name=args.layer,
            max_frames=args.num_frames,
            threshold=0.1,
            draw_pred=True,
            line_len=50,
            line_thk=2)

    else:
        raise ValueError('You must provide some data!')


