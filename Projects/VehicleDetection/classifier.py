import numpy as np
import glob
import os
import pickle
from sklearn.svm import LinearSVC
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.utils import shuffle

from VehicleDetection.processing import extract_features


def load_data():
    cwd = os.getcwd() + '/VehicleDetection/Data/'
    vehicle_folders = [
        'vehicles/GTI_Far/',
        'vehicles/GTI_Left/',
        'vehicles/GTI_MiddleClose/',
        'vehicles/GTI_Right/',
        'vehicles/KITTI_extracted/'
      ]
    non_vehicle_folders = [
        'non-vehicles/Extras/',
        'non-vehicles/GTI/'
      ]

    data = {'features': [], 'labels': [], 'v_cnt': 0, 'nv_cnt': 0}

    for folder in vehicle_folders:
        for im_path in glob.glob(cwd + folder + '*.png'):
            data['features'].append(im_path)
            data['labels'].append(1)
            data['v_cnt'] += 1

    for folder in non_vehicle_folders:
        for im_path in glob.glob(cwd + folder + '*.png'):
            data['features'].append(im_path)
            data['labels'].append(0)
            data['nv_cnt'] += 1

    data['features'] = extract_features(data['features'])
    X_scaler = StandardScaler().fit(data['features'])
    data['features'] = X_scaler.transform(data['features'])
    data['labels'] = np.array(data['labels'])
    return data


def shuffle_and_split(data):
    features, labels = shuffle(data['features'], data['labels'])
    X_train, X_test, y_train, y_test = train_test_split(features, labels)
    return X_train, X_test, y_train, y_test


def train():
    data = load_data()
    print('Loaded %d car images and %d non-car images.' % (data['v_cnt'], data['nv_cnt']))

    X_train, X_test, y_train, y_test = shuffle_and_split(data)
    print('Split the data into %d training and %d testing examples.' % (y_train.shape[0], y_test.shape[0]))
    del data

    model = LinearSVC()
    print('Training the model...')
    model.fit(X_train, y_train)

    print('Model accuracy: %0.4f' % model.score(X_test, y_test))

    with open('VehicleDetection/model.p', 'wb') as f:
        pickle.dump(model, f)
    return model
