from flask import Flask, request, send_file
import cv2

from img2vec_pytorch import Img2Vec
from PIL import Image
from redis import Redis
import numpy as np
import torch
import torchvision
from torchvision.models.detection import retinanet_resnet50_fpn_v2, RetinaNet_ResNet50_FPN_V2_Weights
import torchvision.transforms as transforms

from redis.commands.json.path import Path
from redis.commands.search.field import VectorField, TagField, NumericField
from redis.commands.search.query import Query
from redis.commands.search.indexDefinition import IndexDefinition, IndexType
import os
import time
import json

app = Flask(__name__)
redis = Redis()
img2vec = Img2Vec()
vector_field_name = "vectors"

# define the computation device
g_device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

threshold = 0.15

# define the torchvision image transforms
transform = transforms.Compose([
    transforms.ToTensor(),
])


# Get the model
def get_detection_model(device):
    # load the model 
    weights = RetinaNet_ResNet50_FPN_V2_Weights.COCO_V1
    model = retinanet_resnet50_fpn_v2(weights=weights)
    # load the model onto the computation device
    model = model.eval().to(device)
    return model

g_model = get_detection_model(g_device)

def get_boxes(image, model, device, detection_threshold):
    # transform the image to tensor
    image = transform(image).to(device)
    # add a batch dimension
    image = image.unsqueeze(0) 
    # get the predictions on the image
    with torch.no_grad():
        outputs = model(image) 
    # get score for all the predicted objects
    pred_scores = outputs[0]['scores'].detach().cpu().numpy()
    # get all the predicted bounding boxes
    pred_bboxes = outputs[0]['boxes'].detach().cpu().numpy()
    # get boxes above the threshold score
    boxes = pred_bboxes[pred_scores >= detection_threshold].astype(np.int32)
    return boxes

#box_points = [xmin,ymin, xmax,ymax]
def search_product(image, box_points):
    product = image.crop(box_points)
    query_vector = img2vec.get_vec(product)

    res, _ = getTopK(query_vector, 1)

    return res

@app.route('/')
def index():
    return send_file('index.html')

@app.route('/search', methods=['POST'])
def search():
    if 'image' not in request.files:
        return 'no image found', 400

    file = request.files['image']
    image = Image.open(file)



    boxes = get_boxes(image, g_model, g_device, threshold)

    print (boxes)

    res =  [(box.tolist(), search_product(image, box)) for box in boxes]
    print(res)
    return res

def getTopK(query_vector, k = 10, filter = '*'):
    q = Query(f'({filter})=>[KNN {k} @{vector_field_name} $vec_param AS dist]').sort_by(f'dist').paging(0, k)
    start = time.time()
    res = redis.ft().search(q, query_params={'vec_param': query_vector.tobytes()})
    search_time = time.time() - start

    products = [json.loads(doc.json) for doc in res.docs]
    for prod, doc in zip(products, res.docs):
        prod['distance'] = doc.dist

    return {'products': products}, search_time
