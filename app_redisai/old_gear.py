import time
import io
import base64
from PIL import Image
import numpy as np
import json

import torch
from torchvision import transforms
from yolov5.utils.general import non_max_suppression

import redisAI  # The extension that allows using RedisAI integration

# Helper methods for preprocessing the items.
scaler = transforms.Resize((224, 224))
normalize = transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
to_tensor = transforms.ToTensor()


def getTopK(query_vector, k=10, filter='*'):
    start = time.time()
    # Call the Redis(search) command that searches for top k similar items in the database.
    res = execute('FT.SEARCH', 'idx', f'({filter})=>[KNN {k} @vectors $vec_param AS distance]', 'SORTBY', 'distance',
            'RETURN', 8, 'id', 'brand', 'name', 'family', 'distance', '$.images[0].url', 'AS', 'image', 'PARAMS', 2,
            'vec_param', query_vector.tobytes(), 'DIALECT', 2)
    search_time = time.time() - start

    # Convert the raw response into a list of dictionaries (each represents a single product)
    res_as_list_of_dict = []
    for i in range(2, len(res), 2):
        res_as_dict = {res[i][j]: res[i][j+1] for j in range(0, len(res[i]), 2)}
        res_as_list_of_dict.append(res_as_dict)
    return res_as_list_of_dict, search_time


# box_points = [xmin,ymin, xmax,ymax]
async def search_product(image, box_points, device):
    # Prepare the detected object, convert into tensor input to RedisAI
    start = time.time()
    product = image.crop(box_points)
    product = normalize(to_tensor(scaler(product))).unsqueeze(0).to(device).numpy()
    img_tensor = redisAI.createTensorFromBlob('FLOAT', product.shape, product.tobytes())
    model_runner = redisAI.createModelRunner('encoding_model')
    redisAI.modelRunnerAddInput(model_runner, 'image', img_tensor)
    redisAI.modelRunnerAddOutput(model_runner, 'embedding')

    # Run the model asynchronously in RedisAI to get the embedding.
    embedding_res = await redisAI.modelRunnerRunAsync(model_runner)
    redisAI.setTensorInKey(f'out', embedding_res[0])
    _, data_type, _, shape, _, blob = execute("AI.TENSORGET", f"out")
    query_vector = np.ndarray(shape, dtype=np.float32, buffer=blob)
    print(f"generate embedding time is: {time.time()-start}")

    res, search_time = getTopK(query_vector, 4)
    log(f'KNN search time: {search_time}')
    return res


# args: [image, conf_threshold, overlap_threshold, max_det]
async def run_flow(args):
    device = torch.device('cpu')  # can change to 'cuda'
    # Deserialize the encoded image.
    image = Image.open(io.BytesIO(base64.b64decode((args[1]))))
    # Run the detection model to get the bounding boxes of objects in the image.
    boxes = [[106.43870544433594, 37.45063781738281, 505.6641845703125, 474.35052490234375]]

    return json.dumps({
        'results': [
            {
                'box': box,
                'products': await search_product(image, box, device)
            } for box in boxes
        ]
    })

gb = GB('CommandReader')
gb.map(run_flow)
gb.register(trigger='RunSearchFlow')
