import os
import re

from Av1an.arg_parse import Args
from Chunks.chunk import Chunk
from Av1an.commandtypes import MPCommands, CommandPair, Command
from Encoders.encoder import Encoder
from Av1an.utils import list_index_of_regex


class X265(Encoder):

    def __init__(self):
        super().__init__(
            encoder_bin='x265',
            encoder_help='x265 --fullhelp',
            default_args=['-p', 'slow', '--crf', '23'],
            default_passes=1,
            default_q_range=(20, 40),
            output_extension='mkv'
        )

    def compose_1_pass(self, a: Args, c: Chunk, output: str) -> MPCommands:
        return [
            CommandPair(
                Encoder.compose_ffmpeg_pipe(a),
                ['x265', '--y4m', '--frames', str(c.frames), *a.video_params, '-', '-o', output]
            )
        ]

    def compose_2_pass(self, a: Args, c: Chunk, output: str) -> MPCommands:
        return [
            CommandPair(
                Encoder.compose_ffmpeg_pipe(a),
                ['x265', '--log-level', 'error', '--no-progress', '--pass', '1', '--y4m', '--frames', str(c.frames), *a.video_params, '--stats', f'{c.fpf}.log',
                 '-', '-o', os.devnull]
            ),
            CommandPair(
                Encoder.compose_ffmpeg_pipe(a),
                ['x265', '--log-level', 'error', '--pass', '2', '--y4m', '--frames', str(c.frames), *a.video_params, '--stats', f'{c.fpf}.log',
                 '-', '-o', output]
            )
        ]

    def man_q(self, command: Command, q: int) -> Command:
        """Return command with new cq value

        :param command: old command
        :param q: new cq value
        :return: command with new cq value"""

        adjusted_command = command.copy()

        i = list_index_of_regex(adjusted_command, r"--crf")
        adjusted_command[i + 1] = f'{q}'

        return adjusted_command

    def match_line(self, line: str):
        """Extract number of encoded frames from line.

        :param line: one line of text output from the encoder
        :return: match object from re.search matching the number of encoded frames"""

        return re.search(r"^\[.*\]\s(\d+)\/\d+", line)
