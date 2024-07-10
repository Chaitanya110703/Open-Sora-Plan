import math
from einops import rearrange
import decord
from torch.nn import functional as F
import torch
from typing import Optional
import torch.utils
import torch.utils.data
import torch
from torch.utils.data import Sampler
from typing import List


IMG_EXTENSIONS = ['.jpg', '.JPG', '.jpeg', '.JPEG', '.png', '.PNG']

def is_image_file(filename):
    return any(filename.endswith(extension) for extension in IMG_EXTENSIONS)

class DecordInit(object):
    """Using Decord(https://github.com/dmlc/decord) to initialize the video_reader."""

    def __init__(self, num_threads=1):
        self.num_threads = num_threads
        self.ctx = decord.cpu(0)

    def __call__(self, filename):
        """Perform the Decord initialization.
        Args:
            results (dict): The resulting dict to be modified and passed
                to the next transform in pipeline.
        """
        reader = decord.VideoReader(filename,
                                    ctx=self.ctx,
                                    num_threads=self.num_threads)
        return reader

    def __repr__(self):
        repr_str = (f'{self.__class__.__name__}('
                    f'sr={self.sr},'
                    f'num_threads={self.num_threads})')
        return repr_str

def pad_to_multiple(number, ds_stride):
    remainder = number % ds_stride
    if remainder == 0:
        return number
    else:
        padding = ds_stride - remainder
        return number + padding

class Collate:
    def __init__(self, args):
        self.group_frame = args.group_frame
        self.group_resolution = args.group_resolution

        self.max_height = args.max_height
        self.max_width = args.max_width
        self.ae_stride = args.ae_stride
        self.ae_stride_t = args.ae_stride_t
        self.ae_stride_thw = (self.ae_stride_t, self.ae_stride, self.ae_stride)
        self.ae_stride_1hw = (1, self.ae_stride, self.ae_stride)

        self.patch_size = args.patch_size
        self.patch_size_t = args.patch_size_t
        self.patch_size_thw = (self.patch_size_t, self.patch_size, self.patch_size)
        self.patch_size_1hw = (1, self.patch_size, self.patch_size)

        self.num_frames = args.num_frames
        self.use_image_num = args.use_image_num
        self.max_thw = (self.num_frames, self.max_height, self.max_width)
        self.max_1hw = (1, self.max_height, self.max_width)

    def package(self, batch):

        batch_tubes_vid, input_ids_vid, cond_mask_vid = None, None, None
        batch_tubes_img, input_ids_img, cond_mask_img = None, None, None
        # import ipdb;ipdb.set_trace()
        if self.num_frames > 1:
            batch_tubes_vid = [i['video_data']['video'] for i in batch]  # b [c t h w]
            input_ids_vid = torch.stack([i['video_data']['input_ids'] for i in batch])  # b 1 l
            cond_mask_vid = torch.stack([i['video_data']['cond_mask'] for i in batch])  # b 1 l
        if self.num_frames == 1 or self.use_image_num != 0: 
            batch_tubes_img = [j for i in batch for j in i['image_data']['image']]  # b*num_img [c 1 h w]
            input_ids_img = torch.stack([i['image_data']['input_ids'] for i in batch])  # b image_num l
            cond_mask_img = torch.stack([i['image_data']['cond_mask'] for i in batch])  # b image_num l
        return batch_tubes_vid, input_ids_vid, cond_mask_vid, batch_tubes_img, input_ids_img, cond_mask_img

    def __call__(self, batch):
        batch_tubes_vid, input_ids_vid, cond_mask_vid, batch_tubes_img, input_ids_img, cond_mask_img = self.package(batch)

        ds_stride = self.ae_stride * self.patch_size
        t_ds_stride = self.ae_stride_t * self.patch_size_t
        if self.num_frames > 1 and self.use_image_num == 0:
            pad_batch_tubes, attention_mask = self.process(batch_tubes_vid, t_ds_stride, ds_stride, 
                                                      self.max_thw, self.ae_stride_thw, self.patch_size_thw, extra_1=True)
            # attention_mask: b t h w
            # input_ids, cond_mask = input_ids_vid.squeeze(1), cond_mask_vid.squeeze(1)  # b 1 l -> b l
            input_ids, cond_mask = input_ids_vid, cond_mask_vid  # b 1 l
        elif self.num_frames > 1 and self.use_image_num != 0:
            raise NotImplementedError
            pad_batch_tubes_vid, attention_mask_vid = self.process(batch_tubes_vid, t_ds_stride, ds_stride, 
                                                                   self.max_thw, self.ae_stride_thw, self.patch_size_thw, extra_1=True)
            # attention_mask_vid: b t h w

            pad_batch_tubes_img, attention_mask_img = self.process(batch_tubes_img, 1, ds_stride, 
                                                                   self.max_1hw, self.ae_stride_1hw, self.patch_size_1hw, extra_1=False)
            pad_batch_tubes_img = rearrange(pad_batch_tubes_img, '(b i) c 1 h w -> b c i h w', i=self.use_image_num)
            attention_mask_img = rearrange(attention_mask_img, '(b i) 1 h w -> b i h w', i=self.use_image_num)
            pad_batch_tubes = torch.cat([pad_batch_tubes_vid, pad_batch_tubes_img], dim=2)  # concat at temporal, video first
            # attention_mask_img: b num_img h w
            attention_mask = torch.cat([attention_mask_vid, attention_mask_img], dim=1)  # b t+num_img h w
            input_ids = torch.cat([input_ids_vid, input_ids_img], dim=1)  # b 1+num_img hw
            cond_mask = torch.cat([cond_mask_vid, cond_mask_img], dim=1)  # b 1+num_img hw
        else:
            # import ipdb;ipdb.set_trace()
            pad_batch_tubes_img, attention_mask_img = self.process(batch_tubes_img, 1, ds_stride, 
                                                                   self.max_1hw, self.ae_stride_1hw, self.patch_size_1hw, extra_1=False)
            pad_batch_tubes = rearrange(pad_batch_tubes_img, '(b i) c 1 h w -> b c i h w', i=1)
            attention_mask = rearrange(attention_mask_img, '(b i) 1 h w -> b i h w', i=1)
            input_ids, cond_mask = input_ids_img, cond_mask_img  # b 1 l
        
        assert not torch.any(torch.isnan(pad_batch_tubes)), 'after pad_batch_tubes'
        return pad_batch_tubes, attention_mask, input_ids, cond_mask

    def process(self, batch_tubes, t_ds_stride, ds_stride, max_thw, ae_stride_thw, patch_size_thw, extra_1):
        # pad to max multiple of ds_stride
        batch_input_size = [i.shape for i in batch_tubes]  # [(c t h w), (c t h w)]
        if self.group_frame:
            max_t = max([i[1] for i in batch_input_size])
            max_h = max([i[2] for i in batch_input_size])
            max_w = max([i[3] for i in batch_input_size])
        else:
            max_t, max_h, max_w = max_thw
        pad_max_t, pad_max_h, pad_max_w = pad_to_multiple(max_t-1+self.ae_stride_t if extra_1 else max_t, t_ds_stride), \
                                          pad_to_multiple(max_h, ds_stride), \
                                          pad_to_multiple(max_w, ds_stride)
        pad_max_t = pad_max_t + 1 - self.ae_stride_t if extra_1 else pad_max_t
        each_pad_t_h_w = [[pad_max_t - i.shape[1],
                           pad_max_h - i.shape[2],
                           pad_max_w - i.shape[3]] for i in batch_tubes]
        pad_batch_tubes = [F.pad(im,
                                 (0, pad_w,
                                  0, pad_h,
                                  0, pad_t), value=0) for (pad_t, pad_h, pad_w), im in zip(each_pad_t_h_w, batch_tubes)]
        pad_batch_tubes = torch.stack(pad_batch_tubes, dim=0)

        # make attention_mask
        # first_channel_first_frame, first_channel_other_frame = pad_batch_tubes[:, :1, :1], pad_batch_tubes[:, :1, 1:]  # first channel to make attention_mask
        # attention_mask_first_frame = F.max_pool3d(first_channel_first_frame, kernel_size=(1, *ae_stride_thw[1:]), stride=(1, *ae_stride_thw[1:]))
        # if first_channel_other_frame.numel() != 0:
        #     attention_mask_other_frame = F.max_pool3d(first_channel_other_frame, kernel_size=ae_stride_thw, stride=ae_stride_thw)
        #     attention_mask = torch.cat([attention_mask_first_frame, attention_mask_other_frame], dim=2)
        # else:
        #     attention_mask = attention_mask_first_frame
        # attention_mask = attention_mask[:, 0].bool().float()  # b t h w, do not channel

        max_tube_size = [pad_max_t, pad_max_h, pad_max_w]
        max_latent_size = [((max_tube_size[0]-1) // ae_stride_thw[0] + 1) if extra_1 else (max_tube_size[0] // ae_stride_thw[0]),
                           max_tube_size[1] // ae_stride_thw[1],
                           max_tube_size[2] // ae_stride_thw[2]]
        valid_latent_size = [[int(math.ceil((i[1]-1) / ae_stride_thw[0])) + 1 if extra_1 else int(math.ceil(i[1] / ae_stride_thw[0])),
                            int(math.ceil(i[2] / ae_stride_thw[1])),
                            int(math.ceil(i[3] / ae_stride_thw[2]))] for i in batch_input_size]
        attention_mask = [F.pad(torch.ones(i, dtype=pad_batch_tubes.dtype),
                                (0, max_latent_size[2] - i[2],
                                 0, max_latent_size[1] - i[1],
                                 0, max_latent_size[0] - i[0]), value=0) for i in valid_latent_size]
        attention_mask = torch.stack(attention_mask)  # b t h w

        # if self.group_frame:
        #     if not torch.all(torch.any(attention_mask.flatten(-2), dim=-1)):
        #         print('batch_input_size', batch_input_size)
        #         print('max_t, max_h, max_w', max_t, max_h, max_w)
        #         print('pad_max_t, pad_max_h, pad_max_w', pad_max_t, pad_max_h, pad_max_w)
        #         print('each_pad_t_h_w', each_pad_t_h_w)
        #         print('max_tube_size', max_tube_size)
        #         print('max_latent_size', max_latent_size)
        #         print('valid_latent_size', valid_latent_size)
                # import ipdb;ipdb.set_trace()
            # assert torch.all(torch.any(attention_mask.flatten(-2), dim=-1)), "skip special batch"

        # max_tube_size = [pad_max_t, pad_max_h, pad_max_w]
        # max_latent_size = [((max_tube_size[0]-1) // ae_stride_thw[0] + 1) if extra_1 else (max_tube_size[0] // ae_stride_thw[0]),
        #                    max_tube_size[1] // ae_stride_thw[1],
        #                    max_tube_size[2] // ae_stride_thw[2]]
        # max_patchify_latent_size = [((max_latent_size[0]-1) // patch_size_thw[0] + 1) if extra_1 else (max_latent_size[0] // patch_size_thw[0]),
        #                             max_latent_size[1] // patch_size_thw[1],
        #                             max_latent_size[2] // patch_size_thw[2]]
        # valid_patchify_latent_size = [[int(math.ceil((i[1]-1) / t_ds_stride)) + 1 if extra_1 else int(math.ceil(i[1] / t_ds_stride)),
        #                                int(math.ceil(i[2] / ds_stride)),
        #                                int(math.ceil(i[3] / ds_stride))] for i in batch_input_size]
        # attention_mask = [F.pad(torch.ones(i),
        #                         (0, max_patchify_latent_size[2] - i[2],
        #                          0, max_patchify_latent_size[1] - i[1],
        #                          0, max_patchify_latent_size[0] - i[0]), value=0) for i in valid_patchify_latent_size]
        # attention_mask = torch.stack(attention_mask)  # b t h w
        return pad_batch_tubes, attention_mask

def split_to_even_chunks(indices, lengths, num_chunks):
    """
    Split a list of indices into `chunks` chunks of roughly equal lengths.
    """

    if len(indices) % num_chunks != 0:
        return [indices[i::num_chunks] for i in range(num_chunks)]

    num_indices_per_chunk = len(indices) // num_chunks

    chunks = [[] for _ in range(num_chunks)]
    chunks_lengths = [0 for _ in range(num_chunks)]
    for index in indices:
        shortest_chunk = chunks_lengths.index(min(chunks_lengths))
        chunks[shortest_chunk].append(index)
        chunks_lengths[shortest_chunk] += lengths[index]
        if len(chunks[shortest_chunk]) == num_indices_per_chunk:
            chunks_lengths[shortest_chunk] = float("inf")

    return chunks

def group_frame_fun(indices, lengths):
    # sort by num_frames
    indices.sort(key=lambda i: lengths[i], reverse=True)
    return indices

def group_resolution_fun(indices):
    raise NotImplementedError
    return indices

def group_frame_and_resolution_fun(indices):
    raise NotImplementedError
    return indices

def get_length_grouped_indices(lengths, batch_size, world_size, generator=None, group_frame=False, group_resolution=False, seed=42):
    # We need to use torch for the random part as a distributed sampler will set the random seed for torch.
    if generator is None:
        generator = torch.Generator().manual_seed(seed)  # every rank will generate a fixed order but random index
    # print('lengths', lengths)
    
    indices = torch.randperm(len(lengths), generator=generator).tolist()
    # print('indices', indices)

    if group_frame and not group_resolution:
        indices = group_frame_fun(indices, lengths)
    elif not group_frame and group_resolution:
        indices = group_resolution_fun(indices)
    elif group_frame and group_resolution:
        indices = group_frame_and_resolution_fun(indices)
    # print('sort indices', indices)
    # print('sort lengths', [lengths[i] for i in indices])
    
    megabatch_size = world_size * batch_size
    megabatches = [indices[i: i + megabatch_size] for i in range(0, len(lengths), megabatch_size)]
    # print('\nmegabatches', megabatches)
    megabatches = [sorted(megabatch, key=lambda i: lengths[i], reverse=True) for megabatch in megabatches]
    megabatches_len = [[lengths[i] for i in megabatch] for megabatch in megabatches]
    # print('\nsorted megabatches', megabatches)
    # print('\nsorted megabatches_len', megabatches_len)
    megabatches = [split_to_even_chunks(megabatch, lengths, world_size) for megabatch in megabatches]
    # print('\nsplit_to_even_chunks megabatches', megabatches)
    # print('\nsplit_to_even_chunks len', [lengths[i] for megabatch in megabatches for batch in megabatch for i in batch])
    # return [i for megabatch in megabatches for batch in megabatch for i in batch]

    indices = torch.randperm(len(megabatches), generator=generator).tolist()
    shuffled_megabatches = [megabatches[i] for i in indices]
    # print('\nshuffled_megabatches', shuffled_megabatches)
    # print('\nshuffled_megabatches len', [lengths[i] for megabatch in shuffled_megabatches for batch in megabatch for i in batch])

    return [i for megabatch in shuffled_megabatches for batch in megabatch for i in batch]


class LengthGroupedSampler(Sampler):
    r"""
    Sampler that samples indices in a way that groups together features of the dataset of roughly the same length while
    keeping a bit of randomness.
    """

    def __init__(
        self,
        batch_size: int,
        world_size: int,
        lengths: Optional[List[int]] = None, 
        group_frame=False, 
        group_resolution=False, 
        generator=None,
    ):
        if lengths is None:
            raise ValueError("Lengths must be provided.")

        self.batch_size = batch_size
        self.world_size = world_size
        self.lengths = lengths
        self.group_frame = group_frame
        self.group_resolution = group_resolution
        self.generator = generator

    def __len__(self):
        return len(self.lengths)

    def __iter__(self):
        indices = get_length_grouped_indices(self.lengths, self.batch_size, self.world_size, group_frame=self.group_frame, 
                                             group_resolution=self.group_resolution, generator=self.generator)
        return iter(indices)
