#!/usr/bin/env python3
# Copyright (c) 2022 Lincoln D. Stein (https://github.com/lstein)

import argparse
import shlex
import os
import re
import sys
import copy
import warnings
import time
from ldm.dream.devices import choose_torch_device
import ldm.dream.readline
from ldm.dream.pngwriter import PngWriter, PromptFormatter
from ldm.dream.server import DreamServer, ThreadingDreamServer
from ldm.dream.image_util import make_grid

def main(command, nums=1):
    """Initialize command-line parsers and the diffusion model"""
    arg_parser = create_argv_parser()
    opt = arg_parser.parse_args()
    
   
    # some defaults suitable for stable diffusion weights
    width = 512
    height = 512
    config = 'configs/stable-diffusion/v1-inference.yaml'
    if '.ckpt' in opt.weights:
        weights = opt.weights
    else:
        weights = f'models/ldm/stable-diffusion-v1/{opt.weights}.ckpt'

    print('* Initializing, be patient...\n')
    sys.path.append('.')
    from pytorch_lightning import logging
    from ldm.simplet2i import T2I

    # these two lines prevent a horrible warning message from appearing
    # when the frozen CLIP tokenizer is imported
    import transformers

    transformers.logging.set_verbosity_error()

    # creating a simple text2image object with a handful of
    # defaults passed on the command line.
    # additional parameters will be added (or overriden) during
    # the user input loop
    t2i = T2I(
        width=width,
        height=height,
        sampler_name=opt.sampler_name,
        weights=weights,
        full_precision=opt.full_precision,
        config=config,
        grid  = opt.grid,
        # this is solely for recreating the prompt
        latent_diffusion_weights=opt.laion400m,
        embedding_path="logs/key2022-09-03T07-49-32_my_key/checkpoints/embeddings.pt",
        device_type=opt.device
    )

    # make sure the output directory exists
    if not os.path.exists(opt.outdir):
        os.makedirs(opt.outdir)

    # gets rid of annoying messages about random seed
    logging.getLogger('pytorch_lightning').setLevel(logging.ERROR)

    # load the infile as a list of lines
    infile = None
    if opt.infile:
        try:
            if os.path.isfile(opt.infile):
                infile = open(opt.infile, 'r', encoding='utf-8')
            elif opt.infile == '-':  # stdin
                infile = sys.stdin
            else:
                raise FileNotFoundError(f'{opt.infile} not found.')
        except (FileNotFoundError, IOError) as e:
            print(f'{e}. Aborting.')
            sys.exit(-1)

    # preload the model
    tic = time.time()
    t2i.load_model()
    print(
        f'>> model loaded in', '%4.2fs' % (time.time() - tic)
    )

    if not infile:
        print(
            "\n* Initialization done! Awaiting your command (-h for help, 'q' to quit)"
        )

    cmd_parser = create_cmd_parser()
    main_loop(t2i, opt.outdir, opt.prompt_as_dir, cmd_parser, infile, command, nums)


def main_loop(t2i, outdir, prompt_as_dir, parser, infile, command, nums=1):
    """prompt/read/execute loop"""
    # skip empty lines
    try:
        elements = shlex.split(command)
    except ValueError as e:
        print(str(e))
        return 

    if elements[0].startswith(
        '!dream'
    ):   # in case a stored prompt still contains the !dream command
        elements.pop(0)

    # rearrange the arguments to mimic how it works in the Dream bot.
    switches = ['']
    switches_started = False

    for el in elements:
        if el[0] == '-' and not switches_started:
            switches_started = True
        if switches_started:
            switches.append(el)
        else:
            switches[0] += el
            switches[0] += ' '
    switches[0] = switches[0][: len(switches[0]) - 1]

    try:
        opt = parser.parse_args(switches)
    except SystemExit:
        parser.print_help()
        return
    if len(opt.prompt) == 0:
        print('Try again with a prompt!')
        return
    if opt.seed is not None and opt.seed < 0:   # retrieve previous value!
        try:
            opt.seed = last_seeds[opt.seed]
            print(f'reusing previous seed {opt.seed}')
        except IndexError:
            print(f'No previous seed at position {opt.seed} found')
            opt.seed = None

    normalized_prompt = PromptFormatter(t2i, opt).normalize_prompt()
    do_grid           = opt.grid or t2i.grid
    do_grid = True
    individual_images = not do_grid

    current_outdir = outdir
    file_writer = PngWriter(current_outdir)
    prefix = file_writer.unique_prefix()
    seeds = set()
    results = []
    grid_images = dict() # seed -> Image, only used if `do_grid`
    # Here is where the images are actually generated!
    for i in range(0,nums):
        try:
            def image_writer(image, seed, upscaled=False):
                if do_grid:
                    grid_images[seed] = image
                else:
                    if upscaled and opt.save_original:
                        filename = f'{prefix}.{seed}.postprocessed.png'
                    else:
                        filename = f'{prefix}.{seed}.png'
                    path = file_writer.save_image_and_prompt_to_png(image, f'{normalized_prompt} -S{seed}', filename)
                    if (not upscaled) or opt.save_original:
                        # only append to results if we didn't overwrite an earlier output
                        results.append([path, seed])

                seeds.add(seed)

            t2i.prompt2image(image_callback=image_writer, **vars(opt))
        except AssertionError as e:
            print(e)
            return 

        except OSError as e:
            print(e)
            return
    if do_grid and len(grid_images) > 0:
        grid_img = make_grid(list(grid_images.values()))
        first_seed = next(iter(seeds))
        filename = f'{prefix}.{first_seed}.png'
        # TODO better metadata for grid images
        metadata_prompt = f'{normalized_prompt} -S{first_seed}'
        path = file_writer.save_image_and_prompt_to_png(
            grid_img, metadata_prompt, filename
        )
        results = [[path, seeds]]

    last_seeds = list(seeds)

        
    print('Outputs:')
    log_path = os.path.join(current_outdir, 'dream_log.txt')
    write_log_message(normalized_prompt, results, log_path)

    print('goodbye!')


def write_log_message(prompt, results, log_path):
    """logs the name of the output image, prompt, and prompt args to the terminal and log file"""
    log_lines = [f'{r[0]}: {prompt} -S{r[1]}\n' for r in results]
    print(*log_lines, sep='')

    with open(log_path, 'a', encoding='utf-8') as file:
        file.writelines(log_lines)


SAMPLER_CHOICES=[
    'ddim',
    'k_dpm_2_a',
    'k_dpm_2',
    'k_euler_a',
    'k_euler',
    'k_heun',
    'k_lms',
    'plms',
]

def create_argv_parser():
    parser = argparse.ArgumentParser(
        description="""Generate images using Stable Diffusion.
        Use --web to launch the web interface. 
        Use --from_file to load prompts from a file path or standard input ("-").
        Otherwise you will be dropped into an interactive command prompt (type -h for help.)
        Other command-line arguments are defaults that can usually be overridden
        prompt the command prompt.
"""
    )
    parser.add_argument(
        '--laion400m',
        '--latent_diffusion',
        '-l',
        dest='laion400m',
        action='store_true',
        help='Fallback to the latent diffusion (laion400m) weights and config',
    )
    parser.add_argument(
        '--from_file',
        dest='infile',
        type=str,
        help='If specified, load prompts from this file',
    )
    parser.add_argument(
        '-n',
        '--iterations',
        type=int,
        default=1,
        help='Number of images to generate',
    )
    parser.add_argument(
        '-F',
        '--full_precision',
        dest='full_precision',
        action='store_true',
        help='Use slower full precision math for calculations',
        # MPS only functions with full precision, see https://github.com/lstein/stable-diffusion/issues/237
        default=choose_torch_device() == 'mps',
    )
    parser.add_argument(
        '-g',
        '--grid',
        action='store_true',
        help='Generate a grid instead of individual images',
    )
    parser.add_argument(
        '-A',
        '-m',
        '--sampler',
        dest='sampler_name',
        choices=SAMPLER_CHOICES,
        metavar='SAMPLER_NAME',
        default='k_lms',
        help=f'Set the initial sampler. Default: k_lms. Supported samplers: {", ".join(SAMPLER_CHOICES)}',
    )
    parser.add_argument(
        '--outdir',
        '-o',
        type=str,
        default='outputs/img-samples',
        help='Directory to save generated images and a log of prompts and seeds. Default: outputs/img-samples',
    )
    parser.add_argument(
        '--embedding_path',
        type=str,
        help='Path to a pre-trained embedding manager checkpoint - can only be set on command line',
    )
    parser.add_argument(
        '--prompt_as_dir',
        '-p',
        action='store_true',
        help='Place images in subdirectories named after the prompt.',
    )
    # GFPGAN related args
    parser.add_argument(
        '--gfpgan_bg_upsampler',
        type=str,
        default='realesrgan',
        help='Background upsampler. Default: realesrgan. Options: realesrgan, none. Only used if --gfpgan is specified',

    )
    parser.add_argument(
        '--gfpgan_bg_tile',
        type=int,
        default=400,
        help='Tile size for background sampler, 0 for no tile during testing. Default: 400.',
    )
    parser.add_argument(
        '--gfpgan_model_path',
        type=str,
        default='experiments/pretrained_models/GFPGANv1.3.pth',
        help='Indicates the path to the GFPGAN model, relative to --gfpgan_dir.',
    )
    parser.add_argument(
        '--gfpgan_dir',
        type=str,
        default='../GFPGAN',
        help='Indicates the directory containing the GFPGAN code.',
    )
    parser.add_argument(
        '--web',
        dest='web',
        action='store_true',
        help='Start in web server mode.',
    )
    parser.add_argument(
        '--weights',
        default='model',
        help='Indicates the Stable Diffusion model to use.',
    )
    parser.add_argument(
        '--device',
        '-d',
        type=str,
        default='cuda',
        help="device to run stable diffusion on. defaults to cuda `torch.cuda.current_device()` if available"
    )
    return parser


def create_cmd_parser():
    parser = argparse.ArgumentParser(
        description='Example: dream> a fantastic alien landscape -W1024 -H960 -s100 -n12'
    )
    parser.add_argument('prompt')
    parser.add_argument('-s', '--steps', type=int, help='Number of steps')
    parser.add_argument(
        '-S',
        '--seed',
        type=int,
        help='Image seed; a +ve integer, or use -1 for the previous seed, -2 for the one before that, etc',
    )
    parser.add_argument(
        '-n',
        '--iterations',
        type=int,
        default=1,
        help='Number of samplings to perform (slower, but will provide seeds for individual images)',
    )
    parser.add_argument(
        '-W', '--width', type=int, help='Image width, multiple of 64'
    )
    parser.add_argument(
        '-H', '--height', type=int, help='Image height, multiple of 64'
    )
    parser.add_argument(
        '-C',
        '--cfg_scale',
        default=7.5,
        type=float,
        help='Classifier free guidance (CFG) scale - higher numbers cause generator to "try" harder.',
    )
    parser.add_argument(
        '-g', '--grid', action='store_true', help='generate a grid'
    )
    parser.add_argument(
        '--outdir',
        '-o',
        type=str,
        default=None,
        help='Directory to save generated images and a log of prompts and seeds',
    )
    parser.add_argument(
        '-i',
        '--individual',
        action='store_true',
        help='Generate individual files (default)',
    )
    parser.add_argument(
        '-I',
        '--init_img',
        type=str,
        help='Path to input image for img2img mode (supersedes width and height)',
    )
    parser.add_argument(
        '-T',
        '-fit',
        '--fit',
        action='store_true',
        help='If specified, will resize the input image to fit within the dimensions of width x height (512x512 default)',
    )
    parser.add_argument(
        '-f',
        '--strength',
        default=0.75,
        type=float,
        help='Strength for noising/unnoising. 0.0 preserves image exactly, 1.0 replaces it completely',
    )
    parser.add_argument(
        '-G',
        '--gfpgan_strength',
        default=0,
        type=float,
        help='The strength at which to apply the GFPGAN model to the result, in order to improve faces.',
    )
    parser.add_argument(
        '-U',
        '--upscale',
        nargs='+',
        default=None,
        type=float,
        help='Scale factor (2, 4) for upscaling followed by upscaling strength (0-1.0). If strength not specified, defaults to 0.75'
    )
    parser.add_argument(
        '-save_orig',
        '--save_original',
        action='store_true',
        help='Save original. Use it when upscaling to save both versions.',
    )
    # variants is going to be superseded by a generalized "prompt-morph" function
    #    parser.add_argument('-v','--variants',type=int,help="in img2img mode, the first generated image will get passed back to img2img to generate the requested number of variants")
    parser.add_argument(
        '-x',
        '--skip_normalize',
        action='store_true',
        help='Skip subprompt weight normalization',
    )
    parser.add_argument(
        '-A',
        '-m',
        '--sampler',
        dest='sampler_name',
        default=None,
        type=str,
        choices=SAMPLER_CHOICES,
        metavar='SAMPLER_NAME',
        help=f'Switch to a different sampler. Supported samplers: {", ".join(SAMPLER_CHOICES)}',
    )
    parser.add_argument(
        '-t',
        '--log_tokenization',
        action='store_true',
        help='shows how the prompt is split into tokens'
    )
    return parser


if __name__ == '__main__':
    prompt = 'a chinese dragon in * style'
    main(prompt, 4)