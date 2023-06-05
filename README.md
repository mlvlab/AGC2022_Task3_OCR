# AGC2022 Task3 OCR

1. Download Pretrained model from CRAFT and WIW github.
2. python task3.py --craft_weight ./craft_mlt_25k.pth --wiw_weight ./recognition_model.pth

We use the pretrained model for Detection and finetuned model for Recognition.


### [STAGE 1] Detection: CRAFT 
- Official Code: https://github.com/clovaai/CRAFT-pytorch
- pretrained model: https://drive.google.com/file/d/1Jk4eGD7crsqCCg9C9VjCLkMN3ze8kutZ/view

Baek, Youngmin, et al. "Character region awareness for text detection." Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition. 2019.

### [STAGE 2] Recognition: WIW
- Official Code: https://github.com/clovaai/deep-text-recognition-benchmark
- pretrained model: https://drive.google.com/file/d/1b59rXuGGmKne1AuHnkgDzoYgKeETNMv9/view?usp=share_link

Baek, Jeonghun, et al. "What is wrong with scene text recognition model comparisons? dataset and model analysis." Proceedings of the IEEE/CVF international conference on computer vision. 2019.
