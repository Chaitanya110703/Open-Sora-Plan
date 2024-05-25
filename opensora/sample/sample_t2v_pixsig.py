import math
import os
import torch
import argparse
import torchvision

from diffusers.schedulers import (DDIMScheduler, DDPMScheduler, PNDMScheduler,
                                  EulerDiscreteScheduler, DPMSolverMultistepScheduler,
                                  HeunDiscreteScheduler, EulerAncestralDiscreteScheduler,
                                  DEISMultistepScheduler, KDPM2AncestralDiscreteScheduler)
from diffusers.schedulers.scheduling_dpmsolver_singlestep import DPMSolverSinglestepScheduler
from diffusers.models import AutoencoderKL, AutoencoderKLTemporalDecoder, Transformer2DModel
from omegaconf import OmegaConf
from torchvision.utils import save_image
from transformers import T5EncoderModel, T5Tokenizer, AutoTokenizer

import os, sys

from opensora.models.ae import ae_stride_config, getae, getae_wrapper
from opensora.models.ae.videobase import CausalVQVAEModelWrapper, CausalVAEModelWrapper
from opensora.models.diffusion.opensora.modeling_opensora import OpenSoraT2V
from opensora.models.text_encoder import get_text_enc
from opensora.utils.utils import save_video_grid

from opensora.sample.pipeline_opensora import OpenSoraPipeline

import imageio


def main(args):
    # torch.manual_seed(args.seed)
    weight_dtype = torch.float16
    device = torch.device(args.device)

    vae = getae_wrapper(args.ae)(args.ae_path).to(dtype=weight_dtype)
    # if args.enable_tiling:
    #     vae.vae.enable_tiling()
    #     vae.vae.tile_overlap_factor = args.tile_overlap_factor
    vae.vae_scale_factor = ae_stride_config[args.ae]

    transformer_model = OpenSoraT2V.from_pretrained(args.model_path, low_cpu_mem_usage=False, device_map=None, torch_dtype=weight_dtype)

    # ckpt = transformer_model.state_dict()
    # from safetensors.torch import load_file
    # file_path = "PixArt-Sigma-XL-2-256.safetensors"
    # ckptsf = load_file(file_path)
    # file_path = "/dxyl_code02/dev3d/Open-Sora-Plan/image_test_ckpt/diffusion_pytorch_model.bin"
    # ckptbin = torch.load(file_path)
    # # print(transformer_model)
    # for k, v in ckpt.items():
    #     assert torch.allclose(v, ckptsf[k]) and torch.allclose(v, ckptbin[k])
    # import ipdb;ipdb.set_trace()

    text_encoder = T5EncoderModel.from_pretrained(args.text_encoder_name, cache_dir=args.cache_dir, low_cpu_mem_usage=True, torch_dtype=weight_dtype)
    tokenizer = T5Tokenizer.from_pretrained(args.text_encoder_name, cache_dir=args.cache_dir)
    

    # text_encoder = T5EncoderModel.from_pretrained(r"/dxyl_code02/dev3d/Open-Sora-Plan/text_encoder", cache_dir=args.cache_dir, low_cpu_mem_usage=True, torch_dtype=weight_dtype)
    # tokenizer = T5Tokenizer.from_pretrained(r"/dxyl_code02/dev3d/Open-Sora-Plan/tokenizer", cache_dir=args.cache_dir)

    
    # set eval mode
    transformer_model.eval()
    vae.eval()
    text_encoder.eval()

    if args.sample_method == 'DDIM':  #########
        scheduler = DDIMScheduler()
    elif args.sample_method == 'EulerDiscrete':
        scheduler = EulerDiscreteScheduler()
    elif args.sample_method == 'DDPM':  #############
        scheduler = DDPMScheduler()
    elif args.sample_method == 'DPMSolverMultistep':
        scheduler = DPMSolverMultistepScheduler()
    elif args.sample_method == 'DPMSolverSinglestep':
        scheduler = DPMSolverSinglestepScheduler()
    elif args.sample_method == 'PNDM':
        scheduler = PNDMScheduler()
    elif args.sample_method == 'HeunDiscrete':  ########
        scheduler = HeunDiscreteScheduler()
    elif args.sample_method == 'EulerAncestralDiscrete':
        scheduler = EulerAncestralDiscreteScheduler()
    elif args.sample_method == 'DEISMultistep':
        scheduler = DEISMultistepScheduler()
    elif args.sample_method == 'KDPM2AncestralDiscrete':  #########
        scheduler = KDPM2AncestralDiscreteScheduler()
        
    pipeline = OpenSoraPipeline(vae=vae,
                                text_encoder=text_encoder,
                                tokenizer=tokenizer,
                                scheduler=scheduler,
                                transformer=transformer_model)
    pipeline.to(device)

    
    if not os.path.exists(args.save_img_path):
        os.makedirs(args.save_img_path)
        
    if not isinstance(args.text_prompt, list):
        args.text_prompt = [args.text_prompt]
    if len(args.text_prompt) == 1 and args.text_prompt[0].endswith('txt'):
        text_prompt = open(args.text_prompt[0], 'r').readlines()
        text_prompt = [i.strip() for i in text_prompt]

    video_grids = []
    for prompt in text_prompt:
        videos = pipeline(prompt,
                        num_frames=args.num_frames,
                        height=args.height,
                        width=args.width,
                        num_inference_steps=args.num_sampling_steps,
                        guidance_scale=args.guidance_scale,
                        num_images_per_prompt=1,
                        mask_feature=True,
                        device=args.device, 
                        max_sequence_length=100, 
                        ).images
        try:
            if args.num_frames == 1:
                ext = 'jpg'
                videos = videos[:, 0].permute(0, 3, 1, 2)  # b t h w c -> b c h w
                save_image(videos / 255.0, os.path.join(args.save_img_path,
                                                     prompt.replace(' ', '_')[:100] + f'{args.sample_method}_gs{args.guidance_scale}_s{args.num_sampling_steps}.{ext}'),
                           nrow=1, normalize=True, value_range=(0, 1))  # t c h w

            else:
                ext = 'mp4'
                imageio.mimwrite(
                    os.path.join(
                        args.save_img_path,
                        prompt.replace(' ', '_')[:100] + f'{args.sample_method}_gs{args.guidance_scale}_s{args.num_sampling_steps}.{ext}'
                    ), videos[0],
                    fps=args.fps, quality=9)  # highest quality is 10, lowest is 0
        except:
            print('Error when saving {}'.format(prompt))
        video_grids.append(videos)
    video_grids = torch.cat(video_grids, dim=0)


    # torchvision.io.write_video(args.save_img_path + '_%04d' % args.run_time + '-.mp4', video_grids, fps=6)
    if args.num_frames == 1:
        save_image(video_grids / 255.0, os.path.join(args.save_img_path, f'{args.sample_method}_gs{args.guidance_scale}_s{args.num_sampling_steps}.{ext}'),
                   nrow=math.ceil(math.sqrt(len(video_grids))), normalize=True, value_range=(0, 1))
    else:
        video_grids = save_video_grid(video_grids)
        imageio.mimwrite(os.path.join(args.save_img_path, f'{args.sample_method}_gs{args.guidance_scale}_s{args.num_sampling_steps}.{ext}'), video_grids, fps=args.fps, quality=9)

    print('save path {}'.format(args.save_img_path))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model_path", type=str, default='LanguageBind/Open-Sora-Plan-v1.0.0')
    parser.add_argument("--version", type=str, default=None, choices=[None, '65x512x512', '65x256x256', '17x256x256'])
    parser.add_argument("--num_frames", type=int, default=1)
    parser.add_argument("--height", type=int, default=512)
    parser.add_argument("--width", type=int, default=512)
    parser.add_argument("--device", type=str, default='cuda:0')
    parser.add_argument("--cache_dir", type=str, default='./cache_dir')
    parser.add_argument("--ae", type=str, default='CausalVAEModel_4x8x8')
    parser.add_argument("--ae_path", type=str, default='CausalVAEModel_4x8x8')
    parser.add_argument("--text_encoder_name", type=str, default='DeepFloyd/t5-v1_1-xxl')
    parser.add_argument("--save_img_path", type=str, default="./sample_videos/t2v")
    parser.add_argument("--guidance_scale", type=float, default=7.5)
    parser.add_argument("--sample_method", type=str, default="PNDM")
    parser.add_argument("--num_sampling_steps", type=int, default=50)
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--run_time", type=int, default=0)
    parser.add_argument("--text_prompt", nargs='+')
    parser.add_argument('--tile_overlap_factor', type=float, default=0.25)
    parser.add_argument('--enable_tiling', action='store_true')
    args = parser.parse_args()

    main(args)