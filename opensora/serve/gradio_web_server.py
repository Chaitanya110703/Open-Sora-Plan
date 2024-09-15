import gradio as gr
import os
import torch
from einops import rearrange
import torch.distributed as dist
from torchvision.utils import save_image
import imageio
import math
import argparse
import random
import numpy as np


from opensora.sample.caption_refiner import OpenSoraCaptionRefiner
from opensora.utils.sample_utils import (
    prepare_pipeline, save_video_grid
)

LOGO = """
    <center><img src='https://s21.ax1x.com/2024/07/14/pk5pLBF.jpg' alt='Open-Sora Plan logo' style="width:220px; margin-bottom:1px"></center>
"""
TITLE = """
    <div style="text-align: center; font-size: 45px; font-weight: bold; margin-bottom: 5px;">
        Open-Sora Plan🤗
    </div>
"""
DESCRIPTION = """
    <div style="text-align: center; font-size: 16px; font-weight: bold; margin-bottom: 5px;">
        Support Chinese and English; 支持中英双语
    </div>
    <div style="text-align: center; font-size: 16px; font-weight: bold; margin-bottom: 5px;">
        Welcome to Star🌟 our <a href='https://github.com/PKU-YuanGroup/Open-Sora-Plan' target='_blank'><b>GitHub</b></a>
    </div>
"""

NUM_IMAGES_PER_PROMPT = 1
MAX_SEED = np.iinfo(np.int32).max
def randomize_seed_fn(seed: int, randomize_seed: bool) -> int:
    if randomize_seed:
        seed = random.randint(0, MAX_SEED)
    return seed

@torch.no_grad()
@torch.inference_mode()
def generate(
        prompt: str,
        seed: int = 0,
        num_frames: int = 29, 
        num_samples: int = 1, 
        guidance_scale: float = 4.5,
        num_inference_steps: int = 25,
        randomize_seed: bool = False,
        progress=gr.Progress(track_tqdm=True),
):
    seed = int(randomize_seed_fn(seed, randomize_seed))
    if seed is not None:
        torch.manual_seed(seed)
    if not os.path.exists(args.save_img_path):
        os.makedirs(args.save_img_path, exist_ok=True)

    video_grids = []
    text_prompt = [prompt]

    positive_prompt = """
    high quality, high aesthetic, {}
    """

    negative_prompt = """
    nsfw, lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, 
    low quality, normal quality, jpeg artifacts, signature, watermark, username, blurry.
    """

    for index, prompt in enumerate(text_prompt):
        if caption_refiner_model is not None:
            refine_prompt = caption_refiner_model.get_refiner_output(prompt)
            print(f'\nOrigin prompt: {prompt}\n->\nRefine prompt: {refine_prompt}')
            prompt = refine_prompt
        input_prompt = positive_prompt.format(prompt)
        videos = pipeline(
            input_prompt, 
            negative_prompt=negative_prompt, 
            num_frames=num_frames,
            height=352,
            width=640,
            motion_score=0.9, 
            num_inference_steps=num_inference_steps,
            guidance_scale=guidance_scale,
            num_samples_per_prompt=num_samples,
            max_sequence_length=512,
            ).videos
        if num_frames != 1 and enhance_video_model is not None:
            # b t h w c
            videos = enhance_video_model.enhance_a_video(videos, input_prompt, 2.0, args.fps, 250)
        if num_frames == 1:
            videos = rearrange(videos, 'b t h w c -> (b t) c h w')
            if num_samples != 1:
                for i, image in enumerate(videos):
                    save_image(
                        image / 255.0, 
                        os.path.join(
                            args.save_img_path, 
                            f'{args.sample_method}_{index}_gs{guidance_scale}_s{num_inference_steps}_i{i}.jpg'
                            ),
                        nrow=math.ceil(math.sqrt(videos.shape[0])), 
                        normalize=True, 
                        value_range=(0, 1)
                        )  # b c h w
            save_image(
                videos / 255.0, 
                os.path.join(
                    args.save_img_path, 
                    f'{args.sample_method}_{index}_gs{guidance_scale}_s{num_inference_steps}.jpg'
                    ),
                nrow=math.ceil(math.sqrt(videos.shape[0])), 
                normalize=True, 
                value_range=(0, 1)
                )  # b c h w
        else:
            if num_samples == 1:
                imageio.mimwrite(
                    os.path.join(
                        args.save_img_path,
                        f'{args.sample_method}_{index}_gs{guidance_scale}_s{num_inference_steps}.mp4'
                    ), 
                    videos[0],
                    fps=args.fps, 
                    quality=6
                    )  # highest quality is 10, lowest is 0
            else:
                for i in range(num_samples):
                    imageio.mimwrite(
                        os.path.join(
                            args.save_img_path,
                            f'{args.sample_method}_{index}_gs{guidance_scale}_s{num_inference_steps}_i{i}.mp4'
                        ), videos[i],
                        fps=args.fps, 
                        quality=6
                        )  # highest quality is 10, lowest is 0
                    
                videos = save_video_grid(videos)
                imageio.mimwrite(
                    os.path.join(
                        args.save_img_path,
                        f'{args.sample_method}_{index}_gs{guidance_scale}_s{num_inference_steps}.mp4'
                    ), 
                    videos,
                    fps=args.fps, 
                    quality=6
                    )  # highest quality is 10, lowest is 0)
                videos = videos.unsqueeze(0) # 1 t h w c
        video_grids.append(videos)

    video_grids = torch.cat(video_grids, dim=0)
    
    final_path = os.path.join(
                    args.save_img_path,
                    f'{args.sample_method}_gs{guidance_scale}_s{num_inference_steps}'
                    )
    if num_frames == 1:
        final_path = final_path + '.jpg'
        save_image(
            video_grids / 255.0, 
            final_path, 
            nrow=math.ceil(math.sqrt(len(video_grids))), 
            normalize=True, 
            value_range=(0, 1)
            )
    else:
        video_grids = save_video_grid(video_grids)
        final_path = final_path + '.mp4'
        imageio.mimwrite(
            final_path, 
            video_grids, 
            fps=args.fps, 
            quality=6
            )
    print('save path {}'.format(args.save_img_path))
    return final_path, seed



parser = argparse.ArgumentParser()
parser.add_argument("--model_path", type=str, default='LanguageBind/Open-Sora-Plan-v1.0.0')
parser.add_argument("--version", type=str, default='v1_2', choices=['v1_2', 'v1_5'])
parser.add_argument("--caption_refiner", type=str, default=None)
parser.add_argument("--ae", type=str, default='CausalVAEModel_4x8x8')
parser.add_argument("--ae_path", type=str, default='CausalVAEModel_4x8x8')
parser.add_argument("--text_encoder_name_1", type=str, default='DeepFloyd/t5-v1_1-xxl')
parser.add_argument("--text_encoder_name_2", type=str, default=None)
parser.add_argument("--save_img_path", type=str, default="./sample_videos/t2v")
parser.add_argument("--fps", type=int, default=24)
parser.add_argument('--enable_tiling', action='store_true')
parser.add_argument('--save_memory', action='store_true')
parser.add_argument('--compile', action='store_true') 
args = parser.parse_args()

args.model_path = "/storage/ongoing/new/7.19anyres/Open-Sora-Plan/bs32x8x1_anyx93x640x640_fps16_lr1e-5_snr5_ema9999_sparse1d4_dit_l_mt5xxl_vpred_zerosnr/checkpoint-144000/model_ema"
args.version = "v1_2"
args.caption_refiner = "/storage/ongoing/refine_model/llama3_1_instruct_lora/llama3_8B_lora_merged_cn"
args.ae = "WFVAEModel_D8_4x8x8"
args.ae_path = "/storage/lcm/Causal-Video-VAE/results/WFVAE_DISTILL_FORMAL"
args.text_encoder_name_1 = "/storage/ongoing/new/Open-Sora-Plan/cache_dir/mt5-xxl"
args.text_encoder_name_2 = None
args.save_img_path = "./test_gradio"
args.fps = 18

args.enhance_video = "/storage/ongoing/new/VEnhancer/ckpts/venhancer_v2.pt"
args.prediction_type = "v_prediction"
args.rescale_betas_zero_snr = True
args.cache_dir = "./cache_dir"
args.sample_method = 'EulerAncestralDiscrete'

dtype = torch.bfloat16

device = torch.cuda.current_device()

if args.enhance_video is not None:
    from opensora.sample.VEnhancer.enhance_a_video import VEnhancer
    enhance_video_model = VEnhancer(model_path=args.enhance_video, version='v2', device=device)
else:
    enhance_video_model = None

pipeline = prepare_pipeline(args, dtype, device)
if args.caption_refiner is not None:
    caption_refiner_model = OpenSoraCaptionRefiner(args, dtype, device)
else:
    caption_refiner_model = None

with gr.Blocks(css="style.css") as demo:
    gr.Markdown(LOGO)
    gr.Markdown(TITLE)
    gr.Markdown(DESCRIPTION)

    with gr.Row(equal_height=False):
        with gr.Group():
            with gr.Row():
                seed = gr.Slider(
                    label="Seed",
                    minimum=0,
                    maximum=MAX_SEED,
                    step=1,
                    value=0,
                )
            randomize_seed = gr.Checkbox(label="Randomize seed", value=True)
            with gr.Row():
                num_frames = gr.Slider(
                        label="Num Frames",
                        minimum=29,
                        maximum=93,
                        step=16,
                        value=29,
                    )
                num_samples = gr.Slider(
                        label="Num Samples",
                        minimum=1,
                        maximum=4,
                        step=1,
                        value=1,
                    )
            with gr.Row():
                guidance_scale = gr.Slider(
                    label="Guidance scale",
                    minimum=1,
                    maximum=10,
                    step=0.1,
                    value=7.5,
                )
                inference_steps = gr.Slider(
                    label="Inference steps",
                    minimum=10,
                    maximum=200,
                    step=1,
                    value=50,
                )
        with gr.Group():
            with gr.Row():
                prompt = gr.Text(
                    label="Prompt",
                    show_label=False,
                    max_lines=1,
                    placeholder="Enter your prompt",
                    container=False,
                )
                run_button = gr.Button("Run", scale=0)
            result = gr.Video(label="Result")
            # result = gr.Gallery(label="Result", columns=NUM_IMAGES_PER_PROMPT,  show_label=False)
    # gr.Examples(
    #     examples=examples,
    #     inputs=prompt,
    #     outputs=[result, seed],
    #     fn=generate,
    #     cache_examples=CACHE_EXAMPLES,
    # )


    gr.on(
        triggers=[
            prompt.submit,
            run_button.click,
        ],
        fn=generate,
        inputs=[
            prompt,
            seed,
            num_frames, 
            num_samples, 
            guidance_scale,
            inference_steps,
            randomize_seed,
        ],
        outputs=[result, seed],
        api_name="run",
    )



if __name__ == "__main__":
    gradio_port = 11900
    demo.queue(max_size=20).launch(
        server_name="0.0.0.0", 
        server_port=gradio_port, 
        debug=True
        )