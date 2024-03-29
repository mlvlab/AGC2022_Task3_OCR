
import os
import sys
import time
import argparse

import torch
import torch.nn as nn
import torch.backends.cudnn as cudnn
from torch.autograd import Variable
import torch.utils.data
import torch.nn.functional as F


import cv2
from skimage import io
import numpy as np

from .task3_utils import *
from .task3_parse_args import parse_args
from .imgproc import *
from .craft import CRAFT ## detection model
from .wiw import WIW ## recognition model

from PIL import Image

import pdb

def inter(box1, box2, margin=20):
    # box = (x1, y1, x2, y2)
    # obtain x1, y1, x2, y2 of the intersection
    x1 = max(box1[0]-margin, box2[0]-margin)
    y1 = max(box1[1]-margin , box2[1]-margin)
    x2 = min(box1[2]+margin, box2[2]+margin)
    y2 = min(box1[3]+margin, box2[3]+margin)

    # compute the width and height of the intersection
    w = max(0, x2 - x1 + 1)
    h = max(0, y2 - y1 + 1)

    inter = w * h

    return inter

def convert_bbox_and_decision(poster_box, detection_box):
    p_x_min = int(poster_box[0] - (poster_box[2])/2)
    p_x_max = int(poster_box[0] + (poster_box[2])/2)
    p_y_min = int(poster_box[1] - (poster_box[3])/2)
    p_y_max = int(poster_box[1] + (poster_box[3])/2)

    b_x_min = detection_box[0]
    b_x_max = detection_box[2]
    b_y_min = detection_box[1]
    b_y_max = detection_box[3]
    
    if (p_x_min<=b_x_min) and (p_x_max>=b_x_max) and (p_y_min<= b_y_min) and (p_y_max>=b_y_max):
        return True
    else:
        return False

class Task3:
    def __init__(self, **kwargs):
     

        self.frame_array = [] ## list of list: [[state, pred_txt, confidence_score],[],[],...,[] ]
        self.final_answer = "UNCLEAR"
        self.final_answer_confidence = 0
        self.search_margin = 100

        self.frame_candidate = []
        self.hallway_candidate = []
        self.save_dict = {}

        self.inner_answer_list = [] ## list: []
        self.inner_answer = "UNCLEAR"
        self.inner_answer_confidence = 0

        self.find_answer = False # flag - if I find an answer


        self.text_threshold = kwargs['text_threshold']
        self.link_threshold = kwargs['link_threshold']
        self.low_text = kwargs['low_text']
        self.canvas_size = kwargs['canvas_size']
        self.mag_ratio = kwargs['mag_ratio']
        self.batch_max_length = kwargs['batch_max_length']
        self.max_confidence = kwargs['max_confidence']


        # Detection model load (CRAFT) 
        craft = CRAFT()
        craft.load_state_dict(copyStateDict(torch.load(kwargs['craft_weight'])))
        craft = craft.cuda()
        self.craft = torch.nn.DataParallel(craft)
        self.craft.eval()

        
        cudnn.benchmark = True
        cudnn.deterministic = True

        if 'CTC' in kwargs['Prediction']:
            self.converter = CTCLabelConverter(kwargs['character'])
        else:
            self.converter = AttnLabelConverter(kwargs['character'])

        kwargs['num_class'] = len(self.converter.character)

        if kwargs['rgb']:
            kwargs['input_channel'] = 3
    
        # Recognition model load (WIW) #####
        wiw = WIW(**kwargs)
        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.wiw = torch.nn.DataParallel(wiw).cuda()
        self.wiw.load_state_dict(torch.load(kwargs['wiw_weight'], map_location=device))
        self.wiw.eval()

        ## other utils
        self.transform = ResizeNormalize((kwargs['imgW'], kwargs['imgH']))
        # transform = NormalizePAD((3, self.imgH, resized_max_w))
        self.img_cnt=0

    def __call__(self, image, state, poster = None, frame_for_vis=None):
        
        pred_info = [state,"UNCLEAR",0] ## frame_array: list [state, pred text, confidence score]
        self.frame_array.append(pred_info) ## 

        image = image[:, :, ::-1] # BGR to RGB
        image = np.ascontiguousarray(image)


        # Box Detection (CRAFT)
        bboxes, polys, score_text = inference(self.craft, image, self.text_threshold,\
                                    self.link_threshold, self.low_text, self.canvas_size, self.mag_ratio, False, None)
        
        image_show = image.copy()
        crop_image_list = []
        bbox_list = []

        for bbox in bboxes:
            
            x_max = int(np.max(bbox[:,0]))
            x_min = int(np.min(bbox[:,0]))

            y_max = int(np.max(bbox[:,1]))
            y_min = int(np.min(bbox[:,1]))

            cv2.rectangle(image_show,(x_min, y_min),(x_max, y_max), (0, 255 , 0), 2)

            # crop image processing
            try:
                cropped_image = image[y_min:y_max, x_min:x_max] # [H,W,3]
                cropped_img = Image.fromarray(cropped_image)
                cropped_img = cropped_img.convert('RGB') # rgb information

                cropped_img = self.transform(cropped_img).unsqueeze(0) # [1, 1, H, W] 
                crop_image_list.append(cropped_img)
                bbox_list.append((x_min, y_min, x_max, y_max))


            except:
                continue
        

        # crop_image_list
        del_idx = []
        new_bbox_list = []
        for i,current_box in enumerate(bbox_list):
            if i == len(bbox_list)-1: break
            for remain in bbox_list[i+1:]:
                if inter(current_box, remain)>0:
                    del_idx.append(bbox_list.index(current_box))
                    del_idx.append(bbox_list.index(remain))

        new_crop_image_list = []
        for idx in range(len(crop_image_list)):
            if idx not in del_idx:
                new_crop_image_list.append(crop_image_list[idx])
                new_bbox_list.append(bbox_list[idx])


        # Box processsing: For Wrapped Image
        final_crop_image = []
        final_bbox_list = []
        for idx, bbox in enumerate(new_bbox_list):
            if bbox[3] < image.shape[0]//2:
                final_crop_image.append(new_crop_image_list[idx])
                final_bbox_list.append(bbox)

        # Box processsing For Poster Text
        try:
            if poster:
                is_poster = poster['object_is_poster'].squeeze(1).detach().cpu().numpy()
                poster_bbox = poster['object_bbox'].squeeze(1).detach().cpu().numpy()
                ## poster information processing
                poster_bbox = poster_bbox[is_poster == 1]
                
                processing_image = []
                for sel_idx, final_box in enumerate(final_bbox_list):
                    tmp = True
                    for post_box in poster_bbox:
                        if convert_bbox_and_decision(post_bbox, final_box):
                            tmp = False
                    if tmp:
                        processing_image.append(final_crop_image[sel_idx])
                            
            else:
                processing_image = final_crop_image
        except:
            processing_image = final_crop_image

        # Recognition (WIW)
        confidence_score_list = []
        max_idx = -1

        if len(processing_image)!=0:
            image_tensors = torch.cat(processing_image)
            batch_size = image_tensors.size(0)
            crop_image_input = image_tensors.cuda()

            length_for_pred = torch.IntTensor([self.batch_max_length] * batch_size).cuda()
            text_for_pred = torch.LongTensor(batch_size,self.batch_max_length + 1).fill_(0).cuda()
            
            preds = self.wiw(crop_image_input, text_for_pred, is_train=False)

            ## select max probabilty (greedy decoding) then decode index to character
            _, preds_index = preds.max(2)
            preds_txt = self.converter.decode(preds_index, length_for_pred)

            ## compute confidence score 
            preds_prob = F.softmax(preds, dim=2).detach().cpu()
            preds_max_prob, _ = preds_prob.max(dim=2)
            
            for pred, pred_max_prob in zip(preds_txt, preds_max_prob):
                pred_EOS = pred.find('[s]')
                pred = pred[:pred_EOS]  # prune after "end of sentence" token ([s])
                pred_max_prob = pred_max_prob[:pred_EOS]
                if pred_EOS > 0:
                    confidence_score = pred_max_prob.cumprod(dim=0)[-1]
                    confidence_score_list.append(confidence_score)

                else:
                    confidence_score_list.append(0)

            frame_answer = "UNCLEAR" ## Init

            max_confidence = max(confidence_score_list)
            if max_confidence > self.max_confidence:
                max_idx = confidence_score_list.index(max_confidence)
                frame_answer = preds_txt[max_idx].split('[s]')[0]

                if ('x' in frame_answer) or (len(frame_answer)<3):
                    frame_answer = "UNCLEAR"
                    self.frame_array[-1][1] = frame_answer
                    self.frame_array[-1][2] = 0

                else:
                    self.frame_array[-1][1] = frame_answer
                    self.frame_array[-1][2] = max_confidence
                    self.frame_candidate.append(frame_answer)

                
        if len(self.frame_array) > 1 and self.frame_array[-2][0] < self.frame_array[-1][0]:
            end_idx = max(len(self.frame_array)-10, 0)
            start_idx = max(end_idx - self.search_margin, 0)
        
            max_value = -float('inf')
            max_idx = -1
            for idx in range(start_idx, end_idx):
                if self.frame_array[idx][2] > max_value:
                    max_value = self.frame_array[idx][2]
                    max_idx = idx

                if self.frame_array[idx][2] > self.max_confidence: # state, text, score
                    self.hallway_candidate.append(self.frame_array[idx][1])

            self.final_answer = self.frame_array[max_idx][1]
            self.final_answer_confidence = self.frame_array[max_idx][2]

            json_output = json_postprocess(self.final_answer)
            return json_output
            

        elif len(self.frame_array) >1 and self.frame_array[-2][0] > self.frame_array[-1][0]:
            self.frame_array = []
            self.final_answer = "UNCLEAR"
            self.final_answer_confidence = 0
            self.search_margin = 30

            self.frame_candidate = []
            self.hallway_candidate = []
            self.save_dict = {}

            self.inner_answer = "UNCLEAR"
            self.inner_answer_confidence = 0
            self.inner_answer_list = []
            self.find_answer = False 

            json_output = json_postprocess(self.final_answer)
            return json_output

        else:

            if self.frame_array[-1][0]<1:
                json_output = json_postprocess(self.final_answer)
                return json_output

            else:
                if len(self.frame_candidate) > 0 and len(self.hallway_candidate) > 0:
                    if self.find_answer == True:
                        json_output = json_postprocess(self.inner_answer)
                        return json_output

                    else:
                        for inner_answer in self.frame_candidate:
                            for hall_answer in self.hallway_candidate:
                                
                                if len(inner_answer) >= len(hall_answer) and hall_answer in inner_answer:
                                    self.find_answer = True
                                    self.inner_answer = inner_answer
                                    json_output = json_postprocess(self.inner_answer)
                                    return json_output

                                elif len(inner_answer) <= len(hall_answer) and inner_answer in hall_answer:
                                    self.find_answer = True
                                    self.inner_answer = hall_answer
                                    json_output = json_postprocess(self.inner_answer)
                                    return json_output

                            if inner_answer in self.save_dict.keys():
                                self.save_dict[inner_answer] += 1
                            else:
                                self.save_dict[inner_answer] = 0 

                        new_dict = sorted(self.save_dict.items(), key=lambda x: x[1], reverse=True)                
                        self.inner_answer = new_dict[0][0]
                        json_output = json_postprocess(self.inner_answer)
                        return json_output

                else:
                    if self.find_answer == True:
                        json_output = json_postprocess(self.inner_answer)
                        return json_output
                    else:
                        json_output = json_postprocess(self.final_answer)
                        return json_output


if __name__ == "__main__":

    args = parse_args()
    task3 = Task3(**vars(args))

    image_path = './Image_example/간판들.png'
    with open(image_path, 'rb') as f:
        data = f.read()
    encoded_img = np.fromstring(data, dtype = np.uint8)
    image = cv2.imdecode(encoded_img, cv2.IMREAD_COLOR) ## (H,W,3) BGR

    # pdb.set_trace()
    with torch.no_grad():
        task3(image)
