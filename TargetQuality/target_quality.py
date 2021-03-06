import subprocess
from subprocess import STDOUT, PIPE
from Av1an.commandtypes import CommandPair, Command
from Projects import Project
from VMAF import call_vmaf, read_json
from Chunks.chunk import Chunk
from math import log as ln
from math import ceil, floor
from Av1an.bar import process_pipe
import numpy as np
from scipy import interpolate


def transform_vmaf(vmaf):
    if vmaf<99.99:
        return -ln(1-vmaf/100)
    else:
        # return -ln(1-99.99/100)
        return 9.210340371976184


def read_weighted_vmaf(file, percentile=0):
    """Reads vmaf file with vmaf scores in it and return N percentile score from it.

    :return: N percentile score
    :rtype: float
    """

    jsn = read_json(file)

    vmafs = sorted([x['metrics']['vmaf'] for x in jsn['frames']])

    percentile = percentile if percentile != 0 else 0.25
    score = get_percentile(vmafs, percentile)

    return round(score, 2)


def get_percentile(scores, percent):
    """
    Find the percentile of a list of values.
    :param scores: - is a list of values. Note N MUST BE already sorted.
    :param percent: - a float value from 0.0 to 1.0.
    :return: - the percentile of the values
    """
    scores = sorted(scores)
    key = lambda x: x

    k = (len(scores)-1) * percent
    f = floor(k)
    c = ceil(k)
    if f == c:
        return key(scores[int(k)])
    d0 = (scores[int(f)]) * (c-k)
    d1 = (scores[int(c)]) * (k-f)
    return d0+d1


def adapt_probing_rate(rate, frames):
    """
    Change probing rate depending on amount of frames in scene.
    Ensure that low frame count scenes get decent amount of probes

    :param rate: given rate of probing
    :param frames: amount of frames in scene
    :return: new probing rate
    """

    if frames < 20:
        return 1
    elif frames < 40:
        return min(rate, 2)
    elif frames < 120:
        return min(rate, 3)
    elif frames < 240:
        return min(rate, 4)
    elif frames > 240:
        return max(rate, 5)
    elif frames > 480:
        return 10


def get_target_q(scores, target_quality):
    """
    Interpolating scores to get Q closest to target
    Interpolation type for 2 probes changes to linear
    """
    x = [x[1] for x in sorted(scores)]
    y = [float(x[0]) for x in sorted(scores)]

    if len(x) > 2:
        interpolation = 'quadratic'
    else:
        interpolation = 'linear'
    f = interpolate.interp1d(x, y, kind=interpolation)
    xnew = np.linspace(min(x), max(x), max(x) - min(x))
    tl = list(zip(xnew, f(xnew)))
    q = min(tl, key=lambda l: abs(l[1] - target_quality))

    return int(q[0]), round(q[1], 3)


def weighted_search(num1, vmaf1, num2, vmaf2, target):
    """
    Returns weighted value closest to searched

    :param num1: Q of first probe
    :param vmaf1: VMAF of first probe
    :param num2: Q of second probe
    :param vmaf2: VMAF of first probe
    :param target: VMAF target
    :return: Q for new probe
    """

    dif1 = abs(transform_vmaf(target) - transform_vmaf(vmaf2))
    dif2 = abs(transform_vmaf(target) - transform_vmaf(vmaf1))

    tot = dif1 + dif2

    new_point = int(round(num1 * (dif1 / tot) + (num2 * (dif2 / tot))))
    return new_point


def probe_cmd(chunk: Chunk, q, ffmpeg_pipe, encoder, probing_rate) -> CommandPair:
    """
    Generate and return commands for probes at set Q values
    These are specifically not the commands that are generated
    by the user or encoder defaults, since these
    should be faster than the actual encoding commands.
    These should not be moved into encoder classes at this point.
    """
    pipe = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-i', '-', '-vf',
            f'select=not(mod(n\\,{probing_rate}))', *ffmpeg_pipe]

    probe_name = gen_probes_names(chunk, q).with_suffix('.ivf').as_posix()

    if encoder == 'aom':
        params = ['aomenc', '--passes=1', '--threads=8',
                  '--end-usage=q', '--cpu-used=6', f'--cq-level={q}']
        cmd = CommandPair(pipe, [*params, '-o', probe_name, '-'])

    elif encoder == 'x265':
        params = ['x265', '--log-level', '0', '--no-progress',
                  '--y4m', '--preset', 'medium', '--crf', f'{q}']
        cmd = CommandPair(pipe, [*params, '-o', probe_name, '-'])

    elif encoder == 'rav1e':
        params = ['rav1e', '-y', '-s', '10', '--tiles', '8', '--quantizer', f'{q}']
        cmd = CommandPair(pipe, [*params, '-o', probe_name, '-'])

    elif encoder == 'vpx':
        params = ['vpxenc', '-b', '10', '--profile=2','--passes=1', '--pass=1', '--codec=vp9',
                  '--threads=8', '--cpu-used=9', '--end-usage=q',
                  f'--cq-level={q}']
        cmd = CommandPair(pipe, [*params, '-o', probe_name, '-'])

    elif encoder == 'svt_av1':
        params = ['SvtAv1EncApp', '-i', 'stdin',
                  '--preset', '8', '--rc', '0', '--qp', f'{q}']
        cmd = CommandPair(pipe, [*params, '-b', probe_name, '-'])

    elif encoder == 'svt_vp9':
        params = ['SvtVp9EncApp', '-i', 'stdin',
                  '-enc-mode', '8', '-q', f'{q}']
        # TODO: pipe needs to output rawvideo
        cmd = CommandPair(pipe, [*params, '-b', probe_name, '-'])

    elif encoder == 'x264':
        params = ['x264', '--log-level', 'error', '--demuxer', 'y4m',
                  '-', '--no-progress', '--preset', 'slow', '--crf',
                  f'{q}']
        cmd = CommandPair(pipe, [*params, '-o', probe_name, '-'])

    return cmd


def gen_probes_names(chunk: Chunk, q):
    """Make name of vmaf probe
    """
    return chunk.fake_input_path.with_name(f'v_{q}{chunk.name}').with_suffix('.ivf')


def make_pipes(ffmpeg_gen_cmd: Command, command: CommandPair):

    ffmpeg_gen_pipe = subprocess.Popen(ffmpeg_gen_cmd, stdout=PIPE, stderr=STDOUT)
    ffmpeg_pipe = subprocess.Popen(command[0], stdin=ffmpeg_gen_pipe.stdout, stdout=PIPE, stderr=STDOUT)
    pipe = subprocess.Popen(command[1], stdin=ffmpeg_pipe.stdout, stdout=PIPE,
                            stderr=STDOUT,
                            universal_newlines=True)

    return pipe


def vmaf_probe(chunk: Chunk, q,  args: Project, probing_rate):
    """
    Make encoding probe to get VMAF that Q returns

    :param chunk: the Chunk
    :param q: Value to make probe
    :param args: the Project
    :return : path to json file with vmaf scores
    """

    cmd = probe_cmd(chunk, q, args.ffmpeg_pipe, args.encoder, probing_rate)
    pipe = make_pipes(chunk.ffmpeg_gen_cmd, cmd)
    process_pipe(pipe)

    file = call_vmaf(chunk, gen_probes_names(chunk, q), args.n_threads, args.vmaf_path, args.vmaf_res, vmaf_filter=args.vmaf_filter,
                     vmaf_rate=probing_rate)
    return file


def get_closest(q_list, q, positive=True):
    """
    Returns closest value from the list, ascending or descending

    :param q_list: list of q values that been already used
    :param q:
    :param positive: search direction, positive - only values bigger than q
    :return: q value from list
    """
    if positive:
        q_list = [x for x in q_list if x > q]
    else:
        q_list = [x for x in q_list if x < q]

    return min(q_list, key=lambda x: abs(x - q))




